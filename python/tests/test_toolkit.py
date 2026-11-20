"""A simulation project that pins the toolkit as a git submodule (lib/fprime-wsts): the
command line hands its commands to the pinned toolkit, and no directory walk reads the
submodule as part of the project that holds it"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fprime_wsts import fpp, toolkit
from fprime_wsts.cli import main
from fprime_wsts.manifest import TOOLKIT_ROOT

from mission import MISSION, needs_fpp

MARKER = "pinned toolkit ran"


def fake_toolkit(directory: Path, exit_code: int = 3) -> Path:
    """A checkout of the toolkit whose command line only says that it ran, and with what"""
    (directory / "Wsts" / "Core").mkdir(parents=True)
    package = directory / "python" / "fprime_wsts"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "import os, sys\n"
        "def main():\n"
        f"    print({MARKER!r}, sys.argv[1:], os.environ[{toolkit.DELEGATED!r}])\n"
        f"    return {exit_code}\n")
    return directory


def test_the_pinned_toolkit_is_found_up_from_a_manifest_or_a_generated_directory(tmp_path):
    sim = tmp_path / "my-mission-sim"
    generated = sim / "wsts" / "Payload"
    generated.mkdir(parents=True)
    (sim / "wsts.yaml").write_text("project: ../my-mission\n")
    assert toolkit.pinned(sim / "wsts.yaml") is None and toolkit.foreign(generated) is None
    (sim / "lib" / "fprime-wsts").mkdir(parents=True)
    assert toolkit.pinned(sim / "wsts.yaml") == toolkit.pinned(generated) == sim / "lib" / "fprime-wsts"
    # A submodule that was never checked out says how to check it out
    with pytest.raises(fpp.WstsError, match="submodule update --init lib/fprime-wsts"):
        toolkit.foreign(generated)
    fake_toolkit(sim / "lib" / "fprime-wsts")
    assert toolkit.foreign(generated) == (sim / "lib" / "fprime-wsts").resolve()
    # The toolkit that runs is not foreign to a project inside its own checkout
    assert toolkit.foreign(MISSION) is None


def test_a_command_on_a_project_that_pins_another_toolkit_is_run_by_that_one(tmp_path, capfd, monkeypatch):
    monkeypatch.delenv(toolkit.DELEGATED, raising=False)
    sim = tmp_path / "sim"
    (sim / "wsts" / "Payload").mkdir(parents=True)
    pinned = fake_toolkit(sim / "lib" / "fprime-wsts").resolve()
    assert main(["report", str(sim / "wsts" / "Payload"), "--json"]) == 3
    out, err = capfd.readouterr()
    assert MARKER in out and f"['report', '{sim / 'wsts' / 'Payload'}', '--json'] {pinned}" in out
    assert f"using the toolkit this project pins, {pinned}" in err
    # Commands that do not work on a simulation project are this toolkit's own
    assert main(["survey", str(tmp_path), "--no-trial"]) == 1
    assert MARKER not in capfd.readouterr().out
    # A pinned toolkit that runs this one again would loop: it stops and says why
    monkeypatch.setenv(toolkit.DELEGATED, str(pinned))
    assert main(["report", str(sim / "wsts" / "Payload")]) == 2
    assert "pip install -e" in capfd.readouterr().err


def test_no_walk_reads_a_toolkit_checkout_inside_a_project(tmp_path):
    (tmp_path / "Components").mkdir()
    (tmp_path / "Components" / "A.fpp").write_text("module M { }\n")
    nested = fake_toolkit(tmp_path / "lib" / "fprime-wsts")
    (nested / "Wsts" / "Core" / "B.fpp").write_text("module M { }\n")
    assert fpp.find_fpp_files([tmp_path]) == [(tmp_path / "Components" / "A.fpp").resolve()]
    # The toolkit's own directories are read when it is the root that is walked
    assert fpp.find_fpp_files([nested / "Wsts"]) == [(nested / "Wsts" / "Core" / "B.fpp").resolve()]


def test_a_remote_url_is_named_without_credentials():
    assert toolkit.public_url("https://user:token@github.com/o/fprime-wsts") == "https://github.com/o/fprime-wsts"
    assert toolkit.public_url("git@github.com:o/fprime-wsts.git") == "git@github.com:o/fprime-wsts.git"


def _origin() -> str:
    result = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=TOOLKIT_ROOT,
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


@needs_fpp
@pytest.mark.skipif(shutil.which("git") is None or not (TOOLKIT_ROOT / ".git").exists() or not _origin(),
                    reason="needs the toolkit as a git checkout with an origin remote")
def test_init_pins_the_toolkit_as_a_submodule(tmp_path, capsys):
    sim = tmp_path / "sim"
    assert main(["init", str(MISSION), "--output", str(sim), "--deployment", "Payload", "--no-trial", "--json"]) == 0
    pinned = json.loads(capsys.readouterr().out)["toolkit"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=TOOLKIT_ROOT, capture_output=True, text=True).stdout.strip()
    assert pinned["added"] and pinned["path"] == "lib/fprime-wsts" and pinned["commit"] == head
    assert pinned["repository"] == str(sim.resolve()), "a project that is in no repository becomes one"
    submodule = sim / "lib" / "fprime-wsts"
    assert fpp.is_toolkit_checkout(submodule) and (submodule / ".git").is_file(), "its git directory is absorbed"
    git = lambda *args: subprocess.run(["git", *args], cwd=sim, capture_output=True, text=True, check=True).stdout
    assert git("config", "-f", ".gitmodules", "submodule.lib/fprime-wsts.url").strip() == toolkit.public_url(_origin())
    assert git("ls-files", "--stage", "lib/fprime-wsts").split()[:2] == ["160000", head]
    assert not (submodule / "lib" / "fprime" / "cmake").exists(), "the toolkit's own submodules are not checked out"
    # The submodule is not a flight project of its own, and its example is not one either
    assert main(["survey", str(sim), "--no-trial", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["projects"] == []
    # A project that has manifests already is left as it is
    other = tmp_path / "other"
    other.mkdir()
    (other / "wsts.yaml").write_text("deployment: Payload\n")
    assert main(["init", str(MISSION), "--output", str(other), "--no-trial", "--json"]) == 0
    assert "toolkit" not in json.loads(capsys.readouterr().out) and not (other / "lib").exists()
    # --no-submodule leaves the toolkit out
    assert main(["init", str(MISSION), "--output", str(tmp_path / "plain"), "--deployment", "Payload",
                 "--no-trial", "--no-submodule", "--json"]) == 0
    assert "toolkit" not in json.loads(capsys.readouterr().out) and not (tmp_path / "plain" / "lib").exists()
