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
    }
    cfg.update(overrides)
    return cfg


def make_submissions(*submissions):
    return list(submissions)


def sub(submission_id, pct_complete, subgroup=None, last_status_change=None, files_submitted=None, files_pending=None):
    return {
        "submission_id": submission_id,
        "status": "Running",
        "pct_complete": pct_complete,
        "subgroup": subgroup,
        "last_status_change": last_status_change,
        "files_submitted": files_submitted,
        "files_pending": files_pending,
    }
