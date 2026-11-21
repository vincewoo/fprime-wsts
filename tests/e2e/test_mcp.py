"""The MCP server (`fprime-wsts mcp`) attached to the running Mission system, over its own
protocol: what an agent sees. The system is the session's (it was started by the plugin), so
these tests attach to it through its run file and never stop it."""

import asyncio
import json
import sys

import pytest

from conftest import T

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def drive(directory, script):
    """Start the server, run `script(call)` against it, and return what the script returns"""
    async def main():
        params = StdioServerParameters(command=sys.executable, args=[
            "-c", "import sys; from fprime_wsts.cli import main; sys.exit(main())", "mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name, **arguments):
                    result = await session.call_tool(name, {"run": str(directory), **arguments}
                                                     if name not in ("runs_list",) else arguments)
                    return json.loads(result.content[0].text)
                return await script(call)
    return asyncio.run(main())


@pytest.fixture
def system_dir(wsts_system_simulation):
    return wsts_system_simulation.directory


def test_a_running_system_is_found_and_driven(system_dir, gds):
    async def script(call):
        status = await call("status")
        assert set(status["elements"]) == {"cdh", "payload"} and status["state"] == "running"

        listed = await call("runs_list")
        assert listed["runs"][0]["started_here"] is False

        # A short name that both elements have is refused with the candidates
        ambiguous = await call("send_command", command="CMD_NO_OP")
        assert "CdhCore.cmdDisp.CMD_NO_OP" in ambiguous["error"] and "Payload.cmdDisp.CMD_NO_OP" in ambiguous["error"]

        # A command for Payload travels through the hub, and its dispatcher answers
        done = await call("send_command", command="heater.HEATER_SET", args=["ON"], timeout=T)
        assert done["result"] == "completed", done
        assert (await call("gpio", endpoint="heaterPin", element="payload"))["level"] is True
        # Looks back to the command: the event came through the hub before this call
        found = await call("wait_event", event="heater.HeaterSet", timeout=T)
        assert found["found"] is True, found
        assert (await call("send_command", command="heater.HEATER_SET", args=["OFF"], timeout=T))["result"] == \
            "completed"
        assert (await call("gpio", endpoint="heaterPin", element="payload"))["level"] is False

        # A fresh sample of a channel, about a second away
        telemetry = await call("read_telemetry", channels=["Cdh.wstsTlm.hub_Received"], timeout=T)
        assert isinstance(telemetry["channels"]["Cdh.wstsTlm.hub_Received"].get("value"), int), telemetry

        # A fault on the link by its name, then cleared
        armed = await call("fault_set", endpoint="hub", kind="drop", ticks=5)
        assert armed["status"]["cdh"]["active_faults"] == 1
        assert (await call("fault_clear"))["status"]["cdh"]["active_faults"] == 0

        log = await call("tail_log", element="payload", lines=5)
        assert log["path"].endswith("payload/sim.log")
        assert (await call("last_assert")) == {"asserts": {"cdh": None, "payload": None}}

    drive(system_dir, script)


def test_one_element_is_stepped(system_dir, wsts_system):
    async def script(call):
        assert "give element" in (await call("step", ticks=2))["error"]
        await call("pause")
        try:
            before = (await call("status", element="cdh"))["elements"]["cdh"]["tick"]
            stepped = await call("step", ticks=3, element="cdh")
            assert stepped["tick"] == before + 3 and stepped["status"]["cdh"]["paused"] is True
        finally:
            await call("resume")

    drive(system_dir, script)
