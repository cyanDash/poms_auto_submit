"""The one seam every entry point uses to get the vendored poms_client
package importable. Owns detecting POMS_CLIENT_DIR and the setup-instructions
message; callers translate the RuntimeError into whatever failure mode fits
their context (raise, sys.exit, pytest.skip, ...).
"""

import os
import sys


def setup_poms_client_path():
    """Insert poms_client's python/ dir onto sys.path.

    Raises RuntimeError if POMS_CLIENT_DIR isn't set.
    """
    poms_client_dir = os.environ.get("POMS_CLIENT_DIR")
    if not poms_client_dir:
        raise RuntimeError(
            "POMS_CLIENT_DIR is not set. Set up UPS and poms_client first:\n"
            "  source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh\n"
            "  setup poms_client"
        )
    sys.path.insert(0, os.path.join(poms_client_dir, "python"))
