import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import TracebackType
from typing import Mapping

from .models import MirrorError, SourceCommit, require_keys, valid_date


STATE_NAME = "activity-mirror-state.json"
SCAN_NAME = "activity-mirror-scan.json"


def load_state(path: Path) -> dict[str, str]:
	if not path.exists():
		return {}
	try:
		raw: object = json.loads(path.read_text())
	except json.JSONDecodeError:
		raise MirrorError("Local ledger is invalid JSON; restore its private backup.") from None
	state = require_keys(raw, {"version", "commits"}, "local ledger")
	commits = state["commits"]
	if state["version"] != 1 or not isinstance(commits, dict):
		raise MirrorError("Unsupported local ledger format.")
	result: dict[str, str] = {}
	for commit, date in commits.items():
		if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit) or not isinstance(date, str) or not valid_date(date):
			raise MirrorError("Local ledger contains an invalid entry.")
		result[commit] = date
	return result


def load_checkpoints(path: Path, commits: Mapping[str, str]) -> dict[str, str]:
	if not path.exists():
		return {}
	try:
		raw: object = json.loads(path.read_text())
	except json.JSONDecodeError:
		raise MirrorError("Local scan cache is invalid JSON; remove only the scan cache to rebuild it, not the ledger.") from None
	state = require_keys(raw, {"version", "ledger_sha256", "checkpoints"}, "local scan cache")
	checkpoints = state["checkpoints"]
	digest = state["ledger_sha256"]
	if (
		state["version"] != 1 or not isinstance(digest, str)
		or not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(checkpoints, dict)
	):
		raise MirrorError("Unsupported local scan cache format; remove only the scan cache to rebuild it, not the ledger.")
	result: dict[str, str] = {}
	for key, commit in checkpoints.items():
		if (
			not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)
			or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)
		):
			raise MirrorError("Local scan cache contains an invalid entry; remove only the scan cache to rebuild it, not the ledger.")
		result[key] = commit
	if digest != ledger_digest(commits):
		print("Scan checkpoints do not match the local ledger; performing a full source scan.", file=sys.stderr)
		return {}
	return result


def merge_activity(
	seen: Mapping[str, str],
	collected: tuple[SourceCommit, ...]
) -> dict[str, str]:
	merged = dict(seen)
	for commit in collected:
		if commit.commit_hash in merged and merged[commit.commit_hash] != commit.date:
			raise MirrorError("Local history conflicts with the saved ledger; no changes made.")
		merged[commit.commit_hash] = commit.date
	return merged


def save_state(path: Path, commits: Mapping[str, str]) -> None:
	save_json(path, {"version": 1, "commits": dict(commits)})


def save_checkpoints(path: Path, commits: Mapping[str, str], checkpoints: Mapping[str, str]) -> None:
	save_json(path, {
		"version": 1, "ledger_sha256": ledger_digest(commits), "checkpoints": dict(checkpoints)
	})


def ledger_digest(commits: Mapping[str, str]) -> str:
	return hashlib.sha256(json.dumps(dict(commits), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save_json(path: Path, value: object) -> None:
	fd, temporary = tempfile.mkstemp(prefix=".activity-mirror-", dir=path.parent)
	try:
		with os.fdopen(fd, "w") as stream:
			json.dump(value, stream, sort_keys=True)
			stream.flush()
			os.fsync(stream.fileno())
		os.replace(temporary, path)
	finally:
		if os.path.exists(temporary):
			os.unlink(temporary)


class DestinationLock:
	def __init__(self, git_dir: Path):
		self.path = git_dir / "activity-mirror.lock"

	def __enter__(self) -> "DestinationLock":
		try:
			self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
		except FileExistsError:
			raise MirrorError("Destination is locked. Stop other runs; remove a stale lock only after verifying no run is active.") from None
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_value: BaseException | None,
		traceback: TracebackType | None
	) -> None:
		os.close(self.fd)
		self.path.unlink()
