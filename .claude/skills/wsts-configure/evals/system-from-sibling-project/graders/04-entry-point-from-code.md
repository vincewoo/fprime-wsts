---
type: llm
focus: trace
weight: 1
---

The Cdh manifest has main.generate with declarations of cdhStart and cdhStop and start: cdhStart(hostname, port, 50050). The hub port 50050 was taken from the flight code (Entry.cpp's start-up script comment or Payload's Main.cpp), not invented, and the final message says where it came from or that it is an assumption.
