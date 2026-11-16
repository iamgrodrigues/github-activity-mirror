#!/usr/bin/env python3
"""Preview and locally mirror daily activity without exporting source metadata."""

import argparse
import collections
from dataclasses import asdict
import datetime
import json
from pathlib import Path
import sys
from typing import Mapping

from activity_mirror.collector import collect
from activity_mirror.config import load_config
from activity_mirror.destination import inspect_destination, prepare_destination, write_commits
from activity_mirror.ledger import DestinationLock, STATE_NAME, load_state, merge_activity, save_state
from activity_mirror.models import Config, DailyCount, MirrorError, MirrorPlan


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--config", type=Path, default=Path("config.json"))
	parser.add_argument("--since", type=parse_date)
	parser.add_argument("--until", type=parse_date)
	parser.add_argument("--apply", action="store_true", help="Create local mirror commits.")
	parser.add_argument(
		"--allow-metadata-export", action="store_true",
		help="Confirm permission to copy work activity dates/counts into personal Git history."
	)
	args = parser.parse_args()
	try:
		if args.since and args.until and args.since > args.until:
			raise MirrorError("--since must not be after --until.")
		if args.apply and not args.allow_metadata_export:
			raise MirrorError("--apply requires --allow-metadata-export after permission is established.")
		config = load_config(args.config)
		run(config, args.since, args.until, args.apply)
	except (MirrorError, OSError) as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1
	return 0


def run(
	config: Config,
	since: datetime.date | None = None,
	until: datetime.date | None = None,
	apply: bool = False
) -> None:
	destination = config.destination
	snapshot = inspect_destination(destination)
	state_path = snapshot.git_dir / STATE_NAME
	seen = load_state(state_path) if snapshot.exists else {}
	if snapshot.daily_counts and not seen:
		raise MirrorError("Local ledger is missing. Restore its private backup before continuing.")
	seen = merge_activity(seen, collect(config.sources, since, until))
	plan = build_plan(collections.Counter(seen.values()), snapshot.daily_counts)
	print(json.dumps(asdict(plan), indent="\t"))
	if not apply:
		print("Preview only. No files or repositories changed.", file=sys.stderr)
		return
	prepare_destination(destination, snapshot)
	with DestinationLock(snapshot.git_dir):
		current = inspect_destination(destination)
		if current.daily_counts != snapshot.daily_counts or current.head != snapshot.head:
			raise MirrorError("Destination changed during collection. Run the preview again.")
		save_state(state_path, seen)
		write_commits(destination, plan)
	created = sum(daily.count for daily in plan.new_mirror_commits)
	print(f"Created {created} local mirror commits. Nothing was pushed.", file=sys.stderr)


def parse_date(value: str) -> datetime.date:
	try:
		date = datetime.date.fromisoformat(value)
		if date.isoformat() != value:
			raise ValueError
		return date
	except ValueError:
		raise argparse.ArgumentTypeError("Use a calendar date in YYYY-MM-DD format.") from None


def build_plan(desired: Mapping[str, int], existing: tuple[DailyCount, ...]) -> MirrorPlan:
	counts = collections.Counter(desired)
	published = collections.Counter({daily.date: daily.count for daily in existing})
	if any(count > counts[date] for date, count in published.items()):
		raise MirrorError("Mirror exceeds the local ledger. Restore a matching ledger; nothing will be deleted.")
	missing = counts - published
	return MirrorPlan(
		tuple(DailyCount(date, count) for date, count in sorted(counts.items())),
		tuple(DailyCount(date, count) for date, count in sorted(missing.items()))
	)


if __name__ == "__main__":
	sys.exit(main())
