#!/usr/bin/env bash
# Puts the datasheet in place and starts the stand-in bench unit at ./ttyTS200; its
# source is moved out of the working directory so that the run cannot read it.
set -eu
here="$(dirname "$0")"
cp "$here/files/ts200-datasheet.md" .
dev="$(mktemp -d)"
cp "$here/files/fake_ts200.py" "$dev/"
nohup python3 "$dev/fake_ts200.py" "$PWD/ttyTS200" "$dev/ts200-commands.log" 3600 >/dev/null 2>&1 &
echo "$dev/ts200-commands.log" > .ts200-log-path
sleep 1
