#!/usr/bin/env python
"""One-off debug helper: makes a raw POMS POST call via raw_poms_call(),
bypassing make_poms_call()'s buggy error-formatting
(`if res.find("Traceback"):` is always truthy since str.find() returns -1,
not found, when the string isn't in res) so the real response prints
verbatim.

Usage: ./debug_raw_call.py <method> key=value [key=value ...]
Example: ./debug_raw_call.py get_campaign_stage_name campaign_stage_id=26646
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from poms_client_bootstrap import setup_poms_client_path

try:
    setup_poms_client_path()
except RuntimeError as e:
    sys.exit(str(e))

import poms_client as pc
from poms_raw_client import raw_poms_call

EXPERIMENT = "sbnd"
ROLE = "production"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    method = sys.argv[1]
    kwargs = {"experiment": EXPERIMENT, "role": ROLE, "fmt": "json"}
    for arg in sys.argv[2:]:
        k, v = arg.split("=", 1)
        kwargs[k] = v

    pc.update_session_experiment(EXPERIMENT)
    pc.update_session_role(ROLE)

    res, status_code = raw_poms_call(pc, method, **kwargs)
    print(f"\nstatus_code: {status_code}")
    print(f"body:\n{res}")


if __name__ == "__main__":
    main()
