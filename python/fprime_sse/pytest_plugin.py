"""pytest fixtures for tests that drive a running simulation.

Enable with `-p fprime_sse.pytest_plugin`. With `--sse-deployment <dir>` the plugin starts
the simulation (and a headless GDS when `--sse-gds` is given) for the test session;
without it, tests attach to a simulation that is already running.
"""

from pathlib import Path

import pytest

from .bridge import SseBridge
from .runner import Simulation


def pytest_addoption(parser):
    group = parser.getgroup("fprime-sse")
    group.addoption("--sse-deployment", help="generated simulation deployment directory to start for the session")
    group.addoption("--sse-gds", action="store_true", help="also start a headless fprime-gds")
    group.addoption("--sse-bridge", help="bridge address (default: taken from the deployment, else tcp:50100)")


@pytest.fixture(scope="session")
def sse_simulation(request):
    """The running simulation, or None when attaching to one started elsewhere"""
    directory = request.config.getoption("--sse-deployment")
    if not directory:
        yield None
        return
    simulation = Simulation(Path(directory), gds=request.config.getoption("--sse-gds"),
                            bridge=request.config.getoption("--sse-bridge"))
    simulation.start()
    yield simulation
    simulation.stop()


@pytest.fixture
def sse(request, sse_simulation):
    """Bridge client. Faults are cleared and the simulation resumed after each test."""
    address = request.config.getoption("--sse-bridge") or (sse_simulation.bridge if sse_simulation else "tcp:50100")
    bridge = SseBridge(address)
    bridge.subscribe()
    yield bridge
    try:
        bridge.fault_clear("*")
        bridge.resume()
    finally:
        bridge.close()
