import datetime
import json
import os
from pathlib import Path
import shutil

from .config import parse_config, parse_destination, parse_source, resolve_path
from .destination import inspect_destination
from .git import check_repository_root, git
from .models import Config, Destination, MirrorError, valid_date


def configure(path: Path) -> Config | None:
	path = path.expanduser().resolve()
	if path.exists():
		print("Existing configuration left unchanged. Setup never overwrites settings.")
		print("To create another profile, use --setup --config config.new.json.")
		return None
	if shutil.which("git") is None:
		raise MirrorError("Git was not found. Install Git 2.34+ and reopen your terminal before setup.")
	print("GitHub Activity Mirror - guided local setup")
	print("No source files, authentication settings, or remote repositories will be changed.")
	try:
		if not confirm("Do you have permission to export the chosen sources' activity dates/counts?"):
			print("Setup cancelled. No configuration was saved.")
			return None
		destination_raw = prompt_destination(path.parent)
		destination = parse_destination(destination_raw, path.parent)
		sources = [prompt_source(path.parent, destination)]
		while confirm("Add another approved source repository?"):
			sources.append(prompt_source(path.parent, destination))
		history = prompt_history()
		raw: dict[str, object] = {
			"destination": destination_raw,
			"companies": [{"name": "approved-sources", "repositories": sources}],
			"history": history
		}
		config = parse_config(raw, path.parent)
		if path == destination.path or destination.path in path.parents:
			raise MirrorError("Save the configuration outside the personal mirror folder.")
		if any(source.path in path.parents for source in config.sources):
			raise MirrorError("Save the configuration outside every source repository.")
		print(f"\nSettings file: {path}")
		print(f"Personal mirror folder: {config.destination.path}")
		print(f"Personal commit email: {config.destination.identity.email}")
		print(f"Approved source repositories: {len(config.sources)}")
		print(f"History: {history['since'] or 'all dates'} through {history['until'] or 'ongoing'}")
		print("These settings contain private source details. Do not commit or share them.")
		if not confirm("Save these settings and run a read-only preview?"):
			print("Setup cancelled. No configuration was saved.")
			return None
	except (EOFError, KeyboardInterrupt):
		print("\nSetup cancelled. No configuration was saved.")
		return None
	save_new_config(path, raw)
	print("Settings saved. Starting preview only; nothing will be imported or pushed.")
	return config


def confirm(message: str) -> bool:
	while True:
		answer = input(f"{message} [y/N]: ").strip().casefold()
		if answer in ("y", "yes"):
			return True
		if answer in ("", "n", "no"):
			return False
		print("Please enter y or n.")


def prompt_destination(base: Path) -> dict[str, str]:
	name = ""
	email = ""
	path = str(base / "local" / "mirror")
	print("\n1. Your PERSONAL identity, not your source account's identity.")
	print("Use the personal GitHub noreply email from your account's Settings > Emails.")
	while True:
		name = ask("Public personal Git name", name)
		email = ask("Personal GitHub author email", email)
		path = ask("Personal mirror folder (not a source repository)", path)
		raw = {"path": path, "author_name": name, "author_email": email}
		try:
			inspect_destination(parse_destination(raw, base))
			return raw
		except (MirrorError, OSError) as error:
			print(f"Please correct the personal destination: {error}")


def ask(message: str, default: str = "") -> str:
	while True:
		suffix = f" [{default}]" if default else ""
		answer = input(f"{message}{suffix}: ").strip()
		if answer:
			return answer
		if default:
			return default
		print("Please enter a value.")


def prompt_source(base: Path, destination: Destination) -> dict[str, object]:
	print("\n2. An existing approved LOCAL source. No source will be cloned or fetched.")
	while True:
		try:
			path = resolve_path(ask("Source repository folder").strip('"'), base)
			check_repository_root(path, "source")
			ref = ask("Approved source branch/ref", default_ref(path))
			emails = [email.strip() for email in ask("Your SOURCE author email(s), comma-separated").split(",")]
			raw: dict[str, object] = {"path": str(path), "ref": ref, "author_emails": emails}
			source = parse_source(raw, base, destination.path)
			git(source.path, ["rev-parse", "--verify", "--end-of-options", source.ref + "^{commit}"])
			return raw
		except (MirrorError, OSError) as error:
			print(f"Please correct the source: {error}")
			print("Choose its repository root, an existing local ref, and only your own author emails.")


def default_ref(path: Path) -> str:
	upstream = git(path, [
		"for-each-ref", "--format=%(symref)", "refs/remotes/origin/HEAD"
	]).strip()
	if upstream:
		return upstream.removeprefix("refs/remotes/")
	refs = git(path, [
		"for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"
	]).splitlines()
	for candidate in ("origin/main", "origin/master", "main", "master"):
		if candidate in refs:
			return candidate
	return ""


def prompt_history() -> dict[str, str | None]:
	print("\n3. Choose how far back to import. There is no fixed year cutoff.")
	print("Saved limits apply on later runs. They never delete already imported activity.")
	today = datetime.datetime.now(datetime.timezone.utc).date()
	since: datetime.date | None = today.replace(month=1, day=1)
	until: datetime.date | None = None
	while True:
		since = ask_date("First author date", since, "all")
		until = ask_date("Last author date", until, "ongoing")
		if since and until and since > until:
			print("The first date must not be after the last date. Please correct the range.")
			continue
		return {
			"since": since.isoformat() if since else None,
			"until": until.isoformat() if until else None
		}


def ask_date(message: str, default: datetime.date | None, unbounded: str) -> datetime.date | None:
	while True:
		value = ask(
			f"{message} (YYYY-MM-DD or '{unbounded}')",
			default.isoformat() if default else unbounded
		)
		if value.casefold() == unbounded:
			return None
		if valid_date(value):
			return datetime.date.fromisoformat(value)
		print(f"Use a real date in YYYY-MM-DD format, or '{unbounded}'.")


def save_new_config(path: Path, raw: dict[str, object]) -> None:
	contents = json.dumps(raw, indent="\t") + "\n"
	path.parent.mkdir(parents=True, exist_ok=True)
	try:
		fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
	except FileExistsError:
		raise MirrorError("Configuration appeared during setup; it was left unchanged. Choose another file.") from None
	completed = False
	try:
		with os.fdopen(fd, "w") as stream:
			stream.write(contents)
			stream.flush()
			os.fsync(stream.fileno())
		completed = True
	finally:
		if not completed:
			path.unlink()
