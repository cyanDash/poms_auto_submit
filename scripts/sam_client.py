"""sam_client: the real SAM client. get_sam_client() builds it;
is_file_not_found() recognises its answer for a file it does not have."""


class _LazySamClient:
    """samweb_client.SAMWebClient, imported and built on first use."""

    def __init__(self, experiment):
        self._experiment = experiment
        self._client = None

    def __getattr__(self, name):
        if self._client is None:
            import samweb_client
            self._client = samweb_client.SAMWebClient(experiment=self._experiment)
        return getattr(self._client, name)


def is_file_not_found(error):
    """Whether `error` is samweb_client's FileNotFound: SAM's answer for a
    retired file as for one it never had (docs/poms_client_gotchas.md).
    Matched by class name, so nothing here imports samweb_client."""
    return type(error).__name__ == "FileNotFound"


def get_sam_client(experiment, sam=None):
    """`sam` when one is given, else the real client for `experiment`."""
    return sam if sam is not None else _LazySamClient(experiment)
