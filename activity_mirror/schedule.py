import contextlib
from dataclasses import asdict, replace
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Callable

from .config import parse_config, parse_history
from .ledger import DestinationLock
from .models import Config, MirrorError, MirrorPlan, Schedule, require_keys, valid_date
from .publishing import publish, validate_repository, verify_destination
from .setup import ask, ask_date, confirm, save_new_config


def run_scheduled(path: Path, importer: Callable[[Config], MirrorPlan]) -> None:
	job = load_job(path)
	with DestinationLock(path.parent):
		try:
			config = approved_config(job)
			with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
				previous_path = os.environ.get("PATH")
				try:
					os.environ["PATH"] = job.path
					verify_destination(config.destination, job.repository)
					plan = importer(config)
					pushed = publish(config.destination, job.repository)
				finally:
					if previous_path is None:
						os.environ.pop("PATH", None)
					else:
						os.environ["PATH"] = previous_path
		except MirrorError as error:
			save_result(path.parent, {"success": False, "error": str(error)})
			raise
		except OSError:
			message = "Scheduled run could not access its private settings or repositories. Check paths and permissions locally."
			save_result(path.parent, {"success": False, "error": message})
			raise MirrorError(message) from None
		save_result(path.parent, {
			"success": True, "created": sum(day.count for day in plan.new_mirror_commits),
			"published": pushed
		})


def manage_schedule(action: str, config_path: Path, script: Path) -> None:
	from . import schedulers

	path = state_directory() / "job.json"
	if action == "install":
		install_schedule(config_path, script, path)
		return
	if not path.exists():
		print("No managed daily schedule is installed for this user.")
		return
	job = load_job(path, require_approval=False)
	if job.script != script:
		raise MirrorError("This schedule belongs to another tool folder. Manage it from that folder.")
	if action == "status":
		print(schedulers.status(job, path))
		if not job.export_approved or not job.publish_approved:
			print("Recurring approval is revoked; scheduled imports and publishing are blocked.")
		print(f"Daily time: {job.time} (machine local time)")
		print(f"Private state: {path.parent}")
		result = path.parent / "last-run.json"
		if result.exists():
			print("Last run: " + result.read_text().strip())
		else:
			print("No completed run has been recorded yet.")
	elif action == "remove":
		with DestinationLock(path.parent):
			schedulers.remove(job, path)
			path.unlink()
		print("Daily schedule removed. Your configuration, mirror, ledger, and last-run result were kept.")
	else:
		raise MirrorError("Unknown scheduler action.")


def install_schedule(config_path: Path, script: Path, path: Path) -> None:
	from . import schedulers

	if path.exists():
		raise MirrorError("Private scheduling settings already exist. Check --schedule status before attempting another installation.")
	if any(shutil.which(command) is None for command in ("git", "gh")):
		raise MirrorError("Scheduling requires Git and GitHub CLI on PATH. Install the missing tool and reopen your terminal.")
	config_path = config_path.expanduser().resolve()
	contents = config_path.read_bytes()
	config = read_configuration(contents, config_path.parent)
	state = path.parent.resolve()
	if any(folder == state or folder in state.parents for folder in (
		config.destination.path, *(source.path for source in config.sources)
	)):
		raise MirrorError("Keep scheduler state outside all source and mirror folders.")
	print("Optional daily import and push - personal private mirror only")
	print("Sources stay read-only and are never fetched. No credentials will be saved here.")
	print("Runs use the machine's local timezone and require it to be available.")
	try:
		repository = ask("PERSONAL GitHub data repository (OWNER/REPOSITORY)")
		validate_repository(repository)
		while True:
			time = ask("Daily local time (HH:MM)", "00:00")
			if valid_time(time):
				break
			print("Use a 24-hour time such as 00:00 or 18:30.")
		while True:
			since = ask_date(
				"First approved author date", config.history.since
				or datetime.date.today().replace(month=1, day=1), "all"
			)
			until = ask_date("Last approved author date", config.history.until, "ongoing")
			if since is None or until is None or since <= until:
				break
			print("The first date must not be after the last date.")
		print(f"\nDaily job: {time}, import and push only to {repository}")
		print(f"Approved history: {since or 'all dates'} through {until or 'ongoing'}")
		print("Missed-run behavior depends on the platform. Nothing runs in the cloud.")
		if not confirm("Authorize recurring export of these dates/counts and publishing to this private personal repository?"):
			print("Scheduling cancelled. No job was installed.")
			return
	except (EOFError, KeyboardInterrupt):
		print("\nScheduling cancelled. No job was installed.")
		return
	job = Schedule(
		config_path, hashlib.sha256(contents).hexdigest(), repository,
		parse_history({"since": str(since) if since else None, "until": str(until) if until else None}),
		time, Path(sys.executable).absolute(), script, os.environ.get("PATH", ""), datetime.date.today()
	)
	verify_destination(config.destination, repository)
	schedulers.check_available()
	for target in schedulers.definitions(job, path):
		if not target.is_absolute():
			raise MirrorError("Native scheduler files must use absolute paths.")
		target = target.resolve()
		if any(
			folder == target or folder in target.parents
			for folder in (config.destination.path, *(source.path for source in config.sources))
		):
			raise MirrorError("Native scheduler files must stay outside source and mirror folders.")
	path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
	if os.name == "posix" and path.parent.stat().st_mode & 0o077:
		raise MirrorError("Scheduler state folder must be private to your user (mode 0700).")
	save_new_config(path, serialize_job(job))
	schedulers.install(job, path)
	print(schedulers.status(job, path))
	print(f"Daily import and push installed for {time} (machine local time).")
	print("Use --schedule status to check the latest result, or --schedule remove to stop future runs.")


def load_job(path: Path, require_approval: bool = True) -> Schedule:
	try:
		raw: object = json.loads(path.read_text(encoding="utf-8"))
	except json.JSONDecodeError:
		raise MirrorError("Private scheduler settings are invalid JSON; do not guess or recreate approval.") from None
	if not isinstance(raw, dict):
		raise MirrorError("Private scheduler settings must be an object with explicit approval flags.")
	export_approved = raw.get("export_approved")
	publish_approved = raw.get("publish_approved")
	if not isinstance(export_approved, bool) or not isinstance(publish_approved, bool):
		raise MirrorError("Stored export and publishing approval flags must be explicit booleans.")
	if require_approval and (not export_approved or not publish_approved):
		raise MirrorError("Stored export and publishing approval are required before scheduled execution.")
	value = require_keys(raw, {
		"version", "config", "config_sha256", "repository", "history", "time",
		"python", "script", "path", "start_date", "export_approved", "publish_approved"
	}, "scheduler settings")
	if type(value["version"]) is not int or value["version"] != 1:
		raise MirrorError("Unsupported scheduler settings version.")
	text: dict[str, str] = {}
	for key in ("config", "config_sha256", "repository", "time", "python", "script", "path", "start_date"):
		item = value[key]
		if not isinstance(item, str) or not item or any(ord(char) < 32 for char in item):
			raise MirrorError(f"Invalid scheduler {key}; expected nonempty single-line text.")
		text[key] = item
	for key in ("config", "python", "script"):
		if not Path(text[key]).is_absolute():
			raise MirrorError("Scheduled paths must be absolute; reinstall the schedule.")
	if not re.fullmatch(r"[a-f0-9]{64}", text["config_sha256"]):
		raise MirrorError("Invalid scheduler configuration fingerprint.")
	validate_repository(text["repository"])
	if not valid_time(text["time"]) or not valid_date(text["start_date"]):
		raise MirrorError("Invalid scheduler time or start date.")
	job = Schedule(
		Path(text["config"]), text["config_sha256"], text["repository"],
		parse_history(value["history"]), text["time"], Path(text["python"]),
		Path(text["script"]), text["path"], datetime.date.fromisoformat(text["start_date"]),
		export_approved, publish_approved
	)
	if job.script != Path(__file__).resolve().parent.parent / "mirror.py":
		raise MirrorError("Scheduled execution belongs to another tool folder; reinstall from the approved folder.")
	return job


def approved_config(job: Schedule) -> Config:
	contents = job.config.read_bytes()
	if hashlib.sha256(contents).hexdigest() != job.config_sha256:
		raise MirrorError("Configuration changed since approval. Remove and reinstall the schedule to review its scope.")
	return replace(read_configuration(contents, job.config.parent), history=job.history)


def read_configuration(contents: bytes, base: Path) -> Config:
	try:
		return parse_config(json.loads(contents), base)
	except (json.JSONDecodeError, UnicodeDecodeError):
		raise MirrorError("Configuration is not valid UTF-8 JSON.") from None


def save_result(folder: Path, result: dict[str, object]) -> None:
	result = {"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), **result}
	fd, temporary = tempfile.mkstemp(prefix=".last-run-", dir=folder)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as stream:
			json.dump(result, stream)
			stream.write("\n")
			stream.flush()
			os.fsync(stream.fileno())
		os.replace(temporary, folder / "last-run.json")
	finally:
		if os.path.exists(temporary):
			os.unlink(temporary)


def state_directory() -> Path:
	if sys.platform == "win32":
		base = os.environ.get("LOCALAPPDATA")
		if not base:
			raise MirrorError("Windows LOCALAPPDATA is unavailable; use your normal signed-in user session.")
		return Path(base) / "GitHubActivityMirror"
	if sys.platform == "darwin":
		return Path.home() / "Library" / "Application Support" / "GitHub Activity Mirror"
	if sys.platform.startswith("linux"):
		return Path.home() / ".local" / "state" / "github-activity-mirror"
	raise MirrorError("Scheduling is supported only on Linux, macOS, and Windows.")


def serialize_job(job: Schedule) -> dict[str, object]:
	return json.loads(json.dumps({
		"version": 1, **asdict(job)
	}, default=str))


def valid_time(value: str) -> bool:
	return re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is not None
