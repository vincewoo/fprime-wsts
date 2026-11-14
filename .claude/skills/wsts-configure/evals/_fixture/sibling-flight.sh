#!/usr/bin/env bash
# Places a copy of the example flight project (examples/mission) beside the checkout, as a
# user's own project would be: ../<name>, with its own lib/ (links to the checkout's
# submodules) and settings.ini paths of its own. It is committed in a git repository of
# its own, so that a grader can tell whether anything in it was changed.
#
# Usage (from the checkout root): sibling-flight.sh <name>
set -eu
name="$1"
root="$(pwd)"
dest="$(cd .. && pwd)/$name"
rm -rf "$dest"
cp -R "$root/examples/mission" "$dest"
mkdir -p "$dest/lib"
ln -s "$root/lib/fprime" "$dest/lib/fprime"
ln -s "$root/lib/fprime-vxworks" "$dest/lib/fprime-vxworks"
sed -i 's#^framework_path: .*#framework_path: lib/fprime#; s#^library_locations: .*#library_locations: lib/fprime-vxworks:lib/sensor-lib#' "$dest/settings.ini"
# The flight project of a real user does not point at the toolkit's example
sed -i 's#^; WSTS toolkit example: #; #' "$dest/settings.ini"
git -C "$dest" init -q
git -C "$dest" add -A
git -C "$dest" -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "flight software"
