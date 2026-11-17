"""Command line interface: fprime-wsts"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .fpp import WstsError


def cmd_generate(args: argparse.Namespace, hint: bool = True) -> int:
    from .generate import generate
    from .manifest import load_manifest
    from .system import find_project, generate_system, is_system_manifest, load_system

    manifest_path = Path(args.manifest).resolve()
    output = Path(args.output) if args.output else None
    notices = []
    if is_system_manifest(manifest_path):
        system = load_system(manifest_path)
        result = generate_system(system, output, check=args.check,
                                 project=Path(args.project).resolve() if args.project else None)
        reports = {name: element.report for name, element in result.elements.items()}
        outputs = {name: element.output for name, element in result.elements.items()}
        notices = result.notices
        if args.json:
            print(json.dumps({"output": str(result.output), "changed": result.changed, "system": result.description,
                              "reports": reports}, indent=2))
        else:
            for name, report in reports.items():
                print(f"element {name} ({report['topology']}):")
                _print_report(report)
                _host_summary(report, outputs[name], write=not args.check)
            for link in result.description["links"]:
                print(f"link {link['name']}: {link['a']['element']}.{link['a']['endpoint']} <-> "
                      f"{link['b']['element']}.{link['b']['endpoint']}")
        options = {name: report.get("configure_options", []) for name, report in reports.items()}
        if any(options.values()):
            by_hand = "cd {0}/<element> && fprime-util generate <options> && fprime-util build\n" + \
                "\n".join(f"  options for {name}: {' '.join(values)}" for name, values in options.items() if values)
        else:
            by_hand = "cd {0}/<element> && fprime-util generate && fprime-util build"
        what = "Simulated system"
    else:
        manifest = load_manifest(manifest_path)
        project = _project_of(args, manifest, manifest_path)
        result = generate(project, manifest, output, check=args.check)
        notices = result.notices
        if args.json:
            print(json.dumps({"output": str(result.output), "changed": result.changed, "report": result.report}, indent=2))
        else:
            _print_report(result.report)
            _host_summary(result.report, result.output, write=not args.check)
        options = "".join(f" {option}" for option in result.report.get("configure_options", []))
        what, by_hand = "Simulated deployment", f"cd {{0}} && fprime-util generate{options} && fprime-util build"
    args.result_output = result.output  # for `fprime-wsts build <manifest>`
    for notice in notices:
        print(f"note: {notice}", file=sys.stderr)
    if args.check:
        if result.changed:
            print(f"{result.output} is out of date:", file=sys.stderr)
            for name in result.changed:
                print(f"  {name}", file=sys.stderr)
            return 1
        if not args.json:
            print(f"{result.output} is up to date")
        return 0
    if not args.json:
        print(f"{what} written to {result.output}")
        if hint:
            print(f"Build it with: fprime-wsts build {result.output}")
            print(f"  (by hand: {by_hand.format(result.output)})")
    return 0


def _project_of(args: argparse.Namespace, manifest, manifest_path: Path) -> Path:
    from .system import find_project

    if getattr(args, "project", None):
        return Path(args.project).resolve()
    if manifest.project is not None:
        return manifest.project_root()
    return find_project(manifest_path.parent)


def _host_summary(report: dict, output: Path, write: bool) -> None:
    """Ask this host for what the build needs, say what is missing, and leave the answer beside the report"""
    from . import hostcheck

    requirements = report.get("host_requirements")
    if requirements is None:
        return
    result = hostcheck.probe(requirements, output)
    if write and output.is_dir():
        hostcheck.write(output, result, requirements)
    for line in hostcheck.summary(result):
        print(f"  host: {line}")
    if result["missing"]:
        print(f"  host: details and what to do: fprime-wsts check-host {output}")


def cmd_check_host(args: argparse.Namespace) -> int:
    """List what the host must provide for the build, and whether it does"""
    from . import hostcheck
    from .generate import generate
    from .manifest import load_manifest
    from .system import generate_system, is_system_manifest, load_system

    target = Path(args.target).resolve()
    checks = {}  # name -> (requirements, directory its paths are relative to)
    if target.is_dir():
        system = target / "wsts-system.json"
        if system.is_file():
            for element in json.loads(system.read_text())["elements"]:
                directory = target / element["directory"]
                checks[element["name"]] = (hostcheck.load_requirements(directory), directory)
        else:
            checks[target.name] = (hostcheck.load_requirements(target), target)
    elif is_system_manifest(target):
        result = generate_system(load_system(target), None, check=True,
                                 project=Path(args.project).resolve() if args.project else None)
        for name, element in result.elements.items():
            checks[name] = (element.report["host_requirements"], element.output)
    else:
        manifest = load_manifest(target)
        result = generate(_project_of(args, manifest, target), manifest, None, check=True)
        checks[result.output.name] = (result.report["host_requirements"], result.output)
    results = {}
    for name, (requirements, base) in checks.items():
        results[name] = hostcheck.probe(requirements, base)
        if target.is_dir() and base.is_dir():
            hostcheck.write(base, results[name], requirements)
        if not args.json:
            if len(checks) > 1:
                print(f"element {name}:")
            print(hostcheck.render(results[name], requirements, base), end="" if name == list(checks)[-1] else "\n")
    if args.json:
        print(json.dumps(results if len(results) > 1 else next(iter(results.values())), indent=2))
    return 0 if all(result["ok"] for result in results.values()) else 1


def format_warning(text: str, width: int = 0, indent: str = "  ") -> str:
    """A warning of the generator laid out for a terminal: wrapped, and with the ways out it
    lists ("Either a; or b; or c.") one to a line. The report keeps it as one sentence."""
    import shutil
    import textwrap

    width = width or max(60, min(shutil.get_terminal_size((100, 24)).columns, 110))
    head, either, rest = text.partition(" Either ")
    options = [option.strip() for option in rest.rstrip(".").split("; or ")] if either and "; or " in rest else []
    if not options:
        head = text
    lines = textwrap.wrap(f"warning: {head}", width, initial_indent=indent, subsequent_indent=indent + "  ",
                          break_long_words=False, break_on_hyphens=False)
    if options:
        lines.append(f"{indent}  Ways out:")
        for option in options:
            lines += textwrap.wrap(option[0].upper() + option[1:], width, initial_indent=f"{indent}    - ",
                                   subsequent_indent=f"{indent}      ", break_long_words=False, break_on_hyphens=False)
    return "\n".join(lines)


def _print_report(report: dict) -> None:
    for sub in report["substitutions"]:
        endpoint = f"endpoint '{sub['endpoint']}'" if sub["endpoint"] is not None else "no bus endpoint"
        print(f"  {sub['instance']}: {sub['flight_component']} -> {sub['surrogate']} [{sub['chosen_by']}] {endpoint}")
    for warning in report["warnings"]:
        print(format_warning(warning))
    dictionary = report["dictionary"]
    removed = sum(len(v) for v in dictionary["removed"].values())
    added = sum(len(v) for v in dictionary["added"].values())
    print(f"  dictionary: {removed} flight item(s) removed, {added} WSTS item(s) added, flight IDs unchanged")


def cmd_report(args: argparse.Namespace) -> int:
    path = Path(args.directory) / "wsts-report.json"
    if not path.is_file():
        raise WstsError(f"{path} not found: is this a generated simulation deployment?")
    host = path.parent / "wsts-host.md"
    if args.json:
        print(path.read_text())
    else:
        print((path.parent / "wsts-report.md").read_text())
        if host.is_file():
            # What `generate` or `check-host` found on this machine: its compiler, and what it lacks
            print(host.read_text())
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .build import build_cli

    return build_cli(args)


def cmd_survey(args: argparse.Namespace) -> int:
    from .survey import survey_cli

    return survey_cli(args)


def cmd_init(args: argparse.Namespace) -> int:
    from .survey import init_cli

    return init_cli(args)


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import run_cli

    return run_cli(args)


def _bridges(directory: Path, element=None) -> dict:
    """{name: bridge address} of the simulation running from a generated directory: every
    element of a system (or the one asked for), or the one deployment"""
    from .bridge import WstsSystem, bridge_address  # noqa: F401
    from .runner import read_run_file
    from .system import SYSTEM_FILE

    directory = directory.resolve()
    if (directory / SYSTEM_FILE).is_file():
        description = json.loads((directory / SYSTEM_FILE).read_text())
        running = (read_run_file(directory) or {}).get("elements", {})
        found = {e["name"]: running.get(e["name"], e["bridge"]) for e in description["elements"]}
        if element is not None:
            if element not in found:
                raise WstsError(f"--element {element}: not an element of {directory} (elements: {', '.join(found)})")
            found = {element: found[element]}
    elif (directory / "wsts-report.json").is_file():
        if element is not None:
            raise WstsError(f"--element {element}: {directory} is a single deployment")
        found = {directory.name: bridge_address(directory)}
    else:
        raise WstsError(f"{directory} is not a generated simulation deployment or system (no wsts-report.json or "
                        f"{SYSTEM_FILE})")
    off = [name for name, address in found.items() if address == "none"]
    if off:
        raise WstsError(f"the bridge of {', '.join(off)} is off (bridge: none in the manifest)")
    return found


def _field(text: str):
    """NAME=VALUE of an action. The value is a JSON value where it reads as one (3, true,
    "0x10"), else text, in which \\n, \\r, \\t and \\xNN stand for those characters."""
    name, separator, value = text.partition("=")
    if not separator or not name.isidentifier():
        raise WstsError(f"'{text}': expected FIELD=VALUE, for example endpoint=i2cBus")
    try:
        return name, json.loads(value)
    except ValueError:
        pass
    try:
        return name, value.encode("latin-1", "backslashreplace").decode("unicode_escape")
    except UnicodeDecodeError:
        return name, value


# Actions that mean something for every element of a system at once
SYSTEM_WIDE = ("pause", "resume", "set_rate", "status", "fault_clear")


def _send(found: dict, op: str, fields: dict, timeout: float) -> dict:
    from .bridge import BridgeError, WstsBridge

    answers = {}
    for name, address in found.items():
        try:
            with WstsBridge(address, connect_timeout=timeout) as bridge:
                answer = bridge.status() if op == "status" and not fields else bridge.request(op, **fields)
        except BridgeError as error:
            raise WstsError(f"{name} ({address}): {error}"
                            + ("" if "connect" not in str(error) else
                               ": is the simulation running? Start it with `fprime-wsts run`")) from None
        answer.pop("id", None)
        answers[name] = answer
    return answers


def cmd_ctl(args: argparse.Namespace) -> int:
    """Send one action to a running simulation through its bridge"""
    fields = dict(_field(text) for text in args.fields)
    found = _bridges(Path(args.directory), args.element)
    if len(found) > 1 and args.op not in SYSTEM_WIDE:
        raise WstsError(f"'{args.op}' goes to one element of a system: add --element with one of {', '.join(found)} "
                        f"(only {', '.join(SYSTEM_WIDE)} go to every element)")
    answers = _send(found, args.op, fields, args.timeout)
    print(json.dumps(answers if len(answers) > 1 else next(iter(answers.values())), indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Where a running simulation is: tick, run state, faults, bus counters"""
    answers = _send(_bridges(Path(args.directory), args.element), "status", {}, args.timeout)
    if args.json:
        print(json.dumps(answers if len(answers) > 1 else next(iter(answers.values())), indent=2))
        return 0
    for name, status in answers.items():
        state = "paused" if status.get("paused") else "running"
        links = (f", links {status['links']} started, {status['links_up']} up" if status.get("links") else "")
        mode = ", lockstep" if status.get("lockstep") else ", stepped" if status.get("stepped") else ""
        print(f"{name}: {state} at tick {status.get('tick')} ({status.get('sim_seconds', 0):g} s){mode}, "
              f"rate {status.get('rate')}{links}")
        if status.get("stuck"):
            print(f"  stuck: {status['stuck']}")
        print(f"  bus: {status.get('transactions')} transaction(s), {status.get('bytes')} byte(s), "
              f"{status.get('errors')} error(s); faults: {status.get('active_faults')} active, "
              f"{status.get('fault_hits')} hit(s)")
        print(f"  scenario: {status.get('scenario_state')}"
              + (f" (step {status.get('scenario_step')} of {status.get('scenario_steps')})"
                 if status.get("scenario_steps") else "")
              + f"; recording: {'yes' if status.get('recording') else 'no'}")
        print(f"  endpoints: {', '.join(status.get('endpoints', [])) or 'none'}")
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    from .scaffold import scaffold_cli

    return scaffold_cli(args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fprime-wsts", description="F Prime Workstation Test Set (WSTS) toolkit")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    survey = commands.add_parser(
        "survey", help="list what in the deployments of a flight project touches hardware, and what the generator "
                       "would do with each; writes nothing")
    survey.add_argument("dirs", nargs="*", help="flight project roots, or directories that hold them (default: the current directory)")
    survey.add_argument("--deployment", action="append", default=[], metavar="NAME",
                        help="only deployments with this directory name (repeatable)")
    survey.add_argument("--no-trial", action="store_true", help="skip the trial generation with a minimal manifest")
    survey.add_argument("--json", action="store_true", help="print the survey as JSON")
    survey.set_defaults(handler=cmd_survey)

    init = commands.add_parser(
        "init", help="start a simulation project for a flight project: survey it, then write a starter manifest per "
                     "deployment (and a system manifest for several); never overwrites")
    init.add_argument("project", help="flight F Prime project root")
    init.add_argument("--output", help="simulation project directory (default: <project>-sim beside the flight project)")
    init.add_argument("--deployment", action="append", default=[], metavar="NAME",
                      help="only deployments with this directory name (repeatable; default: all)")
    init.add_argument("--no-trial", action="store_true", help="skip the trial generation with a minimal manifest")
    init.add_argument("--json", action="store_true", help="print the survey and what was written as JSON")
    init.set_defaults(handler=cmd_init)

    generate = commands.add_parser("generate", help="create a simulated deployment from a flight deployment, or a simulated system from several")
    generate.add_argument("manifest", help="deployment manifest (wsts.yaml), or a system manifest naming several")
    generate.add_argument("--project", help="flight F Prime project root (default: the manifest's 'project', else found from the manifest location)")
    generate.add_argument("--output", help="output directory (default: manifest 'output', else wsts/<deployment> in the simulation project, or in the flight project)")
    generate.add_argument("--check", action="store_true", help="do not write; fail if the output is out of date")
    generate.add_argument("--json", action="store_true", help="print the result as JSON")
    generate.set_defaults(handler=cmd_generate)

    check_host = commands.add_parser(
        "check-host", help="list what the host must provide for the simulated build (headers, libraries, packages, "
                           "SDK files, compiler), and whether it does: exit status 1 when something is missing")
    check_host.add_argument("target", help="a generated simulation deployment or system directory, or a deployment or system manifest")
    check_host.add_argument("--project", help="flight F Prime project root, for a manifest (default: as for generate)")
    check_host.add_argument("--json", action="store_true", help="print the result as JSON")
    check_host.set_defaults(handler=cmd_check_host)

    build = commands.add_parser(
        "build", help="configure and build a generated simulated deployment, or every element of a generated system; "
                      "given a manifest, generate first. Exit status 1 when a build fails")
    build.add_argument("target", nargs="?", default=".",
                       help="a generated simulation deployment or system directory, or a deployment or system manifest "
                            "(default: the current directory)")
    build.add_argument("-j", "--jobs", type=int, help="parallel build jobs (default: the number of processors)")
    build.add_argument("--fresh", action="store_true",
                       help="configure again from nothing, removing the build directory (fprime-util generate -f)")
    build.add_argument("--element", action="append", metavar="NAME", help="build only this element of a system (repeatable)")
    build.add_argument("-D", dest="define", action="append", metavar="NAME=VALUE",
                       help="CMake option for the configure step, e.g. -D CMAKE_BUILD_TYPE=Debug (repeatable); "
                            "a change configures again")
    build.add_argument("--keep-going", action="store_true", help="build the remaining elements after one fails")
    build.add_argument("--project", help="flight F Prime project root, for a manifest (default: as for generate)")
    build.add_argument("--json", action="store_true",
                       help="print a JSON summary instead of the build output, which goes to the log")
    build.set_defaults(handler=cmd_build)

    report = commands.add_parser("report", help="show the fidelity report of a generated deployment")
    report.add_argument("directory", nargs="?", default=".")
    report.add_argument("--json", action="store_true")
    report.set_defaults(handler=cmd_report)

    run = commands.add_parser("run", help="run a built simulated deployment or system, optionally with the GDS")
    run.add_argument("directory", nargs="?", default=".", help="generated simulation deployment directory, or generated system directory")
    run.add_argument("--gds", action="store_true", help="also start a headless fprime-gds")
    run.add_argument("--gds-port", type=int, default=50000, help="port the deployment connects to (default 50000)")
    run.add_argument("--gds-gui", action="store_true",
                     help="start fprime-gds with its browser GUI (implies --gds); the URL is printed")
    run.add_argument("--gds-gui-port", type=int, default=5000, help="port of the GDS browser GUI (default 5000)")
    run.add_argument("--gds-gui-addr", default="127.0.0.1",
                     help="address the GDS browser GUI is served on (default 127.0.0.1; use 0.0.0.0 to reach it from another machine)")
    run.add_argument("--auto-ports", action="store_true",
                     help="use free ports for the bridge(s), the GDS link and the GDS browser GUI in place of the fixed "
                          "ones, so that several runs fit on one machine; the addresses are printed, are in --json, and "
                          "are in <log directory>/wsts-run.json while the run is up")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--stepped", dest="stepped", action="store_true", default=None,
                      help="stepped time (experimental): a tick is taken when flight software has finished the last "
                           "one, as fast as the host computes; --duration then counts simulated seconds. A system "
                           "runs in lockstep: its elements are stepped together on one time base. Default: time.mode "
                           "of the manifest (of any element's, for a system)")
    mode.add_argument("--realtime", dest="stepped", action="store_false",
                      help="real time, whatever the manifest says: ticks follow the wall clock")
    run.add_argument("--bridge", help="bridge address override, e.g. tcp:50100")
    run.add_argument("--scenario", action="append", metavar="[ELEMENT=]FILE",
                     help="scenario file to play from start-up; for a system, <element>=<file>, once per element, "
                          "and in lockstep a plain <file> is a system scenario (actions with 'time' in simulated "
                          "seconds and the 'element' or 'link' they are for)")
    run.add_argument("--scenario-mode", choices=["stimulus", "responses"], default="stimulus")
    run.add_argument("--record", action="append", metavar="[ELEMENT=]FILE",
                     help="record bus traffic and actions to this file; for a system, <element>=<file> for one "
                          "element, or a plain file to record every element to <stem>.<element>.<suffix>")
    run.add_argument("--record-merged", metavar="FILE",
                     help="record every element of a system to one file, each line tagged with its element; "
                          "replay it with --scenario-merged")
    run.add_argument("--scenario-merged", metavar="FILE",
                     help="play a merged recording (from --record-merged) on a system: its lines are split back per "
                          "element, and each element plays its own from start-up")
    run.add_argument("--duration", type=float, help="stop after this many seconds (default: run until interrupted)")
    run.add_argument("--log-dir", help="directory for logs (default: <directory>/wsts-logs)")
    run.add_argument("--files-dir", help="directory for relative scenario/recording paths (default: the log directory)")
    run.add_argument("--env", action="append", metavar="NAME=VALUE",
                     help="environment variable for the simulated deployment, in addition to the manifest's run.env "
                          "(repeatable; for a system, <element>.NAME=VALUE sets it for one element)")
    run.add_argument("--json", action="store_true", help="print a JSON summary when the run ends")
    run.set_defaults(handler=cmd_run)

    status = commands.add_parser("status", help="show the state of a running simulation: tick, run state, faults, bus counters")
    status.add_argument("directory", nargs="?", default=".", help="generated simulation deployment or system directory")
    status.add_argument("--element", help="one element of a system (default: all)")
    status.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for the bridge (default 2)")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    ctl = commands.add_parser(
        "ctl", help="send one action to a running simulation through its bridge, e.g. "
                    "`fprime-wsts ctl DIR fault_set endpoint=i2cBus kind=error arg=3 count=5`",
        description="Send one action to a running simulation through its bridge and print the answer. The actions "
                    "and their fields are those of scenario files (docs/reference.md, 'Actions'): pause, resume, "
                    "set_rate, fault_set, fault_clear, inject_bytes, gpio_force, gpio_get, i2c_reg_set, reg_set, "
                    "irq_raise, model_set, record_start, record_stop, scenario_load, scenario_start, scenario_stop, "
                    "log, status.")
    ctl.add_argument("directory", help="generated simulation deployment or system directory")
    ctl.add_argument("op", help="the action, e.g. fault_set")
    ctl.add_argument("fields", nargs="*", metavar="FIELD=VALUE",
                     help="fields of the action, e.g. endpoint=i2cBus kind=error arg=3; in text, \\n is a newline")
    ctl.add_argument("--element", help="the element of a system the action is for")
    ctl.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for the bridge (default 2)")
    ctl.set_defaults(handler=cmd_ctl)

    scaffold = commands.add_parser("scaffold", help="create a surrogate component that mirrors a flight component")
    scaffold.add_argument("component", help="FPP name of the component to mirror, e.g. Drv.LinuxSpiDriver")
    scaffold.add_argument("--project", default=".", help="F Prime project root")
    scaffold.add_argument("--name", help="FPP name of the new surrogate (default: <Module>.Sim<Component>)")
    scaffold.add_argument("--output", required=True, help="directory to create the surrogate in")
    scaffold.set_defaults(handler=cmd_scaffold)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except WstsError as error:
        print(f"fprime-wsts: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
