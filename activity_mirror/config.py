import datetime
import json
from pathlib import Path
import re

from .models import Config, Destination, GitIdentity, HistoryWindow, MirrorError, SourceRepository, require_keys, valid_date


def load_config(path: Path) -> Config:
	path = path.expanduser().resolve()
	try:
		raw: object = json.loads(path.read_text())
	except json.JSONDecodeError:
		raise MirrorError("Configuration is not valid JSON.") from None
	return parse_config(raw, path.parent)


def parse_config(raw: object, base: Path) -> Config:
	keys = {"destination", "companies"}
	if isinstance(raw, dict) and "history" in raw:
		keys.add("history")
	config = require_keys(raw, keys, "configuration")
	destination = parse_destination(config["destination"], base)
	companies = config["companies"]
	if not isinstance(companies, list) or not companies:
		raise MirrorError("companies must contain at least one company.")
	names: set[str] = set()
	sources: list[SourceRepository] = []
	for raw_company in companies:
		company = require_keys(raw_company, {"name", "repositories"}, "company")
		name = company["name"]
		if not isinstance(name, str) or not name.strip() or name in names:
			raise MirrorError("Each company needs a unique, nonempty local name.")
		names.add(name)
		repositories = company["repositories"]
		if not isinstance(repositories, list) or not repositories:
			raise MirrorError("Each company must contain at least one repository.")
		for repository in repositories:
			sources.append(parse_source(repository, base, destination.path))
	history = parse_history(config["history"]) if "history" in config else HistoryWindow()
	return Config(destination, tuple(sources), history)


def parse_destination(raw: object, base: Path) -> Destination:
	destination = require_keys(raw, {"path", "author_name", "author_email"}, "destination")
	name = parse_identity_field(destination["author_name"], "author_name")
	email = parse_identity_field(destination["author_email"], "author_email")
	if "@" not in email:
		raise MirrorError("destination.author_email must be an email address.")
	return Destination(resolve_path(destination["path"], base), GitIdentity(name, email))


def parse_identity_field(value: object, key: str) -> str:
	if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\x00<>"):
		raise MirrorError(f"destination.{key} must be a nonempty, single-line Git identity.")
	return value


def resolve_path(value: object, base: Path) -> Path:
	if not isinstance(value, str) or not value.strip():
		raise MirrorError("Repository paths must be nonempty strings.")
	path = Path(value).expanduser()
	return (path if path.is_absolute() else base / path).resolve()


def parse_source(raw: object, base: Path, destination: Path) -> SourceRepository:
	source = require_keys(raw, {"path", "ref", "author_emails"}, "source repository")
	path = resolve_path(source["path"], base)
	if path == destination or path in destination.parents or destination in path.parents:
		raise MirrorError("Source and destination directories must not overlap.")
	ref = source["ref"]
	if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref):
		raise MirrorError("Each source ref must be an explicit Git branch or commit reference.")
	raw_emails = source["author_emails"]
	if not isinstance(raw_emails, list) or not raw_emails:
		raise MirrorError("Each source requires explicit author_emails.")
	emails: list[str] = []
	for email in raw_emails:
		if not isinstance(email, str) or "@" not in email or any(c in email for c in "\r\n\x00"):
			raise MirrorError("Each source requires explicit author_emails.")
		emails.append(email)
	return SourceRepository(path, ref, tuple(emails))


def parse_history(raw: object) -> HistoryWindow:
	history = require_keys(raw, {"since", "until"}, "history")
	dates: list[datetime.date | None] = []
	for key in ("since", "until"):
		value = history[key]
		if value is None:
			dates.append(None)
		elif isinstance(value, str) and valid_date(value):
			dates.append(datetime.date.fromisoformat(value))
		else:
			raise MirrorError(f"history.{key} must be a date in YYYY-MM-DD format or null.")
	since, until = dates
	if since and until and since > until:
		raise MirrorError("history.since must not be after history.until.")
	return HistoryWindow(since, until)
