from pathlib import Path

import pytest

from fprime_sse import fpp
from fprime_sse.fpp import SseError
from fprime_sse.generate import (PlannedSubstitution, _mask, module_dir, module_name, patch_instance, patch_packets,
                                 patch_topology, read_registration)
from fprime_sse.manifest import Surrogate

INSTANCES = '''module Demo {

  # instance decoy: Drv.Uart in a comment
  instance uart: Drv.Uart base id 0x100 \\
    queue size 10 \\
    stack size 64 * 1024 \\
    priority 30 {
      phase Fpp.ToCpp.Phases.configComponents """
      uart.open("/dev/ttyS0"); // { not a brace
      """
    }

  instance other: Drv.Uart base id 0x200
}
'''


def substitution(line, col, has_queue, kind="passive"):
    instance = fpp.Instance("Demo.uart", "Drv.Uart", 0x100, 0x1FF, Path("i.fpp"), line, 3, line, col, "Drv.Uart",
                            has_queue=has_queue)
    original = fpp.Component("Drv.Uart", "active", Path("u.fpp"))
    surrogate = fpp.Component("Sse.SimByteStream", kind, Path("s.fpp"))
    return PlannedSubstitution(instance, original, surrogate, Surrogate("Sse.SimByteStream"), "uart", "", "catalog")


def test_mask_blanks_comments_and_strings_but_keeps_length():
    masked = _mask(INSTANCES)
    assert len(masked) == len(INSTANCES)
    assert "decoy" not in masked and "not a brace" not in masked
    assert masked.count("{") == INSTANCES.count("{") - 1


def test_instance_is_retyped_and_loses_task_attributes():
    patched = patch_instance(INSTANCES, substitution(4, 18, has_queue=True))
    assert "instance uart: Sse.SimByteStream base id 0x100" in patched
    definition = patched.split("instance uart")[1].split("instance other")[0]
    assert "queue size" not in definition and "stack size" not in definition and "priority" not in definition
    assert 'uart.open("/dev/ttyS0")' in definition, "phase code is kept"
    assert "instance other: Drv.Uart base id 0x200" in patched, "other instances are untouched"
    assert "# instance decoy: Drv.Uart" in patched


def test_active_surrogate_keeps_task_attributes():
    patched = patch_instance(INSTANCES, substitution(4, 18, has_queue=True, kind="active"))
    assert "queue size 10" in patched and "priority 30" in patched


def test_patch_refuses_when_source_does_not_match_the_model():
    with pytest.raises(SseError, match="expected 'Drv.Uart'"):
        patch_instance(INSTANCES, substitution(4, 3, has_queue=False))


TOPOLOGY = '''module Demo {
  # topology Fake { }
  deployment topology Demo {
    instance uart
    telemetry packets P {
      packet A id 1 group 1 { uart.Bytes }
    } omit {
      uart.Other
    }
  }
}
'''


def test_topology_gains_instances_inside_its_body():
    topology = fpp.Topology("Demo.Demo", Path("t.fpp"), 3, 3)
    patched = patch_topology(TOPOLOGY, topology, ["sseSetup", "sseControl"])
    body = patched.split("deployment topology Demo {")[1]
    assert body.index("instance sseSetup") < body.index("instance uart")
    assert "instance sseControl" in body
    assert "# topology Fake { }" in patched


def test_packets_extend_an_existing_omit_list():
    patched = patch_packets(TOPOLOGY, 5, 5, ["Demo.sseControl.SimTicks"])
    omit = patched.split("} omit {")[1]
    assert "Demo.sseControl.SimTicks" in omit and "uart.Other" in omit
    assert patched.count("omit") == 1


def test_packets_gain_an_omit_list_when_there_is_none():
    text = TOPOLOGY.replace(" omit {\n      uart.Other\n    }", "")
    patched = patch_packets(text, 5, 5, ["Demo.sseControl.SimTicks"])
    assert "} omit {" in patched and "Demo.sseControl.SimTicks" in patched


def test_read_registration(tmp_path):
    cmake = tmp_path / "CMakeLists.txt"
    cmake.write_text('''# register_fprime_module(SOURCES commented.cpp)
register_fprime_module(
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/topology.fpp"
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/Top.cpp"  # trailing comment
    DEPENDS
        Fw_Logger Svc_PosixTime
)
''')
    groups = read_registration(cmake, "register_fprime_module")
    assert groups["SOURCES"] == [str(tmp_path / "Top.cpp")]
    assert groups["AUTOCODER_INPUTS"] == [str(tmp_path / "topology.fpp")]
    assert groups["DEPENDS"] == ["Fw_Logger", "Svc_PosixTime"]
    assert read_registration(tmp_path / "missing.txt", "register_fprime_module") == {}


def test_module_dir_and_name(tmp_path):
    component = tmp_path / "Components" / "Imu"
    component.mkdir(parents=True)
    (tmp_path / "Components" / "CMakeLists.txt").write_text("add_fprime_subdirectory(Imu)\n")
    (component / "CMakeLists.txt").write_text("register_fprime_library(SOURCES Imu.cpp)\n")
    assert module_dir(component / "Imu.fpp", tmp_path) == component
    assert module_dir(tmp_path / "Components" / "loose.fpp", tmp_path) is None
    assert module_name(component, [tmp_path]) == "Components_Imu"
