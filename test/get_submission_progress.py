#!/usr/bin/env python
"""One-off manual verification: fetches a real submission's progress
(submission_details()) from production POMS and saves the response as
JSON. Read-only. Useful for capturing raw responses per
docs/poms_client_gotchas.md and feedback_save_full_api_responses.

Usage: source setup.sh && ./test/get_submission_progress.py <submission_id> [--outdir DIR]

Writes <outdir>/submission_details_<submission_id>.json (default outdir: cwd).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from poms_client_bootstrap import setup_poms_client_path

try:
    setup_poms_client_path()
except RuntimeError as e:
    sys.exit(str(e))

import poms_client as pc

EXPERIMENT = "sbnd"
ROLE = "production"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("submission_id")
    parser.add_argument("--outdir", default=os.getcwd(), help="directory to write the JSON file into (default: cwd)")
    args = parser.parse_args()

    pc.update_session_experiment(EXPERIMENT)
    pc.update_session_role(ROLE)

    ok, details = pc.submission_details(EXPERIMENT, ROLE, args.submission_id)
    if not ok:
        sys.exit(f"submission_details() failed: {details}")

    os.makedirs(args.outdir, exist_ok=True)
    outpath = os.path.join(args.outdir, f"submission_details_{args.submission_id}.json")
    with open(outpath, "w") as f:
        json.dump(details, f, indent=2)

    print(f"wrote {outpath}")


if __name__ == "__main__":
    main()
