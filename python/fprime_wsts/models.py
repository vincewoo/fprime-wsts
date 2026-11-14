"""Python device models that `fprime-wsts run` starts with a simulated deployment.

A model file is listed under `run.models` in the manifest and defines

    def serve(sim):
        sim.serve_i2c("thermoBus", 0x48, lambda write, length: (0, bytes([42] * length)))

`sim` is a `WstsBridge` connected to the deployment. `serve` registers handlers
(`serve_i2c`, `serve_spi`, `serve_register`, `serve_bytestream`, `serve_gpio`) and returns;
the handlers then answer for as long as the deployment runs. The launcher starts the
deployment paused, runs this module, and resumes once every file's `serve` has returned,
so a model is on its endpoint before flight software's first tick.

    python -m fprime_wsts.models --bridge tcp:50100 models/thermo.py [...]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import traceback
from pathlib import Path

from .bridge import BridgeError, WstsBridge

READY = "[models] ready"


def load(path: Path):
    """The `serve` function of a model file"""
    spec = importlib.util.spec_from_file_location(f"wsts_model_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))  # a model may import what lies beside it
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    serve = getattr(module, "serve", None)
    if not callable(serve):
        raise BridgeError(f"{path} does not define serve(sim): the function that registers the model's handlers "
                          "(sim.serve_i2c, sim.serve_bytestream, ...)")
    return serve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fprime_wsts.models", description=__doc__.split("\n\n")[0])
    parser.add_argument("--bridge", required=True, help="bridge address of the deployment, e.g. tcp:50100")
    parser.add_argument("files", nargs="+", help="Python files that define serve(sim)")
    args = parser.parse_args(argv)
    try:
        sim = WstsBridge(args.bridge)
        for file in args.files:
            load(Path(file).resolve())(sim)
            print(f"[models] {file}: serving", flush=True)
    except BridgeError as error:
        print(f"[models] error: {error}", flush=True)
        return 2
    except Exception:
        traceback.print_exc()
        print(f"[models] error: {file} failed (see above)", flush=True)
        return 2
    print(READY, flush=True)
    try:
        sim.wait_closed()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
