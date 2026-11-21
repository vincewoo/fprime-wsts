---
type: llm
focus: trace
weight: 1
---

The review says that the over-temperature switch (switchPin, a GPIO input) has nothing behind it, so a trip happens only when it is forced (gpio_force, a scenario, or a SIM command), not as a consequence of the heater being on; it states whether that is acceptable for a rehearsal and names what would make the trigger emergent (a model that ties heaterPin to switchPin, e.g. tripping some time after the heater turns on).
