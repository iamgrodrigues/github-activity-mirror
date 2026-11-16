# GitHub Activity Mirror

A local, preview-first tool for reflecting **daily Git commit activity** in a
separate personal repository. It creates empty commits, not copies of work code.

**Establish permission to export activity dates and counts before using real
work sources.** Local access, a private destination, and writing the tool yourself
do not establish that permission.

## What this does

- Reads explicitly configured local repositories and author emails.
- Imports historical or new commits reachable from each configured ref.
- Excludes merge commits, other authors, and duplicate commit hashes.
- Groups activity by the author's calendar date, discarding the original time.
- Creates synthetic personal commits at noon UTC on those dates.
- Retains a private local ledger so reruns and future-company configurations do
  not duplicate previously imported commits.
- Never fetches, pushes, calls GitHub APIs, or changes source repositories.

It does **not** transfer official contribution credit, reproduce language
statistics, collect PR approvals, or guarantee an identical work-account total.
GitHub counts several contribution types; this tool only mirrors selected Git
commits. A rebased/cherry-picked commit has a new hash and is a distinct event.
Synthetic mirror activity should not be represented as original personal code.

## Requirements

Python 3.10+ and Git 2.34+. No Python packages, third-party apps, tokens, GitHub
Actions, or network access are required to run the tool. SHA-1 Git repositories
are supported; other object formats are rejected explicitly.

## Configure locally

```bash
cp config.example.json config.json
chmod 600 config.json
```

Edit `config.json`:

- `destination.path`: a **dedicated** empty directory or an existing mirror made
  by this tool, never a work repository or this tool's own repository.
- `destination.author_name` / `author_email`: your personal Git identity. The
  email must be associated with your personal GitHub account for contribution
  credit. A verified GitHub-provided noreply identity avoids publishing your
  personal email address.
- `companies`: local labels grouping source repositories. Each repository
  specifies its root path, exact author emails, and an explicit `ref`.
- Prefer the upstream default branch, such as `origin/main`, to exclude
  unmerged feature work. The tool uses the locally available ref without
  fetching; use your normal approved work process to update source clones.

Relative paths are resolved relative to the configuration file, not the shell.
`config.json`, `config.*.json` (except the example), and `local/` are ignored by
this repository. Never force-add real configurations or ledgers.

For a future company, add another company entry and its approved sources. Once
old activity has been imported, its company entry can be removed; the local
ledger preserves the old activity without needing ongoing access to that repo.
Keep one destination/ledger for a cumulative mirror, or use separate configs and
destinations for isolated histories. Use the same personal author identity when
continuing an existing destination.

## Preview first

```bash
python3 mirror.py --config config.json
python3 mirror.py --config config.json --since 2024-01-01 --until 2024-12-31
```

Standard output contains only dates and counts:

```json
{
	"daily_totals": [{"date": "2024-01-02", "count": 2}],
	"new_mirror_commits": [{"date": "2024-01-02", "count": 2}]
}
```

The preview writes nothing. Date filters apply only to newly collected source
commits; they never erase already imported activity. Future-dated commits cause
an error unless excluded by your explicit date range. Missing repositories or
refs fail the run rather than silently dropping activity.

## Create local mirror commits

After permission is established and the preview is reviewed:

```bash
python3 mirror.py --config config.json --apply --allow-metadata-export
```

This writes **only to the local destination**. It does not publish anything.
Rerunning creates only missing commits. An interrupted commit loop can be resumed
using the same ledger. A lock blocks simultaneous writes to that destination.
After a crash, inspect running processes before manually removing a stale
`.git/activity-mirror.lock`.

The destination must remain file-free, use only its `main` branch, and contain
only this tool's generic empty commits. Unexpected history, staged files, or
untracked files stop the run. There is no automatic deletion, force push, or
history rewriting.

## Publish separately, only when authorized

Create a **separate private personal GitHub repository**, empty with no initial
README, license, or gitignore. Do not fork or copy company repositories.

Verify the authenticated account is your personal account and verify the exact
remote owner/repository before publishing. From the configured destination,
add its personal remote and push `main` using your normal approved Git workflow.
This tool deliberately does not automate that step or store credentials.

GitHub receives the synthetic commits' dates/counts, your configured **personal**
author/committer identity, the generic message, and standard newly generated Git
objects. There are no original work hashes or code in the published history.
Both original author time and timezone are replaced by noon UTC.

To display a private destination's activity on your public graph, enable GitHub's
private-contribution visibility setting. Activity must satisfy GitHub's
[contribution criteria](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference).
Graph processing may take time. Publishing dates/counts reveals work cadence,
even when the destination repository is private.

## Local-only data and recovery

`<destination>/.git/activity-mirror-state.json` stores original commit hashes and
dates **locally**, solely for deduplication. No source names, paths, emails, PRs,
messages, or code are saved in that ledger. Its file permissions are restricted
to the current user.

The ledger is not tracked or pushed. Keep a private local backup, excluding it
from unapproved cloud sync, telemetry, and backup destinations. A fresh clone of
the published mirror does **not** restore the ledger. Restore the matching local
ledger before resuming; the tool refuses to guess if existing mirror history
has no ledger.

Original hashes are read locally, but never inserted into generated commits.
Source commands request only commit hash, author email, and author date.
Diagnostics suppress Git output that might expose source metadata. Run failures
are explicit and return a nonzero exit status.

## Development

```bash
python3 -m unittest -v
```

Tests create temporary synthetic repositories only. They exercise historical
imports, personal-only output, date filtering, read-only previews, duplicate
sources, future-company changes, interrupted imports, and explicit failures.
They do not access work repositories, GitHub, or real credentials.
