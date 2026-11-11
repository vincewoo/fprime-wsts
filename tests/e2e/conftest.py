"""End-to-end tests run against a simulated deployment and a headless GDS.

Run from the repository root (see README, "Running the tests"):

    pytest tests/e2e/test_linux_demo.py -p fprime_sse.pytest_plugin \
        --sse-deployment examples/linux-demo/sse/LinuxDemo --sse-gds \
        --dictionary examples/linux-demo/sse/LinuxDemo/build-artifacts/Linux/LinuxDemo/dict/LinuxDemoTopologyDictionary.json
"""

import pytest


@pytest.fixture
def gds(sse_simulation, fprime_test_api):
    """GDS test API, with the simulation guaranteed to be up first"""
    fprime_test_api.clear_histories()
    return fprime_test_api
