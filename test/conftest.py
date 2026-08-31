import os
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo_root, "scripts"))

# poms_auto_submit tests build submissions via helpers.sub(), which never
# sets jobsub_job_id -- condor_progress.get_pct_complete() short-circuits to
# None on a falsy jobsub_job_id before ever touching subprocess, so the
# suite stays offline without needing a global patch here. Tests that want
# to exercise the condor_q-primary path inject get_condor_pct_complete
# directly instead; test_condor_progress.py tests the real function.
