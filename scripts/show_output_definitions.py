#!/usr/bin/env python3
"""show_output_definitions: prints a Campaign Stage's Output Definitions with
their depth, tier and file count, as output_definitions.py infers them. It
first prints the steps of the inference, each with the tags it keeps
and drops and why. With --all-tags it also prints every lineage tag found
under the sampled inputs, the newest create_date seen for it and whether it
was accepted, rejected (another campaign's) or ignored (a Test Launch tag).
Read-only: no ini, no State File, no SAM or POMS write. See docs/adr/0023.

By hand (after `source setup.sh`):
    python scripts/show_output_definitions.py --campaign <campaign_name> --stage <campaign_stage_name> [--all-tags]
    python scripts/show_output_definitions.py --submission <submission_id> [--all-tags]

--submission reads the launch command and Input Dataset of that Submission,
for a campaign POMS no longer lists its stages for.
"""

import argparse
import sys

import output_definitions
import sam_client
from duplicate_cleanup import TIER_ORDER


def build_cfg(campaign_name, campaign_stage_name, experiment):
    """The minimal cfg the lookup needs: no ini, no state_file."""
    return {
        "experiment": experiment, "role": "production",
        "campaign_name": campaign_name, "campaign_stage_name": campaign_stage_name,
    }


class _SubmissionView:
    """The one Submission's launch command and Input Dataset, in the shape
    the inference reads from a PomsSession."""

    def __init__(self, source):
        self._source = source

    def get_launch_source(self):
        return self._source

    def get_stage_params(self):
        return {"dataset": self._source["dataset"]}


def _tier(name):
    return next((tier for tier in TIER_ORDER if name.endswith(f"_{tier}_sbnd")), "?")


def main(argv=None, session=None, sam=None):
    """Prints the table; returns the exit code. `session` and `sam` are the
    POMS session and SAM client, the real ones when None."""
    parser = argparse.ArgumentParser(description="Show a Campaign Stage's Output Definitions. Read-only.")
    parser.add_argument("--campaign", help="campaign_name")
    parser.add_argument("--stage", help="campaign_stage_name")
    parser.add_argument("--submission", type=int,
                        help="look up by Submission id instead of --campaign and --stage")
    parser.add_argument("--experiment", default="sbnd")
    parser.add_argument("--all-tags", action="store_true",
                        help="also list every lineage tag with its newest create_date and verdict")
    args = parser.parse_args(argv)
    if args.submission is None and not (args.campaign and args.stage):
        parser.error("--campaign and --stage are required without --submission")

    cfg = build_cfg(args.campaign, args.stage, args.experiment)
    if session is None:
        from poms_client_bootstrap import setup_poms_client_path
        setup_poms_client_path()
        import poms_client as pc
        from poms_session import PomsSession
        session = PomsSession(pc, cfg)
    sam = sam_client.get_sam_client(args.experiment, sam)

    if args.submission is not None:
        source = session.get_submission_source(args.submission)
        if source and not source.get("dataset"):
            print(f"Submission {args.submission} has no Input Dataset in its stage snapshot", file=sys.stderr)
            return 1
        session = _SubmissionView(source)

    trace = output_definitions.trace_output_definitions(cfg, session, sam)
    for step in trace["steps"]:
        print(output_definitions.render_step(step, multiline=True))
    print()
    if trace["error"]:
        print(f"cannot infer the output definitions: {trace['error']}", file=sys.stderr)
        return 1

    print("definition depth tier files")
    for d in trace["definitions"]:
        print(f"{d['name']} {d['depth']} {_tier(d['name'])} {sam.countFiles(defname=d['name'])}")
    if args.all_tags:
        print()
        print("tag depth newest_create_date status")
        for row in trace["rows"]:
            print(f"{row['tag']} {row['depth']} {row['newest_create_date'] or '-'} {row['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
