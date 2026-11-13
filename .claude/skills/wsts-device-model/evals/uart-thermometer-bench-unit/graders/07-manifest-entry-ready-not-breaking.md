---
type: llm
focus: trace
weight: 1
---

A models entry for the TS-200 (kind bytestream, endpoint tsUart) is ready in payload.yaml or the final message, and it is not left live: the flight Payload topology has no tsUart driver, so a live entry would make fprime-wsts generate fail. The final message says what the entry waits for.
