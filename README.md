# GitHub Activity Mirror

Reflect approved daily development activity on your personal GitHub profile
without copying source code or changing the original repositories.

**No Python knowledge or JSON editing is needed.** The guided setup asks a few
questions, saves your settings, and shows a preview. It never imports or pushes
automatically. Daily automation is a separate, optional step below.

> Get permission to export activity dates and counts before using real sources.
> A private destination does not replace that permission.

## Quick start

### 1. Install the two requirements

- [Python 3.10+](https://www.python.org/downloads/)
- [Git 2.34+](https://git-scm.com/install/)

Already installed? Keep your existing installation. After installing, restart
your terminal/editor so it can find the commands.

### 2. Download and open this project

On this repository's GitHub page, choose **Code > Download ZIP**, then extract it
into a personal folder outside your source repositories. Already have a local
copy? Use that folder instead.

Open the folder containing `mirror.py` in VS Code, then choose
**Terminal > New Terminal**. You can also use your normal terminal in that folder.
Keep the whole project, including the `activity_mirror` folder.

### 3. Run guided setup

**Windows PowerShell**

```powershell
python mirror.py --setup
```

**macOS / Linux**

```bash
python3 mirror.py --setup
```

The wizard will:

1. Ask you to acknowledge export permission.
2. Ask for your **personal** name/email and a separate mirror folder.
3. Check each approved local source folder and suggest an available default ref.
4. Ask for your source author email(s).
5. Let you choose the first and last dates to import.
6. Ask before saving private settings, then run a **read-only preview**.

Have your personal GitHub noreply email ready from **Settings > Emails**, plus
your source folder and the author email used on your source commits.

Press Enter to accept a displayed default. The start date defaults to the current
year, not a hardcoded year; enter any earlier date or `all`. The end can be a
specific date or `ongoing`. Your choices are saved for later runs.

You can cancel before saving with Ctrl+C. Setup never overwrites an existing
configuration. If one already exists, it stays unchanged; use
`--setup --config config.new.json` for a separate profile. No sources are cloned,
fetched, or changed, and no account settings are changed. Settings must stay
outside every source and the personal mirror folder.

### 4. Review, then import locally

The preview shows daily totals and how many new empty commits would be created.
If these are correct and export is authorized, run:

**Windows PowerShell**

```powershell
python mirror.py --apply --allow-metadata-export
```

**macOS / Linux**

```bash
python3 mirror.py --apply --allow-metadata-export
```

These commands use the default `config.json` and the history limits saved by the
wizard. For a different profile, add `--config config.new.json`. Older manual
configurations retain their original date behavior; use explicit `--since` and
`--until` flags if they have no saved history limits.

**Expected:** `Created N local mirror commits. Nothing was pushed.`
Only the personal mirror changes. Its folder looks empty because the activity
is stored in Git history, not files. Do not add a README or other files there.

## Put it on your profile

Local imports do not update GitHub until you push the **separate private personal
data repository**. For the first publication, verify your account and destination:
follow [Publish your private mirror](docs/manual-setup.md#7-publish-to-your-private-personal-repository).
Never push a source repository or configuration as the data mirror.

Enable **Private contributions** in your personal profile's **Contribution
settings**. The data repository can remain private; visitors see daily aggregate
counts, not its repository details.

## Next time

Run `python3 mirror.py` to preview using your saved settings, then use the apply
command above only if you approve the result. On Windows, use `python` instead.
Push separately after verifying your personal account and destination again.

Nothing runs automatically unless you enable the optional schedule below.
Repeated or overlapping imports are deduplicated.
Changing the date window never removes already imported activity.

## Optional: update automatically every day

After your private mirror is connected to GitHub, install
[GitHub CLI](https://cli.github.com/) if needed, then run:

```bash
python3 mirror.py --schedule install
```

On Windows, use `python`. The wizard asks for your personal data repository,
daily time (**midnight** by default; enter `12:00` for noon), approved history
dates, and recurring export/push permission. It installs a job for your user,
not a cloud workflow.
Sources are never fetched; keep their local refs current through your normal
approved workflow.

Check or stop the job with:

```bash
python3 mirror.py --schedule status
python3 mirror.py --schedule remove
```

To change the time, remove the schedule and install it again.
Your computer must be running; on Linux/WSL, cron and the Linux environment must
also be running. This does not wake or turn on the computer.
Linux has been verified; the macOS and Windows adapters are experimental.
See [daily scheduling](docs/scheduling.md) for platform support, missed runs,
authentication, private state, and recovery.

## Safety and help

- Source code, messages, original hashes, and employer links are not put into
  generated commits. Only dates/counts, a generic message, and personal identity
  are represented.
- Keep your configuration and local ledger private. Do not commit or share them.
- Dates/counts can reveal activity patterns. This is data minimization, not
  anonymity or a transfer of genuine PR/review credit or language statistics.
- The visible hash `4b825dc642cb6eb9a060e54bf8d69288fbee4904` is Git's standard
  **empty-tree identifier**, not a private commit hash.

[Troubleshooting](docs/manual-setup.md#troubleshooting) |
[Manual setup and reference](docs/manual-setup.md) |
[Empty-tree hash explained](docs/manual-setup.md#why-the-empty-tree-hash-is-safe) |
[MIT license](LICENSE)
