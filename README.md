# GitHub Activity Mirror

A local, preview-first tool for reflecting **daily Git commit activity** in a
separate personal repository. It creates empty commits, not copies of work code.

The goal is to reflect permitted development activity on a personal profile
without changing the original account's identity or touching its repositories.
Everything runs locally; you decide what to preview, import, and publish.

**Establish permission to export activity dates and counts before using real
work sources.** Local access, a private destination, and writing the tool yourself
do not establish that permission.

## Two repositories, different purposes

- **The tool repository** contains reusable Python code, documentation, a generic
  configuration example, and tests using synthetic data. It does not need your
  actual configuration or activity history.
- **Your data repository** contains only generated empty commits. Their dates
  let GitHub display permitted activity on your personal contribution graph.
  Keep this repository private; it does not need to be public for private
  contributions to appear on your profile.

A public tool does not require public data. Keep configurations and the local
ledger out of both repositories' tracked files.

## Get started

1. Copy or clone the tool into a personal development folder.
2. Install Python and Git if needed; no Python dependencies are required.
3. Copy the example configuration and replace every placeholder with your own
   approved local sources and personal destination identity.
4. Preview a small date range first and review its dates and counts.
5. Apply locally only after permission and the preview are confirmed.
6. Publish only the dedicated data repository, separately and intentionally.

The sections below explain each step. No scheduler is installed: repeat the
preview/apply process when you want to update the mirror.

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
After acquiring the lock, apply rechecks both daily counts and the destination's
exact HEAD. If history changed during collection, even without changing its
counts, the run stops before saving the ledger or generating commits.
After a crash, inspect running processes before manually removing a stale
`.git/activity-mirror.lock`.

The destination must remain file-free, use only its `main` branch, and contain
only this tool's generic empty commits. Unexpected history, staged files, or
untracked files stop the run. There is no automatic deletion, force push, or
history rewriting.

## Publish separately, only when authorized

Create a **separate private personal GitHub repository**, empty with no initial
README, license, or gitignore. Do not fork or copy company repositories.
Do not use your personal profile README repository as the destination.

Do not add a README to the data repository later, either. The writer deliberately
accepts only file-free, recognizable synthetic history; a README would stop
future imports. Explain the approach in the tool's documentation or your profile
instead of weakening that safety boundary.

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
contribution criteria, including an associated author email and an eligible
default branch.
Graph processing may take time. Publishing dates/counts reveals work cadence,
even when the destination repository is private.

## What is and is not published

| Information | Where it belongs |
| --- | --- |
| Approved source paths, refs, and author email filters | Ignored local configuration only |
| Original commit hashes and calendar dates | Local ledger inside the destination's `.git` directory |
| Source code, filenames, commit messages, issue details, and repository URLs | Not copied into the generated history |
| Daily activity counts and calendar dates | Represented by synthetic commits in the data repository |
| Personal author/committer identity and a generic message | Present in every generated commit |

This is data minimization, not anonymity. Dates and counts can reveal work
patterns or support inferences when combined with other public information.
Making the data repository public would expose its detailed synthetic history
to everyone, without improving the tool's functionality.

Before sharing the tool or any derivative, inspect **all Git history and refs**,
not just the latest files. Keep employer-specific names, links, email addresses,
credentials, local paths, and real configurations out of published material.
An ignore rule prevents accidental staging; it does not remove a file that was
already committed.

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

The implementation uses a small package, with no framework or dependencies:

- [`mirror.py`](mirror.py): CLI validation and orchestration. Builds one immutable
  dates/counts plan used by both preview output and apply.
- [`activity_mirror/models.py`](activity_mirror/models.py): frozen dataclasses
  describing configuration, source activity, and the aggregate plan.
- [`activity_mirror/config.py`](activity_mirror/config.py): validates local JSON
  configuration and resolves paths; company labels stay at this boundary.
- [`activity_mirror/collector.py`](activity_mirror/collector.py): reads approved
  source refs and authors, returning deduplicated commit hashes and dates.
- [`activity_mirror/ledger.py`](activity_mirror/ledger.py): keeps source hashes
  local, merges activity, and handles atomic checkpoints and the destination lock.
- [`activity_mirror/destination.py`](activity_mirror/destination.py): inspects the
  destination and generates empty commits. Its writer receives only the personal
  destination identity/path and the dates/counts plan, never source metadata.
- [`activity_mirror/git.py`](activity_mirror/git.py): shared subprocess boundary,
  isolated Git environment, and sanitized Git errors.

Flow: load configuration, inspect the destination, collect and merge activity,
build and print the plan, then optionally lock, recheck, checkpoint, and write.
Checkpointing before the write loop preserves interrupted-run recovery.
CLI flags, configuration JSON, preview JSON, and the ledger format are unchanged.
Internal Python helpers now use typed dataclasses instead of nested dictionaries.

```bash
python3 -m unittest -v
```

Tests create temporary synthetic repositories only. They exercise historical
imports, personal-only output, date filtering, read-only previews, duplicate
sources, future-company changes, interrupted imports, concurrent history
rewrites, and explicit failures.
They do not access work repositories, GitHub, or real credentials.

## License

MIT. See [LICENSE](LICENSE). The license covers this tool, not any source data
you may have permission to process.
