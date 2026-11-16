import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import mirror
from activity_mirror import destination as mirror_destination
from activity_mirror import setup as mirror_setup
from activity_mirror.config import parse_config
from activity_mirror.models import Destination, GitIdentity


class MirrorTests(unittest.TestCase):
	def setUp(self):
		self.temporary = tempfile.TemporaryDirectory()
		self.addCleanup(self.temporary.cleanup)
		self.root = Path(self.temporary.name)
		self.source = self.root / "source"
		self.destination = self.root / "mirror"
		self.source.mkdir()
		self.git(self.source, "init", "--initial-branch=main")
		self.config = {
			"destination": {
				"path": self.destination,
				"author_name": "Personal Developer",
				"author_email": "personal@example.test"
			},
			"companies": [{
				"name": "synthetic-company",
				"repositories": [{
					"path": self.source,
					"ref": "main",
					"author_emails": ["work@example.test"]
				}]
			}]
		}

	def test_preview_is_read_only_and_outputs_only_daily_counts(self):
		first = self.commit("2024-01-02T08:13:47-03:00")
		self.commit("2024-01-02T20:59:14+02:00")
		self.commit("2024-01-03T10:00:00+00:00", email="other@example.test")
		before = self.git(self.source, "rev-parse", "HEAD")
		output = self.run_mirror()
		expected = [{"date": "2024-01-02", "count": 2}]
		self.assertEqual(output, {"daily_totals": expected, "new_mirror_commits": expected})
		self.assertFalse(self.destination.exists())
		self.assertEqual(self.git(self.source, "rev-parse", "HEAD"), before)
		self.assertNotIn(first, json.dumps(output))
		self.assertNotIn("work@example.test", json.dumps(output))

	def test_historical_apply_is_private_idempotent_and_resumes_after_partial_history(self):
		original = self.commit("2023-02-01T04:21:09-03:00")
		self.commit("2023-02-01T19:11:12-03:00")
		self.run_mirror(apply=True)
		head = self.git(self.destination, "rev-parse", "HEAD")
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "2")
		self.assertEqual(self.run_mirror(apply=True)["new_mirror_commits"], [])
		self.assertEqual(self.git(self.destination, "rev-parse", "HEAD"), head)
		history = self.git(self.destination, "log", "--format=fuller")
		self.assertIn("personal@example.test", history)
		for secret in (original, "work@example.test", "Confidential source message", "PrivateFeature.cls"):
			self.assertNotIn(secret, history)
		self.assertIn("PrivateFeature.cls", self.git(self.source, "ls-tree", "-r", "HEAD"))
		self.assertEqual(self.git(self.destination, "ls-tree", "-r", "HEAD"), "")
		self.assertEqual(self.git(self.destination, "log", "--format=%aI").splitlines(), [
			"2023-02-01T12:00:00+00:00", "2023-02-01T12:00:00+00:00"
		])
		state = self.destination / ".git" / mirror.STATE_NAME
		self.assertEqual(state.stat().st_mode & 0o777, 0o600)
		# Simulate a source already checkpointed before an interrupted commit loop.
		new = self.commit("2023-02-02T08:00:00+00:00")
		ledger = json.loads(state.read_text())
		ledger["commits"][new] = "2023-02-02"
		state.write_text(json.dumps(ledger))
		self.run_mirror(apply=True)
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "3")
		self.assertEqual(self.git(self.destination, "remote").strip(), "")

	def test_duplicate_sources_and_company_changes_keep_old_activity_without_rescanning(self):
		self.commit("2024-03-01T10:00:00+00:00")
		source = self.config["companies"][0]["repositories"][0]
		self.config["companies"].append({"name": "overlap", "repositories": [source.copy()]})
		self.run_mirror(apply=True)
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "1")
		new_source = self.root / "future-source"
		new_source.mkdir()
		self.git(new_source, "init", "--initial-branch=main")
		self.commit("2024-03-01T11:00:00+00:00", repository=new_source)
		self.config["companies"] = [{
			"name": "future-company",
			"repositories": [{**source, "path": new_source}]
		}]
		self.run_mirror(apply=True)
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "2")

	def test_date_window_limits_new_imports_without_deleting_old_activity(self):
		self.commit("2024-01-01T10:00:00+00:00")
		self.commit("2024-02-01T10:00:00+00:00")
		result = self.run_mirror(since=datetime.date(2024, 2, 1), apply=True)
		self.assertEqual(result["daily_totals"], [{"date": "2024-02-01", "count": 1}])
		self.run_mirror(until=datetime.date(2024, 1, 31), apply=True)
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "2")

	def test_only_configured_ref_is_read_and_merge_commits_are_excluded(self):
		self.commit("2024-01-01T10:00:00+00:00")
		self.git(self.source, "checkout", "-b", "feature")
		self.commit("2024-01-02T10:00:00+00:00")
		self.git(self.source, "checkout", "main")
		self.assertEqual(self.run_mirror()["daily_totals"], [{"date": "2024-01-01", "count": 1}])
		self.git(
			self.source, "-c", "user.name=Work Developer", "-c", "user.email=work@example.test",
			"-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
			"merge", "--no-ff", "-m", "Private merge message", "feature"
		)
		self.assertEqual(self.run_mirror()["daily_totals"], [
			{"date": "2024-01-01", "count": 1}, {"date": "2024-01-02", "count": 1}
		])

	def test_apply_rejects_rewritten_destination_even_when_daily_counts_match(self):
		self.commit("2024-01-01T10:00:00+00:00")
		self.run_mirror(apply=True)
		self.commit("2024-01-02T10:00:00+00:00")
		state = self.destination / ".git" / mirror.STATE_NAME
		before_state = state.read_bytes()
		original_head = self.git(self.destination, "rev-parse", "HEAD")
		original_lock = mirror.DestinationLock

		@contextlib.contextmanager
		def concurrent_rewrite(git_dir):
			with original_lock(git_dir) as acquired:
				env = self.environment()
				env.update({
					"GIT_AUTHOR_NAME": "Personal Developer",
					"GIT_AUTHOR_EMAIL": "personal@example.test",
					"GIT_AUTHOR_DATE": "2024-01-01T12:00:00+00:00",
					"GIT_COMMITTER_NAME": "Personal Developer",
					"GIT_COMMITTER_EMAIL": "personal@example.test",
					"GIT_COMMITTER_DATE": "2024-01-01T13:00:00+00:00"
				})
				subprocess.run([
					"git", "-C", str(self.destination), "-c", "core.hooksPath=/dev/null",
					"-c", "commit.gpgSign=false", "commit", "--amend", "--no-edit",
					"--allow-empty", "--quiet"
				], env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
				self.assertNotEqual(self.git(self.destination, "rev-parse", "HEAD"), original_head)
				yield acquired

		with mock.patch.object(mirror, "DestinationLock", concurrent_rewrite):
			with self.assertRaisesRegex(mirror.MirrorError, "changed during collection"):
				self.run_mirror(apply=True)
		self.assertEqual(state.read_bytes(), before_state)
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "1")

	def test_destination_writer_needs_only_personal_identity_and_daily_counts(self):
		destination = Destination(
			self.destination, GitIdentity("Personal Developer", "personal@example.test")
		)
		plan = mirror.build_plan({"2024-01-02": 2, "2024-01-01": 1}, ())
		snapshot = mirror_destination.inspect_destination(destination)
		mirror_destination.prepare_destination(destination, snapshot)
		mirror_destination.write_commits(destination, plan)
		self.assertEqual(self.git(self.destination, "log", "--format=%aI").splitlines(), [
			"2024-01-02T12:00:00+00:00", "2024-01-02T12:00:00+00:00",
			"2024-01-01T12:00:00+00:00"
		])
		self.assertEqual(self.git(self.destination, "log", "--format=%ae:%ce").splitlines(), [
			"personal@example.test:personal@example.test"
		] * 3)
		objects = self.git(
			self.destination, "cat-file", "--batch-all-objects", "--batch-check=%(objecttype)"
		).splitlines()
		self.assertCountEqual(objects, ["commit", "commit", "commit", "tree"])
		self.assertFalse((self.destination / ".git" / mirror.STATE_NAME).exists())

	def test_interrupted_writer_keeps_checkpoint_and_resumes_only_missing_commits(self):
		self.commit("2024-01-01T10:00:00+00:00")
		self.commit("2024-01-02T10:00:00+00:00")
		original_git = mirror_destination.git
		writes = 0

		def interrupted_git(path, arguments, **kwargs):
			nonlocal writes
			if "commit" in arguments:
				writes += 1
				if writes == 2:
					raise mirror.MirrorError("Synthetic write interruption.")
			return original_git(path, arguments, **kwargs)

		with mock.patch.object(mirror_destination, "git", interrupted_git):
			with self.assertRaisesRegex(mirror.MirrorError, "Synthetic write interruption"):
				self.run_mirror(apply=True)
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "1")
		state = json.loads((self.destination / ".git" / mirror.STATE_NAME).read_text())
		self.assertEqual(len(state["commits"]), 2)
		self.assertEqual(self.run_mirror(apply=True)["new_mirror_commits"], [
			{"date": "2024-01-02", "count": 1}
		])
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "2")
		self.assertEqual(self.run_mirror(apply=True)["new_mirror_commits"], [])

	def test_errors_never_silently_drop_sources_or_accept_foreign_destinations(self):
		self.commit("2024-01-01T10:00:00+00:00")
		self.config["companies"][0]["repositories"][0]["ref"] = "missing"
		with self.assertRaises(mirror.MirrorError):
			self.run_mirror(apply=True)
		self.assertFalse(self.destination.exists())
		self.config["companies"][0]["repositories"][0]["ref"] = "main"
		self.run_mirror(apply=True)
		(self.destination / ".git" / mirror.STATE_NAME).unlink()
		with self.assertRaisesRegex(mirror.MirrorError, "ledger is missing"):
			self.run_mirror(apply=True)

	def test_future_dates_and_dirty_destinations_are_rejected(self):
		self.commit("2099-01-01T10:00:00+00:00")
		with self.assertRaisesRegex(mirror.MirrorError, "future-dated"):
			self.run_mirror()
		self.run_mirror(until=datetime.date(2025, 1, 1), apply=True)
		(self.destination / "unexpected.txt").write_text("do not publish")
		with self.assertRaisesRegex(mirror.MirrorError, "no files"):
			self.run_mirror()

	def test_configuration_rejects_overlap_and_unknown_fields(self):
		raw = {
			"destination": {
				"path": str(self.source / "nested"),
				"author_name": "Personal Developer",
				"author_email": "personal@example.test"
			},
			"companies": [{
				"name": "test",
				"repositories": [{
					"path": str(self.source), "ref": "main",
					"author_emails": ["work@example.test"]
				}]
			}]
		}
		config_path = self.root / "config.json"
		config_path.write_text(json.dumps(raw))
		with self.assertRaisesRegex(mirror.MirrorError, "overlap"):
			mirror.load_config(config_path)
		raw["destination"]["path"] = str(self.destination)
		config_path.write_text(json.dumps(raw))
		loaded = mirror.load_config(config_path)
		self.assertEqual(loaded.destination.path, self.destination)
		raw["token"] = "must-not-be-needed"
		config_path.write_text(json.dumps(raw))
		with self.assertRaisesRegex(mirror.MirrorError, "required keys"):
			mirror.load_config(config_path)

	def test_apply_requires_explicit_acknowledgment_before_reading_sources(self):
		result = subprocess.run(
			["python3", str(Path(mirror.__file__).resolve()), "--apply", "--config", "/missing-config"],
			text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
		)
		self.assertEqual(result.returncode, 1)
		self.assertIn("--allow-metadata-export", result.stderr)

	def test_cli_loads_local_configuration_and_applies_only_when_requested(self):
		self.commit("2024-01-02T10:00:00+00:00")
		config_path = self.root / "config.json"
		config_path.write_text(json.dumps(self.config, default=str))
		command = ["python3", str(Path(mirror.__file__).resolve()), "--config", str(config_path)]
		preview = subprocess.run(command, check=True, text=True, capture_output=True)
		self.assertFalse(self.destination.exists())
		self.assertEqual(json.loads(preview.stdout)["new_mirror_commits"], [
			{"date": "2024-01-02", "count": 1}
		])
		subprocess.run(
			command + ["--apply", "--allow-metadata-export"],
			check=True, text=True, capture_output=True
		)
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "1")

	def test_setup_preserves_existing_configuration_without_reading_sources(self):
		config_path = self.root / "config.json"
		original = json.dumps(self.config, default=str).encode()
		config_path.write_bytes(original)
		for answers in ("", "yes\n"):
			with self.subTest(answers=answers):
				result = subprocess.run(
					[sys.executable, str(Path(mirror.__file__).resolve()), "--setup", "--config", str(config_path)],
					input=answers, text=True, capture_output=True, env=self.environment()
				)
				self.assertEqual(config_path.read_bytes(), original)
				self.assertFalse(self.destination.exists())
				self.assertEqual(result.returncode, 0, result.stderr)
				self.assertIn("unchanged", result.stdout)

	def test_setup_saves_private_settings_and_previews_saved_history_before_explicit_apply(self):
		self.commit("2023-12-31T10:00:00+00:00")
		self.commit("2024-01-02T10:00:00+00:00")
		self.commit("2024-02-02T10:00:00+00:00")
		before = self.git(self.source, "rev-parse", "HEAD")
		result = self.run_setup(self.setup_answers())
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn("Starting preview only", result.stdout)
		self.assertIn("New mirror commits: 1", result.stdout)
		self.assertFalse(self.destination.exists())
		config_path = self.root / "config.wizard.json"
		raw = json.loads(config_path.read_text())
		self.assertEqual(raw["history"], {"since": "2024-01-01", "until": "2024-01-31"})
		if os.name == "posix":
			self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
		expected = [{"date": "2024-01-02", "count": 1}]
		plan, _ = json.JSONDecoder().raw_decode(result.stdout[result.stdout.index("{\n"):])
		self.assertEqual(plan, {"daily_totals": expected, "new_mirror_commits": expected})
		command = [sys.executable, str(Path(mirror.__file__).resolve()), "--config", str(config_path)]
		repeated = subprocess.run(command, check=True, text=True, capture_output=True, env=self.environment())
		self.assertEqual(json.loads(repeated.stdout)["new_mirror_commits"], expected)
		override = subprocess.run(
			command + ["--since", "2023-12-01", "--until", "2024-02-29"],
			check=True, text=True, capture_output=True, env=self.environment()
		)
		self.assertEqual(len(json.loads(override.stdout)["new_mirror_commits"]), 3)
		self.assertFalse(self.destination.exists())
		subprocess.run(
			command + ["--apply", "--allow-metadata-export"],
			check=True, text=True, capture_output=True, env=self.environment()
		)
		self.assertEqual(self.git(self.destination, "log", "--format=%aI").splitlines(), [
			"2024-01-02T12:00:00+00:00"
		])
		self.assertEqual(self.git(self.source, "rev-parse", "HEAD"), before)

	def test_setup_cancellation_does_not_save_settings_or_create_a_destination(self):
		self.commit("2024-01-02T10:00:00+00:00")
		for answers in (["n"], ["y"], self.setup_answers()[:-1] + ["n"]):
			with self.subTest(answers=answers):
				result = self.run_setup(answers)
				self.assertEqual(result.returncode, 0, result.stderr)
				self.assertIn("cancelled", result.stdout)
				self.assertFalse((self.root / "config.wizard.json").exists())
				self.assertFalse(self.destination.exists())

	def test_setup_retries_invalid_source_and_history_answers(self):
		self.commit("2024-01-02T10:00:00+00:00")
		answers = [
			"y", "Personal Developer", "personal@example.test", str(self.destination),
			str(self.root / "missing"), str(self.source), "", "work@example.test", "n",
			"2024-02-30", "2024-02-01", "2024-01-31", "2024-01-01", "", "y"
		]
		result = self.run_setup(answers)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn("Please correct the source", result.stdout)
		self.assertIn("Use a real date", result.stdout)
		self.assertIn("first date must not be after", result.stdout)
		raw = json.loads((self.root / "config.wizard.json").read_text())
		self.assertEqual(raw["history"], {"since": "2024-01-01", "until": "2024-01-31"})
		self.assertFalse(self.destination.exists())

	def test_setup_writer_never_replaces_files_and_cleans_up_failed_writes(self):
		path = self.root / "config.wizard.json"
		path.write_bytes(b"existing private settings")
		with self.assertRaisesRegex(mirror.MirrorError, "left unchanged"):
			mirror_setup.save_new_config(path, {"new": "settings"})
		self.assertEqual(path.read_bytes(), b"existing private settings")
		path.unlink()
		with mock.patch.object(mirror_setup.os, "fsync", side_effect=OSError("Synthetic disk failure")):
			with self.assertRaisesRegex(OSError, "Synthetic disk failure"):
				mirror_setup.save_new_config(path, {"new": "settings"})
		self.assertFalse(path.exists())

	def test_setup_cannot_be_combined_with_apply_or_date_flags(self):
		for flags in (
			["--apply"], ["--allow-metadata-export"],
			["--since", "2024-01-01"], ["--until", "2024-01-31"]
		):
			with self.subTest(flags=flags):
				result = self.run_setup([], flags)
				self.assertEqual(result.returncode, 1)
				self.assertIn("cannot be combined", result.stderr)
				self.assertFalse((self.root / "config.wizard.json").exists())
				self.assertFalse(self.destination.exists())

	def test_setup_refuses_to_save_settings_inside_a_source_or_mirror(self):
		self.commit("2024-01-02T10:00:00+00:00")
		for folder in (self.source, self.destination):
			with self.subTest(folder=folder.name):
				path = folder / "config.setup.json"
				result = self.run_setup(self.setup_answers(), config_path=path)
				self.assertEqual(result.returncode, 1)
				self.assertIn("outside", result.stderr)
				self.assertFalse(path.exists())
		self.assertEqual(self.git(self.source, "status", "--porcelain"), "")
		self.assertFalse(self.destination.exists())

	def test_setup_supports_all_history_and_source_paths_with_spaces(self):
		moved = self.root / "approved source"
		self.source.rename(moved)
		self.source = moved
		self.commit("2023-01-02T10:00:00+00:00")
		self.commit("2024-01-02T10:00:00+00:00")
		result = self.run_setup([
			"y", "Personal Developer", "personal@example.test", str(self.destination),
			f'"{self.source}"', "", "work@example.test", "n", "all", "ongoing", "y"
		])
		self.assertEqual(result.returncode, 0, result.stderr)
		raw = json.loads((self.root / "config.wizard.json").read_text())
		self.assertEqual(raw["history"], {"since": None, "until": None})
		plan, _ = json.JSONDecoder().raw_decode(result.stdout[result.stdout.index("{\n"):])
		self.assertEqual(plan["new_mirror_commits"], [
			{"date": "2023-01-02", "count": 1}, {"date": "2024-01-02", "count": 1}
		])
		self.assertEqual(self.git(self.source, "status", "--porcelain"), "")
		self.assertFalse(self.destination.exists())

	def test_saved_history_rejects_invalid_and_conflicting_limits_before_collection(self):
		for history in (
			{"since": "2024-02-30", "until": None},
			{"since": None, "until": 2024},
			{"since": "2024-02-01", "until": "2024-01-01"}
		):
			with self.subTest(history=history):
				self.config["history"] = history
				with self.assertRaisesRegex(mirror.MirrorError, "history."):
					self.run_mirror(apply=True)
				self.assertFalse(self.destination.exists())
		self.config["history"] = {"since": "2024-01-01", "until": "2024-01-31"}
		path = self.root / "config.wizard.json"
		path.write_text(json.dumps(self.config, default=str))
		result = subprocess.run(
			[
				sys.executable, str(Path(mirror.__file__).resolve()), "--config", str(path),
				"--until", "2023-12-31"
			], text=True, capture_output=True, env=self.environment()
		)
		self.assertEqual(result.returncode, 1)
		self.assertIn("including saved history limits", result.stderr)
		self.assertFalse(self.destination.exists())

	def test_setup_reports_missing_git_before_prompting_or_saving(self):
		path = self.root / "config.wizard.json"
		with mock.patch.object(mirror_setup.shutil, "which", return_value=None):
			with mock.patch("builtins.input") as prompt:
				with self.assertRaisesRegex(mirror.MirrorError, "Git was not found"):
					mirror_setup.configure(path)
		prompt.assert_not_called()
		self.assertFalse(path.exists())

	def test_configuration_examples_are_accepted_by_the_loader(self):
		root = Path(mirror.__file__).resolve().parent
		mirror.load_config(root / "config.example.json")
		manual = (root / "docs" / "manual-setup.md").read_text()
		example = manual.split("```json\n", 1)[1].split("\n```", 1)[0]
		parse_config(json.loads(example), self.root)

	def run_mirror(self, **kwargs):
		output = io.StringIO()
		config = parse_config(json.loads(json.dumps(self.config, default=str)), self.root)
		with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
			mirror.run(config, **kwargs)
		return json.loads(output.getvalue())

	def run_setup(self, answers, extra=(), config_path=None):
		return subprocess.run(
			[
				sys.executable, str(Path(mirror.__file__).resolve()), "--setup",
				"--config", str(config_path or self.root / "config.wizard.json"), *extra
			],
			input="\n".join(answers) + "\n", text=True, capture_output=True,
			cwd=self.root, env=self.environment()
		)

	def setup_answers(self):
		return [
			"y", "Personal Developer", "personal@example.test", str(self.destination),
			str(self.source), "", "work@example.test", "n",
			"2024-01-01", "2024-01-31", "y"
		]

	def commit(self, date, email="work@example.test", repository=None):
		repository = repository or self.source
		(repository / "PrivateFeature.cls").write_text(f"Sensitive synthetic source content: {date}\n")
		self.git(repository, "add", "PrivateFeature.cls")
		env = self.environment()
		env.update({
			"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
			"GIT_AUTHOR_NAME": "Work Developer", "GIT_AUTHOR_EMAIL": email,
			"GIT_COMMITTER_NAME": "Work Developer", "GIT_COMMITTER_EMAIL": email
		})
		subprocess.run([
			"git", "-C", str(repository), "-c", "core.hooksPath=/dev/null",
			"-c", "commit.gpgSign=false", "commit", "--allow-empty", "--quiet",
			"-m", "Confidential source message about PrivateFeature.cls"
		], env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		return self.git(repository, "rev-parse", "HEAD").strip()

	def git(self, repository, *arguments):
		return subprocess.run(
			["git", "-C", str(repository), *arguments], env=self.environment(),
			check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
		).stdout

	def environment(self):
		env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
		env.update({
			"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
			"GIT_TERMINAL_PROMPT": "0"
		})
		return env


if __name__ == "__main__":
	unittest.main()
