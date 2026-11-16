import collections
from dataclasses import dataclass
from pathlib import Path

from .git import check_repository_root, git
from .models import DailyCount, Destination, MirrorError, MirrorPlan, valid_date


MESSAGE = "Record development activity\n\nActivity-Mirror-Version: 1"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass(frozen=True)
class DestinationSnapshot:
	exists: bool
	git_dir: Path
	daily_counts: tuple[DailyCount, ...]
	head: str | None


def inspect_destination(destination: Destination) -> DestinationSnapshot:
	path = destination.path
	if path.exists() and not path.is_dir():
		raise MirrorError("Destination is not a directory.")
	exists = path.exists() and any(path.iterdir())
	git_dir = path / ".git"
	if not exists:
		return DestinationSnapshot(False, git_dir, (), None)
	check_repository_root(path, "destination")
	if Path(git(path, ["rev-parse", "--absolute-git-dir"]).strip()) != git_dir:
		raise MirrorError("Destination must be a standalone repository, not a worktree.")
	counts = read_mirror(destination)
	head = git(path, ["rev-parse", "HEAD"]).strip() if counts else None
	return DestinationSnapshot(True, git_dir, counts, head)


def read_mirror(destination: Destination) -> tuple[DailyCount, ...]:
	path = destination.path
	if git(path, ["status", "--porcelain", "--untracked-files=all"]).strip():
		raise MirrorError("Destination must have no files or staged changes.")
	heads = git(path, ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"]).splitlines()
	if git(path, ["symbolic-ref", "-q", "HEAD"]).strip() != "refs/heads/main":
		raise MirrorError("Destination must use the main branch.")
	if not heads:
		return ()
	if heads != ["refs/heads/main"]:
		raise MirrorError("Destination must contain only the main branch and no tags.")
	raw = git(path, ["log", "--format=%T%x00%ae%x00%an%x00%aI%x00%P%x00%B%x00", "HEAD", "--"])
	fields = raw.split("\x00")
	counts: collections.Counter[str] = collections.Counter()
	for index in range(0, len(fields) - 1, 6):
		tree, email, name, timestamp, parents, message = fields[index:index + 6]
		if (
			tree.strip() != EMPTY_TREE or email != destination.identity.email
			or name != destination.identity.name or message.strip() != MESSAGE
			or len(parents.split()) > 1 or not timestamp.endswith("T12:00:00+00:00")
			or not valid_date(timestamp[:10])
		):
			raise MirrorError("Destination contains non-mirror history; use a dedicated empty repository.")
		counts[timestamp[:10]] += 1
	return tuple(DailyCount(date, count) for date, count in sorted(counts.items()))


def prepare_destination(destination: Destination, snapshot: DestinationSnapshot) -> None:
	destination.path.mkdir(parents=True, exist_ok=True)
	if not snapshot.exists:
		git(destination.path, ["init", "--initial-branch=main"])


def write_commits(destination: Destination, plan: MirrorPlan) -> None:
	for daily in plan.new_mirror_commits:
		for _ in range(daily.count):
			git(destination.path, [
				"-c", "core.hooksPath=/dev/null",
				"-c", "commit.gpgSign=false",
				"-c", "core.logAllRefUpdates=false",
				"commit", "--allow-empty", "--no-verify", "--quiet", "-m", MESSAGE
			], identity=destination.identity, date=daily.date)
