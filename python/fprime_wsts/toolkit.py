"""The toolkit a simulation project builds against: a git submodule of the project.

A simulation project pins fprime-wsts as a submodule at `lib/fprime-wsts` (`fprime-wsts init`
adds it). Everything the generator writes into an overlay then points into the project
(`library_locations`, the shim's include directory, the toolkit's modules), so the generated
output is the same on every machine and builds against the toolkit commit the project names,
wherever fprime-wsts itself is checked out.

The Python half of the toolkit has to match the C++ half it generates for. When the
fprime-wsts that runs is not the one a project pins, the command is run again by the pinned
one (`delegate`), from the same Python environment.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .fpp import WstsError, is_toolkit_checkout
from .manifest import TOOLKIT_ROOT

#: Where a simulation project keeps its submodule of the toolkit, relative to its root
SUBMODULE = Path("lib") / "fprime-wsts"
#: Set for a command that a pinned toolkit runs on behalf of another (`delegate`)
DELEGATED = "WSTS_PINNED_TOOLKIT"


def pinned(path: Path) -> Optional[Path]:
    """The toolkit pinned by the project that holds `path`: `lib/fprime-wsts` in the nearest
    directory, from `path` up, that has one. None when no directory up from it has one."""
    path = Path(path).resolve()
    start = path if path.is_dir() else path.parent
    for directory in [start, *start.parents]:
        candidate = directory / SUBMODULE
        if candidate.is_dir():
            return candidate
    return None


def foreign(path: Path) -> Optional[Path]:
    """The toolkit pinned by the project that holds `path` when it is not the one running"""
    toolkit = pinned(path)
    if toolkit is None or toolkit.resolve() == TOOLKIT_ROOT:
        return None
    if not is_toolkit_checkout(toolkit):
        project = toolkit.parent.parent
        raise WstsError(f"{toolkit} is not checked out: the project at {project} pins fprime-wsts there as a "
                        f"submodule. Check it out with `git -C {project} submodule update --init {SUBMODULE.as_posix()}`")
    return toolkit.resolve()


def delegate(toolkit: Path, argv: Sequence[str], replace: bool) -> int:
    """Run `fprime-wsts argv` with the toolkit at `toolkit`, in this Python environment. With
    `replace`, this process becomes that command (signals reach it as they would this one)."""
    if os.environ.get(DELEGATED) == str(toolkit):
        raise WstsError(f"the toolkit at {toolkit} is pinned by this project, but running it ran the one at "
                        f"{TOOLKIT_ROOT} again: a package on this Python's path takes precedence over "
                        f"{toolkit / 'python'}. Install the pinned one: pip install -e {toolkit / 'python'}")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(toolkit / "python"), *filter(None, [env.get("PYTHONPATH")])])
    env[DELEGATED] = str(toolkit)
    # Not `-m`: that puts the current directory first, where another checkout's package may be
    bootstrap = (f"import sys; sys.path.insert(0, {str(toolkit / 'python')!r}); "
                 "from fprime_wsts.cli import main; sys.exit(main())")
    command = [sys.executable, "-c", bootstrap, *argv]
    print(f"fprime-wsts: using the toolkit this project pins, {toolkit}", file=sys.stderr)
    sys.stderr.flush()
    if replace:
        sys.stdout.flush()
        os.execve(sys.executable, command, env)
    return subprocess.run(command, env=env).returncode


# ----------------------------------------------------------------------
# Pinning (fprime-wsts init)
# ----------------------------------------------------------------------


def _git(args: List[str], cwd: Path, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise WstsError(f"git {' '.join(args)} (in {cwd}) failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip() if result.returncode == 0 else ""


def public_url(url: str) -> str:
    """A remote URL without the credentials it may carry (https://user:token@host/...)"""
    return re.sub(r"^(\w+://)[^/@]+@", r"\1", url)


def pin(project: Path) -> dict:
    """Add the toolkit that runs to the simulation project at `project` as a git submodule at
    `lib/fprime-wsts`, at the commit checked out here. The project becomes a git repository
    when it is not inside one. The clone is taken from this checkout (nothing is downloaded);
    the submodule's URL is this checkout's `origin`. The toolkit's own submodules are not
    checked out: a simulated build uses the flight project's framework.

    Returns what was done: `submodule`, `commit`, `url`, `added`, and `warnings` about a
    commit that others cannot fetch; or `skipped` with the reason."""
    project = Path(project).resolve()
    path = project / SUBMODULE
    relative = SUBMODULE.as_posix()
    if path.exists():
        return {"submodule": str(path), "added": False}
    if shutil.which("git") is None:
        return {"skipped": "git is not installed"}
    if not (TOOLKIT_ROOT / ".git").exists():
        return {"skipped": f"the toolkit at {TOOLKIT_ROOT} is not a git checkout"}
    url = public_url(_git(["config", "--get", "remote.origin.url"], TOOLKIT_ROOT, check=False))
    if not url:
        return {"skipped": f"the toolkit's checkout at {TOOLKIT_ROOT} has no `origin` remote to name as the "
                           "submodule's URL"}
    commit = _git(["rev-parse", "HEAD"], TOOLKIT_ROOT)
    warnings = []
    if _git(["status", "--porcelain", "--untracked-files=no"], TOOLKIT_ROOT):
        warnings.append(f"the toolkit's checkout has changes that are not committed: the submodule has commit "
                        f"{commit[:12]} without them")
    if not _git(["branch", "-r", "--contains", commit], TOOLKIT_ROOT, check=False):
        warnings.append(f"commit {commit[:12]} of the toolkit is on no branch of a remote: push it, or a clone of "
                        "the simulation project cannot check out its submodule")
    inside = _git(["rev-parse", "--show-toplevel"], project, check=False)
    top = Path(inside).resolve() if inside else None
    if top is not None and (top == TOOLKIT_ROOT or TOOLKIT_ROOT in top.parents or top in TOOLKIT_ROOT.parents):
        return {"skipped": f"the git repository the simulation project is in ({top}) holds the toolkit already"}
    if top is None:
        _git(["init", "--quiet"], project)
        top = project
    within = path.relative_to(top).as_posix()
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(["clone", "--quiet", "--no-checkout", str(TOOLKIT_ROOT), str(path)], project)
    _git(["checkout", "--quiet", "--detach", commit], path)
    _git(["remote", "set-url", "origin", url], path)
    # An existing repository at the path is added as it is: nothing is cloned again
    _git(["submodule", "add", "--quiet", url, within], top)
    _git(["submodule", "absorbgitdirs", within], top)
    return {"submodule": str(path), "path": relative, "commit": commit, "url": url, "added": True,
            "repository": str(top), "warnings": warnings}
