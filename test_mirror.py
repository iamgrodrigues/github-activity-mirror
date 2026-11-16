import contextlib
from dataclasses import replace
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import mirror
from activity_mirror import collector as mirror_collector
from activity_mirror import destination as mirror_destination
from activity_mirror import setup as mirror_setup
from activity_mirror import publishing as mirror_publishing
from activity_mirror import schedule as mirror_schedule
from activity_mirror import schedulers as native_schedulers
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

	def test_unchanged_sources_skip_history_scans_and_preview_keeps_checkpoints_read_only(self):
		self.commit("2024-01-01T10:00:00+00:00")
		self.run_mirror(apply=True)
		state = {
			path.name: path.read_bytes()
			for path in (self.destination / ".git").glob("activity-mirror-*.json")
		}
		with mock.patch.object(mirror_collector, "git", wraps=mirror_collector.git) as commands:
			preview = self.run_mirror()
			self.assertEqual({
				path.name: path.read_bytes()
				for path in (self.destination / ".git").glob("activity-mirror-*.json")
			}, state)
			self.assertEqual(self.run_mirror(apply=True), preview)
		self.assertEqual(preview["daily_totals"], [{"date": "2024-01-01", "count": 1}])
		self.assertEqual(preview["new_mirror_commits"], [])
		self.assertEqual([call for call in commands.call_args_list if call.args[1][0] == "log"], [])
		for path in (self.destination / ".git").glob("activity-mirror-*.json"):
			self.assertEqual(path.stat().st_mode & 0o777, 0o600)

	def test_incremental_scan_reads_only_new_history_including_backdated_merged_commits(self):
		self.commit("2024-01-02T10:00:00+00:00")
		self.git(self.source, "checkout", "-b", "older-feature")
		backdated = self.commit("2024-01-01T10:00:00+00:00")
		self.git(self.source, "checkout", "main")
		self.run_mirror(apply=True)
		new = self.commit("2024-01-03T10:00:00+00:00")
		self.git(
			self.source, "-c", "user.name=Work Developer", "-c", "user.email=work@example.test",
			"-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
			"merge", "--no-ff", "-s", "ours", "-m", "Synthetic merge", "older-feature"
		)
		scanned = []
		original_git = mirror_collector.git

		def record_scan(path, arguments):
			output = original_git(path, arguments)
			if arguments[0] == "log":
				scanned.extend(line.split("\x00")[0] for line in output.splitlines())
			return output

		with mock.patch.object(mirror_collector, "git", side_effect=record_scan):
			result = self.run_mirror(apply=True)
		self.assertEqual(set(scanned), {backdated, new})
		self.assertEqual(result["new_mirror_commits"], [
			{"date": "2024-01-01", "count": 1}, {"date": "2024-01-03", "count": 1}
		])
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "3")

	def test_scan_checkpoints_invalidate_when_author_filters_or_history_limits_change(self):
		self.commit("2023-01-01T10:00:00+00:00")
		self.commit("2024-01-01T10:00:00+00:00")
		self.commit("2024-01-02T10:00:00+00:00", email="other@example.test")
		self.run_mirror(since=datetime.date(2024, 1, 1), apply=True)
		backfill = self.run_mirror(since=datetime.date(2023, 1, 1), apply=True)
		self.assertEqual(backfill["new_mirror_commits"], [{"date": "2023-01-01", "count": 1}])
		self.config["companies"][0]["repositories"][0]["author_emails"].append("other@example.test")
		result = self.run_mirror(since=datetime.date(2023, 1, 1), apply=True)
		self.assertEqual(result["new_mirror_commits"], [{"date": "2024-01-02", "count": 1}])
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "3")

	def test_rewritten_sources_and_unavailable_checkpoints_keep_old_activity_without_missing_new_commits(self):
		self.commit("2024-01-01T10:00:00+00:00")
		self.run_mirror(apply=True)
		self.git(self.source, "checkout", "--orphan", "rewritten")
		rewritten = self.commit("2024-01-02T10:00:00+00:00")
		self.git(self.source, "update-ref", "refs/heads/main", rewritten)
		self.git(self.source, "checkout", "main")
		self.assertEqual(self.run_mirror(apply=True)["new_mirror_commits"], [
			{"date": "2024-01-02", "count": 1}
		])
		cache_path = self.destination / ".git" / mirror.SCAN_NAME
		cache = json.loads(cache_path.read_text())
		cache["checkpoints"] = {key: "f" * 40 for key in cache["checkpoints"]}
		cache_path.write_text(json.dumps(cache))
		self.commit("2024-01-03T10:00:00+00:00")
		self.assertEqual(self.run_mirror(apply=True)["new_mirror_commits"], [
			{"date": "2024-01-03", "count": 1}
		])
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "3")

	def test_deepening_a_shallow_source_backfills_history_even_when_its_tip_is_unchanged(self):
		self.commit("2024-01-01T10:00:00+00:00")
		tip = self.commit("2024-01-02T10:00:00+00:00")
		shallow = self.root / "shallow"
		self.git(self.root, "clone", "--depth", "1", self.source.as_uri(), str(shallow))
		self.config["companies"][0]["repositories"][0]["path"] = shallow
		self.run_mirror(apply=True)
		self.git(shallow, "fetch", "--unshallow", "origin")
		self.assertEqual(self.git(shallow, "rev-parse", "HEAD").strip(), tip)
		self.assertEqual(self.run_mirror(apply=True)["new_mirror_commits"], [
			{"date": "2024-01-01", "count": 1}
		])
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "2")

	def test_incremental_import_rejects_concurrent_ledger_changes_before_saving_checkpoints(self):
		self.commit("2024-01-01T10:00:00+00:00")
		self.run_mirror(apply=True)
		cache_path = self.destination / ".git" / mirror.SCAN_NAME
		cache_before = cache_path.read_bytes()
		self.commit("2024-01-02T10:00:00+00:00")
		state_path = self.destination / ".git" / mirror.STATE_NAME
		concurrent_state = json.loads(state_path.read_text())
		concurrent_state["commits"]["f" * 40] = "2024-01-03"
		original_lock = mirror.DestinationLock

		@contextlib.contextmanager
		def concurrent_update(git_dir):
			with original_lock(git_dir) as acquired:
				state_path.write_text(json.dumps(concurrent_state))
				yield acquired

		with mock.patch.object(mirror, "DestinationLock", concurrent_update):
			with self.assertRaisesRegex(mirror.MirrorError, "ledger changed during collection"):
				self.run_mirror(apply=True)
		self.assertEqual(json.loads(state_path.read_text()), concurrent_state)
		self.assertEqual(cache_path.read_bytes(), cache_before)
		self.assertEqual(self.git(self.destination, "rev-list", "--count", "HEAD").strip(), "1")

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
		self.assertFalse((self.destination / ".git" / mirror.SCAN_NAME).exists())
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

	def test_scheduled_run_requires_both_stored_approvals_before_import_or_push(self):
		path = self.scheduled_settings()
		job = json.loads(path.read_text())
		for permission in ("export_approved", "publish_approved"):
			with self.subTest(permission=permission):
				path.write_text(json.dumps({**job, permission: False}))
				with mock.patch.object(mirror_schedule, "verify_destination") as verify:
					with mock.patch.object(mirror_schedule, "publish") as push:
						importer = mock.Mock()
						with self.assertRaisesRegex(mirror.MirrorError, "approval"):
							mirror_schedule.run_scheduled(path, importer)
				verify.assert_not_called()
				importer.assert_not_called()
				push.assert_not_called()

	def test_scheduled_approval_does_not_cover_changed_source_settings(self):
		path = self.scheduled_settings()
		self.config["companies"][0]["repositories"][0]["ref"] = "different-ref"
		(self.root / "config.json").write_text(json.dumps(self.config, default=str))
		with mock.patch.object(mirror_schedule, "verify_destination") as verify:
			with mock.patch.object(mirror_schedule, "publish") as push:
				importer = mock.Mock()
				with self.assertRaisesRegex(mirror.MirrorError, "changed"):
					mirror_schedule.run_scheduled(path, importer)
		verify.assert_not_called()
		importer.assert_not_called()
		push.assert_not_called()

	def test_scheduled_run_uses_only_approved_history_and_does_not_push_failed_imports(self):
		self.commit("2023-12-31T10:00:00+00:00")
		self.commit("2024-01-02T10:00:00+00:00")
		self.commit("2024-02-02T10:00:00+00:00")
		path = self.scheduled_settings()
		with mock.patch.object(mirror_schedule, "verify_destination"):
			with mock.patch.object(mirror_schedule, "publish", return_value=True) as push:
				with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
					mirror_schedule.run_scheduled(path, lambda config: mirror.run(config, apply=True))
				push.assert_called_once()
		self.assertEqual(self.git(self.destination, "log", "--format=%aI").splitlines(), [
			"2024-01-02T12:00:00+00:00"
		])
		with mock.patch.object(mirror_schedule, "verify_destination"):
			with mock.patch.object(mirror_schedule, "publish") as push:
				with self.assertRaisesRegex(mirror.MirrorError, "Synthetic import failure"):
					mirror_schedule.run_scheduled(path, mock.Mock(side_effect=mirror.MirrorError("Synthetic import failure")))
				push.assert_not_called()

	def test_publishing_rejects_unexpected_remote_account_or_public_target(self):
		self.commit("2024-01-02T10:00:00+00:00")
		self.config["destination"]["author_email"] = "personal-developer@users.noreply.github.com"
		self.run_mirror(apply=True)
		self.git(self.destination, "remote", "add", "origin", "https://github.com/personal-developer/activity-data.git")
		destination = Destination(
			self.destination, GitIdentity("Personal Developer", "personal-developer@users.noreply.github.com")
		)
		account = {"login": "personal-developer", "type": "User"}
		repository = {
			"full_name": "personal-developer/activity-data", "private": True,
			"owner": account, "fork": False, "archived": False,
			"disabled": False, "default_branch": "main", "permissions": {"push": True}
		}
		for case in ("remote", "push-url", "account", "public"):
			with self.subTest(case=case):
				self.git(self.destination, "remote", "set-url", "origin", (
					"https://github.com/other-owner/activity-data.git" if case == "remote"
					else "https://github.com/personal-developer/activity-data.git"
				))
				self.git(self.destination, "config", "remote.origin.pushurl", (
					"https://github.com/other-owner/activity-data.git" if case == "push-url"
					else "https://github.com/personal-developer/activity-data.git"
				))
				responses = [
					{"login": "other-owner", "type": "User"} if case == "account" else account,
					{**repository, "private": False} if case == "public" else repository
				]
				with mock.patch.object(mirror_publishing, "github", side_effect=responses):
					with self.assertRaises(mirror.MirrorError):
						mirror_publishing.verify_destination(destination, "personal-developer/activity-data")

	def test_scheduled_run_retries_failed_push_without_duplicating_local_history(self):
		self.commit("2024-01-02T10:00:00+00:00")
		path = self.scheduled_settings()
		with mock.patch.object(mirror_schedule, "verify_destination"):
			with mock.patch.object(mirror_schedule, "publish", side_effect=[
				mirror.MirrorError("Synthetic failed push"), True
			]) as publish:
				with self.assertRaisesRegex(mirror.MirrorError, "Synthetic failed push"):
					mirror_schedule.run_scheduled(path, lambda config: mirror.run(config, apply=True))
				head = self.git(self.destination, "rev-parse", "HEAD")
				failed = json.loads((self.root / "last-run.json").read_text())
				self.assertFalse(failed["success"])
				mirror_schedule.run_scheduled(path, lambda config: mirror.run(config, apply=True))
				self.assertEqual(publish.call_count, 2)
		self.assertEqual(self.git(self.destination, "rev-parse", "HEAD"), head)
		result = json.loads((self.root / "last-run.json").read_text())
		self.assertTrue(result["success"])
		self.assertTrue(result["published"])
		self.assertEqual(result["created"], 0)
		if os.name == "posix":
			self.assertEqual((self.root / "last-run.json").stat().st_mode & 0o777, 0o600)

	def test_scheduler_cancellation_does_not_save_or_register_a_job(self):
		path = self.scheduled_settings()
		config = self.root / "config.json"
		original = config.read_bytes()
		path.unlink()
		answers = ["personal-developer/activity-data", "", "", "", "n"]
		with mock.patch.object(mirror_schedule.shutil, "which", return_value="/synthetic/tool"):
			with mock.patch("builtins.input", side_effect=answers):
				with mock.patch.object(native_schedulers, "install") as install:
					with mock.patch.object(mirror_schedule, "verify_destination") as verify:
						with contextlib.redirect_stdout(io.StringIO()):
							mirror_schedule.install_schedule(config, Path(mirror.__file__).resolve(), path)
		install.assert_not_called()
		verify.assert_not_called()
		self.assertFalse(path.exists())
		self.assertEqual(config.read_bytes(), original)
		self.assertFalse(self.destination.exists())

	def test_revoked_approval_still_allows_removing_the_managed_schedule(self):
		path = self.scheduled_settings()
		raw = json.loads(path.read_text())
		raw["publish_approved"] = False
		path.write_text(json.dumps(raw))
		with mock.patch.object(mirror_schedule, "state_directory", return_value=self.root):
			with mock.patch.object(native_schedulers, "status", return_value="Registered"):
				output = io.StringIO()
				with contextlib.redirect_stdout(output):
					mirror_schedule.manage_schedule("status", self.root / "config.json", Path(mirror.__file__).resolve())
				self.assertIn("approval is revoked", output.getvalue())
			with mock.patch.object(native_schedulers, "remove") as remove:
				with contextlib.redirect_stdout(io.StringIO()):
					mirror_schedule.manage_schedule("remove", self.root / "config.json", Path(mirror.__file__).resolve())
				remove.assert_called_once()
		self.assertFalse(path.exists())
		self.assertTrue((self.root / "config.json").exists())

	def test_native_install_and_removal_refuse_existing_or_modified_definitions(self):
		path = self.scheduled_settings()
		job = mirror_schedule.load_job(path)
		with mock.patch.object(native_schedulers.sys, "platform", "linux"):
			with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "user-config")}):
				files = native_schedulers.definitions(job, path)
				service = next(iter(files))
				service.parent.mkdir(parents=True, exist_ok=True)
				service.write_bytes(b"Unrelated existing user service\n")
				with mock.patch.object(native_schedulers, "command") as command:
					with self.assertRaisesRegex(mirror.MirrorError, "not overwritten"):
						native_schedulers.install(job, path)
					with self.assertRaisesRegex(mirror.MirrorError, "unverified"):
						native_schedulers.remove(job, path)
					command.assert_not_called()
				self.assertEqual(service.read_bytes(), b"Unrelated existing user service\n")

	def test_macos_and_windows_definitions_preserve_argument_boundaries_and_daily_time(self):
		path = self.scheduled_settings()
		job = replace(
			mirror_schedule.load_job(path), time="00:00",
			script=self.root / "folder & $percent%" / "mirror script.py"
		)
		with mock.patch.object(native_schedulers.sys, "platform", "darwin"):
			plist = plistlib.loads(next(iter(native_schedulers.definitions(job, path).values())))
			self.assertEqual(plist["ProgramArguments"], [
				str(job.python), str(job.script), "--scheduled-run", str(path)
			])
			self.assertEqual(plist["WorkingDirectory"], str(job.script.parent))
			self.assertEqual(plist["StartCalendarInterval"], {"Hour": 0, "Minute": 0})
		with mock.patch.object(native_schedulers, "windows_user_sid", return_value="S-1-5-21-123"):
			xml = native_schedulers.windows_definition(job, path)
		root = ET.fromstring(xml)
		self.assertEqual(root.findtext("t:Actions/t:Exec/t:Command", namespaces=native_schedulers.TASK_NS), str(job.python))
		self.assertEqual(root.findtext(
			"t:Actions/t:Exec/t:WorkingDirectory", namespaces=native_schedulers.TASK_NS
		), str(job.script.parent))
		self.assertEqual(root.findtext(
			"t:Triggers/t:CalendarTrigger/t:StartBoundary", namespaces=native_schedulers.TASK_NS
		), "2024-01-01T00:00:00")
		arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=native_schedulers.TASK_NS)
		self.assertIn(f'"{job.script}"', arguments)
		self.assertIn(f'--scheduled-run {path}', arguments)

	def test_windows_registration_never_forces_replacement_and_removal_checks_actual_actions(self):
		path = self.scheduled_settings()
		job = mirror_schedule.load_job(path)
		with mock.patch.object(native_schedulers.sys, "platform", "win32"):
			with mock.patch.object(native_schedulers, "windows_user_sid", return_value="S-1-5-21-123"):
				with mock.patch.object(native_schedulers, "command") as command:
					native_schedulers.install(job, path)
					arguments = command.call_args.args[0]
					self.assertNotIn("/F", arguments)
					self.assertEqual(command.call_args.kwargs["answer"], "N\n")
				root = ET.fromstring((self.root / "task.xml").read_bytes())
				root.find("t:Actions/t:Exec/t:Command", native_schedulers.TASK_NS).text = "unrelated.exe"
				output = ET.tostring(root, encoding="unicode")
				with mock.patch.object(native_schedulers, "command", return_value=subprocess.CompletedProcess(
					[], 0, output, ""
				)) as command:
					with self.assertRaisesRegex(mirror.MirrorError, "differs"):
						native_schedulers.remove(job, path)
					self.assertEqual(command.call_count, 1)
					self.assertIn("/Query", command.call_args.args[0])
				self.assertTrue((self.root / "task.xml").exists())

	def test_native_boundary_decodes_utf16_task_xml_without_corrupting_paths(self):
		payload = '<?xml version="1.0" encoding="UTF-16"?><Task><Path>Ren\u00e9e &amp; scripts</Path></Task>'
		with mock.patch.object(native_schedulers.subprocess, "run", return_value=subprocess.CompletedProcess(
			[], 0, payload.encode("utf-16"), b""
		)):
			result = native_schedulers.command(["schtasks", "/Query", "/XML"])
		self.assertEqual(ET.fromstring(result.stdout).findtext("Path"), "Ren\u00e9e & scripts")

	def test_macos_removal_rejects_a_registered_path_that_only_shares_the_expected_prefix(self):
		path = self.scheduled_settings()
		job = mirror_schedule.load_job(path)
		target = self.root / "agent.plist"
		target.write_bytes(b"owned native definition")
		with mock.patch.object(native_schedulers.sys, "platform", "darwin"):
			with mock.patch.object(native_schedulers, "definitions", return_value={target: target.read_bytes()}):
				with mock.patch.object(native_schedulers, "command", return_value=subprocess.CompletedProcess(
					[], 0, f"  path = {target}.unrelated\n", ""
				)) as command:
					with self.assertRaisesRegex(mirror.MirrorError, "does not belong"):
						native_schedulers.remove(job, path)
					self.assertEqual(command.call_count, 1)
					self.assertIn("print", command.call_args.args[0])

	def test_linux_cron_install_status_and_removal_preserve_unrelated_jobs(self):
		path = self.scheduled_settings()
		job = mirror_schedule.load_job(path)
		before = "MAILTO=personal@example.test\nSHELL=/bin/bash\n17 8 * * 1 printf unrelated\n"
		with mock.patch.object(native_schedulers.sys, "platform", "linux"):
			with mock.patch.object(native_schedulers, "read_crontab", side_effect=[before, before]):
				with mock.patch.object(native_schedulers, "command") as command:
					native_schedulers.install(job, path)
					after = command.call_args.kwargs["answer"]
					self.assertEqual(command.call_args.args[0], ["crontab", "-"])
					self.assertEqual(after, before + native_schedulers.cron_block(job, path))
			with mock.patch.object(native_schedulers, "read_crontab", return_value=after):
				self.assertIn("registered", native_schedulers.status(job, path))
				with mock.patch.object(native_schedulers, "command") as command:
					native_schedulers.remove(job, path)
					self.assertEqual(command.call_args.kwargs["answer"], before)
			self.assertFalse((self.root / "crontab.txt").exists())

	def test_linux_cron_does_not_replace_concurrent_edits_or_modified_managed_blocks(self):
		path = self.scheduled_settings()
		job = mirror_schedule.load_job(path)
		before = "17 8 * * 1 printf unrelated\n"
		changed = before + "42 9 * * 2 printf another\n"
		with mock.patch.object(native_schedulers, "read_crontab", return_value=changed):
			with mock.patch.object(native_schedulers, "command") as command:
				with self.assertRaisesRegex(mirror.MirrorError, "changed"):
					native_schedulers.write_crontab(before, "replacement")
				command.assert_not_called()
		table = native_schedulers.cron_block(job, path).replace("--scheduled-run", "--unrelated")
		with self.assertRaisesRegex(mirror.MirrorError, "missing or changed"):
			native_schedulers.verify_cron_block(table, job, path)

	def test_linux_cron_distinguishes_empty_table_from_access_failures(self):
		for result, empty in (
			(subprocess.CompletedProcess([], 1, "", "no crontab for example-user\n"), True),
			(subprocess.CompletedProcess([], 1, "", "permission denied\n"), False)
		):
			with self.subTest(empty=empty):
				with mock.patch.object(native_schedulers, "command", return_value=result):
					if empty:
						self.assertEqual(native_schedulers.read_crontab(), "")
					else:
						with self.assertRaisesRegex(mirror.MirrorError, "left unchanged"):
							native_schedulers.read_crontab()

	def test_linux_cron_refuses_inherited_timezone_delay_or_incompatible_shell(self):
		path = self.scheduled_settings()
		job = mirror_schedule.load_job(path)
		for table in ("CRON_TZ=UTC\n", "RANDOM_DELAY=60\n", "SHELL=/bin/csh\n"):
			with self.subTest(table=table):
				with mock.patch.object(native_schedulers.sys, "platform", "linux"):
					with mock.patch.object(native_schedulers, "read_crontab", return_value=table):
						with mock.patch.object(native_schedulers, "command") as command:
							with self.assertRaises(mirror.MirrorError):
								native_schedulers.install(job, path)
							command.assert_not_called()
							self.assertFalse((self.root / "crontab.txt").exists())

	def test_publishing_sends_only_the_validated_tip_without_overriding_existing_authentication(self):
		destination = Destination(self.destination, GitIdentity("Personal Developer", "personal@example.test"))
		head = "a" * 40
		with mock.patch.object(mirror_publishing, "verify_destination", return_value=head):
			with mock.patch.object(mirror_publishing, "git") as git:
				self.assertTrue(mirror_publishing.publish(destination, "personal-developer/activity-data"))
		arguments = git.call_args.args[1]
		index = arguments.index("push")
		self.assertEqual(arguments[index + 1:], [
			"--no-verify", "--recurse-submodules=no", "--porcelain", "origin", f"{head}:refs/heads/main"
		])
		self.assertNotIn("--force", arguments)
		self.assertFalse(any("credential." in item or "extraHeader" in item for item in arguments))
		self.assertEqual(git.call_args.kwargs["timeout"], 120)

	def test_scheduler_cli_rejects_conflicting_flags_and_status_does_not_read_sources(self):
		path = self.scheduled_settings()
		command = [sys.executable, str(Path(mirror.__file__).resolve())]
		for flags in (
			["--scheduled-run", str(path), "--apply"],
			["--schedule", "install", "--since", "2024-01-01"],
			["--schedule", "status", "--setup"]
		):
			with self.subTest(flags=flags):
				result = subprocess.run(command + flags, text=True, capture_output=True, env=self.environment())
				self.assertEqual(result.returncode, 1)
				self.assertIn("cannot be combined", result.stderr)
		env = {**self.environment(), "HOME": str(self.root), "LOCALAPPDATA": str(self.root)}
		result = subprocess.run(command + ["--schedule", "status"], text=True, capture_output=True, env=env)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn("No managed daily schedule", result.stdout)
		self.assertFalse(self.destination.exists())

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

	def scheduled_settings(self):
		config_path = self.root / "config.json"
		config_path.write_text(json.dumps(self.config, default=str))
		job = {
			"version": 1, "config": str(config_path),
			"config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
			"repository": "personal-developer/activity-data",
			"history": {"since": "2024-01-01", "until": "2024-01-31"},
			"export_approved": True, "publish_approved": True,
			"time": "00:00", "python": sys.executable,
			"script": str(Path(mirror.__file__).resolve()),
			"path": os.environ.get("PATH", ""), "start_date": "2024-01-01"
		}
		path = self.root / "job.json"
		path.write_text(json.dumps(job))
		return path

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
