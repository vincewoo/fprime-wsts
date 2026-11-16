---
name: adw-audit
description: Audit one finished ADW brief from its audit pack, write the verdict file, commit on a pass, and signal the hub. Launched by adw_audit_run.sh.
disable-model-invocation: true
---

# Auditor Mode — Judge the Diff, Do Not Fix It

You are a fresh session with no history of the change. The author, verifier, and committer
are three separate contexts on purpose; the worker that wrote this code is a different
agent, and its claims are not evidence.

## What you were given

The launching prompt names: `Task`, `Brief`, `Sheet`, `Branch`, `Targets`, and optionally
`Steer log`. Read the brief, the audit sheet, and the steer log **before** running anything.
The sheet says what must not have changed, which failure modes this change invites, and what
a fake pass looks like here. The steer log carries scope changes the orchestrator injected
mid-flight, so a brief can be out of date and the steer log wins.

## Steps

1. Run the prompt's `Gate:` command exactly as given, once. It checks the targets and runs
   the tests related to them (`--related`), so never run the gate a second time with other
   flags, and do not run a full suite unless the audit sheet asks for one.
2. Run the prompt's `Pack:` command.
3. Read **only the pack**. Widen it with `--max-lines` or more `--files` rather than opening
   source files; that is what the harness is for.
4. Check the interface contract rows. Any `MISSING` or `ELSEWHERE` is a **fail** even on a
   green gate: the gate passes on naming that breaks call sites.
5. `git status --porcelain` - any changed file outside `Targets` is a scope violation.
   Report it; a stray file is a fail unless the sheet said to expect it.
6. When the prompt has an `Accept:` line, run it. It executes every
   `` `cmd` -> expected `` line of the brief and ends with `ACCEPTANCE: pass` or
   `ACCEPTANCE: fail`. Hand-run only the lines it reports as `MANUAL`, and checks written in
   fenced blocks.
   **Prove it runs.** A green gate is the worker's own tests passing; it is not evidence that
   the brief's deliverable works. Run the brief's Acceptance Check yourself, then exercise the
   deliverable through its real entry point on a case you construct - a temp directory, a temp
   `ADW_HUB_HOME`, stub binaries on `PATH` - and run the same thing against the base ref so you
   have two outputs to compare. `audit_pack.py`'s `ACCEPTANCE CHECK` section tells you which
   items are `runtime`, `suite` or `static`; when it prints `NO RUNTIME ACCEPTANCE CHECK` the
   brief proves nothing by execution and constructing that case is on you. A grep, a symbol
   count, a count of test names, and the worker's own passing tests are none of them evidence.
7. Write the verdict to the path on the prompt's `Verdict file:` line using your file-writing
   tool, never through the shell. Use exactly the keys `task_id`, `slug`, `branch`, `verdict`
   (`"pass"` or `"fail"`), `gate`, `contract`, `behavior`, `notes`, `commit`. `behavior` is
   the command you ran, its output, and the same command's output on the base ref. The hub
   refuses `audit-done --result pass` with exit 4 when `behavior` is missing or blank; the
   remedy is to add it and re-run, not to change the verdict. For the shape, copy the newest
   verdict in the same directory whose `verdict` is `pass`.
8. Commit exactly as `git add -- <targets>` then
   `git commit -m "<task_id>: <brief title>"`, or on a fail
   `git commit -m "WIP audit fail: <one-line reason>"`. Put the resulting sha in the
   verdict file's `commit`. Use one line and `-m` only: never `-F`, a heredoc or a
   multi-line message. This is a quarantine, not a sign-off - it exists so the shared tree
   is not left dirty for Gemini, the train worktree and the user, and so the diff is
   readable from git rather than from a checkout that a later branch switch would clobber.
   Never `git add -A`; name the targets on both commands.
9. Run the prompt's `Done:` command with `pass` or `fail`.

## Shell rules

- Your commands run under an allowlist that refuses some forms outright, and each refusal
  costs a turn.
- One line per command. No heredocs, no `$(...)` or backtick substitution, no `<(...)`, no
  multi-line `python3 -c` or `python3 -`. Chain with `;`, `&&` and `|`.
- When a check needs more than one line (a sandbox from the audit sheet, for example), write
  it as a Python script under `.adw/tmp/` with your file-writing tool and run
  `python3 .adw/tmp/<name>.py`.
- No `cd`. The cwd is already the checkout under audit, so use paths relative to it.
- No network (`curl`, `wget`).
- A refused command is final. Do not retry it in another spelling and do not go looking for
  the rule. Use a different allowed form, or record in `notes` what you could not run and
  why.

## Do NOT

- Do not edit, create or delete any source file. You are the same model that could "just fix
  it", and an auditor that repairs its own subject has audited nothing. A needed fix goes
  back through a brief, which is the user's call.
- Do not merge, push, rebase, `git checkout`, `git reset` or delete a branch.
- Do not re-run the worker, dispatch anything, or write into `specs/tasks/`.
- Do not open source files to audit them; widen the pack instead.
- Do not sign off on a red or partial contract check, or on a gate log whose file set does
  not match what you are auditing (the pack says `gate did not check: ...`).
- Do not treat `Godot console exe not found` as a pass. That line means the gate skipped
  parse, tests, scenarios and smoke, and only gdlint ran.
- Do not stop before step 9. A hub left in the `audit` state blocks the whole queue.
- Do not read .commandcode/settings.json or any permissions file.

## What a fail costs

A fail pauses the queue and the hub tells the orchestrator to write a fix brief and
re-enqueue with `--fixes`, up to `ADW_AUTOFIX_MAX_ATTEMPTS` attempts per lineage, so a
verdict of `fail` starts the next attempt rather than waiting for the user - which is why
the notes must say precisely what is broken and what would prove it fixed.

## Judgement

A green gate plus a clean contract plus no scope violation is necessary, not sufficient.
Read the diff in the pack for logic, edge cases and architectural fit, and say what you
actually checked in `notes`.
