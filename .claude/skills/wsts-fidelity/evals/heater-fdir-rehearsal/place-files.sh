#!/usr/bin/env bash
# The flight project and its simulation project (the example's), generated and committed
set -eu
fixture="$(cd "$(dirname "$0")/../../../wsts-configure/evals/_fixture" && pwd)"
"$fixture/sibling-flight.sh" orbiter-fsw
"$fixture/sibling-sim.sh" orbiter-fsw
