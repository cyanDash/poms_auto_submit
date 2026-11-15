import json
from pathlib import Path
from urllib.parse import unquote

from output_definitions import _parse_launch_command

RAW = Path(__file__).resolve().parent.parent / "docs" / "raw"
SHIPPED = "/exp/sbnd/app/users/sdas1/production_helper_scripts/ar23plus_cfg/scripts/metadata_postscripts_sbnnusyst.sh"
CUSTOM = "/exp/sbnd/app/users/someone/custom/metadata_postscripts_custom.sh"


def real_command():
    details = json.loads((RAW / "submission_details_3179008.json").read_text())
    return unquote(details["submission"]["command_executed"])


def test_real_command_names_the_postscript_it_ships_and_runs():
    assert _parse_launch_command(real_command())["postscript"] == SHIPPED


def test_postscript_that_runs_wins_over_one_only_shipped():
    # An override such as -Osubmit.f_3=dropbox://<custom> plus a changed
    # postscript line: both files are shipped, only the custom one runs.
    command = real_command().replace(
        f"-f dropbox://{SHIPPED}", f"-f dropbox://{SHIPPED} -f dropbox://{CUSTOM}"
    ).replace("INPUT}/metadata_postscripts_sbnnusyst.sh", "INPUT}/metadata_postscripts_custom.sh")

    assert _parse_launch_command(command)["postscript"] == CUSTOM


def test_postscript_replaced_through_an_override_is_used():
    command = real_command().replace(SHIPPED, CUSTOM).replace(
        "INPUT}/metadata_postscripts_sbnnusyst.sh", "INPUT}/metadata_postscripts_custom.sh"
    )

    assert _parse_launch_command(command)["postscript"] == CUSTOM


def test_shipped_postscript_is_used_when_no_postscript_line_names_one():
    command = real_command().replace("INPUT}/metadata_postscripts_sbnnusyst.sh", "INPUT}/something_else.sh")

    assert _parse_launch_command(command)["postscript"] == SHIPPED
