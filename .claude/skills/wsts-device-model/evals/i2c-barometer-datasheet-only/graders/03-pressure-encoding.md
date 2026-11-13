---
type: llm
focus: trace
weight: 1
---

Pressure is settable in hPa through a model parameter and encoded as round(hPa*4096) in 24-bit two's complement, low byte at 0x28, high byte at 0x2A.
