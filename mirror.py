#!/usr/bin/env python3
"""Preview and locally mirror daily activity without exporting source metadata."""

import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


MESSAGE = "Record development activity\n\nActivity-Mirror-Version: 1"
STATE_NAME = "activity-mirror-state.json"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class MirrorError(Exception):
	pass


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--config", type=Path, default=Path("config.json"))
	parser.add_argument("--since", type=parse_date)
	parser.add_argument("--until", type=parse_date)
	parser.add_argument("--apply", action="store_true", help="Create local mirror commits.")
	parser.add_argument(
		"--allow-metadata-export", action="store_true",
		help="Confirm permission to copy work activity dates/counts into personal Git history."
	)
	args = parser.parse_args()
	try:
		if args.since and args.until and args.since > args.until:
			raise MirrorError("--since must not be after --until.")
		if args.apply and not args.allow_metadata_export:
			raise MirrorError("--apply requires --allow-metadata-export after permission is established.")
		config = load_config(args.config)
		run(config, args.since, args.until, args.apply)
	except (MirrorError, OSError) as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1
	return 0


def parse_date(value):
	try:
		date = datetime.date.fromisoformat(value)
		if date.isoformat() != value:
			raise ValueError
		return date
	except ValueError:
		raise argparse.ArgumentTypeError("Use a calendar date in YYYY-MM-DD format.") from None


def load_config(path):
	path = path.expanduser().resolve()
	try:
		config = json.loads(path.read_text())
	except json.JSONDecodeError:
		raise MirrorError("Configuration is not valid JSON.") from None
	require_keys(config, {"destination", "companies"}, "configuration")
	destination = config["destination"]
	require_keys(destination, {"path", "author_name", "author_email"}, "destination")
	for key in ("author_name", "author_email"):
		value = destination[key]
		if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\x00<>"):
			raise MirrorError(f"destination.{key} must be a nonempty, single-line Git identity.")
	if "@" not in destination["author_email"]:
		raise MirrorError("destination.author_email must be an email address.")
	destination["path"] = resolve_path(destination["path"], path.parent)
	companies = config["companies"]
	if not isinstance(companies, list) or not companies:
		raise MirrorError("companies must contain at least one company.")
	names = set()
	for company in companies:
		require_keys(company, {"name", "repositories"}, "company")
		if not isinstance(company["name"], str) or not company["name"].strip() or company["name"] in names:
			raise MirrorError("Each company needs a unique, nonempty local name.")
		names.add(company["name"])
		repositories = company["repositories"]
		if not isinstance(repositories, list) or not repositories:
			raise MirrorError("Each company must contain at least one repository.")
		for source in repositories:
			require_keys(source, {"path", "ref", "author_emails"}, "source repository")
			source["path"] = resolve_path(source["path"], path.parent)
			if paths_overlap(source["path"], destination["path"]):
				raise MirrorError("Source and destination directories must not overlap.")
			ref = source["ref"]
			if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref):
				raise MirrorError("Each source ref must be an explicit Git branch or commit reference.")
			emails = source["author_emails"]
			if not isinstance(emails, list) or not emails or any(
				not isinstance(email, str) or "@" not in email or any(c in email for c in "\r\n\x00")
				for email in emails
			):
				raise MirrorError("Each source requires explicit author_emails.")
	return config


def require_keys(value, keys, label):
	if not isinstance(value, dict) or set(value) != keys:
		raise MirrorError(f"Invalid {label}; required keys: {', '.join(sorted(keys))}.")


def resolve_path(value, base):
	if not isinstance(value, str) or not value.strip():
		raise MirrorError("Repository paths must be nonempty strings.")
	path = Path(value).expanduser()
	return (path if path.is_absolute() else base / path).resolve()


def paths_overlap(first, second):
	return first == second or first in second.parents or second in first.parents


def run(config, since=None, until=None, apply=False):
	destination = config["destination"]
	path = destination["path"]
	if path.exists() and not path.is_dir():
		raise MirrorError("Destination is not a directory.")
	exists = path.exists() and any(path.iterdir())
	if exists:
		check_repository_root(path, "destination")
		git_dir = Path(git(path, ["rev-parse", "--absolute-git-dir"]).strip())
		if git_dir != path / ".git":
			raise MirrorError("Destination must be a standalone repository, not a worktree.")
		seen = load_state(git_dir / STATE_NAME)
		existing = read_mirror(path, destination)
		if existing and not seen:
			raise MirrorError("Local ledger is missing. Restore its private backup before continuing.")
	else:
		seen = {}
		existing = collections.Counter()
	for commit, date in collect(config, since, until).items():
		if commit in seen and seen[commit] != date:
			raise MirrorError("Local history conflicts with the saved ledger; no changes made.")
		seen[commit] = date
	desired = collections.Counter(seen.values())
	if any(count > desired[date] for date, count in existing.items()):
		raise MirrorError("Mirror exceeds the local ledger. Restore a matching ledger; nothing will be deleted.")
	missing = desired - existing
	print(json.dumps({
		"daily_totals": daily_counts(desired),
		"new_mirror_commits": daily_counts(missing)
	}, indent="\t"))
	if not apply:
		print("Preview only. No files or repositories changed.", file=sys.stderr)
		return
	path.mkdir(parents=True, exist_ok=True)
	if not exists:
		git(path, ["init", "--initial-branch=main"])
		git_dir = path / ".git"
	with destination_lock(git_dir):
		# Recheck after acquiring the lock: another process may have changed Git history.
		current = read_mirror(path, destination)
		if current != existing:
			raise MirrorError("Destination changed during collection. Run the preview again.")
		save_state(git_dir / STATE_NAME, seen)
		for date, count in sorted(missing.items()):
			for _ in range(count):
				git(path, [
					"-c", "core.hooksPath=/dev/null",
					"-c", "commit.gpgSign=false",
					"-c", "core.logAllRefUpdates=false",
					"commit", "--allow-empty", "--no-verify", "--quiet", "-m", MESSAGE
				], identity=destination, date=date)
	print(f"Created {sum(missing.values())} local mirror commits. Nothing was pushed.", file=sys.stderr)


def check_repository_root(path, label):
	root = Path(git(path, ["rev-parse", "--show-toplevel"]).strip()).resolve()
	if root != path:
		raise MirrorError(f"The {label} path must be the repository root.")
	if git(path, ["rev-parse", "--show-object-format"]).strip() != "sha1":
		raise MirrorError(f"The {label} repository must use Git's SHA-1 object format.")


def git(path, arguments, identity=None, date=None):
	env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
	env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
	if identity:
		env.update({
			"GIT_AUTHOR_NAME": identity["author_name"],
			"GIT_AUTHOR_EMAIL": identity["author_email"],
			"GIT_COMMITTER_NAME": identity["author_name"],
			"GIT_COMMITTER_EMAIL": identity["author_email"],
			"GIT_AUTHOR_DATE": f"{date}T12:00:00+00:00",
			"GIT_COMMITTER_DATE": f"{date}T12:00:00+00:00"
		})
	result = subprocess.run(
		["git", "-C", str(path), *arguments], env=env,
		stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
	)
	if result.returncode:
		# Git errors can contain private paths, URLs, refs, and identities.
		raise MirrorError(f"Git {arguments[0]} failed (exit {result.returncode}); check local configuration and repository state.")
	return result.stdout


def load_state(path):
	if not path.exists():
		return {}
	try:
		state = json.loads(path.read_text())
	except json.JSONDecodeError:
		raise MirrorError("Local ledger is invalid JSON; restore its private backup.") from None
	require_keys(state, {"version", "commits"}, "local ledger")
	if state["version"] != 1 or not isinstance(state["commits"], dict):
		raise MirrorError("Unsupported local ledger format.")
	for commit, date in state["commits"].items():
		if not re.fullmatch(r"[0-9a-f]{40}", commit) or not valid_date(date):
			raise MirrorError("Local ledger contains an invalid entry.")
	return state["commits"]


def valid_date(value):
	if not isinstance(value, str):
		return False
	try:
		return datetime.date.fromisoformat(value).isoformat() == value
	except ValueError:
		return False


def read_mirror(path, destination):
	if git(path, ["status", "--porcelain", "--untracked-files=all"]).strip():
		raise MirrorError("Destination must have no files or staged changes.")
	heads = git(path, ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"]).splitlines()
	if git(path, ["symbolic-ref", "-q", "HEAD"]).strip() != "refs/heads/main":
		raise MirrorError("Destination must use the main branch.")
	if not heads:
		return collections.Counter()
	if heads != ["refs/heads/main"]:
		raise MirrorError("Destination must contain only the main branch and no tags.")
	raw = git(path, ["log", "--format=%T%x00%ae%x00%an%x00%aI%x00%P%x00%B%x00", "HEAD", "--"])
	fields = raw.split("\x00")
	counts = collections.Counter()
	for index in range(0, len(fields) - 1, 6):
		tree, email, name, timestamp, parents, message = fields[index:index + 6]
		if (
			tree.strip() != EMPTY_TREE or email != destination["author_email"]
			or name != destination["author_name"] or message.strip() != MESSAGE
			or len(parents.split()) > 1 or not timestamp.endswith("T12:00:00+00:00")
			or not valid_date(timestamp[:10])
		):
			raise MirrorError("Destination contains non-mirror history; use a dedicated empty repository.")
		counts[timestamp[:10]] += 1
	return counts


def collect(config, since, until):
	commits = {}
	today = datetime.datetime.now(datetime.timezone.utc).date()
	for company in config["companies"]:
		for source in company["repositories"]:
			path = source["path"]
			check_repository_root(path, "source")
			ref = git(path, ["rev-parse", "--verify", "--end-of-options", source["ref"] + "^{commit}"]).strip()
			output = git(path, ["log", "--no-merges", "--format=%H%x00%ae%x00%as", ref, "--"])
			emails = {email.casefold() for email in source["author_emails"]}
			for line in output.splitlines():
				commit, email, date = line.split("\x00")
				if email.casefold() not in emails:
					continue
				if not valid_date(date):
					raise MirrorError("A source commit has an invalid author date.")
				day = datetime.date.fromisoformat(date)
				if (since and day < since) or (until and day > until):
					continue
				if day > today:
					raise MirrorError("A source commit is future-dated; narrow the date range before proceeding.")
				commits[commit] = date
	return commits


def daily_counts(counts):
	return [{"date": date, "count": count} for date, count in sorted(counts.items())]


class destination_lock:
	def __init__(self, git_dir):
		self.path = git_dir / "activity-mirror.lock"

	def __enter__(self):
		try:
			self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
		except FileExistsError:
			raise MirrorError("Destination is locked. Stop other runs; remove a stale lock only after verifying no run is active.") from None
		return self

	def __exit__(self, *_):
		os.close(self.fd)
		self.path.unlink()


def save_state(path, commits):
	fd, temporary = tempfile.mkstemp(prefix=".activity-mirror-", dir=path.parent)
	try:
		with os.fdopen(fd, "w") as stream:
			json.dump({"version": 1, "commits": commits}, stream, sort_keys=True)
			stream.flush()
			os.fsync(stream.fileno())
		os.replace(temporary, path)
	finally:
		if os.path.exists(temporary):
			os.unlink(temporary)


if __name__ == "__main__":
	sys.exit(main())
