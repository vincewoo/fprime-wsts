"""The sandbox of the files of flight software (`files` in the manifest).

What the generator does with it: the sandboxed file system chosen for the deployment
(Wsts_Os_File) and the sandbox in the report, which the runner exports as WSTS_FILES_DIR.
Without the key: no `files` in the report, and the sandbox is the files directory of the
run (WSTS_DIR), which the C++ implementation reads itself.

The generator tests need the FPP tools.
"""

import os
from pathlib import Path

import pytest

from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate, sandbox_top
from fprime_wsts.manifest import FILES_KEYS, parse_manifest

from fprime_wsts.runner import Simulation, sandbox_directory

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


# ----------------------------------------------------------------------
# Where the sandbox is: relative to the generated directory
# ----------------------------------------------------------------------


def test_a_relative_sandbox_is_relative_to_the_generated_directory(tmp_path):
    report = {"files": {"sandbox": "./wsts-files"}}
    assert sandbox_directory(report, tmp_path) == (tmp_path / "wsts-files").resolve()
    assert sandbox_directory({"files": {"sandbox": "../shared/files"}}, tmp_path / "cdh") == \
        (tmp_path / "shared" / "files").resolve()
    assert sandbox_directory({"files": {"sandbox": "/srv/files"}}, tmp_path) == Path("/srv/files")
    assert sandbox_directory({"files": {"sandbox": "~/files"}}, tmp_path) == Path(os.path.expanduser("~/files"))
    assert sandbox_directory({}, tmp_path) is None, "the files directory of the run (WSTS_DIR)"


def test_a_sandbox_inside_the_generated_directory_is_named_by_its_top(tmp_path):
    assert sandbox_top("./wsts-files", tmp_path) == "wsts-files"
    assert sandbox_top("wsts-files/ram", tmp_path) == "wsts-files"
    assert sandbox_top("../out/files", tmp_path / "out") == "files", "out of the directory and back in"
    assert sandbox_top(str(tmp_path / "files" / "a"), tmp_path) == "files"
    assert sandbox_top("../wsts-files", tmp_path) is None
    assert sandbox_top("/srv/files", tmp_path) is None
    assert sandbox_top(None, tmp_path) is None
    with pytest.raises(WstsError, match="is the generated directory itself"):
        sandbox_top("./", tmp_path)


def fake_deployment(directory: Path, seen: Path) -> Path:
    """A stand-in for the built deployment: writes down the sandbox it was given, and exits"""
    binary = directory / "build-artifacts" / "Linux" / "sim" / "bin" / "Cdh"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(f'#!/bin/sh\nprintf %s "$WSTS_FILES_DIR" > "{seen}"\n')
    binary.chmod(0o755)
    return seen


@needs_fpp
def test_the_sandbox_goes_from_the_manifest_to_the_run_and_survives_regeneration(tmp_path):
    output = tmp_path / "wsts" / "cdh"
    manifest = element_manifest("cdh", files={"sandbox": "./wsts-files"})
    generated = generate(MISSION, manifest, output)
    assert generated.report["files"] == {"sandbox": "./wsts-files"}, "as written: the runner resolves it"

    seen = fake_deployment(output, tmp_path / "seen-files-dir")
    simulation = Simulation(output, bridge="none").start()
    assert simulation.sim.wait(timeout=30) == 0
    simulation.stop()
    assert seen.read_text() == str((output / "wsts-files").resolve()), \
        "relative to the generated directory, not to the manifest"
    assert (output / "wsts-files").is_dir(), "made by the run"

    # Files flight software wrote there are neither deleted by a regeneration nor a change
    # for --check
    saved = output / "wsts-files" / "ram0" / "SensorSimPrmDb.dat"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"\x01\x02")
    assert generate(MISSION, manifest, output, check=True).changed == []
    generate(MISSION, manifest, output)
    assert saved.read_bytes() == b"\x01\x02"


@needs_fpp
def test_a_sandbox_cannot_be_where_generate_writes(tmp_path):
    for number, sandbox in enumerate(("./Cdh/files", "./WstsAdapted", "./wsts-logs/files")):
        with pytest.raises(WstsError, match="written by generate or cleared by run"):
            generate(MISSION, element_manifest("cdh", files={"sandbox": sandbox}), tmp_path / str(number))
