---
type: llm
focus: trace
weight: 1
---

Payload's fpgaRegs (SensorLib.RegisterInterface) is substituted with Wsts.SimRegister. If a register block model is attached, its identity register at offset 0 is 0xF00D0001, read from Components/FpgaMonitor.
