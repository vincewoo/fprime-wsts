"""End-to-end tests run against the simulated Mission system and a headless GDS.

Run from the repository root (see README, "Running the tests"):

    pytest tests/e2e -p fprime_wsts.pytest_plugin \
        --wsts-system examples/mission-sim/wsts/Mission --wsts-gds \
        --dictionary examples/mission-sim/wsts/Mission/wsts-logs/MissionDictionary.json

The system is started once for the session. The GDS is connected to the ground element,
Cdh, with the merged dictionary of both elements: commands for Payload and its events
and telemetry travel through the hub, as in operations.
"""

import json

import pytest

T = 10  # seconds allowed for an event or channel to arrive
QUIET = 3  # seconds to watch for something that must not arrive


@pytest.fixture
def gds(wsts_system_simulation, fprime_test_api):
    """GDS test API on the ground element, with the whole system guaranteed to be up first"""
    fprime_test_api.clear_histories()
    return fprime_test_api


@pytest.fixture
def cdh(wsts_system):
    """Bridge client of the Cdh element (VxWorks flight computer, ground link)"""
    return wsts_system["cdh"]


@pytest.fixture
def payload(wsts_system):
    """Bridge client of the Payload element (Linux computer behind the hub)"""
    return wsts_system["payload"]


@pytest.fixture(scope="session")
def dictionary(request, wsts_system_simulation):
    """The merged dictionary the GDS uses, as JSON. Starting the system writes it."""
    path = request.config.getoption("--dictionary")
    with open(path) as file:
        return json.load(file)


def events_seen(gds, name):
    return [e for e in gds.get_event_test_history().retrieve() if name in e.get_str()]


def channels_seen(gds, name):
    return [c for c in gds.get_telemetry_test_history().retrieve() if name in c.get_str()]
