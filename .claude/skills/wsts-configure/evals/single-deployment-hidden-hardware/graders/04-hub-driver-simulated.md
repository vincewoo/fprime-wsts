---
type: llm
focus: trace
weight: 1
---

Payload's hubDriver (Drv.TcpClient) is substituted with Wsts.SimByteStream for the standalone run, with the reason in the manifest or the final message. If the agent first kept it as a real socket, it diagnosed the resulting start-up assert (event loop through the hub while the link is down, re-entering the buffer manager) and reported it as flight software behaviour rather than a toolkit bug.
