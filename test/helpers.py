def make_cfg(**overrides):
    cfg = {
        "experiment": "sbnd",
        "role": "production",
        "campaign_name": "test_campaign",
        "campaign_stage_name": "test_stage",
        "pct_complete_threshold": 80,
        "submit_two_slices": False,
    }
    cfg.update(overrides)
    return cfg


def make_progress(*submissions):
    return {"campaign_stage_id": 42, "submissions": list(submissions)}


def sub(submission_id, pct_complete):
    return {"submission_id": submission_id, "status": "Running", "pct_complete": pct_complete}
