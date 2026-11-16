#!/usr/bin/env python3
"""Read a POMS campaign stage's configuration: dataset, split type,
param_overrides, test_param_overrides. Read-only, makes no changes.

Flags any override value with leading/trailing whitespace or a literal '${' --
both have broken POMS's launch-time argument parser before ("extra command
line arguments"), because override values get pasted into an unquoted shell
command server-side. See REFERENCE.md.

Must run in a shell that has already sourced poms_auto_submit's setup.sh
(POMS_CLIENT_DIR set, sbndpro production-role bearer token minted). Use
show_stage.sh instead of this directly unless you've already done that setup
yourself in the current shell.

Usage: show_stage.py <campaign_name> <campaign_stage_name> [--json]
"""
import argparse
import json
import sys
from pathlib import Path

# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
POMS_AUTO_SUBMIT_SCRIPTS = str(Path(__file__).resolve().parents[4] / "scripts")
sys.path.insert(0, POMS_AUTO_SUBMIT_SCRIPTS)

from poms_client_bootstrap import setup_poms_client_path  # noqa: E402


def flag_value(value):
    reasons = []
    if value != value.strip():
        reasons.append("leading/trailing whitespace")
    if "${" in value:
        reasons.append("contains '${' -- unescaped shell variable syntax")
    return reasons


def print_overrides(label, overrides):
    print(f"{label}:")
    if not overrides:
        print("  (none)")
        return
    for key, value in overrides:
        print(f"  {key!r:30} {value!r}")
        for reason in flag_value(value):
            print(f"    !! {reason} -- this will likely break launch. See REFERENCE.md.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("campaign_name")
    parser.add_argument("campaign_stage_name")
    parser.add_argument("--experiment", default="sbnd")
    parser.add_argument("--role", default="production")
    parser.add_argument("--json", action="store_true", help="dump the raw stage row as JSON instead of the formatted summary")
    args = parser.parse_args()

    try:
        setup_poms_client_path()
    except RuntimeError as e:
        sys.exit(str(e))

    import poms_client as pc
    pc.update_session_experiment(args.experiment)
    pc.update_session_role(args.role)

    ok, resp = pc.show_campaign_stages(campaign_name=args.campaign_name)
    if not ok:
        sys.exit(f"show_campaign_stages failed for campaign {args.campaign_name!r}")

    stage = next(
        (s for s in resp.get("campaign_stages", []) if s.get("name") == args.campaign_stage_name),
        None,
    )
    if stage is None:
        names = [s.get("name") for s in resp.get("campaign_stages", [])]
        sys.exit(
            f"stage {args.campaign_stage_name!r} not found in campaign {args.campaign_name!r}. "
            f"Stages in this campaign: {names}"
        )

    if args.json:
        print(json.dumps(stage, indent=2))
        return

    print(f"campaign_stage_id: {stage.get('campaign_stage_id')}")
    print(f"dataset:           {stage.get('dataset')}")
    print(f"cs_split_type:     {stage.get('cs_split_type')}")
    print(f"test_split_type:   {stage.get('test_split_type')}")
    print(f"completion_pct:    {stage.get('completion_pct')}")
    print()
    print_overrides("param_overrides", stage.get("param_overrides") or [])
    print()
    print_overrides("test_param_overrides", stage.get("test_param_overrides") or [])


if __name__ == "__main__":
    main()
