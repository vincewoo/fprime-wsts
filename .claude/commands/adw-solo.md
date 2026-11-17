---
description: Solo ADW loop (one session does everything) — decompose a goal into briefs, dispatch, gate, audit, one branch per brief, commit, stop only at the merge report. Not the hub: for that use adw-orch.
---

Run the ADW loop for: $ARGUMENTS

**You are the PM. Run the whole queue unattended and come back ONCE, at the end, with the
merge report.** Every call the briefs leave open is yours to make: cap values, naming,
weights, branch stacking, fixing a worker's collateral damage. State each decision in the
final report instead of asking for it up front.

**Mode A — a goal was given.** Decompose it into atomic ~30-line single-responsibility
briefs using the skeleton in CLAUDE.md (Deliverable / Interface Contract / Behavior /
Constraints / Out of Scope / Acceptance Check). Write each to `specs/tasks/NN-<slug>.md`
with the next unused NN, append them in order to `specs/adw_queue.md`, print the list, and
**keep going** — no approval gate on the decomposition.
Write `specs/tasks/NN-<slug>.audit.md` beside each brief in the same turn: what must NOT
change, the failure modes it invites, how to tell a real pass from a fake one. Never shown to
the worker.

**Mode B — no argument.** Work the existing `specs/adw_queue.md`.

**0. Refuse if this is the hub's window.** `adw orch-hook --check`.
Exit 0 means you are in the hub's orchestrator pane: print the line it gave you, tell the user
to use `/adw-orch` instead, and **stop** — do not claim the role, plan, branch or dispatch.
Exit 1 means you are not, and the loop continues.

**1. Claim the role first.** `adw claim-orch --pid $PPID --window "$(tmux display-message -p '#{session_name}:#{window_name}')"`. The window must be this project's own orch window, never another project's, and the hub refuses a window in another session. Exit 0 and you are the orchestrator. Exit 1 means another session holds it: print the
holder line the command gave you, offer `adw relay --message "<goal>"` to hand the goal
over or `adw claim-orch --force` to move the role here, and stop. A project that pins its own engine runs `python3 tools/adw/adw_hub.py` wherever this says `adw`.

Write every Interface Contract line with a real declaration keyword — `` - `func foo(a, b)` ``,
`` - `var bar: int = 0` `` — even inside prose bullets. `audit_pack.py` greps for the keyword;
a contract of bare names (`` `foo()` ``) silently checks nothing, and the pack now says so
loudly instead of pretending the brief had no contract.

Then, for each queued brief in order:

2. **Branch.** `git checkout -b adw/<NN-slug>` off main. One branch per brief, never commit
   straight to main. If brief B cannot compile or test without brief A's code, stack B on A's
   branch instead and say so in the report — do not merge anything to main yourself.
3. **Dispatch.** `python3 tools/adw/agy_write.py --brief specs/tasks/NN-slug.md --file <target> [--context_files ...]`.
   Big file (`ui_manager.gd`, `bot.gd`, `game_manager.gd`)? The Gemini tier rewrites whole
   files, so those go to the Qwen tier in patch mode: `.venv-adw/bin/python tools/adw/local_write.py --symbol <func> ...`
   (patch mode is GDScript-only — a Python target is a whole-file `local_write.py` call).
   **`tools/` Python targets go to Qwen FIRST**, agy is for `.gd`. On 2026-08-18 agy failed
   3/3 on one (wrote nothing, invented a completion record) while one-shotting GDScript; it is
   weakest exactly where the gate is also blind to the language.
   On `FAILED`, retry once on the other tier before surfacing the failure.
   A part of a brief that is a ≤15-line surgical edit (a var declaration, a reset line, a dict
   key) is yours to `Edit` directly — dispatch the substantial part, not the one-liners.
4. **Gate.** `tools/adw/gate.sh --files <targets>`. Add `--all-tests` whenever the changed file
   has few or no related suites, or when the brief's own acceptance depends on a suite that
   kinship-matching will not pick (`round_selection_ui.gd` does NOT pull in
   `skip_reward_test.gd`). For a non-GDScript target the gate proves nothing — run the brief's
   Acceptance Check command and treat its output as the evidence.
5. **Audit.** `python3 tools/adw/audit_pack.py --brief specs/tasks/NN-slug.md --files <targets>`.
   Audit that pack, not the source files.
6. **Fix collateral yourself.** Workers reliably damage things next to the target: deleted
   comments and blank lines around a patched function, dead null-guards, a stripped trailing
   newline, verbose narration comments. Repair those with a direct `Edit`, re-run the gate, and
   list what you repaired in the report. Do not burn a dispatch on a two-line cleanup.
7. **Commit** to the branch, mark the brief `<!-- adw: done -->`, remove its line from
   `specs/adw_queue.md`, and go to the next brief. Commit only the files the brief owns —
   never the user's unrelated working-tree changes. No commit co-author.

At the **end of the queue**: `tools/adw/gate.sh --full`, then ONE report. Per brief: branch,
files touched, gate verdict, audit verdict, worker collateral you repaired, and every decision
the brief did not specify. End with the merge order. The user merges.

**Stop early and report only for:** a brief failing 3 dispatch attempts on both tiers, a
`MISSING`/`ELSEWHERE` contract row you cannot explain, a gate failure you cannot fix inside the
brief's scope, or anything that would need a decision outside the goal you were given (new
gameplay scope, deleting user data, touching main). Do not stop for ordinary judgement calls.

- **A failed gate or audit is not a stopping point.** Fix it and re-run, up to three attempts
  on one brief, deciding on your own what the fix is; do not ask for a go-ahead between
  attempts. Stop and report only when the third attempt still fails, and then say what each
  attempt changed and why you think the brief itself is wrong.

Non-negotiables, each one earned the hard way:

- **A worker's reply text is not evidence.** `agy` returns `status: SUCCESS` with a confident
  `DONE` having written nothing. Only the target's mtime and the gate verdict count. The
  wrappers enforce this; never dispatch by calling `agy` directly.
- **A gate log containing `Godot console exe not found` is a FAILURE, not a pass.**
  `validate.py` skips parse/tests/scenarios/smoke with a one-line warning when the binary is
  missing, so a broken environment reports green while only gdlint ran. Always invoke the gate
  through `tools/adw/gate.sh`.
- **Check what the gate actually covered.** The pack prints `gate did not check: ...` when the
  log is from a different file set, and `1 of 59 suite(s) related` means almost nothing ran.
- **Never batch-audit.** One brief, one audit pack, one verdict.
- **Never sign off on a red or partial contract check.** `MISSING`/`ELSEWHERE` means the call
  sites are broken even though the gate passed.
- **A `gemini_completed_tasks/*.md` record is the worker's own claim, not evidence.** Its
  pasted "gate output" has been fabricated (a `--full` run reporting 1121 test cases when the
  suite is 539). ADW dispatches are exempt from writing one at all
  (`.agents/rules/audit-report.md`); if one appears anyway, leave it untracked and ignore it.
- If a brief fails 3 dispatch attempts on both tiers, stop and report. Do not write the module
  yourself unless the user says to.
