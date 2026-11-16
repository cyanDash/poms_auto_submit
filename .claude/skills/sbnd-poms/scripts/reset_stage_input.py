#!/usr/bin/env python3
"""Set a POMS campaign stage's Input Dataset and reset its split counter
(cs_last_split) to 0, so the next launch starts from the first slice of that
dataset. Same POMS call as PomsSession.set_recovery_input_dataset() in
poms_auto_submit/scripts/poms_session.py.

Use before relaunching a recovery slice by hand. Delete any stale
<dataset>_slice0_files<N> SAM definition first, or the launch can reuse its
old snapshot (see LOGS.md / REFERENCE.md).

Usage (via reset_stage_input.sh):
  reset_stage_input.py <campaign_name> <campaign_stage_name> <dataset>
"""
import argparse
import sys
from pathlib import Path

# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
POMS_AUTO_SUBMIT_SCRIPTS = str(Path(__file__).resolve().parents[4] / "scripts")
sys.path.insert(0, POMS_AUTO_SUBMIT_SCRIPTS)

from poms_client_bootstrap import setup_poms_client_path  # noqa: E402
from poms_raw_client import raw_poms_call  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("campaign_name")
    parser.add_argument("campaign_stage_name")
    parser.add_argument("dataset")
    parser.add_argument("--split-type", default=None,
                        help='also set cs_split_type, e.g. "nfiles(200)"')
    parser.add_argument("--experiment", default="sbnd")
    parser.add_argument("--role", default="production")
    args = parser.parse_args()

    try:
        setup_poms_client_path()
    except RuntimeError as e:
        sys.exit(str(e))
    import poms_client as pc
    pc.update_session_experiment(args.experiment)
    pc.update_session_role(args.role)

    stage_id = pc.get_campaign_stage_id(args.experiment, args.campaign_name, args.campaign_stage_name)
    print(f"campaign_stage_id: {stage_id}")
    extra = {"cs_split_type": args.split_type} if args.split_type else {}
    data, status = raw_poms_call(
        pc, "update_campaign_stage", pcl_call=1, campaign_stage=stage_id,
        experiment=args.experiment, role=args.role, dataset=args.dataset, cs_last_split=0,
        **extra,
    )
    if status not in (200, 202):
        sys.exit(f"update_campaign_stage failed: HTTP {status}\n{data}")
    print(f"OK: dataset={args.dataset} cs_last_split=0" + (f" cs_split_type={args.split_type}" if args.split_type else ""))


if __name__ == "__main__":
    main()
