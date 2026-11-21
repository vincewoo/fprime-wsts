#!/usr/bin/env bash
# The flight project and its simulation project, generated from the flight project as it is
set -eu
fixture="$(cd "$(dirname "$0")/../../../wsts-configure/evals/_fixture" && pwd)"
"$fixture/sibling-flight.sh" orbiter-fsw
"$fixture/sibling-sim.sh" orbiter-fsw
