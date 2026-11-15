from sam_client import get_sam_client, is_file_not_found


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


def test_file_not_found_is_recognised_by_class_name_only():
    from helpers import FileNotFound

    assert is_file_not_found(FileNotFound("File 'f' not found")) is True
    assert is_file_not_found(RuntimeError("File 'f' not found")) is False


def test_given_client_is_returned_as_it_is():
    given = object()

    assert get_sam_client("sbnd", given) is given
