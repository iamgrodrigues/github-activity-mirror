# GitHub Activity Mirror

Show approved development activity on your personal GitHub contribution graph.
The tool reads local Git history and creates empty, dated commits in a separate
private repository. It never copies source code or changes the originals.

> Get permission to export activity dates and counts before using real sources.
> A private destination does not replace that permission.

## Quick start

**No Python knowledge or JSON editing is needed.** On Windows, replace `python3`
with `python` in the commands below.

### 1. Install and download

Install [Python 3.10+](https://www.python.org/downloads/) and
[Git 2.34+](https://git-scm.com/install/) if you do not have them already.

Choose **Code > Download ZIP** on this project's GitHub page and extract the
whole project into a personal folder outside your source repositories.
Open it in VS Code, then choose **Terminal > New Terminal**.
Run all commands below from this tool folder, not a source or data repository.

### 2. Run guided setup

```bash
python3 mirror.py --setup
```

The wizard asks for your local repository folders, source author emails,
personal identity, and history dates. Use your personal GitHub noreply email
from **Settings > Emails** for the generated commits.
It saves private settings and shows a preview; it does not import or push.

### 3. Review and import

If the preview looks right and export is authorized, run:

```bash
python3 mirror.py --apply --allow-metadata-export
```

This creates the missing activity commits locally. **Nothing is pushed yet.**
The mirror folder looks empty because the activity is in Git history.
Do not add files to it.

### 4. Put it on your GitHub profile

Follow the [publishing guide](docs/manual-setup.md#7-publish-to-your-private-personal-repository)
to verify your personal account, create a **separate empty private repository**,
and connect/push only the generated mirror.

Then enable **Private contributions** in your GitHub profile's
**Contribution settings**. Never publish your source repositories or private settings.

### 5. Optional: update automatically every day

After publishing, make sure [GitHub CLI](https://cli.github.com/) is installed
and uses your personal account, then run:

```bash
python3 mirror.py --schedule install
```

Choose a daily time (`12:00` means noon), history dates, and approve recurring
imports/pushes. The code installs the local job for you: **no GitHub Action or
manual cron setup is needed**.

Your computer must be running; Linux/WSL also needs its environment and cron running.
Keep source repositories updated through your normal approved workflow.
Linux has been verified; macOS and Windows scheduling is experimental.
See [daily scheduling](docs/scheduling.md) to check, change, stop, or troubleshoot it.

## Next time

With scheduling enabled, the local job imports and pushes automatically.
Without it, follow [manual updates](docs/manual-setup.md#updating-next-time).
Unchanged sources skip history scans; changed branches scan only newly reachable
commits. The first import after upgrading builds this private checkpoint.
See [incremental scanning](docs/manual-setup.md#faster-repeat-imports) for details.

## Safety and help

- Generated commits contain dates/counts, a generic message, and your personal
  identity, not source code, original messages/hashes, or employer links.
- Keep settings and the local tracking ledger private. Dates/counts can still
  reveal activity patterns; this does not transfer genuine PR/review credit.

[Setup options and reference](docs/manual-setup.md) |
[Troubleshooting](docs/manual-setup.md#troubleshooting) |
[MIT license](LICENSE)
