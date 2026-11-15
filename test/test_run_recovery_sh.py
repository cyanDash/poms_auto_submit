import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_recovery.sh"

# Every samweb subcommand is logged; discovery ones (file-lineage,
# get-metadata) are not answered.
FAKE_SAMWEB = """#!/usr/bin/env bash
echo "$1" >> "$FAKE_CALLS_FILE"
case "$1" in
  list-definition-files) echo in1; echo in2 ;;
  create-definition) echo "$3" > "$FAKE_DIMENSION_FILE" ;;
  count-definition-files) [[ "$2" == input_ds ]] && echo 10 || echo 3 ;;
  *) exit 1 ;;
esac
"""


def run_script(tmp_path, *outputs):
    fake = tmp_path / "samweb"
    fake.write_text(FAKE_SAMWEB)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               FAKE_DIMENSION_FILE=str(tmp_path / "dimension.txt"),
               FAKE_CALLS_FILE=str(tmp_path / "calls.txt"))
    return subprocess.run(
        ["bash", str(SCRIPT), "input_ds", "camp", str(tmp_path / "prestage.log"), *outputs],
        capture_output=True, text=True, env=env,
    )


def test_recovery_dimension_has_one_isparentof_per_output(tmp_path):
    # A file is a member of only one output definition, so
    # "isparentof: (defname: a and defname: b)" is an empty set and
    # "not" of it keeps every input file (live: 1000 of 1000, expected 269).
    result = run_script(tmp_path, "out_a", "out_b")
    dimension = (tmp_path / "dimension.txt").read_text().strip()

    assert result.returncode == 0, result.stderr
    assert dimension == (
        "defname: input_ds and not (isparentof: (defname: out_a) and isparentof: (defname: out_b))"
    )
    assert result.stdout.splitlines() == ["0.7000", "0.98", "input_ds_camp_recovery_campaign"]


def test_output_names_come_from_the_arguments_not_from_lineage(tmp_path):
    run_script(tmp_path, "out_a")

    calls = (tmp_path / "calls.txt").read_text().split()
    assert "file-lineage" not in calls
    assert "get-metadata" not in calls


def test_no_output_names_is_a_usage_error(tmp_path):
    result = run_script(tmp_path)

    assert result.returncode != 0
    assert "usage" in result.stderr
    assert not (tmp_path / "dimension.txt").exists()
