---
type: llm
focus: trace
weight: 1
---

The model buffers bytes across onBytes calls, so a frame split over several sends is still parsed.
