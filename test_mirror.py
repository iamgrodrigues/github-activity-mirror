import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import mirror


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
		self.assertEqual(loaded["destination"]["path"], self.destination)
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

	def run_mirror(self, **kwargs):
		output = io.StringIO()
		with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
			mirror.run(self.config, **kwargs)
		return json.loads(output.getvalue())

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
