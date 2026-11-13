---
tags: [wsts-device-model]
max_turns: 80
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Payload is getting a NOVA PX-310 barometer on the existing I2C bus (endpoint i2cBus, address 0x5D). We don't have hardware yet — the engineering unit arrives next month — but I want the sim model now so we can start integrating flight software against it. Datasheet is px310-datasheet.md. Our tests need to be able to set the pressure in hPa. Skip the full F´ build.

Input files: px310-datasheet.md
