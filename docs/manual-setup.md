# Manual Setup and Reference

This is the optional manual path and detailed reference. For the shorter guided
setup, start with the [quick start](../README.md). You do not need to edit JSON
or follow the manual installation below when using the wizard.

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

## Start here

**You do not need to know Python or edit the program.** You will install two
tools, edit a settings file, and copy commands into a terminal.

For your first installation, follow these steps in order:

1. [Install and check the tools](#1-install-and-check-the-tools).
2. [Download and open the project](#2-download-and-open-the-project).
3. [Find your source and identity details](#3-find-your-source-and-identity-details).
4. [Create your local settings file](#4-create-your-local-settings-file).
5. [Preview a small date range](#5-preview-a-small-date-range).
6. [Create the local mirror commits](#6-create-the-local-mirror-commits).
7. [Publish to your private personal repository](#7-publish-to-your-private-personal-repository).
8. [Show private contributions on your profile](#8-show-private-contributions-on-your-profile).

Already set up? Use [Updating next time](#updating-next-time). If a step fails,
stop there and use [Troubleshooting](#troubleshooting); do not continue to push.

All names, emails, source paths, and activity totals used as examples below are
fictional. Replace uppercase placeholders with your own values before running
a command. Keep quotation marks around paths, including paths without spaces.

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

## 1. Install and check the tools

| Tool | Required? | Installation |
| --- | --- | --- |
| Python 3.10 or newer | Yes; runs the program | Use the installer for your operating system from [python.org](https://www.python.org/downloads/). On Linux, follow your distribution's Python 3 installation instructions. |
| Git 2.34 or newer | Yes; reads and creates local history | Follow the instructions for your operating system on [Install Git](https://git-scm.com/install/). |
| Visual Studio Code | Optional; makes editing the settings file easier | Download from [Visual Studio Code](https://code.visualstudio.com/). No Python extension is required. |
| GitHub CLI | Only for the publishing checks in step 7 | Install from [GitHub CLI](https://cli.github.com/) when you reach that step. |

If a suitable version is already installed, do not install it a second time.
After installing, close and reopen your terminal and VS Code.

Open **PowerShell** from the Windows Start menu, or **Terminal** on macOS/Linux.
In VS Code, **Terminal > New Terminal** opens an equivalent command window.
On Windows, choose the PowerShell terminal profile for the Windows examples.

**Windows PowerShell**

```powershell
python --version
git --version
```

**macOS / Linux**

```bash
python3 --version
git --version
```

**Check:** both commands must print version numbers meeting the minimums above.
If a command is not recognized or opens the Microsoft Store instead of printing
a Python version, finish installing/configuring that tool before continuing.

There are no Python packages to install: no `pip install`, virtual environment,
or Python coding is needed. The mirror runs locally without a token or network
access. Downloading this project and publishing results are separate online steps.

## 2. Download and open the project

Choose a personal folder outside your source repositories. These commands create
one under your home folder.

**Windows PowerShell**

```powershell
New-Item -ItemType Directory -Force "$HOME/Projects/Personal" | Out-Null
Set-Location "$HOME/Projects/Personal"
```

**macOS / Linux**

```bash
mkdir -p "$HOME/Projects/Personal"
cd "$HOME/Projects/Personal"
```

**Then, on either operating system**

```text
git clone https://github.com/iamgrodrigues/github-activity-mirror.git
cd github-activity-mirror
```

Already downloaded it? Open your existing copy instead of cloning it again.
In VS Code, choose **File > Open Folder**, select `github-activity-mirror`, then
choose **Terminal > New Terminal**.

**Check:** the folder contains `mirror.py`, `config.example.json`, and the
`activity_mirror` folder. Keep the whole project; downloading only `mirror.py`
is not enough.

Unless stated otherwise, run the remaining commands from this project folder.
Paths starting with `./local/mirror` below assume the unchanged destination path
in the example configuration.

## 3. Find your source and identity details

A **source** is an existing local Git repository whose activity you are permitted
to export. A **destination** is the separate personal mirror created by this tool.
Never use the same folder for both.

Collect the following values before editing the configuration:

| Value | How to find it |
| --- | --- |
| Source folder | The full path to an approved repository already on your computer, not its GitHub URL. |
| Source branch/ref | The locally available upstream default branch, usually `origin/main` or `origin/master`. |
| Source author email | The author email on commits you actually wrote; historical commits may use more than one email. |
| Personal name | The public personal name you want on the generated commits. |
| Personal author email | In your **personal** GitHub account, open **Settings > Emails** and copy your GitHub-provided noreply address. |

You can check the source path and available branches with these read-only commands.
Replace `FULL_SOURCE_PATH` first:

```text
git -C "FULL_SOURCE_PATH" rev-parse --show-toplevel
git -C "FULL_SOURCE_PATH" branch --all
```

**Check:** the first command prints the repository root you intended. If the branch
list shows `remotes/origin/main`, put `origin/main` in the configuration, without
the `remotes/` prefix. Prefer the upstream default branch, not a feature branch.

To confirm an author email, select a commit you wrote in your usual Git history
viewer and inspect its **Author** details. If you have its commit ID, this command
also shows the author's email:

```text
git -C "FULL_SOURCE_PATH" show -s --format="%ae" YOUR_COMMIT_ID
```

Use your source author email, not someone else's. `git config user.email` alone
is not proof: it describes a setting, not necessarily the author of older commits.
Keep source details and command output local; do not paste them into public issues.

The tool does not clone, fetch, or update sources. If a local upstream ref is old,
update it only through your normal approved source-repository workflow.

## 4. Create your local settings file

For a **new installation**, copy the example using the command for your system.
If `config.json` already exists, skip copying and edit that file instead.

**Windows PowerShell**

```powershell
Copy-Item config.example.json config.json
```

**macOS / Linux**

```bash
cp config.example.json config.json
chmod 600 config.json
```

Open `config.json` in VS Code or another text editor. If using a basic Windows
editor, check that the filename is not accidentally `config.json.txt`.

Here is a complete example. Replace the personal name, personal noreply address,
source path, source email, and branch with the values from step 3:

```json
{
	"destination": {
		"path": "./local/mirror",
		"author_name": "Your Personal Name",
		"author_email": "123456+YOUR_USERNAME@users.noreply.github.com"
	},
	"companies": [
		{
			"name": "example-company",
			"repositories": [
				{
					"path": "/ABSOLUTE/PATH/TO/APPROVED/SOURCE",
					"ref": "origin/main",
					"author_emails": ["your-approved-work-email@example.com"]
				}
			]
		}
	],
	"history": {
		"since": "2024-01-01",
		"until": null
	}
}
```

What each setting means:

- **`destination.path`**: leave `./local/mirror` for the first setup. The tool
  creates this directory on apply; you do not need to create or initialize it.
  Never point it at a source, the tool's repository, or your profile repository.
- **`author_name` and `author_email`**: your personal identity for the generated
  commits. Copy your real personal noreply address from GitHub; do not invent
  the numeric ID or copy the example address.
- **Company `name`**: any unique local label, such as `example-company`. It is
  not published and does not have to identify an actual company.
- **Source `path`**: replace the uppercase example with the source's full local
  root path. On Windows use forward slashes, for example
  `C:/Users/YOUR_USER/Projects/approved-source`, not single backslashes in JSON.
- **`ref`**: the exact branch/ref found in step 3.
- **`author_emails`**: a list of your approved source author emails. To match
  two historical identities, use `["first@example.com", "second@example.com"]`.
- **`history`**: optional saved defaults for `since` and `until`. Use
  `YYYY-MM-DD` strings or `null` for an unbounded endpoint. The wizard saves
  these choices automatically; older configurations without `history` still work.

Keep the double quotes and commas shown above. JSON does not accept comments or
a trailing comma after the last item. Save the file before continuing.

Relative paths are resolved from the configuration file's folder, not the shell.
Keep the settings file private. On Windows, use a private user folder with
appropriate Windows access permissions; Unix `chmod` permissions do not provide
the same protection there.

`config.json`, other `config.*.json` files except the example, and `local/` are
ignored by this project. Never force-add them or put them in unapproved cloud sync.

## 5. Preview a small date range

Start with a short **approved** historical range that contains some of your
activity. The dates below are examples; replace them with your chosen range.
Dates use `YYYY-MM-DD`, and both endpoints are included.

### Choose how far back to import

There is **no hardcoded year cutoff**. The wizard saves your chosen limits in
`history`; command-line dates override the corresponding saved endpoint:

| Option | History limit |
| --- | --- |
| `--since YYYY-MM-DD` | Earliest author date to collect, including that date |
| `--until YYYY-MM-DD` | Latest author date to collect, including that date |
| Both together | Only newly collected activity inside that date range |

For example, `--since 2020-01-01 --until 2024-12-31` selects five calendar years,
while `--since 2024-01-01 --until 2024-01-07` selects one week. Use the same
chosen values for preview and apply. These are date limits, not a maximum number
of commits.

If you omit a date flag, its saved `history` value applies. A missing or `null`
saved endpoint is unbounded, but future-dated commits are still rejected.
For older configurations without `history`, omitting both flags scans all
matching history reachable from your configured refs. Explicit bounds are
recommended for a first manual import.

Changing the window does **not** remove activity already imported into the
ledger or mirror. To backfill an earlier approved period, preview that period
and apply it; the ledger prevents duplicate hashes.

### Run the preview

**Windows PowerShell**

```powershell
python mirror.py --config config.json --since 2024-01-01 --until 2024-01-07
```

**macOS / Linux**

```bash
python3 mirror.py --config config.json --since 2024-01-01 --until 2024-01-07
```

**Expected result:** JSON containing only dates and counts, followed by
`Preview only. No files or repositories changed.`

For example, two matching commits on one date would produce:

```json
{
	"daily_totals": [{"date": "2024-01-02", "count": 2}],
	"new_mirror_commits": [{"date": "2024-01-02", "count": 2}]
}
```

- **`daily_totals`**: all activity represented by the combined local ledger and
  this collection, including previously imported activity.
- **`new_mirror_commits`**: only the missing synthetic commits that apply would
  create.
- **Empty lists**: no activity matched, or no additional commits are needed.
  Check the selected range, author email, and source ref if this is unexpected.

**Check:** the dates and counts make sense. A preview creates no destination,
ledger, or commits and changes no source. If anything looks wrong, edit the
configuration and preview again; do not apply yet.

Date filters limit newly collected activity, not activity already in the ledger.
To import a larger approved period later, preview that period first. Future-dated
commits cause an error unless excluded by your selected range.

## 6. Create the local mirror commits

Only continue when you have export permission and are happy with the preview.
Use **the same configuration and date range** as the preview, adding both flags
shown below.

**Windows PowerShell**

```powershell
python mirror.py --config config.json --since 2024-01-01 --until 2024-01-07 --apply --allow-metadata-export
```

**macOS / Linux**

```bash
python3 mirror.py --config config.json --since 2024-01-01 --until 2024-01-07 --apply --allow-metadata-export
```

`--apply` allows local destination writes. `--allow-metadata-export` acknowledges
permission to export dates/counts; it is not a GitHub login or permission grant.

**Expected result:** the same kind of dates/counts plan and a message such as:

```text
Created 2 local mirror commits. Nothing was pushed.
```

The number depends on your plan. Only `local/mirror` changes; no source is changed
and nothing is uploaded. An empty-looking destination folder is normal: the
activity is in Git history, not ordinary files.

**Check:** inspect the generated history from the tool folder:

If you imported no activity and there are no commits yet, skip these history
checks and publishing until activity exists.

```text
git -C "./local/mirror" log -3 --format="%ad %ae %s" --date=short
git -C "./local/mirror" ls-tree -r HEAD
```

The log must show your personal author email and the generic message
`Record development activity`. The second command must print nothing, confirming
that the generated commit has no files.

Do not add a README or any other file to the destination, now or later.
The writer requires file-free history on `main`; adding files stops future
imports. Keep the explanation here or on your profile instead.

Rerunning creates only missing commits. The local ledger supports interrupted-run
recovery. A lock blocks overlapping writes, and apply rejects history that changed
during collection, even if its daily counts stayed the same.

## 7. Publish to your private personal repository

**Optional, separate online step.** You can stop after step 6 for a local-only
mirror. Publishing sends synthetic activity dates/counts and your personal commit
identity to GitHub. It does not transfer genuine review/PR attribution or source
code. Publish only when this export is authorized.

### Check your personal account

Install [GitHub CLI](https://cli.github.com/) if needed, reopen the terminal, then:

```text
gh --version
gh api user --jq ".login"
```

**Check:** the second command must print your **personal** GitHub username.
If it prints a different account, stop. Keep existing account-routing setups;
do not change global Git identity or organization credentials to force this step.
If it says you are not logged in, use the appropriate sign-in setup below before
repeating the check.

On a fresh **personal-only** setup with no existing authentication, you can start
the browser sign-in flow with:

```text
gh auth login --hostname github.com --web
```

Use your personal browser account. For this HTTPS walkthrough, choose HTTPS
and allow Git authentication when prompted. This setup can change Git credential
configuration, so do not run it over an existing managed/multi-account setup.
Re-run the username check afterward. Never put tokens in configuration files,
commands, or remote URLs.

### Create an empty private repository

In the GitHub website, signed in to your personal account:

1. Choose **New repository**.
2. Set **Owner** to your personal username.
3. Name it `github-activity-mirror-data`.
4. Select **Private**.
5. Leave README, `.gitignore`, and license initialization **off**.
6. Create the repository. Do not fork a source or use your profile repository.

If your mirror already has its own personal private repository, reuse it instead
of creating another.

Replace `YOUR_USERNAME` below and verify the target:

```text
gh repo view YOUR_USERNAME/github-activity-mirror-data --json nameWithOwner,visibility
```

**Check:** the owner/name must be your intended personal data repository, and
visibility must be `PRIVATE`. Stop on any mismatch.

### Connect and push only the destination

Run these commands from the **tool folder**. If you changed `destination.path`,
replace `./local/mirror` everywhere with your configured destination path.
First inspect any existing remote:

```text
git -C "./local/mirror" remote -v
```

For a new mirror, this prints nothing. Add the personal HTTPS URL, replacing
`YOUR_USERNAME` first:

```text
git -C "./local/mirror" remote add origin "https://github.com/YOUR_USERNAME/github-activity-mirror-data.git"
git -C "./local/mirror" remote -v
```

If `origin` already exists, **skip adding it** and verify its existing URL matches
your personal private data repository. Do not overwrite an unexpected remote.
If the remote is correct and publishing is authorized:

```text
git -C "./local/mirror" push --set-upstream origin main
```

Git must also use your approved personal credentials. The earlier `gh` username
check does not guarantee a separately configured Git credential helper uses the
same account. If authentication fails or selects the wrong account, stop and fix
your personal authentication setup; do not fall back to an organization account.

**Check:** refresh the private repository on GitHub. Its history should show the
generic activity commits, and it should have no tracked files. Confirm `main` is
the repository's default branch in its settings. Keep the repository private.

## 8. Show private contributions on your profile

On your personal GitHub profile, open **Contribution settings** above the graph
and enable **Private contributions**.

Contributions must meet GitHub's criteria: use your associated personal email,
a standalone repository rather than a fork, and the repository's default branch.
Graph processing may be delayed; a successful push need not appear immediately.
Check the year containing your imported dates, not just the current year.

**Done:** you have a local copy of this public tool, a private personal data
repository, and a local settings file/ledger that are not published. Showing
private contributions reveals daily aggregate counts to profile visitors, even
though repository details stay private.

## Updating next time

1. Open the **same tool folder** and keep the same configuration, personal
   destination identity, and local ledger. Do not repeat setup or recreate them.
2. Refresh source refs only through your normal approved source workflow.
3. Run the step 5 preview for your next approved date range.
4. If the plan is correct, run step 6 with **that same range**.
5. Before publishing, repeat the personal username, private target, and destination
   remote checks in step 7, then push the destination again.

Zero new commits is normal when nothing new matches. Date windows can overlap:
the ledger deduplicates already-seen hashes. Nothing runs automatically; there is
no scheduler, automatic fetch, or automatic push.

For an additional source, copy a repository entry inside `repositories`, separated
by a comma, and fill in its approved details. For a future company, add a company
entry with a unique local label. After importing old activity, its source/company
entry can be removed; the ledger retains it without ongoing source access.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `python`, `python3`, or `git` is not recognized | Complete step 1 and reopen your terminal/editor. Windows examples use `python`; macOS/Linux examples use `python3`. |
| Python cannot open `mirror.py`, or the package cannot be imported | Open the complete tool folder from step 2, not a source or destination folder. Do not run a separately downloaded copy of the script. |
| Configuration is not valid JSON | Save `config.json`; preserve double quotes and commas; remove comments/trailing commas. Use forward slashes in Windows paths. |
| Git fails while reading a source | Check its full root path, local branch/ref, and author email in step 3. Git errors are deliberately sanitized; inspect source details locally, not in public logs. |
| Preview returns no activity | Check that your selected date range contains your commits, their actual author emails match, and the configured ref includes them. No network refresh occurs automatically. |
| Apply requires `--allow-metadata-export` | Use both flags from step 6 only after permission is established; do not bypass the acknowledgment. |
| Destination has files, unexpected history, or exceeds the ledger | Stop. Do not delete files/history to force acceptance. Check that you chose the dedicated mirror and restore a matching private ledger if needed. |
| Local ledger is missing | Restore the matching private backup. A GitHub clone does not contain this local-only file; deleting or recreating it can lose deduplication history. |
| Destination is locked or changed during collection | Stop other runs, then preview again. Remove a stale lock only after verifying no process is active; a lock is not permission to delete the ledger. |
| `origin` already exists | Inspect `git -C "./local/mirror" remote -v`. Reuse it only if it is the exact intended personal private target; do not overwrite an unexpected URL. |
| Push authentication fails | Check personal account selection and Git credentials. Do not paste tokens into commands or use another account as a fallback. |
| Graph has not changed | Confirm the push succeeded, the commit email is associated with your personal account, `main` is the default branch, private contributions are enabled, and you are viewing the imported year. Allow processing time. |

When requesting help, share only sanitized errors and fictional configuration.
Never post real source paths, author emails, repository links, tokens, or the ledger.

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
messages, or code are saved in that ledger. On Unix, its mode is `0600`; on Windows,
protect the containing folder with your user account's access permissions.

The ledger is not tracked or pushed. Keep a private local backup, excluding it
from unapproved cloud sync, telemetry, and backup destinations. A fresh clone of
the published mirror does **not** restore the ledger. Restore the matching local
ledger before resuming; the tool refuses to guess if existing mirror history
has no ledger.

Original hashes are read locally, but never inserted into generated commits.
Source commands request only commit hash, author email, and author date.
Diagnostics suppress Git output that might expose source metadata. Run failures
are explicit and return a nonzero exit status.

### Why the empty-tree hash is safe

The `EMPTY_TREE` constant in [destination.py](../activity_mirror/destination.py)
is `4b825dc642cb6eb9a060e54bf8d69288fbee4904`: Git's standard SHA-1 identifier
for an **empty tree**, meaning a file listing with no entries.

Every SHA-1 Git repository uses that same identifier for an empty tree. It is
not an original source commit hash, a credential, or a reference to any employer.
The tool uses it to reject destination commits containing files.

You can reproduce it without reading any repository or private data. Run the
command for your operating system; Python's standard library hashes Git's
empty-tree header, `tree 0` followed by a zero byte.

**Windows PowerShell**

```powershell
python -c "import hashlib; print(hashlib.sha1(b'tree 0\x00').hexdigest())"
```

**macOS / Linux**

```bash
python3 -c "import hashlib; print(hashlib.sha1(b'tree 0\x00').hexdigest())"
```

Both print:

```text
4b825dc642cb6eb9a060e54bf8d69288fbee4904
```

Original source commit hashes are different: they are kept only in the private
local ledger and are not inserted into the generated history.

## Development

The implementation uses a small package, with no framework or dependencies:

- [`mirror.py`](../mirror.py): CLI validation and orchestration. Builds one immutable
  dates/counts plan used by both preview output and apply.
- [`activity_mirror/models.py`](../activity_mirror/models.py): frozen dataclasses
  describing configuration, source activity, and the aggregate plan.
- [`activity_mirror/config.py`](../activity_mirror/config.py): validates local JSON
  configuration and resolves paths; company labels stay at this boundary.
- [`activity_mirror/setup.py`](../activity_mirror/setup.py): local interactive
  configuration, saved history limits, and exclusive creation of private settings.
- [`activity_mirror/collector.py`](../activity_mirror/collector.py): reads approved
  source refs and authors, returning deduplicated commit hashes and dates.
- [`activity_mirror/ledger.py`](../activity_mirror/ledger.py): keeps source hashes
  local, merges activity, and handles atomic checkpoints and the destination lock.
- [`activity_mirror/destination.py`](../activity_mirror/destination.py): inspects the
  destination and generates empty commits. Its writer receives only the personal
  destination identity/path and the dates/counts plan, never source metadata.
- [`activity_mirror/git.py`](../activity_mirror/git.py): shared subprocess boundary,
  isolated Git environment, and sanitized Git errors.

Flow: load configuration, inspect the destination, collect and merge activity,
build and print the plan, then optionally lock, recheck, checkpoint, and write.
Checkpointing before the write loop preserves interrupted-run recovery.
Existing non-setup CLI flags, preview JSON, and the ledger format are unchanged.
Configuration accepts an optional `history` object; legacy configurations without
it retain their original unbounded defaults. Python helpers use typed dataclasses.

```bash
python3 -m unittest -v
```

Tests create temporary synthetic repositories only. They exercise historical
imports, personal-only output, date filtering, read-only previews, duplicate
sources, future-company changes, interrupted imports, concurrent history
rewrites, and explicit failures.
They do not access work repositories, GitHub, or real credentials.

## License

MIT. See [LICENSE](../LICENSE). The license covers this tool, not any source data
you may have permission to process.
