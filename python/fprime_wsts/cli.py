"""Command line interface: fprime-wsts"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .fpp import WstsError


def cmd_generate(args: argparse.Namespace) -> int:
    from .generate import generate
    from .manifest import load_manifest
    from .system import find_project, generate_system, is_system_manifest, load_system

    manifest_path = Path(args.manifest).resolve()
    output = Path(args.output) if args.output else None
    if is_system_manifest(manifest_path):
        system = load_system(manifest_path)
        result = generate_system(system, output, check=args.check,
                                 project=Path(args.project).resolve() if args.project else None)
        reports = {name: element.report for name, element in result.elements.items()}
        if args.json:
            print(json.dumps({"output": str(result.output), "changed": result.changed, "system": result.description,
                              "reports": reports}, indent=2))
        else:
            for name, report in reports.items():
                print(f"element {name} ({report['topology']}):")
                _print_report(report)
            for link in result.description["links"]:
                print(f"link {link['name']}: {link['a']['element']}.{link['a']['endpoint']} <-> "
                      f"{link['b']['element']}.{link['b']['endpoint']}")
        what, build = "Simulated system", "Build each element with: cd {0}/<element> && fprime-util generate && fprime-util build"
    else:
        manifest = load_manifest(manifest_path)
        if args.project:
            project = Path(args.project).resolve()
        elif manifest.project is not None:
            project = manifest.project_root()
        else:
            project = find_project(manifest_path.parent)
        result = generate(project, manifest, output, check=args.check)
        if args.json:
            print(json.dumps({"output": str(result.output), "changed": result.changed, "report": result.report}, indent=2))
        else:
            _print_report(result.report)
        what, build = "Simulated deployment", "Build it with: cd {0} && fprime-util generate && fprime-util build"
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
        print(build.format(result.output))
    return 0


def _print_report(report: dict) -> None:
    for sub in report["substitutions"]:
        endpoint = f"endpoint '{sub['endpoint']}'" if sub["endpoint"] is not None else "no bus endpoint"
        print(f"  {sub['instance']}: {sub['flight_component']} -> {sub['surrogate']} [{sub['chosen_by']}] {endpoint}")
    for warning in report["warnings"]:
        print(f"  warning: {warning}")
    dictionary = report["dictionary"]
    removed = sum(len(v) for v in dictionary["removed"].values())
    added = sum(len(v) for v in dictionary["added"].values())
    print(f"  dictionary: {removed} flight item(s) removed, {added} WSTS item(s) added, flight IDs unchanged")


def cmd_report(args: argparse.Namespace) -> int:
    path = Path(args.directory) / "wsts-report.json"
    if not path.is_file():
        raise WstsError(f"{path} not found: is this a generated simulation deployment?")
    if args.json:
        print(path.read_text())
    else:
        print((path.parent / "wsts-report.md").read_text())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import run_cli

    return run_cli(args)


def cmd_scaffold(args: argparse.Namespace) -> int:
    from .scaffold import scaffold_cli

    return scaffold_cli(args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fprime-wsts", description="F Prime Workstation Test Set (WSTS) toolkit")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="create a simulated deployment from a flight deployment, or a simulated system from several")
    generate.add_argument("manifest", help="deployment manifest (wsts.yaml), or a system manifest naming several")
    generate.add_argument("--project", help="flight F Prime project root (default: the manifest's 'project', else found from the manifest location)")
    generate.add_argument("--output", help="output directory (default: manifest 'output', else wsts/<deployment> in the simulation project, or in the flight project)")
    generate.add_argument("--check", action="store_true", help="do not write; fail if the output is out of date")
    generate.add_argument("--json", action="store_true", help="print the result as JSON")
    generate.set_defaults(handler=cmd_generate)

    report = commands.add_parser("report", help="show the fidelity report of a generated deployment")
    report.add_argument("directory", nargs="?", default=".")
    report.add_argument("--json", action="store_true")
    report.set_defaults(handler=cmd_report)

    run = commands.add_parser("run", help="run a built simulated deployment or system, optionally with the GDS")
    run.add_argument("directory", nargs="?", default=".", help="generated simulation deployment directory, or generated system directory")
    run.add_argument("--gds", action="store_true", help="also start a headless fprime-gds")
    run.add_argument("--gds-port", type=int, default=50000, help="port the deployment connects to (default 50000)")
    run.add_argument("--bridge", help="bridge address override, e.g. tcp:50100")
    run.add_argument("--scenario", help="scenario file to play from start-up")
    run.add_argument("--scenario-mode", choices=["stimulus", "responses"], default="stimulus")
    run.add_argument("--record", help="record bus traffic and actions to this file")
    run.add_argument("--duration", type=float, help="stop after this many seconds (default: run until interrupted)")
    run.add_argument("--log-dir", help="directory for logs (default: <directory>/wsts-logs)")
    run.add_argument("--files-dir", help="directory for relative scenario/recording paths (default: the log directory)")
    run.add_argument("--env", action="append", metavar="NAME=VALUE",
                     help="environment variable for the simulated deployment, in addition to the manifest's run.env "
                          "(repeatable; for a system, <element>.NAME=VALUE sets it for one element)")
    run.add_argument("--json", action="store_true", help="print a JSON summary when the run ends")
    run.set_defaults(handler=cmd_run)

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
