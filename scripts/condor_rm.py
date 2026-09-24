"""CondorRm: the seam to condor_rm, removing a Submission's DAGMan controller
job. See docs/adr/0018-remove-stalled-stragglers-before-recovery.md.
"""

import logging
import subprocess

CONDOR_RM_TIMEOUT_SECONDS = 30

# Same wrapper directory as condor_progress.CONDOR_Q_BIN, not resolved via $PATH.
CONDOR_RM_BIN = "/opt/jobsub_lite/bin/condor_rm"


def remove(experiment, jobsub_job_id):
    """condor_rm the DAG behind jobsub_job_id on its owning schedd. Returns
    whether HTCondor accepted the removal."""
    cluster_id, _, schedd = (jobsub_job_id or "").partition("@")
    if not cluster_id.isdigit():
        return False

    cmd = [CONDOR_RM_BIN, "-G", experiment]
    if schedd:
        cmd += ["-name", schedd]
    cmd.append(cluster_id)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=CONDOR_RM_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError):
        logging.exception("condor_rm failed for jobsub_job_id=%s", jobsub_job_id)
        return False
    if result.returncode != 0:
        logging.warning("condor_rm exited %d for jobsub_job_id=%s: %s", result.returncode, jobsub_job_id, result.stderr)
        return False
    return True
