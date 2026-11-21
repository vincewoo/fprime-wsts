"""The sandbox of the files of flight software (`files` in the manifest).

What the generator does with it: the sandboxed file system chosen for the deployment
(Wsts_Os_File) and the sandbox in the report, which the runner exports as WSTS_FILES_DIR.
Without the key: no `files` in the report, and the sandbox is the files directory of the
run (WSTS_DIR), which the C++ implementation reads itself.

The generator tests need the FPP tools.
"""

import pytest

from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate
from fprime_wsts.manifest import FILES_KEYS, parse_manifest

from mission import MISSION, element_manifest, needs_fpp


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


def test_the_manifest_names_a_sandbox():
    manifest = parse_manifest({"deployment": "D"})
    assert manifest.files_sandbox is None
    assert parse_manifest({"deployment": "D", "files": {"sandbox": "./wsts-files"}}).files_sandbox == "./wsts-files"


def test_a_sandbox_that_is_not_a_directory_string_is_an_error():
    with pytest.raises(WstsError, match=r"files: unknown key\(s\): \(not a mapping\)"):
        parse_manifest({"deployment": "D", "files": "./wsts-files"})
    for value in ("", "   ", 12, True):
        with pytest.raises(WstsError, match="files.sandbox must be a directory"):
            parse_manifest({"deployment": "D", "files": {"sandbox": value}})


def test_an_unknown_sandbox_key_is_named():
    with pytest.raises(WstsError, match=r"files: unknown key\(s\): sandboxes \(did you mean 'sandbox'\?\)"):
        parse_manifest({"deployment": "D", "files": {"sandboxes": "./wsts-files"}})


def test_a_sandbox_written_at_the_top_names_its_section():
    with pytest.raises(WstsError, match=r"unknown key\(s\): sandbox \(it goes under 'files:'\)"):
        parse_manifest({"deployment": "D", "sandbox": "./wsts-files"})


def test_the_schema_holds_the_sandbox_keys():
    import json
    from pathlib import Path

    schema = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "wsts.schema.json").read_text())
    assert set(schema["properties"]["files"]["properties"]) == FILES_KEYS


# ----------------------------------------------------------------------
# The generated overlay
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def sandboxed(tmp_path_factory):
    return generate(MISSION, element_manifest("cdh", files={"sandbox": "./wsts-files"}),
                    tmp_path_factory.mktemp("files") / "out")


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    return generate(MISSION, element_manifest("cdh", files=None), tmp_path_factory.mktemp("files") / "out")


@needs_fpp
def test_the_deployment_chooses_the_sandboxed_file_system(plain):
    deployment = (plain.output / "Cdh" / "CMakeLists.txt").read_text()
    chosen = deployment.split("CHOOSES_IMPLEMENTATIONS", 1)[1].split(")", 1)[0]
    assert "Wsts_Os_Queue" in chosen and "Wsts_Os_RawTime" in chosen and "Wsts_Os_Task" in chosen
    assert "Wsts_Os_File" in chosen
    assert "files" not in plain.report


@needs_fpp
def test_the_report_carries_the_sandbox(sandboxed, plain):
    assert sandboxed.report["files"] == {"sandbox": "./wsts-files"}
    assert "Sandbox of the files of flight software" in (sandboxed.output / "wsts-report.md").read_text()
    # The sandbox is of the run: the generated build is the same whatever it is
    assert (sandboxed.output / "Cdh" / "CMakeLists.txt").read_text() == \
        (plain.output / "Cdh" / "CMakeLists.txt").read_text()


def test_only_a_sandbox_inside_the_output_is_kept():
    from fprime_wsts.generate import kept_in_output, sandbox_top

    def top(sandbox):
        return sandbox_top(parse_manifest({"deployment": "D", "files": {"sandbox": sandbox}}))

    assert top("./wsts-files") == "wsts-files" and top("state/files/") == "state"
    assert top("../shared-files") is None and top("/var/tmp/files") is None and top(".") is None
    assert sandbox_top(parse_manifest({"deployment": "D"})) is None
    assert kept_in_output("wsts-files", "wsts-files") and not kept_in_output("wsts-files", None)
    assert kept_in_output("build-fprime-automatic-native") and kept_in_output("wsts-logs")


@needs_fpp
def test_what_flight_software_saved_in_the_sandbox_outlives_a_regeneration(tmp_path):
    """A run leaves flight software's files in the sandbox (a saved parameter database). They are
    neither deleted when the overlay is generated again nor reported as a change by --check."""
    manifest = element_manifest("cdh", files={"sandbox": "./wsts-files"})
    output = generate(MISSION, manifest, tmp_path / "out").output
    saved = output / "wsts-files" / "ram0" / "SensorSimPrmDb.dat"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"\x01\x02")
    assert generate(MISSION, manifest, output, check=True).changed == []
    generate(MISSION, manifest, output)
    assert saved.read_bytes() == b"\x01\x02"
    # Without a sandbox in the output, the directory is the generator's, as any other
    plain = element_manifest("cdh", files=None)
    assert "wsts-files/ram0/SensorSimPrmDb.dat" in generate(MISSION, plain, output, check=True).changed
