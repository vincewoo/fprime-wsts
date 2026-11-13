#!/usr/bin/env python3
"""Stand-in for a TS-200 bench unit: a pseudo-terminal at LINK that behaves like firmware 2.1.

Differences from datasheet Rev C, as on the real firmware: errors are "?E02" (unknown
command) and "?E03" (bad argument), and RATE accepts 0..60. The temperature wanders around
22.7 C with a few hundredths of noise. Every command received is logged to LOG.

    fake_ts200.py LINK LOG [SECONDS]
"""
import os, pty, random, select, sys, time, tty

link, log = sys.argv[1], sys.argv[2]
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 3600
master, slave = pty.openpty()
tty.setraw(slave)
if os.path.lexists(link):
    os.unlink(link)
os.symlink(os.ttyname(slave), link)
rng = random.Random(7)
units, rate, cal = "C", 0, 0.0
buf, start, next_stream = b"", time.time(), None

def temp():
    c = 22.70 + cal + rng.uniform(-0.04, 0.04)
    return c if units == "C" else c * 9 / 5 + 32

def fmt():
    return f"T={temp():+06.2f}\r\n".encode()

with open(log, "a") as logf:
    while time.time() - start < seconds:
        r, _, _ = select.select([master], [], [], 0.02)
        now = time.time()
        if rate and next_stream and now >= next_stream:
            os.write(master, fmt())
            next_stream = now + rate
        if not r:
            continue
        buf += os.read(master, 256)
        while b"\r\n" in buf:
            line, buf = buf.split(b"\r\n", 1)
            cmd = line.decode("latin-1")
            logf.write(f"{now - start:.3f} {cmd!r}\n"); logf.flush()
            time.sleep(0.008)
            if cmd == "*IDN?":
                out = "ACME,TS-200,0042,2.1"
            elif cmd == "T?":
                os.write(master, fmt()); continue
            elif cmd == "STAT?":
                out = "S=0x00"
            elif cmd.startswith("RATE "):
                try:
                    n = int(cmd[5:])
                    assert 0 <= n <= 60
                    rate, next_stream, out = n, now + n, "OK"
                except Exception:
                    out = "?E03"
            elif cmd in ("UNITS C", "UNITS F"):
                units, out = cmd[-1], "OK"
            elif cmd.startswith("CAL "):
                try:
                    v = float(cmd[4:]); assert -5 <= v <= 5
                    cal, out = v, "OK"
                except Exception:
                    out = "?E03"
            elif cmd == "FACTORY":
                cal, units, rate, out = 0.0, "C", 0, "OK"
            else:
                out = "?E02"
            os.write(master, (out + "\r\n").encode())
