"""What has moved under a simulation project since it was last generated.

A simulation project is generated from three things that change on their own: the flight
project (sibling checkout), the toolkit it pins (`lib/fprime-wsts`), and its own manifests.
`drift(<manifest or simulation project>)` says, without writing anything:

- whether generating again would change the generated tree, and if generation fails now,
  why (a flight instance the manifest names is gone, say);
- what would change, by area of the report rather than by file: substitutions, hardware
  drivers kept, endpoints and their models, links, simulator components, dictionary items
  missing, time, capacity, warnings, modules built and how. Each change that needs a
  decision says which;
- the flight project's commits since the generated tree was last written (by the
  simulation project's git history, else the report's modification time);
- the toolkit pin: the commit `lib/fprime-wsts` is at, and how far the toolkit checkout
  running this is from it.

It runs `fprime-wsts generate --check --json`, which generates into a scratch directory with
the toolkit the project pins, and compares the reports with those on disk.

The `wsts-sync` skill runs this through its `scripts/drift.py`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .fpp import WstsError

BOOTSTRAP = "import sys; from fprime_wsts.cli import main; sys.exit(main())"


# ----------------------------------------------------------------------
# Finding the manifest
# ----------------------------------------------------------------------


def find_manifest(target: Path) -> Path:
    """The manifest of a simulation project: the one given, wsts-system.yaml, wsts.yaml, or the
    one YAML file of the directory that names a deployment"""
    from .manifest import read_yaml

    target = Path(target).resolve()
    if target.is_file():
        return target
    if not target.is_dir():
        raise WstsError(f"{target} not found")
    for name in ("wsts-system.yaml", "wsts.yaml"):
        if (target / name).is_file():
            return target / name
    candidates = []
    for path in sorted(target.glob("*.yaml")):
        try:
            data = read_yaml(path)
        except WstsError:
            continue
        if isinstance(data, dict) and ("deployment" in data or "elements" in data):
            candidates.append(path)
    systems = [p for p in candidates if "elements" in read_yaml(p)]
    if len(systems) == 1:
        return systems[0]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise WstsError(f"no manifest in {target}: name it (wsts.yaml, or wsts-system.yaml for a system)")
    raise WstsError(f"several manifests in {target} ({', '.join(p.name for p in candidates)}): name the one to check")


def element_manifests(manifest: Path) -> Dict[str, Path]:
    """{element: manifest} of a system manifest, or {"": manifest} of a deployment's"""
    from .system import is_system_manifest, load_system

    if is_system_manifest(manifest):
        system = load_system(manifest)
        return {e.name: (system.root / e.manifest).resolve() for e in system.elements}
    return {"": manifest}


# ----------------------------------------------------------------------
# Comparing two reports
# ----------------------------------------------------------------------


def _change(area: str, change: str, subject: str, detail: str = "", decide: str = "") -> dict:
    found = {"area": area, "change": change, "subject": subject}
    if detail:
        found["detail"] = detail
    if decide:
        found["decide"] = decide
    return found


def _keyed(items: Iterable[dict], key: str, fields: Iterable[str]) -> Dict[str, tuple]:
    fields = list(fields)
    return {str(item.get(key)): tuple(item.get(f) for f in fields) for item in items or []}


def _compare(area: str, old: Dict[str, Any], new: Dict[str, Any], describe: Callable[[Any], str] = str,
             decide_added: Callable[[str, Any], str] = lambda k, v: "",
             decide_removed: Callable[[str, Any], str] = lambda k, v: "") -> List[dict]:
    changes = []
    for key in sorted(set(old) | set(new)):
        if key not in old:
            changes.append(_change(area, "added", key, describe(new[key]), decide_added(key, new[key])))
        elif key not in new:
            changes.append(_change(area, "removed", key, describe(old[key]), decide_removed(key, old[key])))
        elif old[key] != new[key]:
            changes.append(_change(area, "changed", key, f"{describe(old[key])} -> {describe(new[key])}"))
    return changes


def _set(items) -> Dict[str, bool]:
    return {str(i) if not isinstance(i, (dict, list)) else json.dumps(i, sort_keys=True): True for i in items or []}


def _short(instance: str) -> str:
    return instance.rsplit(".", 1)[-1]


def find_renames(old: dict, new: dict) -> Dict[str, str]:
    """{old instance: new instance} for replaced instances that flight renamed: one gone and
    one new of the same flight component, surrogate and reason, each endpoint named after its
    instance (or neither with one). Only pairs that leave no doubt."""
    def subs(report):
        return {s["instance"]: s for s in report.get("substitutions") or []}

    before, after = subs(old), subs(new)
    gone = [s for name, s in before.items() if name not in after]
    added = [s for name, s in after.items() if name not in before]

    def shape(sub):
        endpoint = sub.get("endpoint")
        return (sub.get("flight_component"), sub.get("surrogate"), sub.get("chosen_by"),
                None if endpoint is None else endpoint == _short(sub["instance"]))

    renames = {}
    for sub in gone:
        matches = [a for a in added if shape(a) == shape(sub)]
        rivals = [g for g in gone if shape(g) == shape(sub)]
        if len(matches) == 1 and len(rivals) == 1:
            renames[sub["instance"]] = matches[0]["instance"]
    return renames


def _renamed(report: dict, renames: Dict[str, str]) -> dict:
    """A report with renamed instances (and the endpoints and dictionary items named after
    them) given their new names"""
    import re

    text = json.dumps(report)
    for before, after in renames.items():
        text = text.replace(f'"{before}', f'"{after}').replace(f'"{before}.', f'"{after}.')
        text = re.sub(rf'(?<![\w.]){re.escape(_short(before))}(?![\w])', _short(after), text)
    return json.loads(text)


def diff_reports(old: dict, new: dict) -> List[dict]:
    """What changed between two reports of one element, by area. Instances flight renamed are
    one change each, and the rest is compared under their new names."""
    from .fidelity import _endpoints

    changes: List[dict] = []
    renames = find_renames(old, new)
    for before, after in sorted(renames.items()):
        changes.append(_change("substitutions", "renamed", before, f"-> {after}",
                               f"flight renamed it: name {_short(after)} in the manifest wherever it names "
                               f"{_short(before)} (an instance, an endpoint of a model or device)"))
    if renames:
        old = _renamed(old, renames)
    for key in ("topology", "flight_toolchain", "bridge"):
        if old.get(key) != new.get(key):
            changes.append(_change("deployment", "changed", key, f"{old.get(key)} -> {new.get(key)}"))

    def sub_describe(v):
        flight, surrogate, chosen, endpoint = v
        return f"{flight} by {surrogate} ({chosen})" + (f", endpoint {endpoint}" if endpoint else "")

    changes += _compare(
        "substitutions",
        _keyed(old.get("substitutions"), "instance", ("flight_component", "surrogate", "chosen_by", "endpoint")),
        _keyed(new.get("substitutions"), "instance", ("flight_component", "surrogate", "chosen_by", "endpoint")),
        sub_describe,
        decide_added=lambda k, v: (f"new hardware instance in flight, replaced automatically: does endpoint "
                                   f"'{v[3]}' need a model?" if v[3] else ""),
        decide_removed=lambda k, v: "flight no longer has it, or no longer replaces it: drop what the manifest "
                                    "says about it (models, devices, options)")
    changes += _compare("kept hardware", _set(old.get("kept_hardware_instances")),
                        _set(new.get("kept_hardware_instances")), lambda v: "",
                        decide_added=lambda k, v: "a hardware driver the toolkit does not replace: substitute it, "
                                                  "or keep it with the reason (wsts-configure decisions §5)")
    changes += _compare("models", _keyed(old.get("models"), "name", ("class", "endpoint", "kind", "address")),
                        _keyed(new.get("models"), "name", ("class", "endpoint", "kind", "address")),
                        lambda v: f"{v[0]} on {v[1]} ({v[2]})")
    changes += _compare("links", _keyed(old.get("links"), "link", ("endpoint", "peer")),
                        _keyed(new.get("links"), "link", ("endpoint", "peer")), lambda v: f"{v[0]} <-> {v[1]}")
    changes += _compare("register blocks",
                        _keyed(old.get("register_blocks"), "instance", ("endpoint", "flight_component", "model")),
                        _keyed(new.get("register_blocks"), "instance", ("endpoint", "flight_component", "model")),
                        lambda v: f"{v[1]} on {v[0]}, model {v[2]}")
    changes += _compare("simulator components",
                        _keyed(old.get("simulators", {}).get("instances"), "instance", ("component",)),
                        _keyed(new.get("simulators", {}).get("instances"), "instance", ("component",)),
                        lambda v: v[0])
    # Endpoints with nothing answering, which a new endpoint usually is
    for report in (old, new):
        report.setdefault("_dir", ".")

    def bare(report):
        return {e["endpoint"]: e for e in _endpoints(report, None, [])}

    old_ep, new_ep = bare(old), bare(new)
    for name, endpoint in sorted(new_ep.items()):
        was = old_ep.get(name)
        if endpoint["fidelity"] == "nothing" and (was is None or was["fidelity"] != "nothing"):
            changes.append(_change("endpoints", "added" if was is None else "changed", name,
                                   f"{endpoint['kind']} endpoint with nothing answering: flight software sees "
                                   f"{endpoint.get('flight_sees')}",
                                   "a model, or leave it unanswered on purpose (wsts-configure decisions §8)"))
    for name in sorted(set(old_ep) - set(new_ep)):
        changes.append(_change("endpoints", "removed", name, f"{old_ep[name]['kind']} endpoint"))
    changes += _compare("project modules", _set(old.get("project_modules")), _set(new.get("project_modules")),
                        lambda v: "",
                        decide_added=lambda k, v: "a new flight module: check whether it reaches hardware "
                                                  "(`fprime-wsts survey` lists components that open devices)")
    for key, label in (("kept", "libraries kept"), ("dropped", "libraries dropped")):
        changes += _compare(label, _set(old.get("libraries", {}).get(key)), _set(new.get("libraries", {}).get(key)),
                            lambda v: "")
    changes += _compare("stub variants", _set(old.get("stub_variants")), _set(new.get("stub_variants")), lambda v: "")
    changes += _compare("BUILD_TESTING variants", _keyed(old.get("testing_variants"), "module", ("selected",)),
                        _keyed(new.get("testing_variants"), "module", ("selected",)),
                        lambda v: "selected" if v[0] else "not selected",
                        decide_added=lambda k, v: "" if v[0] else "a module that changes under BUILD_TESTING: does it "
                                                                  "need its stub variant (cmake.stub_variants)?")
    od, nd = old.get("dictionary", {}), new.get("dictionary", {})
    for kind in ("commands", "events", "channels", "params"):
        changes += _compare(f"dictionary: {kind} missing", _set(od.get("removed", {}).get(kind)),
                            _set(nd.get("removed", {}).get(kind)), lambda v: "",
                            decide_added=lambda k, v: "ground procedures and tests that use it fail in simulation")
        added_old = len(od.get("added", {}).get(kind, []))
        added_new = len(nd.get("added", {}).get(kind, []))
        if added_old != added_new:
            changes.append(_change("dictionary", "changed", f"{kind} added by WSTS", f"{added_old} -> {added_new}"))
    if od.get("flight_ids_unchanged", True) != nd.get("flight_ids_unchanged", True):
        changes.append(_change("dictionary", "changed", "flight_ids_unchanged",
                               f"{od.get('flight_ids_unchanged')} -> {nd.get('flight_ids_unchanged')}",
                               "flight IDs must not move: report it"))
    ot, nt = old.get("time", {}), new.get("time", {})
    for key in ("mode", "tick_source"):
        if ot.get(key) != nt.get(key):
            changes.append(_change("time", "changed", key, f"{ot.get(key)} -> {nt.get(key)}"))
    if (ot.get("time_source") or {}).get("instance") != (nt.get("time_source") or {}).get("instance"):
        changes.append(_change("time", "changed", "time_source",
                               f"{(ot.get('time_source') or {}).get('instance')} -> "
                               f"{(nt.get('time_source') or {}).get('instance')}"))
    changes += _compare("waits on the host",
                        {",".join(w.get("instances") or w.get("components") or []): ",".join(w.get("kinds", []))
                         for w in ot.get("waits_on_host", [])},
                        {",".join(w.get("instances") or w.get("components") or []): ",".join(w.get("kinds", []))
                         for w in nt.get("waits_on_host", [])},
                        decide_added=lambda k, v: "matters in stepped time: see the report's Time section")
    for table in sorted(set(old.get("capacity", {})) | set(new.get("capacity", {}))):
        a, b = old.get("capacity", {}).get(table, {}), new.get("capacity", {}).get(table, {})
        if (a.get("used"), a.get("size")) != (b.get("used"), b.get("size")):
            full = bool(b.get("size")) and (b.get("used") or 0) / b["size"] >= 0.8
            changes.append(_change("capacity", "changed", table,
                                   f"{a.get('used')}/{a.get('size')} -> {b.get('used')}/{b.get('size')}",
                                   "nearly full: raise it in the simulation's configuration" if full else ""))
    changes += _compare("warnings", _set(old.get("warnings")), _set(new.get("warnings")), lambda v: "",
                        decide_added=lambda k, v: "settle it (wsts-configure troubleshooting)")
    changes += _compare("fidelity notes", _set(old.get("fidelity_notes")), _set(new.get("fidelity_notes")),
                        lambda v: "")
    changes += _compare("host requirements: modules",
                        _keyed(old.get("host_requirements", {}).get("modules"), "module", ("instances",)),
                        _keyed(new.get("host_requirements", {}).get("modules"), "module", ("instances",)),
                        lambda v: "", decide_added=lambda k, v: "run check-host before building")
    covered = {"topology", "flight_toolchain", "bridge", "substitutions", "kept_hardware_instances", "models",
               "links", "register_blocks", "simulators", "project_modules", "libraries", "stub_variants",
               "testing_variants", "dictionary", "time", "capacity", "warnings", "fidelity_notes",
               "host_requirements", "_dir"}
    for key in sorted((set(old) | set(new)) - covered):
        if old.get(key) != new.get(key):
            changes.append(_change("other", "changed", key))
    return changes


# ----------------------------------------------------------------------
# Git
# ----------------------------------------------------------------------


def _git(directory: Path, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _repo(directory: Path) -> Optional[Path]:
    top = _git(directory, "rev-parse", "--show-toplevel")
    return Path(top) if top else None


def last_generated(sim_root: Path, output: Path) -> Optional[dict]:
    """When the generated tree was last written: by the simulation project's history of it, else
    by the time its report was written"""
    repo = _repo(sim_root)
    if repo is not None:
        when = _git(repo, "log", "-1", "--format=%cI %H", "--", str(output))
        if when:
            stamp, commit = when.split(" ", 1)
            return {"at": stamp, "commit": commit, "repo": str(repo),
                    "by": f"commit {commit[:7]} of the simulation project"}
    reports = sorted(output.rglob("wsts-report.json"), key=lambda p: p.stat().st_mtime)
    if reports:
        stamp = datetime.datetime.fromtimestamp(reports[-1].stat().st_mtime, datetime.timezone.utc)
        return {"at": stamp.isoformat(timespec="seconds"), "by": f"modification time of {reports[-1]}"}
    return None


def flight_state(project: Path, since: Optional[dict], limit: int = 30) -> dict:
    """The flight project's commits (to its own directory) since the tree was generated: by the
    generating commit when both projects share a repository, else by its date"""
    found: Dict[str, Any] = {"project": str(project)}
    repo = _repo(project)
    if repo is None:
        found["git"] = False
        return found
    found["head"] = _git(repo, "log", "-1", "--format=%h %cs %s", "--", str(project))
    status = _git(repo, "status", "--porcelain", "--", str(project)) or ""
    found["uncommitted_files"] = len([line for line in status.splitlines() if line.strip()])
    if since:
        same_repo = since.get("repo") and Path(since["repo"]).resolve() == repo.resolve()
        selection = [f"{since['commit']}..HEAD"] if same_repo else [f"--since={since['at']}"]
        log = _git(repo, "log", *selection, "--format=%ct %h %cs %s", "--", str(project)) or ""
        commits = []
        after = datetime.datetime.fromisoformat(since["at"]).timestamp()
        for line in log.splitlines():
            if not line:
                continue
            stamp, rest = line.split(" ", 1)
            # --since takes the second of generation itself: only what came after it
            if same_repo or int(stamp) > after:
                commits.append(rest)
        found["commits_since_generated"] = len(commits)
        found["commits"] = commits[:limit]
        found["dated_by"] = "commit range" if same_repo else "date (separate repositories)"
        if commits:
            oldest = commits[-1].split()[0]
            stat = _git(repo, "diff", "--stat=120", f"{oldest}~1", "HEAD", "--", str(project)) or ""
            found["files_changed"] = stat.splitlines()[-1].strip() if stat else ""
    return found


def toolkit_state(sim_root: Path) -> dict:
    """The toolkit the project pins, and where the running one is against it"""
    from .manifest import TOOLKIT_ROOT
    from .toolkit import pinned

    found: Dict[str, Any] = {"running": str(TOOLKIT_ROOT), "running_head": _git(TOOLKIT_ROOT, "log", "-1",
                                                                                "--format=%h %cs %s")}
    toolkit = pinned(sim_root)
    if toolkit is None:
        found["pinned"] = None
        found["note"] = ("the project pins no toolkit (no lib/fprime-wsts): it is generated with whichever runs, "
                         "and generated paths follow that one")
        return found
    found["pinned"] = str(toolkit)
    head = _git(toolkit, "rev-parse", "HEAD")
    found["pinned_head"] = _git(toolkit, "log", "-1", "--format=%h %cs %s")
    dirty = _git(toolkit, "status", "--porcelain", "--untracked-files=no") or ""
    found["pinned_has_local_changes"] = bool(dirty.strip())
    if toolkit.resolve() == TOOLKIT_ROOT:
        found["note"] = "the pinned toolkit is the one running"
        return found
    if head and _git(TOOLKIT_ROOT, "cat-file", "-e", f"{head}^{{commit}}") is not None:
        ahead = _git(TOOLKIT_ROOT, "log", "--format=%h %cs %s", f"{head}..HEAD") or ""
        behind = _git(TOOLKIT_ROOT, "rev-list", "--count", f"HEAD..{head}") or "0"
        commits = [line for line in ahead.splitlines() if line]
        found["running_is_ahead_by"] = len(commits)
        found["running_is_behind_by"] = int(behind)
        found["commits_not_pinned"] = commits[:30]
    else:
        found["note"] = (f"the pinned commit {head} is not in the running toolkit's history (fetch it there to "
                         "compare)")
    return found


# ----------------------------------------------------------------------
# All of it
# ----------------------------------------------------------------------


MISSING_INSTANCE = re.compile(r"names instance '(\w+)', which is not in topology (\S+)")


def suggest_instances(error: str, old_reports: Dict[str, dict], projects: Iterable[Path]) -> List[str]:
    """When the manifest names an instance flight no longer has: the instances of the same
    component that the flight sources define, which the last generation did not know"""
    found = []
    for name, topology in MISSING_INSTANCE.findall(error):
        known = {s["instance"]: s for r in old_reports.values() for s in r.get("substitutions") or []}
        old = next((s for full, s in known.items() if _short(full) == name), None)
        if old is None:
            continue
        component = old["flight_component"]
        short_component = component.rsplit(".", 1)[-1]
        pattern = re.compile(rf"\binstance\s+(\w+)\s*:\s*(?:[\w.]*\.)?{re.escape(short_component)}\b")
        candidates = set()
        for project in projects:
            for fpp_file in project.rglob("*.fpp"):
                if any(part.startswith(("build-", ".")) or part in ("wsts", "lib") for part in
                       fpp_file.relative_to(project).parts[:-1]):
                    continue
                candidates |= set(pattern.findall(fpp_file.read_text(errors="replace")))
        new = sorted(c for c in candidates if c not in {_short(k) for k in known})
        if new:
            found.append(f"'{name}' was a {component}; flight now defines {', '.join(new)} of that component, "
                         f"which the last generation did not have: if flight renamed it, rename it in the manifest "
                         f"(the instance, and the endpoint of its model or device)")
        else:
            found.append(f"'{name}' was a {component}, and flight defines no new instance of it: if flight removed "
                         "it, drop what the manifest says about it")
    return found


def check(manifest: Path, executable: Optional[List[str]] = None) -> dict:
    """`fprime-wsts generate --check --json` on a manifest, as the toolkit it pins runs it"""
    command = [*(executable or [sys.executable, "-c", BOOTSTRAP]), "generate", str(manifest), "--check", "--json"]
    done = subprocess.run(command, capture_output=True, text=True, cwd=manifest.parent)
    notes = [line for line in done.stderr.splitlines() if line.startswith(("note:", "fprime-wsts: using"))]
    if done.returncode not in (0, 1) or not done.stdout.strip():
        error = [line for line in done.stderr.splitlines() if line.strip() and not line.startswith("note:")]
        return {"generates": False, "error": "\n".join(error[-20:]) or f"exit status {done.returncode}",
                "notes": notes}
    result = json.loads(done.stdout)
    reports = result.get("reports") or {Path(result["output"]).name: result["report"]}
    return {"generates": True, "up_to_date": done.returncode == 0, "output": result["output"],
            "changed": result.get("changed", []), "reports": reports, "notes": notes,
            "system": "system" in result}


def _summarize_files(changed: List[str]) -> Dict[str, int]:
    """Changed files by their first two directories"""
    groups: Dict[str, int] = {}
    for name in changed:
        parts = name.split("/")
        key = "/".join(parts[:2]) if len(parts) > 2 else name
        groups[key] = groups.get(key, 0) + 1
    return dict(sorted(groups.items()))


def drift(target: Path, executable: Optional[List[str]] = None) -> dict:
    from .fidelity import load_reports
    from .manifest import load_manifest
    from .system import find_project

    manifest = find_manifest(target)
    sim_root = manifest.parent
    result: Dict[str, Any] = {"manifest": str(manifest), "simulation_project": str(sim_root)}
    checked = check(manifest, executable)
    result["generation"] = {k: v for k, v in checked.items() if k != "reports"}
    output = Path(checked["output"]) if checked.get("output") else None
    if output is None:
        # Generation fails: the tree on disk, where the manifest says it is, still dates it
        candidates = [sim_root / "wsts" / p.name for p in (sim_root / "wsts").glob("*")] if (sim_root / "wsts").is_dir() else []
        output = candidates[0] if len(candidates) == 1 else None
    if checked.get("generates") and output is not None:
        try:
            old = {name: report for name, report in load_reports(output).items()}
        except WstsError:
            old = {}
        new = checked["reports"]
        elements = {}
        for name in sorted(set(old) | set(new)):
            if name not in old:
                elements[name] = {"change": "added", "changes": []}
            elif name not in new:
                elements[name] = {"change": "removed", "changes": []}
            else:
                elements[name] = {"changes": diff_reports(dict(old[name]), dict(new[name]))}
        result["elements"] = elements
        result["generation"]["files_changed"] = _summarize_files(checked.get("changed", []))
        result["generation"].pop("changed", None)
    since = last_generated(sim_root, output) if output is not None and output.exists() else None
    result["last_generated"] = since
    projects: Dict[str, Path] = {}
    for element, path in element_manifests(manifest).items():
        try:
            project = load_manifest(path).project_root() or find_project(path.parent)
        except WstsError:
            continue
        projects.setdefault(str(project), project)
    result["flight"] = [flight_state(p, since) for p in projects.values()]
    result["toolkit"] = toolkit_state(sim_root)
    if not checked.get("generates") and output is not None:
        try:
            old = load_reports(output)
        except WstsError:
            old = {}
        hints = suggest_instances(checked.get("error", ""), old, projects.values())
        if hints:
            result["generation"]["hints"] = hints
    return result


def render(d: dict) -> str:
    out = [f"# Drift of {d['manifest']}", ""]
    g = d["generation"]
    if not g.get("generates"):
        out += ["**Generation fails now:**", "", "```", g.get("error", ""), "```", ""]
        out += [f"- {hint}" for hint in g.get("hints", [])]
        out += ["", "Fix the manifest (or the simulation project) for what changed, then run this again.", ""]
    elif g.get("up_to_date"):
        out += ["The generated tree is up to date: generating again changes nothing.", ""]
    else:
        out += [f"Generating again would change {sum(g['files_changed'].values())} file(s) of {g['output']}:", ""]
        out += [f"- `{k}`: {n}" for k, n in g["files_changed"].items()] + [""]
    for note in g.get("notes", []):
        out.append(f"> {note}")
    for name, element in (d.get("elements") or {}).items():
        label = name or "deployment"
        if element.get("change"):
            out += [f"## Element {label}: {element['change']}", ""]
            continue
        if not element["changes"]:
            continue
        out += [f"## {label}", "", "| Area | Change | Subject | Detail | Decide |", "|---|---|---|---|---|"]
        for c in element["changes"]:
            out.append(f"| {c['area']} | {c['change']} | `{c['subject']}` | {c.get('detail', '')} | "
                       f"{c.get('decide', '')} |")
        out.append("")
    since = d.get("last_generated")
    out += ["## Flight project", ""]
    out.append(f"Last generated: {since['at']} ({since['by']})" if since else "Last generated: unknown")
    for f in d["flight"]:
        if f.get("git") is False:
            out.append(f"- `{f['project']}`: not a git repository")
            continue
        out.append(f"- `{f['project']}` at {f.get('head')}; {f.get('uncommitted_files', 0)} uncommitted file(s)")
        if "commits_since_generated" in f:
            out.append(f"  - {f['commits_since_generated']} commit(s) since then"
                       + (f" ({f['files_changed']})" if f.get("files_changed") else ""))
            out += [f"    - {c}" for c in f.get("commits", [])]
    t = d["toolkit"]
    out += ["", "## Toolkit", ""]
    if t.get("pinned"):
        out.append(f"- Pinned: `{t['pinned']}` at {t.get('pinned_head')}"
                   + ("; **it has local changes**" if t.get("pinned_has_local_changes") else ""))
    out.append(f"- Running: `{t['running']}` at {t.get('running_head')}")
    if "running_is_ahead_by" in t:
        out.append(f"- The running toolkit is {t['running_is_ahead_by']} commit(s) ahead of the pin and "
                   f"{t['running_is_behind_by']} behind")
        out += [f"  - {c}" for c in t.get("commits_not_pinned", [])]
    if t.get("note"):
        out.append(f"- {t['note']}")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="drift.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("target", nargs="?", default=".",
                        help="a deployment or system manifest, or the simulation project that holds it")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    try:
        result = drift(Path(args.target))
    except WstsError as error:
        print(f"drift.py: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else render(result))
    generation = result["generation"]
    return 0 if generation.get("generates") and generation.get("up_to_date") else 1


if __name__ == "__main__":
    sys.exit(main())
