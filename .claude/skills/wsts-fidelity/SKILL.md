---
name: wsts-fidelity
description: Review how far an fprime-wsts simulation can be trusted for a given purpose, and write it up. Reads the generated reports of a simulated deployment or system (and optionally a run's bus traffic), inventories what is real flight code, what is replaced, and what answers on every bus endpoint (link, simulator component, project model, skeleton, stock model, or nothing), then ranks the gaps against what the simulation is for (ops and ground procedure rehearsal, fault response testing, regression CI, driver or FPGA work, multi-computer integration) and gives the step that closes each one. Writes a FIDELITY.md review in the simulation project. Use this whenever someone asks what their simulation covers or does not, how realistic or trustworthy it is, whether they can use it for testing X, what is stubbed or modelled, what is missing before a test campaign or review, or wants a fidelity or coverage report for their team, even if they do not say "fidelity".
---

# Reviewing the fidelity of a simulation

A simulated deployment is flight software with specific things replaced. The toolkit
writes down exactly which in each element's `wsts-report.json`. What it cannot say is
whether those replacements matter: that depends on what the simulation is for. A stock
register model is fine for a GDS walkthrough and useless for testing the driver that reads
it.

Your job is to turn the reports into a judgement the user's team can act on: what this
simulation is good for, what it is not, and what to do about the gaps that matter for
their purpose. The user's job is to say what the purpose is, and to accept or reject your
ranking.

Read `docs/fidelity.md` of the checkout if you have not this session: it is the general
account that this review makes specific.

## Ground rules

- **Review only.** Do not change manifests, models, flight code or the generated tree. The
  output is the review and recommendations; making the fixes is other work (the
  `wsts-device-model` skill, `wsts-configure`, or the user).
- **Ground every claim** in this simulation's reports, sources or a run: name the
  endpoint, instance, model class or dictionary item. A claim that only `docs/fidelity.md`
  supports is a general limitation: label it as such, once, not per element.
- **Do not overstate.** "Answered by a project model" is not "accurate". A model is
  checked against the device only when the project has captures of the device and the
  conformance check passed; say which models are and which are not.
- **Rank for the stated purpose**, and say what the ranking assumes. When the purpose is
  unknown, ask once; if the user cannot say, review for the purpose the project's tests
  and README suggest, and say so.

## Scripts

Run with the Python environment where fprime-wsts is installed. `<skill>` is this skill's
directory (`fprime-wsts/.claude/skills/wsts-fidelity`).

| Script | What it does |
|---|---|
| `scripts/inventory.py GENERATED_DIR [--summary RUN.json \| --live] [--json]` | Per element: flight code (project modules, libraries, topology and main sources, modules built with their BUILD_TESTING stub), replaced instances, and every bus endpoint with what answers it and its fidelity class (`link`, `simulator component`, `project model`, `project model (skeleton: has TODOs)`, `stock model`, `nothing` with what flight software sees). Finds a project model's sources and the device's `devices/<name>/spec.md` and captures (what `wsts-device-model` leaves). Also: kept hardware drivers, dictionary items missing, time and what waits on the host, capacity, warnings. Derives gaps with a severity hint and the next step. With `--summary` (the JSON of `fprime-wsts run --json`) or `--live` (a running simulation), each endpoint's traffic: what flight software used, and what went unanswered |

The severity hints are the toolkit's view without a purpose. You re-rank them in step 3.

## Workflow

### 1. The purpose

Ask what the simulation will be used for, unless the user said. Offer the usual ones
(`references/purposes.md`): rehearsing operations and ground procedures, testing fault
detection and response, regression tests in CI, developing a driver or FPGA interface,
integrating several computers, training. Also ask what is out of scope, if anything
("we know the camera is stubbed").

### 2. The facts

Find the generated directory (`<sim project>/wsts/<deployment or system>`); generate it
first if it is missing or stale (`fprime-wsts generate <manifest> --check` says), and say
so. Then:

```bash
python3 <skill>/scripts/inventory.py <generated dir>
```

Traffic makes the review much stronger: it tells which endpoints flight software uses
and what it got no answer from. If the simulation is built, run it briefly and pass the
summary:

```bash
fprime-wsts run <generated dir> --duration 20 --auto-ports --json > /tmp/run.json
```

```bash
python3 <skill>/scripts/inventory.py <generated dir> --summary /tmp/run.json
```

A run starts only what start-up and the rate groups do. Endpoints used only by commands
show no traffic until a command is sent; say so instead of calling them unused.

Then read what the inventory cannot judge:

- the flight code behind each endpoint that matters for the purpose: what the device
  manager checks, which errors it handles, which telemetry it reports;
- each project model and simulator component, enough to say what it does (fixed values,
  a state machine, a protocol, errors on demand) and what it does not;
- the manifest's comments and the project's README: decisions already made on purpose;
- the project's tests and scenarios: what is already exercised.

### 3. Judge the gaps

For each gap, decide how much it matters **for this purpose**, using
`references/purposes.md`: which areas raise or lower it, and the questions that settle it.
Merge the toolkit's gaps with the ones only reading revealed (a model that never reports
an error the FDIR logic waits for; a sensor that is a constant). Give each one:

- **severity for the purpose**: blocks it, weakens it, or does not matter for it;
- **what it costs**: the test or procedure that cannot be trusted, in the user's terms;
- **next step**: the concrete action, its size, and who or what does it (the
  `wsts-device-model` skill for a device model, a mirror surrogate for dictionary items, a
  scenario or test, a manifest change, a flight change to propose).

Note what is **good**: endpoints with checked models, mirror surrogates that keep the
dictionary, links between elements. A review that only lists gaps hides what can be
relied on.

### 4. Write the review

Write `<sim project>/FIDELITY.md` from `assets/fidelity-review-template.md`: purpose and
assumptions, verdict, what can be relied on, the ranked gaps, general limitations (from
`docs/fidelity.md`, once), and how the review was made (the commands, and the flight and
toolkit commits it was made against). Keep it to what the team will act on. The full
inventory can go in an appendix, or be regenerated with the command.

If a `FIDELITY.md` exists, update it: keep the user's notes, refresh the facts, and say
what changed since the last review.

### 5. Hand off

Tell the user, briefly: the verdict for their purpose, the two or three gaps that matter
most with their next steps, and where the review is. Offer to start on the first next step
(often the `wsts-device-model` skill for one device). Commit only if asked.

## Reference files

- `references/purposes.md`: what each purpose needs, which gaps matter, and the questions that settle them
- `assets/fidelity-review-template.md`: the review's layout
- `docs/fidelity.md` of the checkout: the general differences from flight
