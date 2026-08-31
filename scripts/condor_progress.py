"""CondorProgress: the seam to condor_q. Returns a computed pct_complete for
a submission's jobsub_job_id, or None on any failure. See
docs/adr/0007-condor-q-primary-progress-source.md.
"""

import logging
import subprocess

CONDOR_Q_TIMEOUT_SECONDS = 30

# Order matters: JobStatus's header token is never all-digits; see _parse_data_row().
ATTRS = ["JobStatus", "DAG_NodesDone", "DAG_NodesTotal"]


def get_pct_complete(experiment, jobsub_job_id):
    """DAG_NodesDone / DAG_NodesTotal * 100 for the DAGMan controller job
    behind jobsub_job_id, or None if it can't be determined."""
    if not jobsub_job_id:
        return None
    cluster_id = jobsub_job_id.split("@", 1)[0]
    if not cluster_id.isdigit():
        return None

    try:
        result = subprocess.run(
            ["condor_q", "-G", experiment, cluster_id, "-autoformat:h", *ATTRS],
            capture_output=True, text=True, timeout=CONDOR_Q_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        logging.exception("condor_q failed for jobsub_job_id=%s", jobsub_job_id)
        return None

    if result.returncode != 0:
        logging.warning(
            "condor_q exited %d for jobsub_job_id=%s: %s",
            result.returncode, jobsub_job_id, result.stderr,
        )
        return None

    row = _parse_data_row(result.stdout)
    if row is None:
        logging.warning(
            "condor_q output for jobsub_job_id=%s didn't match the expected shape: %r",
            jobsub_job_id, result.stdout,
        )
        return None

    done, total = row["DAG_NodesDone"], row["DAG_NodesTotal"]
    if done == "undefined" or total == "undefined":
        return None
    try:
        done, total = int(done), int(total)
    except ValueError:
        return None
    if total == 0:
        return None
    return done / total * 100


def _parse_data_row(stdout):
    """Find the one real data line among -autoformat:h's possibly-repeated
    header lines; see docs/adr/0007-condor-q-primary-progress-source.md."""
    for line in stdout.splitlines():
        tokens = line.split()
        if len(tokens) == len(ATTRS) and tokens[0].isdigit():
            return dict(zip(ATTRS, tokens))
    return None
