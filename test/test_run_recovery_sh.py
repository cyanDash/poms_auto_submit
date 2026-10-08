import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_recovery.sh"

FAKE_SAMWEB = """#!/usr/bin/env bash
case "$1" in
  list-definition-files) echo in1 ;;
  file-lineage) echo child_a; echo child_b ;;
  get-metadata) echo "Dataset.Tag: out_$2" ;;
  create-definition) echo "$3" > "$FAKE_DIMENSION_FILE" ;;
  count-definition-files) [[ "$2" == input_ds ]] && echo 10 || echo 3 ;;
esac
"""


def run_script(tmp_path):
    fake = tmp_path / "samweb"
    fake.write_text(FAKE_SAMWEB)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               FAKE_DIMENSION_FILE=str(tmp_path / "dimension.txt"))
    result = subprocess.run(
        ["bash", str(SCRIPT), "input_ds", "camp", str(tmp_path / "outputs.txt")],
        capture_output=True, text=True, env=env,
    )
    return result, (tmp_path / "dimension.txt").read_text().strip()


def test_recovery_dimension_has_one_isparentof_per_output(tmp_path):
    # A file is a member of only one output definition, so
    # "isparentof: (defname: a and defname: b)" is an empty set and
    # "not" of it keeps every input file (live: 1000 of 1000, expected 269).
    result, dimension = run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "isparentof: (defname: out_child_a)" in dimension
    assert "isparentof: (defname: out_child_b)" in dimension
    assert dimension.startswith("defname: input_ds and not (")
