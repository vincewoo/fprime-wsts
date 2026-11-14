---
type: llm
focus: trace
weight: 1
---

The build gaps are settled in the simulation project: cmake.stub_variants with Components/Imager, a stand-in board/revision.h added through cmake.include_early (or a host.cmake), and cmake.cxx_flags with -include cstring (for lib/sensor-lib/SensorLib/Units or the whole build). BUILD_TESTING is not set for the whole build, and no generated file under wsts/ was edited by hand.
