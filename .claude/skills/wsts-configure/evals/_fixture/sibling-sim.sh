#!/usr/bin/env bash
# Places a copy of the example simulation project (examples/mission-sim) beside the checkout,
# as the simulation project of the flight project ../<name> (place that one first, with
# sibling-flight.sh): ../<name>-sim, pinning this checkout as lib/fprime-wsts, generated,
# and committed in a git repository of its own, so that a grader can tell what changed.
#
# Usage (from the checkout root): sibling-sim.sh <name>
set -eu
name="$1"
root="$(pwd)"
dest="$(cd .. && pwd)/$name-sim"
rm -rf "$dest"
cp -R "$root/examples/mission-sim" "$dest"
rm -rf "$dest/wsts"
sed -i "s#^project: ../mission\$#project: ../$name#" "$dest/cdh.yaml" "$dest/payload.yaml"
sed -i 's#\$schema=../../schemas/#$schema=lib/fprime-wsts/schemas/#' "$dest"/*.yaml
mkdir -p "$dest/lib"
ln -s "$root" "$dest/lib/fprime-wsts"
cat > "$dest/.gitignore" <<'IGNORE'
build-*/
wsts-logs/
wsts-files/
wsts-host.*
IGNORE
(cd "$dest" && fprime-wsts generate wsts-system.yaml > /dev/null)
git -C "$dest" init -q
git -C "$dest" add -A
git -C "$dest" -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "simulation project"
