# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The project's agent guide is `AGENTS.md`: the map of the repository, the commands, the rules
of thumb and how to use a simulation in a development loop. It is imported here and is the
place to keep that guidance up to date:

@AGENTS.md

## Additions for Claude Code

Running part of the Python suite (the whole of `pytest python/tests` takes about ten minutes):

```bash
pytest python/tests/test_manifest.py
```

```bash
pytest python/tests/test_generate.py -k <name>
```

The end-to-end tests as CI runs them, after `fprime-wsts build examples/mission-sim/wsts/Mission`:

```bash
D=examples/mission-sim/wsts/Mission
pytest tests/e2e -p fprime_wsts.pytest_plugin --wsts-system "$D" --wsts-gds --dictionary "$D/wsts-logs/MissionDictionary.json"
```

Add `--wsts-auto-ports` when another run may be up, and `--wsts-stepped` for stepped time
(lockstep for a system). One file: replace `tests/e2e` with e.g. `tests/e2e/test_payload.py`.

CI (`.github/workflows/ci.yml`) also runs these, which a change should keep passing:

```bash
fprime-wsts generate examples/mission-sim/wsts-system.yaml --check
```

```bash
fprime-wsts check-host examples/mission-sim/wsts-system.yaml
```

The fprime submodule needs googletest for the C++ unit tests:

```bash
git -C lib/fprime submodule update --init --depth 1 googletest
```
