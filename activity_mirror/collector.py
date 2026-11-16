import datetime

from .git import check_repository_root, git
from .models import MirrorError, SourceCommit, SourceRepository, valid_date


def collect(
	sources: tuple[SourceRepository, ...],
	since: datetime.date | None = None,
	until: datetime.date | None = None
) -> tuple[SourceCommit, ...]:
	commits: dict[str, str] = {}
	today = datetime.datetime.now(datetime.timezone.utc).date()
	for source in sources:
		check_repository_root(source.path, "source")
		ref = git(source.path, [
			"rev-parse", "--verify", "--end-of-options", source.ref + "^{commit}"
		]).strip()
		output = git(source.path, [
			"log", "--no-merges", "--format=%H%x00%ae%x00%as", ref, "--"
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
	return tuple(SourceCommit(commit, date) for commit, date in commits.items())
