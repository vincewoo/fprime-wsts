---
type: llm
focus: trace
weight: 1
---

The missing <board/revision.h> is given a stand-in in the simulation project through cmake.include_early (or the header is reported for the user to supply), and the memcpy compile error in sensor-lib is settled with cmake.cxx_flags -include cstring, not by editing sensor-lib.
