---
tags: [wsts-device-model]
max_turns: 80
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Attached are the captures from last week's bench session with our SS-4 sun sensor (s/n 117) and its ICD. During the session the sun simulator lamp was at alpha = +12.34°, beta = −5.00°. The sensor will be on the Cdh UART endpoint `sunUart`. Please write the WSTS device model for it and show me it matches what the unit did. Model code goes in examples/mission-sim/Models. Skip the full F´ build.

Input files: ss4-icd.md, ss4-bench-2026-11-06.jsonl
