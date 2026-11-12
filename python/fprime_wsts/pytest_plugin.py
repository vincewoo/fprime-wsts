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
    group.addoption("--wsts-env", action="append", metavar="NAME=VALUE",
                    help="environment variable for the simulated deployment (repeatable), as for `fprime-wsts run --env`")


@pytest.fixture(scope="session")
def wsts_simulation(request):
    """The running simulation, or None when attaching to one started elsewhere"""
    directory = request.config.getoption("--wsts-deployment")
    if not directory:
        yield None
        return
    simulation = Simulation(Path(directory), gds=request.config.getoption("--wsts-gds"),
                            bridge=request.config.getoption("--wsts-bridge"),
                            env=parse_env(request.config.getoption("--wsts-env")).get(None, {}))
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
                              env=request.config.getoption("--wsts-env"))
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
