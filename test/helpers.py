class FakeResponse:
    """Stands in for a requests.Response in tests of raw_poms_call() and its
    callers -- text/status_code/headers plus a no-op close()."""

    def __init__(self, text, status_code, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def close(self):
        pass


def make_cfg(**overrides):
    cfg = {
        "experiment": "sbnd",
        "role": "production",
        "campaign_name": "test_campaign",
        "campaign_stage_name": "test_stage",
        "pct_complete_threshold": 80,
        "submit_two_slices": False,
        "max_splits": 5,
        "last_split": 0,
        "do_cleanup": False,
        "recovery_handled": False,
    }
    cfg.update(overrides)
    return cfg


def make_submissions(*submissions):
    return list(submissions)


def sub(submission_id, subgroup=None):
    return {
        "submission_id": submission_id,
        "status": "Running",
        "subgroup": subgroup,
    }
