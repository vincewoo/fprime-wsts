#!/bin/bash
# Run once when the container is created, in the checkout: the pinned submodules,
# googletest for the C++ unit tests, and the toolkit with F Prime's tools (fprime-wsts is
# then on PATH, and so is the MCP server that .mcp.json names)
set -e
git config --global --add safe.directory "$PWD"
git submodule update --init --depth 50
git -C lib/fprime submodule update --init --depth 1 googletest
pip install -r lib/fprime/requirements.txt -e './python[mcp]' pytest jsonschema
fprime-wsts --version
