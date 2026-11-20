# {{PROJECT}} simulation

The fprime-wsts simulation of [`{{FLIGHT_REL}}`]({{FLIGHT_REL}}). Nothing here is compiled
into flight software, and fprime-wsts only reads the flight project.

| Path | Contents |
|---|---|
{{FILES}}

## Generate, build, run

Builds against fprime-wsts pinned as a git submodule at `lib/fprime-wsts`: check it out with
`git submodule update --init lib/fprime-wsts` after cloning. Any `fprime-wsts` on the path
runs commands on this project with the pinned one; for the pytest plugin, install that one
(`pip install -r {{FRAMEWORK_REL}}/requirements.txt -e lib/fprime-wsts/python`).

{{COMMANDS}}

`wsts/` is generated: change the manifests and regenerate, never edit it.

## What is simulated

{{SIMULATED}}

## Not simulated yet

{{NOT_YET}}
