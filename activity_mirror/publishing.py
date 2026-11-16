import json
import os
from pathlib import Path
import re
import subprocess

from .destination import inspect_destination
from .git import git
from .models import Destination, MirrorError


def publish(destination: Destination, repository: str) -> bool:
	head = verify_destination(destination, repository)
	if head is None:
		return False
	git(destination.path, [
		"-c", "core.hooksPath=/dev/null", "-c", "push.followTags=false",
		"-c", "remote.origin.mirror=false",
		"push", "--no-verify", "--recurse-submodules=no", "--porcelain",
		"origin", f"{head}:refs/heads/main"
	], timeout=120)
	return True


def verify_destination(destination: Destination, repository: str) -> str | None:
	owner = validate_repository(repository)
	snapshot = inspect_destination(destination)
	if not snapshot.exists:
		raise MirrorError("Connect an existing dedicated personal mirror before scheduling.")
	expected = f"https://github.com/{repository}.git".lower()
	for flags in (["--all"], ["--push", "--all"]):
		urls = git(destination.path, ["remote", "get-url", *flags, "origin"]).splitlines()
		if len(urls) != 1 or urls[0].lower() not in (expected, expected.removesuffix(".git")):
			raise MirrorError("Destination origin must have only the approved credential-free GitHub HTTPS URL.")
	if not re.fullmatch(
		rf"(?:[0-9]+\+)?{re.escape(owner)}@users\.noreply\.github\.com",
		destination.identity.email, re.IGNORECASE
	):
		raise MirrorError("Automatic publishing requires the approved personal owner's GitHub noreply email.")
	account = github(destination.path, "user")
	if account.get("type") != "User" or str(account.get("login", "")).lower() != owner.lower():
		raise MirrorError("GitHub CLI account does not match the approved personal owner; no account was changed.")
	target = github(destination.path, f"repos/{repository}")
	target_owner = target.get("owner")
	permissions = target.get("permissions")
	if (
		str(target.get("full_name", "")).lower() != repository.lower()
		or target.get("private") is not True or target.get("fork") is not False
		or target.get("archived") is not False or target.get("disabled") is not False
		or target.get("default_branch") != "main"
		or not isinstance(target_owner, dict) or target_owner.get("type") != "User"
		or str(target_owner.get("login", "")).lower() != owner.lower()
		or not isinstance(permissions, dict) or permissions.get("push") is not True
	):
		raise MirrorError("Approved target must be your writable private, non-fork personal repository with main as default.")
	if snapshot.head is not None:
		fields = git(destination.path, ["log", "--format=%ce%x00%cn%x00%cI%x00", "HEAD", "--"]).split("\x00")
		for index in range(0, len(fields) - 1, 3):
			email, name, timestamp = fields[index:index + 3]
			if (
				email.strip() != destination.identity.email or name != destination.identity.name
				or not timestamp.endswith("T12:00:00+00:00")
			):
				raise MirrorError("Mirror committer metadata does not match the approved personal identity.")
	return snapshot.head


def validate_repository(repository: str) -> str:
	if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+", repository):
		raise MirrorError("Use a personal GitHub repository in OWNER/REPOSITORY format, not a URL.")
	owner, name = repository.split("/")
	if name in (".", "..") or name.endswith(".git"):
		raise MirrorError("Use the repository name without a .git suffix.")
	return owner


def github(path: Path, endpoint: str) -> dict[str, object]:
	env = dict(os.environ)
	for key in ("GH_DEBUG", "GH_REPO", "GH_HOST"):
		env.pop(key, None)
	env.update({"GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"})
	try:
		result = subprocess.run(
			["gh", "api", "--hostname", "github.com", endpoint],
			cwd=path, env=env, text=True, encoding="utf-8", capture_output=True, timeout=60
		)
	except (OSError, subprocess.TimeoutExpired):
		raise MirrorError("GitHub CLI could not complete its noninteractive check; verify personal authentication locally.") from None
	except UnicodeDecodeError:
		raise MirrorError("GitHub CLI returned a response that is not valid UTF-8.") from None
	if result.returncode:
		raise MirrorError("GitHub verification failed; check connectivity and approved personal authentication. No fallback account was used.")
	try:
		raw: object = json.loads(result.stdout)
	except json.JSONDecodeError:
		raise MirrorError("GitHub CLI returned an invalid response.") from None
	if not isinstance(raw, dict):
		raise MirrorError("GitHub CLI returned an unexpected response.")
	return raw
