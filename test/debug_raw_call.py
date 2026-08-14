#!/usr/bin/env python
"""One-off debug helper: makes a raw POMS POST call the same way
poms_client.make_poms_call() does, but prints the response body verbatim
instead of running it through make_poms_call()'s buggy error-formatting
(`if res.find("Traceback"):` is always truthy since str.find() returns -1,
not found, when the string isn't in res).

Usage: ./debug_raw_call.py <method> key=value [key=value ...]
Example: ./debug_raw_call.py get_campaign_stage_name campaign_stage_id=26646
"""

import os
import sys

poms_client_dir = os.environ.get("POMS_CLIENT_DIR")
if not poms_client_dir:
    sys.exit(
        "POMS_CLIENT_DIR is not set. Set up UPS and poms_client first:\n"
        "  source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh\n"
        "  setup poms_client"
    )
sys.path.insert(0, os.path.join(poms_client_dir, "python"))

import poms_client as pc

EXPERIMENT = "sbnd"
ROLE = "production"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    method = sys.argv[1]
    kwargs = {"experiment": EXPERIMENT, "role": ROLE, "fmt": "json"}
    for arg in sys.argv[2:]:
        k, v = arg.split("=", 1)
        kwargs[k] = v

    pc.update_session_experiment(EXPERIMENT)
    pc.update_session_role(ROLE)

    config = pc.getconfig({})
    token = pc.auth_token()
    base = pc.base_path(None, config, token is not None)

    if token:
        pc.rs.headers["Authorization"] = f"Bearer {token}"
    else:
        cert = pc.auth_cert()
        pc.rs.cert = (cert, cert)
        pc.rs.verify = False

    url = f"{base}/{method}"
    print(f"POST {url}")
    print(f"data={kwargs}")
    resp = pc.rs.post(url, data=kwargs, verify=False, allow_redirects=False)
    print(f"\nstatus_code: {resp.status_code}")
    print(f"body:\n{resp.text}")


if __name__ == "__main__":
    main()
