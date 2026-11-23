"""pytest fixtures for tests that drive a running simulation.

Enable with `-p fprime_wsts.pytest_plugin`. With `--wsts-deployment <dir>` the plugin starts
the simulation (and a headless GDS when `--wsts-gds` is given) for the test session;
without it, tests attach to a simulation that is already running.

For a system of several elements give `--wsts-system <dir>` instead and use the
`wsts_system` fixture: the GDS then runs on the ground element with the merged dictionary.
"""

from pathlib import Path

import pytest

from .bridge import WstsBridge, WstsSystem
from .runner import Simulation, SystemSimulation, parse_env


def pytest_addoption(parser):
    group = parser.getgroup("fprime-wsts")
    group.addoption("--wsts-deployment", help="generated simulation deployment directory to start for the session")
    group.addoption("--wsts-gds", action="store_true", help="also start a headless fprime-gds")
    group.addoption("--wsts-bridge", help="bridge address (default: taken from the deployment, else tcp:50100)")
    group.addoption("--wsts-system", help="generated system directory to start for the session (several elements)")
    group.addoption("--wsts-auto-ports", action="store_true",
                    help="start the simulation and its GDS on free ports, so that several test sessions fit on one machine")
    group.addoption("--wsts-stepped", action="store_true",
                    help="run the deployment in stepped time: a tick is taken when flight software has finished "
                         "the last one. A system (--wsts-system) runs in lockstep, held at its start until a test "
                         "steps it (wsts_system.step)")
    group.addoption("--wsts-env", action="append", metavar="NAME=VALUE",
                    help="environment variable for the simulated deployment (repeatable), as for `fprime-wsts run --env`")


def pytest_configure(config):
    """With --wsts-auto-ports the GDS started here and fprime-gds's test API (its
    `fprime_test_api` fixture, which reads these options) meet on sockets of this session"""
    _check_toolkit(config)
    config._wsts_zmq = None
    if config.getoption("--wsts-auto-ports") and config.getoption("--wsts-gds"):
        import tempfile

        directory = tempfile.mkdtemp(prefix="wsts-gds-")
        config._wsts_zmq = [f"ipc://{directory}/in", f"ipc://{directory}/out"]
        for namespace in (config.option, getattr(config, "known_args_namespace", None)):
            if namespace is not None and not getattr(namespace, "zmq_transport", None):
                namespace.zmq_transport = list(config._wsts_zmq)


def _check_toolkit(config):
    """The plugin runs in this session's Python: unlike the command line, it cannot hand a
    simulation project that pins another toolkit (lib/fprime-wsts) to that one"""
    from .fpp import WstsError
    from .toolkit import foreign

    for option in ("--wsts-deployment", "--wsts-system"):
        directory = config.getoption(option)
        if not directory:
            continue
        try:
            toolkit = foreign(Path(directory))
        except WstsError as error:
            raise pytest.UsageError(str(error))
        if toolkit is not None:
            config.issue_config_time_warning(pytest.PytestConfigWarning(
                f"{directory} is built with the toolkit its project pins, {toolkit}, and these tests run the "
                f"fprime_wsts package of another checkout: install the pinned one in this environment "
                f"(pip install -e {toolkit / 'python'})"), stacklevel=2)


def pytest_unconfigure(config):
    import shutil

    for url in getattr(config, "_wsts_zmq", None) or []:
        shutil.rmtree(Path(url[len("ipc://"):]).parent, ignore_errors=True)


@pytest.fixture(scope="session")
def wsts_simulation(request):
    """The running simulation, or None when attaching to one started elsewhere"""
    directory = request.config.getoption("--wsts-deployment")
    if not directory:
        yield None
        return
    simulation = Simulation(Path(directory), gds=request.config.getoption("--wsts-gds"),
                            bridge=request.config.getoption("--wsts-bridge"),
                            env=parse_env(request.config.getoption("--wsts-env")).get(None, {}),
                            auto_ports=request.config.getoption("--wsts-auto-ports"),
                            zmq_transport=request.config._wsts_zmq,
                            stepped=True if request.config.getoption("--wsts-stepped") else None)
    simulation.start()
    yield simulation
    simulation.stop()


@pytest.fixture
def wsts(request, wsts_simulation):
    """Bridge client. Faults are cleared and the simulation resumed after each test."""
    address = request.config.getoption("--wsts-bridge") or (wsts_simulation.bridge if wsts_simulation else "tcp:50100")
    bridge = WstsBridge(address)
    bridge.subscribe()
    yield bridge
    try:
        bridge.fault_clear("*")
        bridge.resume()
    finally:
        bridge.close()


@pytest.fixture(scope="session")
def wsts_system_simulation(request):
    """The running system named by --wsts-system"""
    directory = request.config.getoption("--wsts-system")
    if not directory:
        raise pytest.UsageError("this test needs --wsts-system <generated system directory>")
    system = SystemSimulation(Path(directory), gds=request.config.getoption("--wsts-gds"),
                              env=request.config.getoption("--wsts-env"),
                              auto_ports=request.config.getoption("--wsts-auto-ports"),
                              zmq_transport=request.config._wsts_zmq,
                              stepped=True if request.config.getoption("--wsts-stepped") else None)
    system.start()
    yield system
    system.stop()


@pytest.fixture
def wsts_system(wsts_system_simulation):
    """Client for every element of the system. Faults are cleared and all elements resumed
    after each test."""
    system = WstsSystem(wsts_system_simulation.description)
    system.subscribe()
    yield system
    try:
        system.fault_clear("*")
        system.resume()
    finally:
        system.close()


@pytest.fixture
def wsts_restore(tmp_path_factory):
    """Start runs from snapshots (docs/snapshots.md):

        with wsts_restore("after-boot") as sim:   # a snapshot's path, or a snapshots.Snapshot
            sim.fault_set("i2cBus", "error", arg=3)
            sim.step(200)

    Each run is restored before the block starts (its journal replayed and checked: a run that
    differs raises snapshots.RestoreMismatch), held paused at the snapshot's tick, on free ports
    and with logs of its own, and stopped when the block ends. `sim` is a WstsBridge, or a
    WstsSystem for a snapshot of a system, with `log_dir`, the run's log directory, from which
    it can be snapshotted again. Several can run at once: branches of one snapshot."""
    from contextlib import contextmanager

    from . import snapshots

    @contextmanager
    def restore(snapshot, env=None):
        snap = snapshot if isinstance(snapshot, snapshots.Snapshot) else snapshots.load(snapshot)
        log_dir = tmp_path_factory.mktemp("restored")
        if snap.is_system:
            from .lockstep import Conductor

            system = SystemSimulation(snap.directory, log_dir=log_dir, env=env, auto_ports=True, restore=snap)
            system.start()
            try:
                with Conductor(system.description) as conductor:
                    conductor.wait_ready(20.0, alive=lambda: not system.exited())
                    system.complete_restore(conductor, keep_going=lambda: not system.exited())
                client = WstsSystem(system.description)
                client.log_dir = system.log_dir  # where to snapshot it from again
                try:
                    yield client
                finally:
                    client.close()
            finally:
                system.stop()
            return
        simulation = Simulation(snap.directory, log_dir=log_dir, env={**snap.meta.get("env", {}), **(env or {})},
                                auto_ports=True, restore=snap)
        simulation.start()
        try:
            with WstsBridge(simulation.bridge) as client:
                client.log_dir = simulation.log_dir  # where to snapshot it from again
                yield client
        finally:
            simulation.stop()

    return restore
