import os
from pathlib import Path
import subprocess
from typing import Sequence

from .models import GitIdentity, MirrorError


def check_repository_root(path: Path, label: str) -> None:
	root = Path(git(path, ["rev-parse", "--show-toplevel"]).strip()).resolve()
	if root != path:
		raise MirrorError(f"The {label} path must be the repository root.")
	if git(path, ["rev-parse", "--show-object-format"]).strip() != "sha1":
		raise MirrorError(f"The {label} repository must use Git's SHA-1 object format.")


def git(
	path: Path,
	arguments: Sequence[str],
	identity: GitIdentity | None = None,
	date: str | None = None,
	timeout: float | None = None
) -> str:
	env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
	for key in ("GH_DEBUG", "GH_HOST", "GH_REPO"):
		env.pop(key, None)
	env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
	if identity is not None:
		if date is None:
			raise MirrorError("A synthetic commit requires an explicit date.")
		env.update({
			"GIT_AUTHOR_NAME": identity.name,
			"GIT_AUTHOR_EMAIL": identity.email,
			"GIT_COMMITTER_NAME": identity.name,
			"GIT_COMMITTER_EMAIL": identity.email,
			"GIT_AUTHOR_DATE": f"{date}T12:00:00+00:00",
			"GIT_COMMITTER_DATE": f"{date}T12:00:00+00:00"
		})
	env.update({"GH_PROMPT_DISABLED": "1", "GCM_INTERACTIVE": "Never"})
	try:
		result = subprocess.run(
			["git", "-C", str(path), *arguments], env=env,
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout
		)
	except subprocess.TimeoutExpired:
		raise MirrorError("Git operation timed out; check connectivity and personal authentication locally.") from None
	if result.returncode:
		# Git errors can contain private paths, URLs, refs, and identities.
		raise MirrorError(f"Git {arguments[0]} failed (exit {result.returncode}); check local configuration and repository state.")
	return result.stdout
