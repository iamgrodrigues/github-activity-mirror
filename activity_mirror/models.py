import datetime
from dataclasses import dataclass
from pathlib import Path


class MirrorError(Exception):
	pass


@dataclass(frozen=True)
class GitIdentity:
	name: str
	email: str


@dataclass(frozen=True)
class Destination:
	path: Path
	identity: GitIdentity


@dataclass(frozen=True)
class SourceRepository:
	path: Path
	ref: str
	author_emails: tuple[str, ...]


@dataclass(frozen=True)
class Config:
	destination: Destination
	sources: tuple[SourceRepository, ...]


@dataclass(frozen=True)
class SourceCommit:
	commit_hash: str
	date: str


@dataclass(frozen=True)
class DailyCount:
	date: str
	count: int


@dataclass(frozen=True)
class MirrorPlan:
	daily_totals: tuple[DailyCount, ...]
	new_mirror_commits: tuple[DailyCount, ...]


def require_keys(value: object, keys: set[str], label: str) -> dict[str, object]:
	if not isinstance(value, dict) or set(value) != keys:
		raise MirrorError(f"Invalid {label}; required keys: {', '.join(sorted(keys))}.")
	return value


def valid_date(value: object) -> bool:
	if not isinstance(value, str):
		return False
	try:
		return datetime.date.fromisoformat(value).isoformat() == value
	except ValueError:
		return False
