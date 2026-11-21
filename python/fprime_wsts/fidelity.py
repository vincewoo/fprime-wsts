"""What a simulated deployment or system is made of, and where it differs from flight.

`inventory(<generated dir>)` reads the reports of a generated deployment or system
(`wsts-report.json`, per element) and says, per element:

- which code is flight code (project modules, libraries, the topology and main sources),
  and which modules are built in their BUILD_TESTING variant (a stub in place of an SDK);
- which instances are replaced, by what, and why;
- what answers on each bus endpoint: a link to another element, an F´ simulator component,
  a device model of the project (and whether it is still a skeleton, and whether the
  project has hardware captures for it), a stock model of the toolkit, or nothing, and
  what flight software sees there then;
- what is missing from the dictionary, what waits on the host, how full the tables are;

and derives a list of **gaps**, each with a severity hint and the step that closes it. The
hint is the toolkit's view only: how much a gap matters depends on what the simulation is
for, which the `wsts-fidelity` skill asks.

With traffic (the summary of `fprime-wsts run --json`, or a live run), an endpoint that
flight software used and nothing answered is named as such.

The `wsts-fidelity` skill runs this through its `scripts/inventory.py`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fpp import WstsError

#: The stock models of `Wsts/Core/Models.hpp`, and what each gives flight software
STOCK_MODELS = {
    "Wsts::LoopbackByteStream": "echoes what flight software sends",
    "Wsts::ScriptedByteStream": "sends a fixed text at a fixed period, whatever flight software sends",
    "Wsts::RegisterMapI2c": "holds register values (fixed, or a signal of simulated time); no device behaviour",
    "Wsts::LoopbackSpi": "answers each transfer with zeros or an echo; no device behaviour",
    "Wsts::RegisterFile": "registers hold what was written last (or a set value); no side effects",
}

#: What flight software sees on an endpoint where nothing answers
NO_MODEL = {
    "i2c": "an address error on every transaction (counted in BusErrors)",
    "spi": "zeros on every read",
    "bytestream": "its sends accepted and nothing ever arriving",
    "gpio": "the level it wrote; inputs stay low unless forced (gpio_force)",
    "register": "all ones on every read (counted in BusErrors)",
}

SEVERITY = ("high", "medium", "low")

_SKIPPED_DIRS = {"wsts", "lib", "build-wsts-loop", "wsts-logs", ".git", "node_modules"}


def load_reports(directory: Path) -> Dict[str, dict]:
    """{element: report} of a generated system, or {deployment directory name: report} of one
    deployment. Each report gets `_dir`, the element's generated directory."""
    from .system import SYSTEM_FILE

    directory = Path(directory).resolve()
    if (directory / SYSTEM_FILE).is_file():
        description = json.loads((directory / SYSTEM_FILE).read_text())
        found = {}
        for element in description["elements"]:
            path = directory / element["directory"] / "wsts-report.json"
            if not path.is_file():
                raise WstsError(f"{path} not found: generate the system first")
            found[element["name"]] = {**json.loads(path.read_text()), "_dir": str(path.parent)}
        return found
    path = directory / "wsts-report.json"
    if not path.is_file():
        raise WstsError(f"{directory} is not a generated simulation deployment or system (no wsts-report.json or "
                        f"{SYSTEM_FILE}): generate it first, or name the generated directory")
    return {directory.name: {**json.loads(path.read_text()), "_dir": str(directory)}}


def simulation_project(report: dict) -> Optional[Path]:
    """The simulation project of an element, when it has one"""
    relative = report.get("simulation_project")
    if relative is None:
        return None
    return (Path(report["_dir"]) / relative).resolve()


def _project_sources(project: Optional[Path]) -> List[Path]:
    if project is None or not project.is_dir():
        return []
    found = []
    for path in project.rglob("*"):
        parts = path.relative_to(project).parts
        if parts and (parts[0] in _SKIPPED_DIRS or any(p.startswith("build-") for p in parts)):
            continue
        if path.suffix in (".hpp", ".h", ".cpp", ".py") and path.is_file():
            found.append(path)
    return found


def _model_source(cls: str, sources: List[Path]) -> List[Path]:
    """The project's files that define a model class (its header and source)"""
    short = cls.split("::")[-1]
    pattern = re.compile(rf"\b(class|struct)\s+{re.escape(short)}\b|\b{re.escape(short)}::")
    found = []
    for path in sources:
        if path.suffix == ".py":
            continue
        try:
            if pattern.search(path.read_text(errors="replace")):
                found.append(path)
        except OSError:
            continue
    return found


def _has_todo(paths: List[Path]) -> bool:
    return any("TODO" in path.read_text(errors="replace") for path in paths if path.is_file())


def _device_evidence(project: Optional[Path], names: List[str]) -> dict:
    """What the wsts-device-model skill left for a device: devices/<device>/spec.md and captures"""
    if project is None or not (project / "devices").is_dir():
        return {}
    wanted = {re.sub(r"model$", "", n.lower().split("::")[-1]) for n in names if n}
    for directory in sorted((project / "devices").iterdir()):
        if not directory.is_dir():
            continue
        if re.sub(r"[-_ ]", "", directory.name.lower()) not in {re.sub(r"[-_ ]", "", w) for w in wanted}:
            continue
        captures = sorted((directory / "captures").glob("*.jsonl")) if (directory / "captures").is_dir() else []
        evidence = {"device_dir": str(directory.relative_to(project))}
        if (directory / "spec.md").is_file():
            evidence["spec"] = str((directory / "spec.md").relative_to(project))
        if captures:
            evidence["captures"] = len(captures)
        return evidence
    return {}


def _endpoints(report: dict, project: Optional[Path], sources: List[Path]) -> List[dict]:
    """Every bus endpoint of an element, and what answers there"""
    endpoints: Dict[str, dict] = {}

    def entry(name: str) -> dict:
        return endpoints.setdefault(name, {"endpoint": name, "kind": None, "served_by": [], "flight_instances": []})

    for sub in report.get("substitutions", []):
        if not sub.get("endpoint"):
            continue
        kind = sub.get("endpoint_kind")
        if kind == "other":  # a tick source: not a device
            continue
        e = entry(sub["endpoint"])
        e["kind"] = e["kind"] or kind
        e["flight_instances"].append(sub["instance"])
        e["replaced"] = f"{sub['flight_component']} by {sub['surrogate']}"
    for block in report.get("register_blocks", []):
        e = entry(block["endpoint"])
        e["kind"] = "register"
        e["flight_drivers"] = block.get("drivers", [])
    for link in report.get("links", []):
        e = entry(link["endpoint"])
        e["kind"] = e["kind"] or "bytestream"
        e["served_by"].append({"what": "link", "fidelity": "link", "peer": link.get("peer"), "link": link["link"]})
    for device in report.get("simulators", {}).get("devices", []):
        e = entry(device["endpoint"])
        e["kind"] = e["kind"] or device.get("kind")
        e["served_by"].append({"what": device["instance"], "component": device["component"],
                               "fidelity": "simulator component"})
    for model in report.get("models", []):
        e = entry(model["endpoint"])
        e["kind"] = e["kind"] or model.get("kind")
        served: Dict[str, Any] = {"what": model["name"], "class": model["class"]}
        if model.get("kind") == "i2c":
            served["address"] = hex(int(model.get("address", 0)))
        if model["class"] in STOCK_MODELS:
            served["fidelity"] = "stock model"
            served["behaviour"] = STOCK_MODELS[model["class"]]
        else:
            files = _model_source(model["class"], sources)
            if files and _has_todo(files):
                served["fidelity"] = "project model (skeleton: has TODOs)"
            else:
                served["fidelity"] = "project model"
            if files and project is not None:
                served["source"] = [str(f.relative_to(project)) for f in files]
            evidence = _device_evidence(project, [model["name"], model["class"]])
            if evidence:
                served["evidence"] = evidence
        e["served_by"].append(served)
    python_models = report.get("run", {}).get("models", [])
    for e in endpoints.values():
        if not e["served_by"]:
            e["fidelity"] = "python model?" if python_models else "nothing"
            if not python_models:
                e["flight_sees"] = NO_MODEL.get(e["kind"] or "", "nothing answers")
        else:
            order = ["link", "simulator component", "project model", "project model (skeleton: has TODOs)",
                     "stock model"]
            e["fidelity"] = min((s["fidelity"] for s in e["served_by"]), key=order.index)
    return sorted(endpoints.values(), key=lambda e: e["endpoint"])


def _gap(element: str, severity: str, area: str, subject: str, finding: str, step: str) -> dict:
    return {"element": element, "severity": severity, "area": area, "subject": subject, "finding": finding,
            "next_step": step}


def element_inventory(name: str, report: dict, traffic: Optional[Dict[str, dict]] = None) -> dict:
    project = simulation_project(report)
    sources = _project_sources(project)
    endpoints = _endpoints(report, project, sources)
    traffic = traffic or {}
    for e in endpoints:
        t = traffic.get(e["endpoint"])
        if t is not None:
            e["traffic"] = {k: t.get(k, 0) for k in ("transactions", "errors", "unanswered", "fault_hits")}
            addresses = t.get("addresses") or {}
            silent = [address for address, a in addresses.items() if a.get("unanswered")]
            if silent:
                e["traffic"]["unanswered_at"] = sorted(silent)
            # Each I2C device: whether flight software addressed it at all
            for served in e["served_by"]:
                if served.get("address") is not None and e["kind"] == "i2c":
                    served["transactions_in_run"] = next(
                        (a.get("transactions", 0) for address, a in addresses.items()
                         if int(address, 16) == int(served["address"], 16)), 0)
    dictionary = report.get("dictionary", {})
    removed = dictionary.get("removed", {})
    time = report.get("time", {})
    inventory = {
        "element": name,
        "topology": report.get("topology"),
        "flight_toolchain": report.get("flight_toolchain"),
        "flight_code": {
            "project_modules": report.get("project_modules", []),
            "libraries": report.get("libraries", {}).get("kept", []),
            "reused_sources": report.get("reused_sources", {}),
            "stub_variants": report.get("stub_variants", []),
        },
        "replaced": [{k: s.get(k) for k in ("instance", "flight_component", "surrogate", "chosen_by", "endpoint")}
                     for s in report.get("substitutions", [])],
        "kept_hardware_instances": report.get("kept_hardware_instances", []),
        "endpoints": endpoints,
        "simulator_components": [i["instance"] + " (" + i["component"] + ")"
                                 for i in report.get("simulators", {}).get("instances", [])],
        "python_models": report.get("run", {}).get("models", []),
        "dictionary": {
            "identical_to_flight": dictionary.get("identical_to_flight"),
            "flight_ids_unchanged": dictionary.get("flight_ids_unchanged"),
            "removed": removed,
        },
        "time": {k: time.get(k) for k in ("mode", "tick_source", "time_source")},
        "waits_on_host": time.get("waits_on_host", []),
        "capacity": report.get("capacity", {}),
        "warnings": report.get("warnings", []),
        "fidelity_notes": report.get("fidelity_notes", []),
    }
    inventory["gaps"] = _gaps(name, inventory, report)
    return inventory


def _gaps(name: str, inv: dict, report: dict) -> List[dict]:
    gaps = []
    for instance in inv["kept_hardware_instances"]:
        label = instance if isinstance(instance, str) else instance.get("instance", str(instance))
        gaps.append(_gap(name, "high", "hardware", label,
                         "a hardware driver is kept: on the workstation it reaches the host's devices, or fails",
                         "substitute it (catalog or `substitute:` in the manifest), or confirm that it is inert here"))
    for e in inv["endpoints"]:
        used = e.get("traffic", {}).get("transactions")
        unanswered = e.get("traffic", {}).get("unanswered", 0)
        if e["fidelity"] == "nothing":
            severity = ("high" if (unanswered or (used and e["kind"] in ("i2c", "register"))) else
                        "low" if used == 0 or e["kind"] == "gpio" else "medium")
            gaps.append(_gap(name, severity, "device", e["endpoint"],
                             f"no model on this {e['kind'] or ''} endpoint: flight software sees {e['flight_sees']}"
                             + (f"; {unanswered} transaction(s) went unanswered in the run" if unanswered else "")
                             + ("; flight software did not use it in the run" if used == 0 else ""),
                             "drive it from tests (gpio_force) or a model where flight reads it"
                             if e["kind"] == "gpio" else
                             "a stock model if fixed values are enough (wsts-configure, decisions §8), else a "
                             "device model from the datasheet (wsts-device-model skill)"))
        # Each device on the endpoint (an I2C bus has several), judged by itself
        for served in e["served_by"]:
            subject = e["endpoint"] + (f" @{served['address']}" if served.get("address") else "")
            unused = ("; flight software did not address it in the run"
                      if served.get("transactions_in_run") == 0 else "")
            if served["fidelity"].startswith("project model (skeleton"):
                gaps.append(_gap(name, "medium", "device", subject,
                                 f"{served['class']} is a skeleton (`new-model`): it answers with a settable value, "
                                 f"and its TODOs are where the device's behaviour goes{unused}",
                                 "write its behaviour from the datasheet (wsts-device-model skill)"))
            elif served["fidelity"] == "stock model":
                gaps.append(_gap(name, "low", "device", subject,
                                 f"stock model {served['class']}: {served['behaviour']}{unused}",
                                 "enough for start-up and plumbing; a device model where the device's behaviour "
                                 "(commands, state, errors) is under test (wsts-device-model skill)"))
            elif served["fidelity"] == "project model" and not served.get("evidence", {}).get("captures"):
                gaps.append(_gap(name, "low", "device", subject,
                                 f"model {served['class']} has not been checked against the device (no captures "
                                 f"under devices/){unused}",
                                 "probe a bench unit and run the conformance check (wsts-device-model skill)"))
        if unanswered and e["fidelity"] != "nothing":
            at = ", ".join(e["traffic"].get("unanswered_at", [])) or "the endpoint"
            gaps.append(_gap(name, "high", "device", e["endpoint"],
                             f"{unanswered} transaction(s) at {at} went unanswered: flight software talks to a "
                             "device that no model on this endpoint answers for (a wrong I2C address, or a second "
                             "device on the bus)",
                             "check the address in flight code, and add a model at that address"))
    for module in inv["flight_code"]["stub_variants"]:
        gaps.append(_gap(name, "medium", "build", module,
                         "built in its BUILD_TESTING variant: the stub in place of the SDK runs, not the flight code "
                         "that calls the SDK",
                         "review what the stub returns; a surrogate of the component if its behaviour is under test"))
    removed = inv["dictionary"]["removed"]
    lost = {kind: names for kind, names in removed.items() if names}
    if lost:
        count = sum(len(v) for v in lost.values())
        sample = ", ".join(n for names in lost.values() for n in names[:3])
        gaps.append(_gap(name, "medium", "dictionary", "replaced drivers",
                         f"{count} dictionary item(s) of replaced drivers are missing ({sample}, ...): ground "
                         "procedures and tests that use them fail here",
                         "a mirror surrogate where ground procedures need them (docs/surrogate-guide.md)"))
    if not inv["dictionary"].get("flight_ids_unchanged", True):
        gaps.append(_gap(name, "high", "dictionary", "IDs", "flight IDs moved: the flight dictionary does not "
                         "decode this deployment's telemetry", "report it: the toolkit must not move flight IDs"))
    stepped = inv["time"].get("mode") == "stepped"
    for wait in inv["waits_on_host"]:
        subject = ", ".join(wait.get("instances") or wait.get("components") or [])
        gaps.append(_gap(name, "high" if stepped else "low", "time", subject,
                         f"waits on the host ({', '.join(wait.get('kinds', []))}): {wait.get('known') or wait.get('effect')}",
                         "matters in stepped time only: see docs/stepped-time.md, and the report's Time section"))
    for table, use in inv["capacity"].items():
        size, used = use.get("size") or 0, use.get("used") or 0
        if size and used / size >= 0.8:
            gaps.append(_gap(name, "medium", "capacity", table, f"{used} of {size} used",
                             f"raise it in the simulation's configuration ({use.get('source') or use.get('constant')})"))
    for warning in inv["warnings"]:
        text = warning if isinstance(warning, str) else json.dumps(warning)
        gaps.append(_gap(name, "medium", "generator", "warning", text, "settle it (wsts-configure troubleshooting)"))
    order = {s: i for i, s in enumerate(SEVERITY)}
    return sorted(gaps, key=lambda g: order[g["severity"]])


def inventory(directory: Path, traffic: Optional[Dict[str, Dict[str, dict]]] = None) -> dict:
    """The inventory of a generated deployment or system. `traffic`: {element: bus traffic}
    (`bus_stats` of each element), from a run summary or a live run."""
    reports = load_reports(directory)
    elements = {name: element_inventory(name, report, (traffic or {}).get(name)) for name, report in reports.items()}
    gaps = sorted((g for e in elements.values() for g in e["gaps"]), key=lambda g: SEVERITY.index(g["severity"]))
    return {"directory": str(Path(directory).resolve()), "elements": elements, "gaps": gaps,
            "with_traffic": bool(traffic)}


def traffic_from_summary(path: Path) -> Dict[str, Dict[str, dict]]:
    """Bus traffic per element from the summary `fprime-wsts run --json` printed"""
    summary = json.loads(Path(path).read_text())
    if "elements" in summary:
        return {name: (e.get("final_status") or {}).get("bus_traffic", {}) for name, e in summary["elements"].items()}
    name = Path(summary.get("logs", "")).parent.name or summary.get("deployment", "deployment")
    return {name: (summary.get("final_status") or {}).get("bus_traffic", {})}


def traffic_live(directory: Path) -> Dict[str, Dict[str, dict]]:
    """Bus traffic per element of the simulation running from a generated directory"""
    from .bridge import BridgeError, WstsBridge, bus_traffic
    from .cli import _bridges

    found = {}
    for name, address in _bridges(Path(directory)).items():
        try:
            with WstsBridge(address, connect_timeout=2.0) as bridge:
                found[name] = bus_traffic(bridge.request("bus_stats"))
        except BridgeError as error:
            raise WstsError(f"{name} ({address}): {error}: is the simulation running?") from None
    return found


def render(inv: dict) -> str:
    """The inventory as Markdown, for a person"""
    out = [f"# Fidelity inventory: {inv['directory']}", ""]
    if not inv["with_traffic"]:
        out += ["_Without traffic: endpoints are judged by configuration only. Give a run summary "
                "(`--summary`) or a running simulation (`--live`) to see which endpoints flight software uses._", ""]
    for name, e in inv["elements"].items():
        out += [f"## {name} ({e['topology']}, flight toolchain {e['flight_toolchain']})", ""]
        code = e["flight_code"]
        out += [f"- Flight code: {len(code['project_modules'])} project module(s), libraries "
                f"{', '.join(code['libraries']) or 'none'}; topology and main: "
                f"{', '.join(sum(code['reused_sources'].values(), [])) or 'generated'}"]
        if code["stub_variants"]:
            out += [f"- Built with their BUILD_TESTING stub: {', '.join(code['stub_variants'])}"]
        out += [f"- Time: {e['time'].get('mode')}, tick source {e['time'].get('tick_source')}"]
        if e["kept_hardware_instances"]:
            out += [f"- **Hardware drivers kept**: {', '.join(map(str, e['kept_hardware_instances']))}"]
        out += ["", "| Endpoint | Kind | Answered by | Fidelity | Traffic |", "|---|---|---|---|---|"]
        for ep in e["endpoints"]:
            served = "; ".join(
                (s.get("class") or s.get("component") or s.get("what"))
                + (f" @{s['address']}" if s.get("address") else "")
                + (f" → {s['peer']}" if s.get("peer") else "")
                for s in ep["served_by"]) or f"nothing: flight sees {ep.get('flight_sees', '?')}"
            t = ep.get("traffic")
            traffic = (f"{t['transactions']} tx, {t['errors']} err, {t['unanswered']} unanswered" if t else "")
            out.append(f"| `{ep['endpoint']}` | {ep['kind'] or '?'} | {served} | {ep['fidelity']} | {traffic} |")
        removed = {k: len(v) for k, v in e["dictionary"]["removed"].items() if v}
        out += ["", f"- Dictionary: {'identical to flight' if e['dictionary']['identical_to_flight'] else 'differs'}"
                + (f"; removed {', '.join(f'{n} {k}' for k, n in removed.items())}" if removed else "")]
        if e["simulator_components"]:
            out += [f"- Simulator components: {', '.join(e['simulator_components'])}"]
        if e["python_models"]:
            out += [f"- Python models (run.models): {', '.join(e['python_models'])}"]
        out.append("")
    out += ["## Gaps", "", "| Severity | Element | Area | Subject | Finding | Next step |", "|---|---|---|---|---|---|"]
    for g in inv["gaps"]:
        out.append(f"| {g['severity']} | {g['element']} | {g['area']} | `{g['subject']}` | {g['finding']} | "
                   f"{g['next_step']} |")
    if not inv["gaps"]:
        out.append("| | | | | none found from the reports | |")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="inventory.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", help="generated simulation deployment or system directory")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--summary", help="the JSON summary of `fprime-wsts run --json`, for each endpoint's traffic")
    source.add_argument("--live", action="store_true", help="read each endpoint's traffic from the running simulation")
    parser.add_argument("--json", action="store_true", help="print the inventory as JSON")
    args = parser.parse_args(argv)
    try:
        traffic = (traffic_from_summary(Path(args.summary)) if args.summary
                   else traffic_live(Path(args.directory)) if args.live else None)
        result = inventory(Path(args.directory), traffic)
    except WstsError as error:
        print(f"inventory.py: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
