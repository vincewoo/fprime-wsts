"""Code that waits on the host (docs/stepped-time.md, phase 3): what the generator reads
from the sources of the components a deployment builds, and how it reports it."""

import json
from pathlib import Path

import pytest

from fprime_wsts import hostwait
from fprime_wsts.generate import generate
from mission import MISSION, REPO, copy_flight, flight_manifest, needs_fpp

SOURCE = """\
// usleep(5) in a comment, and /* sleep(1) */ in another
#include <thread>
void Worker::run() {
    usleep(100); ::sleep(1); obj.sleep(2); Foo::sleep(3); m_task->sleep(4);
    const char* text = "clock_gettime(CLOCK_REALTIME, &now)";
    auto t = std::chrono::steady_clock::now();
    std::thread worker([] {});
    unsigned cores = std::thread::hardware_concurrency();
    std::this_thread::sleep_for(period);
    time(NULL); reading.time(0); Os::Task::delay(interval);
    std::condition_variable ready; sem_wait(&given);
    recv(fd, buffer, size, 0); m_socket.recv(buffer);
}
"""


def calls(text):
    return [(f.line, f.kind, f.call) for f in hostwait.scan_text(text, Path("Worker.cpp"))]


def test_calls_that_wait_on_the_host_are_found_by_line():
    assert calls(SOURCE) == [
        (4, "sleeps", "sleep"),  # ::sleep, the global one
        (4, "sleeps", "usleep"),
        (6, "host_clock", "std::chrono::steady_clock::now"),
        (7, "own_thread", "std::thread"),
        (9, "sleeps", "std::this_thread::sleep_for"),
        (10, "sleeps", "Os::Task::delay"),
        (10, "host_clock", "time"),
        (11, "waits_for_thread", "sem_wait"),
        (11, "waits_for_thread", "std::condition_variable"),
        (12, "waits_on_io", "recv"),
    ]


def test_comments_strings_members_and_other_scopes_are_not_calls():
    assert calls('// sleep(1)\n/* usleep(\n2) */\nputs("nanosleep(");\nx.poll(); y->select(); A::recv();\n') == []
    # Simulated time is the right thing to read
    assert calls("Os::RawTime now; now.now(); Wsts::Control::get().flightTime(s, us);\n") == []


def module(tmp_path, cmake, files):
    directory = tmp_path / "Module"
    directory.mkdir()
    if cmake is not None:
        (directory / "CMakeLists.txt").write_text(cmake)
    for name, text in files.items():
        (directory / name).write_text(text)
    return directory


def test_a_module_is_read_in_the_variant_the_build_takes(tmp_path):
    directory = module(tmp_path, """\
if (BUILD_TESTING)
    register_fprime_library(SOURCES "${CMAKE_CURRENT_LIST_DIR}/Stub.cpp")
    register_fprime_ut(SOURCES "${CMAKE_CURRENT_LIST_DIR}/test/ut/Test.cpp")
else ()
    register_fprime_library(SOURCES "${CMAKE_CURRENT_LIST_DIR}/Real.cpp" HEADERS "${CMAKE_CURRENT_LIST_DIR}/Real.hpp")
endif ()
""", {"Stub.cpp": "void f() {}\n", "Real.cpp": "void f() { usleep(10); }\n",
      "Real.hpp": "inline void g() { gettimeofday(&t, 0); }\n", "Other.cpp": "void h() { sleep(1); }\n"})
    assert [f.name for f in hostwait.module_files(directory)] == ["Real.cpp", "Real.hpp"]
    assert [f.name for f in hostwait.module_files(directory, testing=True)] == ["Stub.cpp"]
    assert [f.call for f in hostwait.scan_module(directory)] == ["usleep", "gettimeofday"]
    assert hostwait.scan_module(directory, testing=True) == []


def test_a_module_that_names_no_file_literally_is_read_whole(tmp_path):
    directory = module(tmp_path, 'register_fprime_library(SOURCES ${MY_SOURCES})\n',
                       {"A.cpp": "void f() { nanosleep(&t, 0); }\n", "notes.txt": "sleep(1)\n"})
    assert [f.call for f in hostwait.scan_module(directory)] == ["nanosleep"]


def test_the_most_serious_effect_decides_and_comes_first():
    sleeper = hostwait.Entry("flight", ["App.Sleeper"], ["sleeper"], Path("/p/Sleeper"),
                             [hostwait.Finding("sleeps", "usleep", Path("/p/Sleeper/S.cpp"), 3)])
    waiter = hostwait.Entry("flight", ["App.Waiter"], ["waiter"], Path("/p/Waiter"),
                            [hostwait.Finding("host_clock", "clock_gettime", Path("/p/Waiter/W.cpp"), 7),
                             hostwait.Finding("waits_for_thread", "sem_wait", Path("/p/Waiter/W.cpp"), 9)])
    quiet = hostwait.Entry("flight", ["App.Quiet"], ["quiet"], Path("/p/Quiet"))
    ground = hostwait.Entry("framework", ["Drv.TcpClient"], ["comDriver"], known=hostwait.FRAMEWORK["Drv.TcpClient"])
    listed = hostwait.describe([sleeper, quiet, ground, waiter], lambda p: p.relative_to("/p").as_posix())
    assert [(i["components"], i["effect"]) for i in listed] == [
        (["App.Waiter"], "may_stall"), (["Drv.TcpClient"], "not_repeatable"), (["App.Sleeper"], "slower")]
    assert listed[0]["kinds"] == ["host_clock", "waits_for_thread"]
    assert listed[0]["calls"][1] == {"kind": "waits_for_thread", "call": "sem_wait", "file": "Waiter/W.cpp", "line": 9}
    assert "module" not in listed[1] and "calls" not in listed[1] and "recv()" in listed[1]["known"]
    assert hostwait.verdict(listed).startswith(
        "3 modules of this deployment do something that waits on the host: one may keep a tick from finishing, "
        "one keeps two stepped runs from recording the same bytes, one only makes stepped runs slower.")
    text = "\n".join(hostwait.render(listed))
    assert "| `waiter` | `App.Waiter` (flight code) | `Waiter` | `clock_gettime` at `Waiter/W.cpp:7`; `sem_wait` at " \
           "`Waiter/W.cpp:9` | may stall a tick |" in text
    assert "**Waits for another thread.**" in text and "WSTS_STEP_LIMIT_MS" in text
    warning = hostwait.warning(listed[0])
    assert warning.startswith("time.mode is stepped, and waiter (App.Waiter, flight code) reads the host's clock and "
                              "waits for another thread: clock_gettime at Waiter/W.cpp:7; sem_wait at Waiter/W.cpp:9.")


def test_nothing_to_report():
    assert hostwait.describe([hostwait.Entry("flight", ["App.Quiet"], ["quiet"], Path("/p"))], str) == []
    assert "stepped time should work" in hostwait.verdict([])


@needs_fpp
def test_the_example_reports(tmp_path):
    """Checked in: Payload has nothing that waits on the host; Cdh keeps its ground link"""
    reports = {element: json.loads((REPO / "examples" / "mission-sim" / "wsts" / "Mission" / element /
                                    "wsts-report.json").read_text()) for element in ("cdh", "payload")}
    assert reports["payload"]["time"]["waits_on_host"] == []
    assert [(i["instances"], i["effect"]) for i in reports["cdh"]["time"]["waits_on_host"]] == [
        (["Cdh.comDriver"], "not_repeatable")]
    assert reports["cdh"]["time"]["time_source"] == {
        "instance": "Cdh.chronoTime", "flight_component": "Svc.ChronoTime", "given_by": "SensorSim.SimTime"}
    markdown = (REPO / "examples" / "mission-sim" / "wsts" / "Mission" / "payload" / "wsts-report.md").read_text()
    assert "## Time" in markdown and "replaced by `Wsts.SimTime`" in markdown


@needs_fpp
@pytest.mark.parametrize("mode", ["realtime", "stepped"])
def test_a_flight_component_that_waits_on_the_host(tmp_path, mode):
    project = copy_flight(tmp_path)
    source = project / "Components" / "HeaterControl" / "HeaterControl.cpp"
    source.write_text(source.read_text() + "\nstatic void settle() { std::condition_variable cv; usleep(50); }\n")
    result = generate(project, flight_manifest("Payload", time={"mode": mode}), tmp_path / "out")
    listed = result.report["time"]["waits_on_host"]
    heater = next(i for i in listed if i["components"] == ["Components.HeaterControl"])
    assert heater["role"] == "flight" and heater["instances"] == ["Payload.heater"]
    assert heater["effect"] == "may_stall" and heater["module"].endswith("flight/Components/HeaterControl")
    assert [c["call"] for c in heater["calls"]] == ["usleep", "std::condition_variable"]
    # The flight hub driver is kept here (no system link replaces it): a socket, by what is known of it
    assert any(i["role"] == "framework" and i["instances"] == ["Payload.hubDriver"] for i in listed)
    warned = [w for w in result.report["warnings"] if w.startswith("time.mode is stepped")]
    if mode == "stepped":
        assert any("Payload.heater (Components.HeaterControl, flight code)" in w for w in warned)
    else:
        assert warned == [], "in real time it is a note in the report, not a warning"
    assert "| `Payload.heater` | `Components.HeaterControl` (flight code) |" in \
        (result.output / "wsts-report.md").read_text()
