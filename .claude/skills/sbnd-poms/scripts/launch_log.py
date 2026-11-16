#!/usr/bin/env python3
"""Print the launch stdout POMS saved for a Submission (the same text as the
POMS launch page). Read-only.

Must run in a shell that has already sourced poms_auto_submit's setup.sh; use
launch_log.sh instead of this directly unless you have.

Usage: launch_log.py <campaign_name> <campaign_stage_name> <submission_id> [--follow]
  --follow  keep polling and print new lines until POMS stops writing the file
"""
import argparse
import sys
import time
from pathlib import Path

# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
POMS_AUTO_SUBMIT_SCRIPTS = str(Path(__file__).resolve().parents[4] / "scripts")
sys.path.insert(0, POMS_AUTO_SUBMIT_SCRIPTS)

from poms_client_bootstrap import setup_poms_client_path  # noqa: E402

POLL_SECONDS = 5


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("campaign_name")
    parser.add_argument("campaign_stage_name")
    parser.add_argument("submission_id", type=int)
    parser.add_argument("--experiment", default="sbnd")
    parser.add_argument("--role", default="production")
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()

    try:
        setup_poms_client_path()
    except RuntimeError as e:
        sys.exit(str(e))

    import poms_client as pc
    from poms_session import PomsSession

    session = PomsSession(pc, {
        "experiment": args.experiment,
        "role": args.role,
        "campaign_name": args.campaign_name,
        "campaign_stage_name": args.campaign_stage_name,
    })

    printed = 0
    while True:
        lines, still_writing = session.launch_log(args.submission_id)
        sys.stdout.write("".join(lines[printed:]))
        sys.stdout.flush()
        printed = len(lines)
        if not (args.follow and still_writing):
            break
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
