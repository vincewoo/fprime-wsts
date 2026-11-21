# Fidelity review: <simulation project>

<!-- Written by the wsts-fidelity skill. Facts come from the generated reports and the
     commands under "How this review was made"; judgements are for the purpose below. -->

**Purpose:** <what the simulation will be used for, in the team's words>
**Out of scope:** <what the team already knows it does not cover, if anything>
**Verdict:** <one or two sentences: fit for the purpose, fit with the listed gaps closed,
or not fit, and why>

## What can be relied on

<!-- Flight code that runs unchanged, endpoints with checked models, mirror surrogates that
     keep the dictionary, links between elements. Name them. -->

- ...

## Gaps, for this purpose

<!-- Most severe first. One row per gap; merge the inventory's with what reading the code
     found. Severity: blocks / weakens / does not matter (for this purpose). -->

| # | Severity | Where | Gap | What it costs | Next step |
|---|---|---|---|---|---|
| 1 | blocks | `<element>.<endpoint or instance>` | ... | <the test or procedure that cannot be trusted> | <action, size, who or which skill> |

## Endpoints

<!-- Per element: what answers each bus endpoint, its fidelity, and whether flight software
     used it in the run. The inventory's table, trimmed to what the purpose needs. -->

| Element | Endpoint | Kind | Answered by | Fidelity | In the run |
|---|---|---|---|---|---|

## General limitations

<!-- From docs/fidelity.md, once, and only those that bear on the purpose. -->

- ...

## How this review was made

- Generated directory: `<path>`, flight project at `<commit>`, toolkit at `<commit>`
- `python3 <fprime-wsts>/.claude/skills/wsts-fidelity/scripts/inventory.py <generated dir> [--summary <run.json>]`
- Run: `<the fprime-wsts run command, or "no run">`
- Read: <the flight components, models and tests read>

## Changes since the last review

<!-- On an update: what moved (new endpoints, models written, gaps closed). -->
