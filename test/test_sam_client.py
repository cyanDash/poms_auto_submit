from sam_client import get_sam_client


def test_real_client_is_built_on_first_use_only(monkeypatch):
    import sys
    import types

    built = []

    class SAMWebClient:
        def __init__(self, experiment):
            built.append(experiment)

        def listFiles(self, defname=None):
            return [defname]

    monkeypatch.setitem(sys.modules, "samweb_client", types.SimpleNamespace(SAMWebClient=SAMWebClient))

    sam = get_sam_client("sbnd")
    assert built == []
    assert sam.listFiles(defname="ds") == ["ds"]
    assert sam.listFiles(defname="ds2") == ["ds2"]
    assert built == ["sbnd"]


def test_given_client_is_returned_as_it_is():
    given = object()

    assert get_sam_client("sbnd", given) is given
