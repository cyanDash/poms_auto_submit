import types

import pytest

from helpers import FakeResponse
from poms_raw_client import raw_poms_call


def make_pc(post, **overrides):
    """Defaults for the auth/base_path plumbing raw_poms_call() uses; only
    `post` needs overriding per test."""
    overrides.setdefault("getconfig", lambda kwargs: {})
    overrides.setdefault("auth_token", lambda: "test-token")
    overrides.setdefault(
        "base_path",
        lambda test_client, config, has_token: "https://pomsgpvm02.fnal.gov:9443/poms/sbnd/analysis",
    )
    overrides.setdefault("auth_cert", lambda: None)
    overrides.setdefault("rs", types.SimpleNamespace(post=post, headers={}))
    return types.SimpleNamespace(**overrides)


def test_returns_body_and_status_on_non_redirect():
    pc = make_pc(post=lambda url, **kw: FakeResponse("some error body", 400))

    assert raw_poms_call(pc, "launch_jobs", campaign_stage_id=1) == ("some error body", 400)


def test_returns_location_header_on_303():
    pc = make_pc(
        post=lambda url, **kw: FakeResponse("", 303, headers={"Location": "https://.../x?submission_id=5"}),
    )

    res, status = raw_poms_call(pc, "launch_jobs", campaign_stage_id=1)

    assert status == 303
    assert res == "https://.../x?submission_id=5"


def test_posts_to_base_plus_method():
    calls = []
    pc = make_pc(post=lambda url, **kw: calls.append(url) or FakeResponse("", 303, headers={"Location": "x"}))

    raw_poms_call(pc, "launch_jobs", campaign_stage_id=1)

    assert calls == ["https://pomsgpvm02.fnal.gov:9443/poms/sbnd/analysis/launch_jobs"]


def test_strips_none_valued_kwargs_before_posting():
    calls = []
    pc = make_pc(
        post=lambda url, **kw: calls.append(kw["data"]) or FakeResponse("", 303, headers={"Location": "x"}),
    )

    raw_poms_call(pc, "launch_jobs", campaign_stage_id=1, test_launch=None)

    assert calls == [{"campaign_stage_id": 1}]


def test_uses_bearer_token_when_available():
    pc = make_pc(
        post=lambda url, **kw: FakeResponse("", 303, headers={"Location": "x"}),
        auth_token=lambda: "abc123",
    )

    raw_poms_call(pc, "launch_jobs", campaign_stage_id=1)

    assert pc.rs.headers["Authorization"] == "Bearer abc123"


def test_falls_back_to_cert_auth_when_no_token():
    pc = make_pc(
        post=lambda url, **kw: FakeResponse("", 303, headers={"Location": "x"}),
        auth_token=lambda: None,
        auth_cert=lambda: "/tmp/cert",
    )

    raw_poms_call(pc, "launch_jobs", campaign_stage_id=1)

    assert pc.rs.cert == ("/tmp/cert", "/tmp/cert")
    assert pc.rs.verify is False


def test_returns_500_when_no_cert_and_https_base():
    pc = make_pc(
        post=lambda url, **kw: pytest.fail("should not POST without a client certificate"),
        auth_token=lambda: None,
        auth_cert=lambda: None,
    )

    assert raw_poms_call(pc, "launch_jobs", campaign_stage_id=1) == ("No client certificate", 500)
