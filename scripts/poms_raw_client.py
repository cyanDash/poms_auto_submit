"""raw_poms_call(): the seam to poms_client's HTTP layer, bypassing
make_poms_call()'s response-mangling bug (see docs/poms_client_gotchas.md).
Two adapters justify pulling this out on its own: PomsSession.submit_next_slice()
(production) and test/debug_raw_call.py (manual debugging) both need the real,
unmangled response body/status instead of make_poms_call()'s raised, gutted
RuntimeError.
"""

import logging
import warnings


def raw_poms_call(pc, method, **kwargs):
    """POST to POMS's `method` endpoint directly, mirroring make_poms_call()'s
    auth+POST logic but without its Traceback-mangling bug. Returns
    (res, status_code) always -- res is the redirect Location on 303, the
    real unmangled body otherwise. Never raises; callers decide what a given
    body/status means.
    """
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    config = pc.getconfig({})
    token = pc.auth_token()
    base = pc.base_path(None, config, token is not None)
    if token:
        pc.rs.headers["Authorization"] = f"Bearer {token}"
    else:
        cert = pc.auth_cert()
        if cert is None and base[:6] == "https:":
            return "No client certificate", 500
        pc.rs.cert = (cert, cert)
        pc.rs.verify = False

    logging.debug("POST %s/%s data=%s", base, method, kwargs)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resp = pc.rs.post(f"{base}/{method}", data=kwargs, verify=False, allow_redirects=False)
    res, status_code = resp.text, resp.status_code
    resp.close()
    if status_code == 303:
        res = resp.headers["Location"]
    return res, status_code
