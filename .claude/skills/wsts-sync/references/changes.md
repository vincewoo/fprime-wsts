# Drift: what each change means, and where it is settled

`scripts/drift.py` sorts what would change into areas. This file says, per area, what a
change usually comes from and what to do. "Decisions" links point into the
`wsts-configure` skill's `references/decisions.md`.

## Generation fails

The generator stops at the first problem. The usual ones after a flight update:

| Message | Cause | Settle |
|---|---|---|
| `manifest names instance 'X', which is not in topology T` | Flight renamed or removed `X` | The hint names the instances of the same component flight now defines that the last generation did not know. One candidate and a flight commit that says so: a rename, so rename `X` in every manifest key that names it (`substitute`, `keep`, `models[].endpoint`, `devices[].endpoint`, link ends in `wsts-system.yaml`) and in comments. No candidate: a removal, so drop those keys, and say what the simulation loses |
| `there is no endpoint 'E'` | A model or device names an endpoint whose instance was renamed or removed | As above: endpoints are named after their instances |
| A link end names an instance that is gone | The hub or link driver was renamed | Rename the end in `wsts-system.yaml` |
| An FPP error from the flight model | Flight sources do not analyse as the generator reads them (a new module the overlay does not include, a library not checked out) | Check the flight project's submodules; a new library may need `libraries` in the manifest |
| ID or port number checks fail | Flight added instances whose IDs collide with the simulation's (`ids` in `wsts-system.yaml`, the WSTS instances' base IDs) | Move the simulation's IDs, never flight's |
| A key the toolkit no longer knows | After a toolkit bump | The toolkit's commits or `docs/reference.md` name the replacement |

Re-run `drift.py` after each fix: the next problem only shows once this one is gone.

## Areas of the comparison

| Area | Usually from | Settle |
|---|---|---|
| `substitutions` renamed | Flight renamed an instance: same component, surrogate and reason | Mechanical once the manifest follows the new name. The rest of the comparison is under the new name, so what is left is real change |
| `substitutions` added | A new hardware instance, replaced by the catalog (or by a link of the system) | Its endpoint needs a model or not ([decisions §8](../../wsts-configure/references/decisions.md#8-devices-behind-the-buses)) |
| `substitutions` removed | Flight removed the instance, or a manifest entry no longer applies | Drop what the manifest says about it |
| `substitutions` changed | Another surrogate or reason: a catalog change (toolkit bump, project catalog), or flight changed the instance's component | Read why; a project surrogate may need updating to the new component's ports |
| `kept hardware` added | A hardware driver the catalog does not know | Substitute it or keep it with a reason ([decisions §5](../../wsts-configure/references/decisions.md#5-hardware-instances)) |
| `endpoints` added, nothing answering | A new instance's endpoint | What flight software sees there (the detail says); a model, or unanswered on purpose |
| `endpoints` removed | Its instance went | Its model and device entries went with it in the manifest |
| `models` changed | The endpoint was renamed (follows a rename), or the manifest changed | Mechanical with a rename; else check the model's parameters |
| `links` | A link driver renamed or removed, or `wsts-system.yaml` changed | Both ends must still pair up ([decisions §10](../../wsts-configure/references/decisions.md#10-systems)) |
| `register blocks` | The register interface component or its drivers changed | The register model's map may need the new registers; flight's register map is in the driver |
| `simulator components` | The manifest's `instances`, or a renamed flight instance they connect to | Their connections in the manifest follow flight names |
| `project modules` added | A new flight component | Read it: does it open a device, call an SDK, wait on the host? `fprime-wsts survey` flags components whose sources open devices |
| `BUILD_TESTING variants` added | A module whose CMake differs under `BUILD_TESTING` | Whether its stub variant is wanted (`cmake.stub_variants`, [decisions §9](../../wsts-configure/references/decisions.md#9-what-does-not-build-on-the-host)) |
| `dictionary: … missing` added | A replaced driver brings commands, events or channels the surrogate lacks | Ground procedures or tests that use them fail in simulation: a mirror surrogate (`docs/surrogate-guide.md`) where they matter |
| `dictionary` `flight_ids_unchanged` | Flight IDs moved | Never expected: report it as a toolkit problem |
| `time` | The tick or time source changed | Check the catalog still replaces it ([decisions §6](../../wsts-configure/references/decisions.md#6-ticks-and-time)) |
| `waits on the host` | New code that sleeps, reads the clock, starts threads or blocks on I/O | Matters in stepped time only (`docs/stepped-time.md`) |
| `capacity` | Flight added commands or parameters | Near full: raise the table in the simulation's configuration overrides; full: the build or start-up fails |
| `warnings` added | Anything the generator could not settle | Each says what it needs |
| `host requirements: modules` added | A new module the build compiles on the host | `fprime-wsts check-host` before building |
| `fidelity notes`, `channels added by WSTS`, `other` | Follow from the rest | Mention only if they change what the user should expect |

## Files that change with no report change

The generated tree holds copies of flight sources (the topology's set-up code, `main`),
patched where needed. A flight edit to them changes files and nothing in the report.
That is mechanical: regenerate. Only look closer when the build or run then fails.

## After a toolkit bump

Everything in the generated tree can change at once: templates, paths, catalog entries,
telemetry. Read the toolkit's commits between the old and new pin
(`git -C lib/fprime-wsts log --oneline <old>..<new>`) for renamed manifest keys, changed
defaults and catalog changes, and expect a full rebuild.
