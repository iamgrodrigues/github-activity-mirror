import datetime
import hashlib
import json
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Mapping

from .git import check_repository_root, git
from .models import CollectedActivity, MirrorError, SourceCommit, SourceRepository, valid_date


def collect(
	sources: tuple[SourceRepository, ...],
	since: datetime.date | None = None,
	until: datetime.date | None = None,
	checkpoints: Mapping[str, str] | None = None
) -> CollectedActivity:
	commits: dict[str, str] = {}
	previous = checkpoints if checkpoints is not None else {}
	scanned: dict[str, str] = {}
	today = datetime.datetime.now(datetime.timezone.utc).date()
	for source in sources:
		check_repository_root(source.path, "source")
		ref = git(source.path, [
			"rev-parse", "--verify", "--end-of-options", source.ref + "^{commit}"
		]).strip()
		key = source_key(source, since, until)
		checkpoint = scanned.get(key, previous.get(key))
		if checkpoint == ref:
			scanned[key] = ref
			continue
		revisions = [ref]
		if checkpoint:
			if git(source.path, ["rev-list", "--ignore-missing", "--no-walk", checkpoint, "--"]).strip():
				# Exclude only previously scanned ancestry, not commits with older dates.
				revisions.append("^" + checkpoint)
			else:
				print("A saved source checkpoint is unavailable; rescanning that source.", file=sys.stderr)
		output = git(source.path, [
			"log", "--no-merges", "--format=%H%x00%ae%x00%as", *revisions, "--"
		])
		emails = {email.casefold() for email in source.author_emails}
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
		scanned[key] = ref
	return CollectedActivity(
		tuple(SourceCommit(commit, date) for commit, date in commits.items()),
		MappingProxyType(scanned)
	)


def source_key(
	source: SourceRepository,
	since: datetime.date | None,
	until: datetime.date | None
) -> str:
	common_dir = Path(git(source.path, ["rev-parse", "--git-common-dir"]).strip())
	if not common_dir.is_absolute():
		common_dir = source.path / common_dir
	graph = [
		hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
		for path in (common_dir / "shallow", common_dir / "info" / "grafts")
	]
	replacements = git(source.path, ["for-each-ref", "--format=%(refname):%(objectname)", "refs/replace"])
	scope = [
		str(source.path), source.ref, sorted({email.casefold() for email in source.author_emails}),
		since.isoformat() if since else None, until.isoformat() if until else None,
		graph, replacements
	]
	return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
