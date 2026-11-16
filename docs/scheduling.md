# Daily scheduling

This optional setup imports approved activity and pushes **only your synthetic
personal mirror** every day. It does not install a GitHub Action or upload source
credentials. You can continue using manual previews/imports without enabling it.

## Before you start

1. Complete the [quick start](../README.md) and connect your dedicated private
   personal mirror using [the publishing guide](manual-setup.md#7-publish-to-your-private-personal-repository).
2. Install [GitHub CLI](https://cli.github.com/) if it is missing.
3. From the tool folder, check `gh api --hostname github.com user --jq ".login"`.
   It must show your personal account. Keep any existing account-routing setup;
   the scheduler never signs in or switches accounts for you.

Automatic publishing requires:

- A private, non-fork repository owned by your personal account, writable by
  that account, with `main` as its default branch.
- An existing dedicated, file-free local mirror on `main`.
- Exactly one `origin` fetch URL and push URL, both the intended credential-free
  `https://github.com/YOUR_USERNAME/YOUR_DATA_REPOSITORY.git`.
- Your matching personal GitHub **noreply email** as the mirror identity.
- Permission for recurring export of the selected activity dates/counts.

Do not use a source repository as the destination.

## Turn it on

Open a terminal in the **tool folder**:

```bash
python3 mirror.py --schedule install
```

On Windows, replace `python3` with `python`. For another configuration, add
`--config config.other.json`.

The wizard asks for:

1. Your personal data repository, as `OWNER/REPOSITORY`.
2. A daily 24-hour time; press Enter for **00:00**, in the machine's local timezone.
3. First and last approved author dates. Use `all` or `ongoing` only when that
   wider scope is authorized. These job-specific dates do not edit your config.
4. Explicit permission for recurring imports **and pushes**.

The wizard checks your personal account, private destination, and native
scheduler before activation. It never replaces an existing job or signs in with
another account. Only one managed schedule per OS user is supported.

**Expected:** a registered/enabled daily job and a confirmation of the selected
time. A missed run may execute after activation. Cancelling before confirmation
installs nothing. If installation fails, stop and inspect status; do not assume
the job is active or delete private recovery state.

## Check or stop it

```bash
python3 mirror.py --schedule status
python3 mirror.py --schedule remove
```

Status verifies the native definition and shows the latest recorded run result.
A registered job is not proof that an import or push has succeeded: before the
first completed run, status explicitly says that none has been recorded.

Removal stops future scheduling but keeps your configuration, mirror, ledger,
and last-run result. It refuses to delete modified or unrelated scheduler jobs.
If a run is active, wait for it to finish before removal.

To change the time, history window, or configuration, remove the schedule and
install it again. Configuration changes invalidate the old export approval:
future runs stop before reading source history until you review and reinstall.
Remove the job before moving/upgrading the tool folder or its Python interpreter.

## What runs, and when

| Platform | Native mechanism | Availability and missed runs |
| --- | --- | --- |
| Linux | Current user's crontab | Requires `crontab` and an already running cron daemon. No active login session is needed. A missed run waits for the next daily slot; its import includes accumulated activity. No daemon or startup settings are changed. |
| macOS | Logged-in user's launchd agent | Runs while that user is logged in. Sleeping calendar events are coalesced on wake. Runs missed while powered off or logged out are not guaranteed to catch up. |
| Windows | Task Scheduler, current user, least privilege | Runs only while that user is signed in, without storing a password. `StartWhenAvailable` allows delayed starts. It does not wake the machine. |

The macOS and Windows adapters are experimental until exercised on native
machines; simulated definition checks do not prove native installation.

The schedule uses the machine's local timezone, including daylight-saving changes,
and is not an exact-to-the-second guarantee. A Linux installation inside WSL
requires cron and the Linux environment to be running; it does not start WSL
from Windows or change Windows startup settings.

On Linux, only the explicitly marked managed block is added/removed. Other
crontab content is preserved, and concurrent edits detected before writing stop
the operation. Avoid editing crontab during installation/removal. Existing
`CRON_TZ`, `RANDOM_DELAY`, or a non-POSIX cron shell are rejected rather than
silently changing timing or unrelated jobs. Scheduled tool/state paths cannot
contain backslashes or control characters; spaces and literal `%` are supported.

Each run:

1. Checks stored permissions and the approved configuration fingerprint.
2. Verifies the configured personal account and private destination.
3. Reads **existing local refs only**, applying the approved date window.
4. Reuses the existing ledger, import validation, and destination lock.
5. Rechecks the destination and pushes one validated synthetic commit tip to
   `main`, without force-pushing, tags, source refs, or local hooks.
6. Records a private summary, not source metadata.

Normal approved source fetches remain your responsibility. If you have not
refreshed a configured upstream ref, a daily run cannot see newer remote commits.
After the first successful import, [incremental scanning](manual-setup.md#faster-repeat-imports)
skips unchanged source history and reads only newly reachable commits for
changed branches. Scope changes or missing checkpoints can require a full scan.

## Privacy and failures

Private scheduler state is outside the tool and data repositories:

| Platform | State folder |
| --- | --- |
| Linux | `~/.local/state/github-activity-mirror/` |
| macOS | `~/Library/Application Support/GitHub Activity Mirror/` |
| Windows | `%LOCALAPPDATA%/GitHubActivityMirror/` |

`job.json` contains local paths, selected dates, permission flags, and a
configuration fingerprint. `last-run.json` contains the latest success/failure
summary. Neither belongs in a repository or an unapproved cloud backup.
The source-hash ledger stays inside the mirror's `.git` folder as before.
Unix state is user-private; on Windows protect your user profile with its normal
account access permissions. No tokens or passwords are saved in job settings.

GitHub CLI verifies the personal account and target. The push uses your existing
approved Git credentials/account routing; configure both to select the same
personal account before enabling automation. No Git credential helper is replaced,
even temporarily. Authentication must work noninteractively for the same OS user;
expired credentials, unavailable keychains, or offline networking cause an
explicit failure, never a tool-initiated switch to another account.

If a push fails after a successful import, the local commits and ledger remain.
The next successful daily run retries publication even if no new commits were
created, without duplicating activity. A failed import never proceeds to push.
Overlapping scheduled executions are rejected by the job lock.

For failures, use `--schedule status` and inspect the named job in crontab,
launchd, or Task Scheduler. Keep native/private state after an interrupted
installation for diagnosis. Do not delete the ledger, force-push, or replace a
foreign job to make status pass. Git and scheduler diagnostics deliberately
omit raw command errors that could contain private paths or URLs.

See also [Linux crontab](https://man7.org/linux/man-pages/man5/crontab.5.html),
[Apple's launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html),
and [Windows Task Scheduler](https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-start-page).
