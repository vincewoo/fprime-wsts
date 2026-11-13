#!/usr/bin/env python3
"""Check a WSTS device model against what the real device did.

Replays hardware captures (the JSON lines that probe.py writes, which are also WSTS
recording files) against a C++ device model. Every capture line that flight software
would have caused (bytes sent, an I2C or SPI transaction, a pin driven, a register
access) becomes the same call on the model through the WSTS bus. Every line the device
produced (bytes back, a read, a pin level, an interrupt) is compared with what the model
produced, by the step's `compare` mode.

The model is compiled with Wsts/Core only, outside any F Prime build, in a few seconds
(the core objects are cached). It must therefore be plain C++, as WSTS models are.

    conform.py --manifest payload.yaml --model imu captures/imu.jsonl
    conform.py --class Sim::TempSensor --header Models/TempSensor.hpp --kind bytestream \\
               --endpoint uart --root examples/mission-sim captures/*.jsonl

Compare modes (a capture line's "compare" field, or --plan to override by step name):
    exact              bytes / value / status must be identical (the default)
    length             same number of bytes
    mask:<hex>         compare only bits set in the mask; bytes beyond the mask exactly
    regex:<pattern>    the model's bytes, as latin-1 text, must fully match the pattern
    ignore             do not compare (the call is still made, so state advances)

Exit status: 0 if nothing failed, 1 if a comparison failed, 2 on a usage or build error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "model_runner.cpp.in"
FLIGHT_SIDE = {"bs_tx", "i2c", "spi", "gpio_out", "reg_rd", "reg_wr"}
DEVICE_SIDE = {"bs_rx", "gpio_in", "reg_irq"}


class UsageError(Exception):
    pass


# ----------------------------------------------------------------------
# Locating things
# ----------------------------------------------------------------------

def find_wsts_root(explicit: Optional[str], starts: List[Path]) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("WSTS_ROOT"):
        candidates.append(Path(os.environ["WSTS_ROOT"]))
    for start in starts + [Path.cwd(), HERE]:
        for parent in [start.resolve()] + list(start.resolve().parents):
            candidates.append(parent)
            lib = parent / "lib"
            if lib.is_dir():
                candidates.extend(sorted(p for p in lib.iterdir() if p.is_dir()))
    for candidate in candidates:
        if (candidate / "Wsts" / "Core" / "Bus.hpp").is_file():
            return candidate.resolve()
    raise UsageError("cannot find the fprime-wsts checkout (Wsts/Core/Bus.hpp); pass --wsts-root")


def project_root(start: Path) -> Path:
    """The root a manifest's headers are relative to: the simulation project or flight project"""
    for parent in [start] + list(start.parents):
        if (parent / "library.cmake").is_file() or (parent / "settings.ini").is_file():
            return parent
    return start


def load_yaml(path: Path):
    try:
        import yaml  # type: ignore
    except ImportError:
        if path.suffix == ".json":
            return json.loads(path.read_text())
        raise UsageError("PyYAML is needed to read " + str(path))
    return yaml.safe_load(path.read_text())


def model_from_manifest(path: Path, name: str) -> dict:
    manifest = load_yaml(path) or {}
    for spec in manifest.get("models") or []:
        if spec.get("name") == name:
            return {
                "class": spec["class"],
                "header": spec.get("header", "Wsts/Core/Models.hpp"),
                "endpoint": spec["endpoint"],
                "kind": spec.get("kind", "bytestream"),
                "address": int(str(spec.get("address", 0)), 0),
                "params": {str(k): str(v) for k, v in (spec.get("params") or {}).items()},
                "root": project_root(path.resolve().parent),
            }
    names = [m.get("name") for m in manifest.get("models") or []]
    raise UsageError(f"no model named {name!r} in {path} (models: {names})")


# ----------------------------------------------------------------------
# Building the runner
# ----------------------------------------------------------------------

def compiler() -> str:
    for name in (os.environ.get("CXX"), "g++", "c++", "clang++"):
        if name and shutil.which(name):
            return name
    raise UsageError("no C++ compiler found (set CXX)")


def run(cmd: List[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(" ".join(cmd) + "\n" + result.stdout + result.stderr)
        raise UsageError("compilation failed (output above)")


def build_runner(model: dict, sources: List[Path], includes: List[Path], wsts_root: Path,
                 build_dir: Path, core_dir: Path, verbose: bool) -> Path:
    cxx = compiler()
    flags = ["-std=c++17", "-O1", "-g", "-pthread", "-DWSTS_SIM=1", "-DWSTS_CONFORM=1"]
    inc = [f"-I{p}" for p in [wsts_root] + includes]
    core_dir.mkdir(parents=True, exist_ok=True)
    objects = []
    for src in sorted((wsts_root / "Wsts" / "Core").glob("*.cpp")):
        obj = core_dir / (src.stem + ".o")
        if not obj.exists() or obj.stat().st_mtime < src.stat().st_mtime:
            if verbose:
                print(f"compiling {src.name}", file=sys.stderr)
            run([cxx, *flags, *inc, "-c", str(src), "-o", str(obj)])
        objects.append(obj)
    runner = build_dir / "model_runner.cpp"
    text = TEMPLATE.read_text().replace("@HEADER@", model["header"]).replace("@CLASS@", model["class"])
    if not runner.exists() or runner.read_text() != text:
        runner.write_text(text)
    exe = build_dir / "model_runner"
    if verbose:
        print("compiling the model and runner", file=sys.stderr)
    run([cxx, *flags, *inc, str(runner), *map(str, sources), *map(str, objects), "-o", str(exe)])
    return exe


# ----------------------------------------------------------------------
# Captures
# ----------------------------------------------------------------------

def read_captures(paths: List[Path]) -> List[dict]:
    records = []
    for path in paths:
        offset = len(records)
        for number, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise UsageError(f"{path}:{number}: {exc}")
            if "rec" not in obj:
                continue  # an action line of a scenario
            obj["_where"] = f"{path.name}:{number}"
            obj["_order"] = offset + len(records)
            records.append(obj)
    return records


def plan_overrides(path: Optional[Path]) -> Dict[str, dict]:
    if not path:
        return {}
    plan = load_yaml(path) or {}
    return {step["name"]: step for step in plan.get("steps") or [] if "name" in step}


def hex_bytes(text: str) -> bytes:
    return bytes.fromhex(re.sub(r"[\s:]|0x", "", text or ""))


def show(data: bytes, limit: int = 48) -> str:
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in data[:limit])
    more = "…" if len(data) > limit else ""
    return f"{data[:limit].hex(' ')}{more} |{text}{more}|"


def compare(mode: str, expected: bytes, got: bytes) -> Tuple[bool, str]:
    mode = mode or "exact"
    if mode == "ignore":
        return True, "ignored"
    if mode == "exact":
        return expected == got, ""
    if mode == "length":
        return len(expected) == len(got), f"length {len(got)}, device {len(expected)}"
    if mode.startswith("mask:"):
        mask = hex_bytes(mode[5:])
        if len(expected) != len(got):
            return False, f"length {len(got)}, device {len(expected)}"
        for i, (a, b) in enumerate(zip(expected, got)):
            m = mask[i] if i < len(mask) else 0xFF
            if (a & m) != (b & m):
                return False, f"byte {i} differs under mask {m:#04x}"
        return True, ""
    if mode.startswith("regex:"):
        pattern = re.compile(mode[6:], re.DOTALL)
        ok = pattern.fullmatch(got.decode("latin-1")) is not None
        device_ok = pattern.fullmatch(expected.decode("latin-1")) is not None
        note = "" if device_ok else " (the device's own bytes do not match the pattern either: fix the pattern)"
        return ok, ("model output does not match the pattern" if not ok else "") + note
    raise UsageError(f"unknown compare mode {mode!r}")


class Runner:
    def __init__(self, exe: Path):
        self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def op(self, **fields) -> dict:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(fields) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise UsageError(f"the model runner exited (status {self.proc.wait()}) during {fields}: "
                             "the model crashed or asserted")
        return json.loads(line)

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)


def conform(exe: Path, model: dict, records: List[dict], overrides: Dict[str, dict],
            capture_endpoint: Optional[str]) -> List[dict]:
    endpoint = model["endpoint"]
    kind = model["kind"]
    alias = capture_endpoint or endpoint

    def ep(rec: dict) -> str:
        return endpoint if rec.get("endpoint") == alias else rec.get("endpoint", endpoint)

    runner = Runner(exe)
    results = []
    try:
        r = runner.op(op="set", key="endpoint", value=endpoint)  # as the generated set-up does
        for key, value in model["params"].items():
            r = runner.op(op="set", key=key, value=value)
            if not r["ok"]:
                raise UsageError(f"manifest parameter rejected: {r.get('error')}")
        r = runner.op(op="attach", kind=kind, endpoint=endpoint, addr=model.get("address", 0))
        if not r["ok"]:
            raise UsageError(r.get("error", "attach failed"))
        bound = {endpoint}
        for rec in records:
            e = ep(rec)
            if e in bound:
                continue
            if rec["rec"] in ("bs_rx", "bs_tx"):
                runner.op(op="bind", kind="bytestream", endpoint=e)
            elif rec["rec"] in ("gpio_in", "gpio_out"):
                runner.op(op="bind", kind="gpio", endpoint=e)
            elif rec["rec"] == "reg_irq":
                runner.op(op="bind", kind="register", endpoint=e)
            bound.add(e)

        rx: Dict[str, bytearray] = {}
        irqs: List[str] = []
        tick = 0
        step_params_done = set()
        step_params: Dict[str, dict] = {}
        for rec in records:  # a step's parameters, from whichever of its lines carries them
            if rec.get("step") and rec.get("model_params"):
                step_params.setdefault(rec["step"], {}).update(rec["model_params"])

        def drain() -> None:
            out = runner.op(op="drain")
            for key, value in out.items():
                if key.startswith("rx."):
                    rx.setdefault(key[3:], bytearray()).extend(bytes.fromhex(value))
            irqs.extend(x for x in out.get("irq", "").split(";") if x)

        for rec in sorted(records, key=lambda r: (int(r.get("tick", 0)), r["_order"])):
            kind_rec = rec["rec"]
            e = ep(rec)
            step = rec.get("step", "")
            override = overrides.get(step, {})
            mode = override.get("compare", rec.get("compare", "exact"))
            if kind == "i2c" and kind_rec == "i2c" and int(rec.get("addr", -1)) != model["address"]:
                results.append(dict(where=rec["_where"], step=step, rec=kind_rec, result="SKIP",
                                    detail=f"address {rec.get('addr')} is another device"))
                continue
            params = override.get("model_params", step_params.get(step)) or {}
            if step and step not in step_params_done:
                step_params_done.add(step)
                for key, value in params.items():
                    r = runner.op(op="set", key=str(key), value=str(value))
                    if not r["ok"]:
                        results.append(dict(where=rec["_where"], step=step, rec="set", result="FAIL",
                                            detail=r.get("error", "")))
            target = int(rec.get("tick", 0))
            if target > tick:
                runner.op(op="tick", n=target - tick)
                tick = target

            result, detail = "PASS", ""
            if kind_rec == "bs_tx":
                r = runner.op(op="tx", endpoint=e, data=rec.get("data", ""))
                result = "SENT" if r.get("status", 0) == 0 else "FAIL"
                detail = show(hex_bytes(rec.get("data", "")))
            elif kind_rec == "bs_rx":
                drain()
                got = bytes(rx.pop(e, bytearray()))
                expected = hex_bytes(rec.get("data", ""))
                ok, note = compare(mode, expected, got)
                result = "PASS" if ok else "FAIL"
                detail = note if ok else f"{note}\n      device {show(expected)}\n      model  {show(got)}"
            elif kind_rec in ("i2c", "spi"):
                expected = hex_bytes(rec.get("read", ""))
                fields = dict(op=kind_rec, endpoint=e, write=rec.get("write", ""), rlen=len(expected))
                if kind_rec == "i2c":
                    fields["addr"] = int(rec.get("addr", 0))
                r = runner.op(**fields)
                got = bytes.fromhex(r.get("read", ""))
                status_ok = mode == "ignore" or int(r.get("status", 0)) == int(rec.get("status", 0))
                ok, note = compare(mode, expected, got) if int(rec.get("status", 0)) == 0 else (True, "")
                result = "PASS" if ok and status_ok else "FAIL"
                if not status_ok:
                    note = f"status {r.get('status')}, device {rec.get('status')}. {note}"
                detail = f"write {rec.get('write', '')}" + ("" if result == "PASS" else
                                                           f": {note}\n      device {show(expected)}\n      model  {show(got)}")
            elif kind_rec == "gpio_out":
                r = runner.op(op="gpio_write", endpoint=e, level=bool(rec.get("level")))
                result, detail = "SENT", f"{e} <- {int(bool(rec.get('level')))}"
            elif kind_rec == "gpio_in":
                r = runner.op(op="gpio_read", endpoint=e)
                ok = mode == "ignore" or bool(r.get("level")) == bool(rec.get("level"))
                result = "PASS" if ok else "FAIL"
                detail = f"{e} model {int(bool(r.get('level')))}, device {int(bool(rec.get('level')))}"
            elif kind_rec == "reg_wr":
                value = int.from_bytes(hex_bytes(rec.get("write", "")), "little")
                r = runner.op(op="reg_wr", endpoint=e, offset=str(rec.get("offset", "0")),
                              width=int(rec.get("width", 4)), value=hex(value))
                result = "SENT" if r.get("status", 0) == 0 else "FAIL"
                detail = f"{rec.get('offset')} <- {value:#x}" + ("" if result == "SENT" else f" status {r.get('status')}")
            elif kind_rec == "reg_rd":
                width = int(rec.get("width", 4))
                r = runner.op(op="reg_rd", endpoint=e, offset=str(rec.get("offset", "0")), width=width)
                got = int(r.get("value", "0"), 0).to_bytes(8, "little")[:width]
                expected = hex_bytes(rec.get("read", ""))
                ok, note = compare(mode, expected, got)
                status_ok = int(r.get("status", 0)) == int(rec.get("status", 0))
                result = "PASS" if ok and status_ok else "FAIL"
                detail = f"{rec.get('offset')}: model {int.from_bytes(got, 'little'):#x}, device " \
                         f"{int.from_bytes(expected, 'little'):#x}, status {r.get('status')}/{rec.get('status')}"
            elif kind_rec == "reg_irq":
                drain()
                want = f"{e}:{int(str(rec.get('number', 0)), 0)}"
                ok = mode == "ignore" or want in irqs
                if want in irqs:
                    irqs.remove(want)
                result, detail = ("PASS" if ok else "FAIL"), f"interrupt {want}"
            else:
                result, detail = "SKIP", "not a device record"
            results.append(dict(where=rec["_where"], step=step, rec=kind_rec, result=result, detail=detail, mode=mode))
        drain()
        for e, data in rx.items():
            if data:
                results.append(dict(where="end", step="", rec="bs_rx", result="EXTRA",
                                    detail=f"model sent bytes the device did not: {show(bytes(data))}"))
        for irq in irqs:
            results.append(dict(where="end", step="", rec="reg_irq", result="EXTRA",
                                detail=f"model raised {irq}, the device did not"))
    finally:
        runner.close()
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("captures", nargs="+", type=Path, help="capture files (probe.py output, or WSTS recordings)")
    parser.add_argument("--manifest", type=Path, help="element manifest that names the model")
    parser.add_argument("--model", help="model name in the manifest")
    parser.add_argument("--class", dest="cls", help="model class, e.g. Sim::TempSensor (without --manifest)")
    parser.add_argument("--header", help="model header, relative to --root")
    parser.add_argument("--kind", choices=["bytestream", "i2c", "spi", "gpio", "register", "none"])
    parser.add_argument("--endpoint", help="endpoint the model sits on")
    parser.add_argument("--address", default="0", help="I2C address")
    parser.add_argument("--param", action="append", default=[], help="KEY=VALUE, as manifest params (repeatable)")
    parser.add_argument("--root", type=Path, help="directory headers are relative to (simulation project root)")
    parser.add_argument("--source", action="append", type=Path, default=[], help="model .cpp files (default: next to the header)")
    parser.add_argument("--include", action="append", type=Path, default=[], help="further include directories")
    parser.add_argument("--capture-endpoint", help="endpoint name used in the captures, if not the model's")
    parser.add_argument("--plan", type=Path, help="probe plan whose steps override compare / model_params")
    parser.add_argument("--wsts-root", help="fprime-wsts checkout (default: found from here or the manifest)")
    parser.add_argument("--build-dir", type=Path, help="default: ~/.cache/wsts-conform/<hash>")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.manifest:
            if not args.model:
                raise UsageError("--manifest needs --model")
            model = model_from_manifest(args.manifest, args.model)
        else:
            if not (args.cls and args.header and args.kind and args.endpoint):
                raise UsageError("give --manifest/--model, or --class, --header, --kind and --endpoint")
            model = {"class": args.cls, "header": args.header, "kind": args.kind, "endpoint": args.endpoint,
                     "address": int(args.address, 0), "params": {},
                     "root": (args.root or Path.cwd()).resolve()}
        for item in args.param:
            key, _, value = item.partition("=")
            model["params"][key] = value
        if args.root:
            model["root"] = args.root.resolve()
        root: Path = model["root"]
        wsts_root = find_wsts_root(args.wsts_root, [root] + ([args.manifest.parent] if args.manifest else []))
        sources = [s.resolve() for s in args.source]
        if not sources and not model["header"].startswith("Wsts/"):
            guess = (root / model["header"]).with_suffix(".cpp")
            if guess.is_file():
                sources = [guess]
        includes = [root] + [p.resolve() for p in args.include]
        key = hashlib.sha1(f"{wsts_root}|{root}|{model['class']}|{model['header']}".encode()).hexdigest()[:12]
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        build_dir = (args.build_dir or cache / "wsts-conform" / key).resolve()
        build_dir.mkdir(parents=True, exist_ok=True)
        core_key = hashlib.sha1(str(wsts_root).encode()).hexdigest()[:12]
        core_dir = (args.build_dir or cache / "wsts-conform") / f"core-{core_key}"
        exe = build_runner(model, sources, includes, wsts_root, build_dir, core_dir.resolve(), args.verbose)
        # Each capture file is its own session on the device, so each gets a fresh model:
        # state left by one (a half-received command, a mode) must not leak into the next
        results = []
        overrides = plan_overrides(args.plan)
        for path in args.captures:
            records = read_captures([path])
            if not records:
                raise UsageError(f"no capture records in {path}")
            results += conform(exe, model, records, overrides, args.capture_endpoint)
    except UsageError as exc:
        print(f"conform: {exc}", file=sys.stderr)
        return 2

    failed = [r for r in results if r["result"] in ("FAIL", "EXTRA")]
    if args.json:
        print(json.dumps({"model": model["class"], "results": results, "failed": len(failed)}, indent=1))
    else:
        for r in results:
            print(f"{r['result']:5} {r['where']:<22} {r['step'] or '-':<18} {r['rec']:<8} {r['detail']}")
        counts = {k: sum(1 for r in results if r["result"] == k) for k in ("PASS", "FAIL", "EXTRA", "SENT", "SKIP")}
        print(f"\n{model['class']}: " + ", ".join(f"{v} {k.lower()}" for k, v in counts.items() if v))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
