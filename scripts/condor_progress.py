"""CondorProgress: the seam to condor_q. Returns a computed pct_complete for
a submission's jobsub_job_id, or None on any failure. See
docs/adr/0007-condor-q-primary-progress-source.md.
"""

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

CONDOR_Q_TIMEOUT_SECONDS = 30

# jobsub_lite's condor_q wrapper adds the -G/--group flag get_progress()
# depends on; the plain HTCondor condor_q on $PATH doesn't understand it. Not
# resolved via $PATH because cron's minimal PATH doesn't include this
# directory even though an interactive login shell's does; see
# docs/adr/0007-condor-q-primary-progress-source.md.
CONDOR_Q_BIN = "/opt/jobsub_lite/bin/condor_q"

# Order matters: JobStatus's header token is never all-digits; see _parse_data_row().
ATTRS = ["JobStatus", "DAG_NodesDone", "DAG_NodesTotal"]


JOB_STATUS_COMPLETED = "4"


@dataclass(frozen=True)
class Progress:
    """outcome: "live", "finished", "no_data" or "error"; pct is set when the
    DAG's node counts are known."""
    outcome: str
    pct: Optional[float] = None


def get_progress(experiment, jobsub_job_id):
    """Query condor_q for the DAGMan controller job behind jobsub_job_id.
    Finished DAGs linger in the queue, so "finished" is only ever read off the
    row itself, never inferred from the job being absent."""
    if not jobsub_job_id:
        return Progress("error")
    cluster_id, _, schedd = jobsub_job_id.partition("@")
    if not cluster_id.isdigit():
        return Progress("error")

    cmd = [CONDOR_Q_BIN, "-G", experiment]
    if schedd:
        cmd += ["-name", schedd]
    cmd += [cluster_id, "-autoformat:h", *ATTRS]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CONDOR_Q_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        logging.exception("condor_q failed for jobsub_job_id=%s", jobsub_job_id)
        return Progress("error")

    if result.returncode != 0:
        logging.warning(
            "condor_q exited %d for jobsub_job_id=%s: %s",
            result.returncode, jobsub_job_id, result.stderr,
        )
        return Progress("error")

    row = _parse_data_row(result.stdout)
    if row is None:
        return Progress("no_data")

    pct = _pct(row["DAG_NodesDone"], row["DAG_NodesTotal"])
    if row["JobStatus"] == JOB_STATUS_COMPLETED:
        return Progress("finished", pct)
    if pct is None:
        return Progress("no_data")
    if pct >= 100:
        return Progress("finished", pct)
    return Progress("live", pct)


def _pct(done, total):
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
