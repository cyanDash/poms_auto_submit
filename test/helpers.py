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
        "do_recovery": False,
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


class DefinitionNotFound(Exception):
    """Same name as samweb_client.exceptions.DefinitionNotFound."""


class FakeSam:
    """Stands in for samweb_client.SAMWebClient: definitions, metadata in
    the shape of docs/raw/sam_getMetadata_reco1.json, and children derived
    from the declared parents (docs/raw/sam_lineage.json)."""

    def __init__(self):
        self.definitions = {}
        self.metadata = {}
        self.extra_children = {}
        self.outside_files = set()
        self.lineage_calls = []
        self.batch_sizes = []
        self.retired = set()
        self.retire_calls = []
        self.fail_on_retire = set()
        self.location_removals = []
        self.created_definitions = {}
        self.dimension_queries = []
        self.locations = {}
        self.fail_on_locate = set()

    def add(self, definition, file_name, parent, process_id=None):
        md = {
            "file_name": file_name,
            "file_id": len(self.metadata) + 1,
            "Dataset.Tag": definition,
            "parents": [{"file_name": parent, "file_id": 0}] if parent else [],
        }
        if process_id is not None:
            md["process_id"] = process_id
        self.metadata[file_name] = md
        if definition is not None:
            self.definitions.setdefault(definition, []).append(file_name)
            self.locations[file_name] = [f"dcache:/pnfs/sbnd/fake/{definition}"]

    def add_child_outside(self, parent, child):
        """A live child that belongs to no definition under cleanup."""
        self.extra_children.setdefault(parent, []).append(child)
        self.outside_files.add(child)

    def listFiles(self, defname=None, dimensions=None):
        """Live files only, as SAM lists them by default."""
        if dimensions is not None:
            assert dimensions.startswith("file_name ")
            self.dimension_queries.append(dimensions)
            names = dimensions[len("file_name "):].split(",")
            known = set(self.metadata) | self.outside_files
            return [n for n in names if n in known and n not in self.retired]
        return [n for n in self.definitions.get(defname, []) if n not in self.retired]

    def retireFile(self, name):
        self.retire_calls.append(name)
        if name in self.fail_on_retire:
            raise RuntimeError(f"500 Server Error retiring {name}")
        self.retired.add(name)

    def removeFileLocation(self, name, location):
        self.location_removals.append((name, location))

    def locateFile(self, name):
        """Shape of docs/raw/sam_lineage.json's `locate`."""
        if name in self.fail_on_locate:
            raise RuntimeError(f"500 Server Error locating {name}")
        return [
            {"location_type": "disk", "full_path": full_path, "location": full_path}
            for full_path in self.locations.get(name, [])
        ]

    def descDefinitionDict(self, defname):
        if defname not in self.created_definitions:
            raise DefinitionNotFound(defname)
        return {"defname": defname, "dimensions": self.created_definitions[defname]}

    def createDefinition(self, defname, dims):
        assert defname not in self.created_definitions
        self.created_definitions[defname] = dims

    def getMultipleMetadata(self, names):
        names = list(names)
        self.batch_sizes.append(len(names))
        return [self.metadata[n] for n in names if n in self.metadata]

    def getFileLineage(self, lineage, name):
        assert lineage == "children"
        self.lineage_calls.append(name)
        children = [
            n for n, md in self.metadata.items()
            if md["parents"] and md["parents"][0]["file_name"] == name
        ]
        children += self.extra_children.get(name, [])
        return [{"file_name": n, "file_id": 0} for n in children]


def duplicated_campaign():
    """Two inputs, each run by two workers; the lower process id is kept."""
    sam = FakeSam()
    for i in (1, 2):
        sam.add(None, f"gen_{i}.root", None)
        for pid in (i * 100, i * 100 + 1):
            sam.add("reco1_def", f"reco1_{pid}.root", f"gen_{i}.root", pid)
            sam.add("larcv_def", f"larcv_{pid}.root", f"gen_{i}.root", pid)
    return sam
