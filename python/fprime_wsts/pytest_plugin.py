"""pytest fixtures for tests that drive a running simulation.

Enable with `-p fprime_wsts.pytest_plugin`. With `--wsts-deployment <dir>` the plugin starts
the simulation (and a headless GDS when `--wsts-gds` is given) for the test session;
without it, tests attach to a simulation that is already running.
"""

from pathlib import Path

import pytest

from .bridge import WstsBridge
from .runner import Simulation


def pytest_addoption(parser):
    group = parser.getgroup("fprime-wsts")
    group.addoption("--wsts-deployment", help="generated simulation deployment directory to start for the session")
    group.addoption("--wsts-gds", action="store_true", help="also start a headless fprime-gds")
    group.addoption("--wsts-bridge", help="bridge address (default: taken from the deployment, else tcp:50100)")


@pytest.fixture(scope="session")
def wsts_simulation(request):
    """The running simulation, or None when attaching to one started elsewhere"""
    directory = request.config.getoption("--wsts-deployment")
    if not directory:
        yield None
        return
    simulation = Simulation(Path(directory), gds=request.config.getoption("--wsts-gds"),
                            bridge=request.config.getoption("--wsts-bridge"))
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
