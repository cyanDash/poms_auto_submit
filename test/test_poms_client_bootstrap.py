import os
import sys

import pytest

from poms_client_bootstrap import setup_poms_client_path


def test_raises_when_unset(monkeypatch):
    monkeypatch.delenv("POMS_CLIENT_DIR", raising=False)

    with pytest.raises(RuntimeError, match="POMS_CLIENT_DIR is not set"):
        setup_poms_client_path()


def test_inserts_python_subdir_onto_sys_path(monkeypatch):
    monkeypatch.setenv("POMS_CLIENT_DIR", "/fake/poms_client")
    original_path = list(sys.path)

    try:
        setup_poms_client_path()
        assert sys.path[0] == os.path.join("/fake/poms_client", "python")
    finally:
        sys.path[:] = original_path
