#!/usr/bin/env python3
"""Patch specific param_overrides key/value pairs on a POMS campaign stage in
place -- a partial merge, every other existing key is left untouched. Follows
the same pattern as PomsSession.update_stage_params() in
poms_auto_submit/scripts/poms_session.py (used there for the subgroup
override).

Must run in a shell that has already sourced poms_auto_submit's setup.sh.
Use update_stage_params.sh instead of this directly unless you've already
done that setup yourself in the current shell.

Usage:
  update_stage_params.py <campaign_name> <campaign_stage_name> KEY=VALUE [KEY=VALUE ...]

KEY should NOT include the trailing '=' POMS's own convention adds -- this
script adds it for you, e.g.:

  update_stage_params.py MyCampaign my_stage \\
      -Oglobal.fclfile4=standard_reco1_sbnd_dnnroi_triton.fcl

Refuses to write any value with leading/trailing whitespace or a literal
'${' unless --force is passed -- both have broken POMS's launch-time
argument parser before ("extra command line arguments"), because override
values get pasted into an unquoted shell command server-side. This is a
real, repeatedly-hit bug class, not a hypothetical -- see REFERENCE.md.
"""
import argparse
import sys
from pathlib import Path

# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
POMS_AUTO_SUBMIT_SCRIPTS = str(Path(__file__).resolve().parents[4] / "scripts")
sys.path.insert(0, POMS_AUTO_SUBMIT_SCRIPTS)

from poms_client_bootstrap import setup_poms_client_path  # noqa: E402


def validate(value, force):
    problems = []
    if value != value.strip():
        problems.append("leading/trailing whitespace")
    if "${" in value:
        problems.append("contains '${' (unescaped shell variable syntax)")
    if problems and not force:
        sys.exit(
            f"Refusing to write value {value!r}: {', '.join(problems)}. "
            "This has broken POMS's launch-time argument parser before "
            "('extra command line arguments'). Pass --force to override."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("campaign_name")
    parser.add_argument("campaign_stage_name")
    parser.add_argument("overrides", nargs="+", help="KEY=VALUE pairs (KEY without the trailing '=')")
    parser.add_argument("--experiment", default="sbnd")
    parser.add_argument("--role", default="production")
    parser.add_argument("--force", action="store_true", help="write values even if they look dangerous")
    args = parser.parse_args()

    updates = {}
    for pair in args.overrides:
        if "=" not in pair:
            sys.exit(f"not a KEY=VALUE pair: {pair!r}")
        key, _, value = pair.partition("=")
        key = key + "="
        validate(value, args.force)
        updates[key] = value

    try:
        setup_poms_client_path()
    except RuntimeError as e:
        sys.exit(str(e))

    import poms_client as pc
    pc.update_session_experiment(args.experiment)
    pc.update_session_role(args.role)

    campaign_stage_id = pc.get_campaign_stage_id(args.experiment, args.campaign_name, args.campaign_stage_name)
    print(f"campaign_stage_id: {campaign_stage_id}")
    print("applying updates:")
    for k, v in updates.items():
        print(f"  {k!r} -> {v!r}")

    param_overrides = str(list(updates.items()))  # must pre-serialize; see poms_client_gotchas.md
    data = pc.update_stage_param_overrides(args.experiment, campaign_stage_id, param_overrides=param_overrides)
    if data is None:
        sys.exit("update_stage_param_overrides failed")
    print("OK")


if __name__ == "__main__":
    main()
