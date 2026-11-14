"""`fprime-wsts build`: when it configures, when it configures again, and what it reports.
`fprime-util` is a stand-in here that behaves as the real one does towards a build
directory; the real builds are those of the end-to-end job."""

import json
import os
import stat
from pathlib import Path

import pytest

from fprime_wsts import build as builder
from fprime_wsts.cli import main
from fprime_wsts.fpp import WstsError

FAKE = r'''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
here, args = Path.cwd(), sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as calls:
    calls.write(here.name + " " + " ".join(args) + "\n")
build = here / "build-fprime-automatic-native"
if args[0] == "generate":
    if build.exists() and "-f" not in args:
        print("[ERROR] build directory already exists"); sys.exit(1)
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir()
    if os.environ.get("FAKE_FAIL") == "configure":
        print("CMake Error at CMakeLists.txt:3 (message): SENSOR_LIB_BOARD is not set"); sys.exit(1)
    (build / "CMakeCache.txt").write_text(""); (build / "build.ninja").write_text("")
elif args[0] == "build":
    if os.environ.get("FAKE_FAIL") == here.name:
        print("[1/2] Building X.cpp\n/src/X.cpp:4:5: error: 'memcpy' was not declared in this scope\nninja: build stopped")
        sys.exit(1)
    binary = here / "build-artifacts" / "Linux" / "Dep" / "bin" / "Dep"
    binary.parent.mkdir(parents=True, exist_ok=True); binary.write_text(""); binary.chmod(0o755)
'''


def overlay(directory: Path, cache: str = "A", options=()) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "wsts-report.json").write_text(json.dumps({"configure_options": list(options)} if options else {}))
    (directory / "CMakeLists.txt").write_text(f'set(BOARD "{cache}" CACHE STRING "from cmake.cache")\nproject(X)\n')
    (directory / "settings.ini").write_text("[fprime]\n")
    return directory


@pytest.fixture
def calls(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    fake = tools / "fprime-util"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "calls.txt"
    log.write_text("")
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CALLS", str(log))
    monkeypatch.delenv("FAKE_FAIL", raising=False)

    def read():
        lines = log.read_text().splitlines()
        log.write_text("")
        return lines
    return read


def test_configures_once_then_only_builds(tmp_path, calls):
    out = overlay(tmp_path / "Dep")
    first = builder.build(out, jobs=3, quiet=True)
    assert first["ok"] and first["elements"]["Dep"]["configure"] == "configure"
    assert first["elements"]["Dep"]["binary"].endswith("bin/Dep")
    assert calls() == ["Dep generate", "Dep build -j 3"]
    second = builder.build(out, quiet=True)
    assert second["elements"]["Dep"]["configure"] == "keep", "fprime-util generate refuses an existing build directory"
    assert calls() == ["Dep build"]
    assert "$ (cd " in (out / "wsts-logs" / "build.log").read_text()


def test_configures_again_when_what_the_build_directory_caches_changes(tmp_path, calls):
    out = overlay(tmp_path / "Dep")
    builder.build(out, quiet=True)
    calls()
    overlay(out, cache="B")  # cmake.cache changed in the manifest, and the overlay was regenerated
    changed = builder.build(out, quiet=True)["elements"]["Dep"]
    assert changed["configure"] == "reconfigure" and "cmake.cache" in changed["reason"]
    assert calls() == ["Dep generate -f", "Dep build"]
    assert builder.build(out, quiet=True)["elements"]["Dep"]["configure"] == "keep"
    option = builder.build(out, defines=["CMAKE_BUILD_TYPE=Debug"], quiet=True)["elements"]["Dep"]
    assert option["configure"] == "reconfigure" and "-D options" in option["reason"]
    assert calls() == ["Dep build", "Dep generate -f -DCMAKE_BUILD_TYPE=Debug", "Dep build"]
    assert builder.build(out, fresh=True, quiet=True)["elements"]["Dep"]["reason"] == "--fresh"


def test_options_of_the_report_are_passed_when_configuring(tmp_path, calls):
    out = overlay(tmp_path / "Dep", options=["-DCMAKE_BUILD_TYPE=Debug"])
    builder.build(out, quiet=True)
    assert calls()[0] == "Dep generate -DCMAKE_BUILD_TYPE=Debug"


def test_a_build_directory_made_by_hand_is_kept(tmp_path, calls):
    out = overlay(tmp_path / "Dep")
    made = out / "build-fprime-automatic-native"
    made.mkdir()
    (made / "CMakeCache.txt").write_text("")
    (made / "build.ninja").write_text("")
    kept = builder.build(out, quiet=True)["elements"]["Dep"]
    assert kept["configure"] == "keep" and "--fresh" in kept["reason"]
    assert calls() == ["Dep build"] and not (made / builder.STAMP).exists()


def test_a_failed_configure_step_is_reported_and_then_retried(tmp_path, calls, monkeypatch):
    out = overlay(tmp_path / "Dep")
    monkeypatch.setenv("FAKE_FAIL", "configure")
    failed = builder.build(out, quiet=True)
    element = failed["elements"]["Dep"]
    assert not failed["ok"] and element["stage"] == "configure" and "binary" not in element
    assert element["errors"] == ["CMake Error at CMakeLists.txt:3 (message): SENSOR_LIB_BOARD is not set"]
    assert calls() == ["Dep generate"], "nothing is built after it"
    monkeypatch.delenv("FAKE_FAIL")
    again = builder.build(out, quiet=True)["elements"]["Dep"]
    assert again["configure"] == "reconfigure" and "did not finish" in again["reason"] and again["exit_code"] == 0
    assert calls() == ["Dep generate -f", "Dep build"]


def system(directory: Path) -> Path:
    directory.mkdir()
    (directory / "wsts-system.json").write_text(json.dumps({"system": "S", "elements": [
        {"name": "cdh", "directory": "cdh"}, {"name": "payload", "directory": "payload"}]}))
    overlay(directory / "cdh")
    overlay(directory / "payload")
    return directory


def test_a_system_builds_every_element_and_stops_at_the_first_failure(tmp_path, calls, monkeypatch, capsys):
    out = system(tmp_path / "S")
    assert builder.build(out, quiet=True)["ok"]
    assert calls() == ["cdh generate", "cdh build", "payload generate", "payload build"]
    assert (out / "wsts-logs" / "payload" / "build.log").is_file()
    assert list(builder.build(out, only=["payload"], quiet=True)["elements"]) == ["payload"]
    calls()
    monkeypatch.setenv("FAKE_FAIL", "cdh")
    assert main(["build", str(out), "--json"]) == 1
    printed = capsys.readouterr()
    summary = json.loads(printed.out)
    assert list(summary["elements"]) == ["cdh"] and summary["elements"]["cdh"]["stage"] == "build"
    assert summary["elements"]["cdh"]["errors"] == ["/src/X.cpp:4:5: error: 'memcpy' was not declared in this scope"]
    assert "not built after that failure: payload" in printed.err
    assert main(["build", str(out), "--json", "--keep-going"]) == 1
    assert json.loads(capsys.readouterr().out)["elements"]["payload"]["exit_code"] == 0
    with pytest.raises(WstsError, match="not an element"):
        builder.build(out, only=["radio"], quiet=True)


def test_what_is_not_a_generated_directory(tmp_path, calls, capsys):
    assert main(["build", str(tmp_path)]) == 2
    assert "is not a generated simulation deployment or system" in capsys.readouterr().err
    assert main(["build", str(tmp_path / "missing.yaml")]) == 2
    with pytest.raises(WstsError, match="NAME=VALUE"):
        builder.build(overlay(tmp_path / "Dep"), defines=["DEBUG"], quiet=True)
