#!/usr/bin/env bash
# The flight project beside the checkout, and the user's simulation project with a
# manifest that does not generate yet
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
"$here/../_fixture/sibling-flight.sh" orbiter-fsw
rm -rf ../orbiter-fsw-sim
mkdir -p ../orbiter-fsw-sim
cp "$here/files/wsts.yaml" ../orbiter-fsw-sim/
