from pathlib import Path

import pytest

from fprime_wsts import fpp
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import (PlannedSubstitution, _mask, module_dir, module_name, patch_instance, patch_packets,
                                 patch_topology, read_registration)
from fprime_wsts.manifest import Surrogate

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
    surrogate = fpp.Component("Wsts.SimByteStream", kind, Path("s.fpp"))
    return PlannedSubstitution(instance, original, surrogate, Surrogate("Wsts.SimByteStream"), "uart", "", "catalog")


def test_mask_blanks_comments_and_strings_but_keeps_length():
    masked = _mask(INSTANCES)
    assert len(masked) == len(INSTANCES)
    assert "decoy" not in masked and "not a brace" not in masked
    assert masked.count("{") == INSTANCES.count("{") - 1


def test_instance_is_retyped_and_loses_task_attributes():
    patched = patch_instance(INSTANCES, substitution(4, 18, has_queue=True))
    assert "instance uart: Wsts.SimByteStream base id 0x100" in patched
    definition = patched.split("instance uart")[1].split("instance other")[0]
    assert "queue size" not in definition and "stack size" not in definition and "priority" not in definition
    assert 'uart.open("/dev/ttyS0")' in definition, "phase code is kept"
    assert "instance other: Drv.Uart base id 0x200" in patched, "other instances are untouched"
    assert "# instance decoy: Drv.Uart" in patched


def test_active_surrogate_keeps_task_attributes():
    patched = patch_instance(INSTANCES, substitution(4, 18, has_queue=True, kind="active"))
    assert "queue size 10" in patched and "priority 30" in patched


def test_patch_refuses_when_source_does_not_match_the_model():
    with pytest.raises(WstsError, match="expected 'Drv.Uart'"):
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
    patched = patch_topology(TOPOLOGY, topology, ["wstsSetup", "wstsControl"])
    body = patched.split("deployment topology Demo {")[1]
    assert body.index("instance wstsSetup") < body.index("instance uart")
    assert "instance wstsControl" in body
    assert "# topology Fake { }" in patched


def test_packets_extend_an_existing_omit_list():
    patched = patch_packets(TOPOLOGY, 5, 5, ["Demo.wstsControl.SimTicks"])
    omit = patched.split("} omit {")[1]
    assert "Demo.wstsControl.SimTicks" in omit and "uart.Other" in omit
    assert patched.count("omit") == 1


def test_packets_gain_an_omit_list_when_there_is_none():
    text = TOPOLOGY.replace(" omit {\n      uart.Other\n    }", "")
    patched = patch_packets(text, 5, 5, ["Demo.wstsControl.SimTicks"])
    assert "} omit {" in patched and "Demo.wstsControl.SimTicks" in patched


def test_packets_lose_channels_the_surrogate_lacks():
    text = '''module Demo {
  deployment topology Demo {
    telemetry packets P {
      packet A id 1 group 1 {
        uart.BytesRecv, uart.BytesSent
        Demo.uart.Other
        other.BytesRecv
        other.uart.BytesRecv
      }
    } omit {
      uart.BytesRecv
    }
  }
}
'''
    patched = patch_packets(text, 3, 5, ["Demo.wstsControl.SimTicks"], ["Demo.uart.BytesRecv", "Demo.uart.BytesSent"])
    assert "BytesSent" not in patched
    assert patched.count("uart.BytesRecv") == 1, "only the other instance's spelling is left"
    assert "other.uart.BytesRecv" in patched and "other.BytesRecv" in patched and "Demo.uart.Other" in patched
    assert "Demo.wstsControl.SimTicks" in patched
    assert patched.count("{") == text.count("{"), "the omit list was already there"


def test_packets_without_wsts_channels_still_lose_dropped_ones():
    text = TOPOLOGY.replace("uart.Bytes", "uart.Bytes\n uart.Gone")
    patched = patch_packets(text, 5, 5, [], ["Demo.uart.Gone"])
    assert "uart.Gone" not in patched and "uart.Bytes" in patched and "uart.Other" in patched


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


def test_read_calls_expands_a_variable_the_file_sets_in_the_simulated_variant(tmp_path):
    """The overrides of a project that adds some only when it is not a test build"""
    cmake = tmp_path / "CMakeLists.txt"
    cmake.write_text(
        'if(BUILD_TESTING)\n    set(EXTRA "")\nelse()\n    set(EXTRA "${CMAKE_CURRENT_LIST_DIR}/A.fpp"\n'
        '            "${CMAKE_CURRENT_LIST_DIR}/B.hpp")\nendif()\n'
        'register_fprime_config(cfg CONFIGURATION_OVERRIDES "${CMAKE_CURRENT_LIST_DIR}/C.fpp" ${EXTRA} ${OTHER} INTERFACE)\n')
    (group,) = fpp.read_calls(cmake, "register_fprime_config")
    assert group["CONFIGURATION_OVERRIDES"] == [str(tmp_path / "C.fpp"), str(tmp_path / "A.fpp"), str(tmp_path / "B.hpp"), "${OTHER}"]


def test_instance_references_read_from_fpp_3_and_fpp_4_analyses():
    """FPP 3 writes the instance of an interface instance as {"astNodeId": n}, FPP 4 as n"""
    assert fpp._instance_node({"InterfaceComponentInstance": {"astNodeId": 17}}) == 17
    assert fpp._instance_node({"InterfaceComponentInstance": 17}) == 17
    assert fpp._instance_node({}) is None


def test_the_deployment_topology_is_chosen_among_the_subtopologies_of_its_directory():
    def topology(name, deployment):
        return fpp.Topology(name, Path("t.fpp"), 1, 1, deployment=deployment)

    sub_a, sub_b, main = topology("M.A", False), topology("M.B", False), topology("M.D", True)
    assert fpp.deployment_topologies([sub_a, main, sub_b]) == [main]
    assert fpp.deployment_topologies([sub_a, sub_b]) == [sub_a, sub_b]  # none is the deployment: the manifest decides
    assert fpp.deployment_topologies([main]) == [main]
    other = topology("M.E", True)
    assert fpp.deployment_topologies([main, other]) == [main, other]  # two deployments: the manifest decides
