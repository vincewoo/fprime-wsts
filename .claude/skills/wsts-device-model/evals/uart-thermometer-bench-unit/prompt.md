---
tags: [wsts-device-model]
max_turns: 80
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

We're adding an ACME TS-200 serial thermometer to the Payload simulation in examples/mission-sim (it'll sit on a UART endpoint called `tsUart`). The datasheet is ts200-datasheet.md and our bench unit is plugged in at ./ttyTS200 (9600 8N1). Can you write the WSTS device model for it and check it against the real unit? I'm away from my desk for the afternoon so I can't approve anything — don't change anything on the sensor. No need to run the full F´ build, just make sure the model itself is right.

Input files: ts200-datasheet.md
