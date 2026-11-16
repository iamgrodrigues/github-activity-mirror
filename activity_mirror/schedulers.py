import datetime
import locale
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from .models import MirrorError, Schedule


NAME = "github-activity-mirror"
LABEL = "io.github.activity-mirror"
TASK = "GitHubActivityMirror"
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def install(job: Schedule, path: Path) -> None:
	files = definitions(job, path)
	if any(target.exists() or target.is_symlink() for target in files):
		raise MirrorError("A scheduler definition already exists; it was not overwritten.")
	if sys.platform.startswith("linux"):
		before = read_crontab()
		if f"# BEGIN {NAME}" in before or f"# END {NAME}" in before:
			raise MirrorError("A cron job with this managed name already exists; it was not replaced.")
		for line in before.splitlines():
			if re.match(r"\s*(CRON_TZ|RANDOM_DELAY)\s*=", line):
				raise MirrorError("This crontab customizes timezone or random delay; no machine-local daily job was installed.")
			shell = re.fullmatch(r"\s*SHELL\s*=\s*(.*?)\s*", line)
			if shell and Path(shell[1].strip("'\"")).name not in ("sh", "bash", "dash", "zsh", "ksh", "ash"):
				raise MirrorError("This crontab selects an unsupported non-POSIX shell; nothing was changed.")
	for target, contents in files.items():
		target.parent.mkdir(parents=True, exist_ok=True)
		fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
		with os.fdopen(fd, "wb") as stream:
			stream.write(contents)
			stream.flush()
			os.fsync(stream.fileno())
	if sys.platform.startswith("linux"):
		prefix = before if not before or before.endswith("\n") else before + "\n"
		write_crontab(before, prefix + cron_block(job, path))
	elif sys.platform == "darwin":
		command(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(next(iter(files)))])
	else:
		command(["schtasks", "/Create", "/TN", TASK, "/XML", str(next(iter(files)))], answer="N\n")


def status(job: Schedule, path: Path) -> str:
	verify_files(job, path)
	if sys.platform.startswith("linux"):
		verify_cron_block(read_crontab(), job, path)
		return "Daily schedule is registered in the current user's crontab."
	if sys.platform == "darwin":
		registered = command(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]).stdout
		expected = str(next(iter(definitions(job, path))))
		if not any(
			line.strip() in (f"path = {expected}", f'path = "{expected}"')
			for line in registered.splitlines()
		):
			raise MirrorError("The registered launch agent does not belong to this installation.")
		return "Daily schedule is registered (logged-in macOS user agent)."
	registered = windows_registered(job, path)
	if registered.findtext("t:Settings/t:Enabled", namespaces=TASK_NS) != "true":
		raise MirrorError("The managed Windows task is disabled.")
	return "Daily schedule is registered and enabled (signed-in Windows user task)."


def remove(job: Schedule, path: Path) -> None:
	verify_files(job, path)
	if sys.platform.startswith("linux"):
		before = read_crontab()
		verify_cron_block(before, job, path)
		write_crontab(before, before.replace(cron_block(job, path), "", 1))
	elif sys.platform == "darwin":
		status(job, path)
		command(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
	else:
		windows_registered(job, path)
		command(["schtasks", "/Delete", "/TN", TASK, "/F"])
	for target in definitions(job, path):
		target.unlink()


def check_available() -> None:
	if sys.platform.startswith("linux"):
		executable = "crontab"
	elif sys.platform == "darwin":
		executable = "launchctl"
	elif sys.platform == "win32":
		executable = "schtasks"
	else:
		raise MirrorError("Scheduling is supported only on Linux, macOS, and Windows.")
	if shutil.which(executable) is None:
		raise MirrorError(f"Native scheduler {executable} is unavailable; nothing was installed.")
	if sys.platform.startswith("linux"):
		read_crontab()


def definitions(job: Schedule, path: Path) -> dict[Path, bytes]:
	arguments = [str(job.python), str(job.script), "--scheduled-run", str(path)]
	if any(any(ord(char) < 32 for char in item) for item in arguments):
		raise MirrorError("Scheduler paths cannot contain control characters.")
	if sys.platform.startswith("linux"):
		return {path.parent / "crontab.txt": cron_block(job, path).encode()}
	if sys.platform == "darwin":
		plist = {
			"Label": LABEL, "ProgramArguments": arguments,
			"WorkingDirectory": str(job.script.parent),
			"StartCalendarInterval": {"Hour": int(job.time[:2]), "Minute": int(job.time[3:])},
			"StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null", "Umask": 0o077
		}
		return {Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist": plistlib.dumps(plist)}
	if sys.platform == "win32":
		return {path.parent / "task.xml": windows_definition(job, path)}
	raise MirrorError("Unsupported native scheduler.")


def cron_block(job: Schedule, path: Path) -> str:
	arguments = [str(job.python), str(job.script), "--scheduled-run", str(path)]
	if any("\\" in item or any(ord(char) < 32 for char in item) for item in arguments):
		raise MirrorError("Linux cron requires scheduled tool/state paths without backslashes or control characters.")
	invocation = f"{{ umask 077; cd {shlex.quote(str(job.script.parent))} && exec {shlex.join(arguments)}; }} >/dev/null 2>&1"
	invocation = invocation.replace("%", "\\%")
	hour, minute = (int(value) for value in job.time.split(":"))
	return f"# BEGIN {NAME}\n{minute} {hour} * * * {invocation}\n# END {NAME}\n"


def read_crontab() -> str:
	result = command(["crontab", "-l"], allow_failure=True)
	if result.returncode == 0:
		return result.stdout
	if result.returncode == 1 and not result.stdout and result.stderr.strip().startswith("no crontab for "):
		return ""
	raise MirrorError("Could not read the current user's crontab; it was left unchanged.")


def write_crontab(before: str, after: str) -> None:
	if read_crontab() != before:
		raise MirrorError("Crontab changed during setup/removal; nothing was overwritten. Retry after other edits finish.")
	command(["crontab", "-"], answer=after)


def verify_cron_block(table: str, job: Schedule, path: Path) -> None:
	if (
		table.count(f"# BEGIN {NAME}\n") != 1 or table.count(f"# END {NAME}\n") != 1
		or cron_block(job, path) not in table
	):
		raise MirrorError("The managed cron block is missing or changed; no unrelated job was modified.")


def windows_definition(job: Schedule, path: Path) -> bytes:
	root = ET.Element("Task", {"version": "1.2", "xmlns": TASK_NS["t"]})
	info = ET.SubElement(root, "RegistrationInfo")
	ET.SubElement(info, "Description").text = f"GitHub Activity Mirror managed job: {path}"
	triggers = ET.SubElement(root, "Triggers")
	trigger = ET.SubElement(triggers, "CalendarTrigger")
	ET.SubElement(trigger, "StartBoundary").text = f"{job.start_date}T{job.time}:00"
	ET.SubElement(trigger, "Enabled").text = "true"
	daily = ET.SubElement(trigger, "ScheduleByDay")
	ET.SubElement(daily, "DaysInterval").text = "1"
	principals = ET.SubElement(root, "Principals")
	principal = ET.SubElement(principals, "Principal", {"id": "Author"})
	ET.SubElement(principal, "UserId").text = windows_user_sid()
	ET.SubElement(principal, "LogonType").text = "InteractiveToken"
	ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
	settings = ET.SubElement(root, "Settings")
	for key, value in (
		("MultipleInstancesPolicy", "IgnoreNew"), ("DisallowStartIfOnBatteries", "false"),
		("StopIfGoingOnBatteries", "false"), ("StartWhenAvailable", "true"),
		("Enabled", "true"), ("ExecutionTimeLimit", "PT15M")
	):
		ET.SubElement(settings, key).text = value
	actions = ET.SubElement(root, "Actions", {"Context": "Author"})
	execution = ET.SubElement(actions, "Exec")
	ET.SubElement(execution, "Command").text = str(job.python)
	ET.SubElement(execution, "Arguments").text = subprocess.list2cmdline([
		str(job.script), "--scheduled-run", str(path)
	])
	ET.SubElement(execution, "WorkingDirectory").text = str(job.script.parent)
	return ET.tostring(root, encoding="utf-16", xml_declaration=True)


def windows_user_sid() -> str:
	result = command([
		"powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
		"[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"
	]).stdout.strip()
	if not re.fullmatch(r"S-[0-9]+(?:-[0-9]+)+", result):
		raise MirrorError("Could not verify the current Windows user SID.")
	return result


def windows_registered(job: Schedule, path: Path) -> ET.Element:
	text = command(["schtasks", "/Query", "/TN", TASK, "/XML"]).stdout
	try:
		actual = ET.fromstring(text.lstrip("\ufeff"))
		expected = ET.fromstring(windows_definition(job, path))
	except ET.ParseError:
		raise MirrorError("Windows returned an invalid task definition; nothing was replaced or removed.") from None
	fields = (
		"RegistrationInfo/Description", "Actions/Exec/Command", "Actions/Exec/Arguments",
		"Actions/Exec/WorkingDirectory", "Principals/Principal/UserId",
		"Principals/Principal/LogonType", "Principals/Principal/RunLevel",
		"Triggers/CalendarTrigger/StartBoundary", "Triggers/CalendarTrigger/Enabled",
		"Triggers/CalendarTrigger/ScheduleByDay/DaysInterval",
		"Settings/MultipleInstancesPolicy", "Settings/StartWhenAvailable"
	)
	for field in fields:
		query = "/".join("t:" + part for part in field.split("/"))
		actual_value = actual.findtext(query, namespaces=TASK_NS)
		expected_value = expected.findtext(query, namespaces=TASK_NS)
		if field.endswith("StartBoundary") and actual_value and expected_value:
			try:
				if datetime.datetime.fromisoformat(actual_value) == datetime.datetime.fromisoformat(expected_value):
					continue
			except ValueError:
				raise MirrorError("Windows task has an invalid start date.") from None
		if actual_value != expected_value:
			raise MirrorError("The registered Windows task differs from this installation; nothing was removed.")
	for section in ("Actions", "Triggers", "Principals"):
		container = actual.find("t:" + section, TASK_NS)
		if container is None or len(container) != 1:
			raise MirrorError("The registered Windows task has unexpected actions, triggers, or principals.")
	return actual


def verify_files(job: Schedule, path: Path) -> None:
	for target, expected in definitions(job, path).items():
		if not target.is_file() or target.is_symlink() or target.read_bytes() != expected:
			raise MirrorError("Native scheduler files are missing or changed; refusing to manage an unverified job.")


def command(
	arguments: list[str], answer: str = "", allow_failure: bool = False
) -> subprocess.CompletedProcess[str]:
	try:
		env = dict(os.environ)
		if arguments[0] == "crontab":
			env["LC_ALL"] = "C"
		result = subprocess.run(arguments, input=answer.encode("utf-8"), capture_output=True, timeout=60, env=env)
	except (OSError, subprocess.TimeoutExpired):
		raise MirrorError("Native scheduler is unavailable or timed out; check the current user's scheduler locally.") from None
	if result.returncode and not allow_failure:
		raise MirrorError("Native scheduler command failed. Check the user scheduler; private setup state was retained for recovery.")
	data = result.stdout
	encoding = (
		"utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff"))
		else "utf-8-sig" if data.startswith(b"\xef\xbb\xbf")
		else "utf-16-le" if data.startswith(b"<\x00")
		else locale.getpreferredencoding(False)
	)
	try:
		output = data.decode(encoding)
	except UnicodeDecodeError:
		raise MirrorError("Native scheduler returned text in an unexpected encoding.") from None
	return subprocess.CompletedProcess(arguments, result.returncode, output, result.stderr.decode("latin-1"))
