# {{PROJECT}} simulation

The fprime-wsts simulation of [`{{FLIGHT_REL}}`]({{FLIGHT_REL}}). Nothing here is compiled
into flight software, and fprime-wsts only reads the flight project.

| Path | Contents |
|---|---|
{{FILES}}

## Generate, build, run

Needs the fprime-wsts checkout at `{{WSTS_REL}}`, installed in the active Python
environment (`pip install -r {{WSTS_REL}}/lib/fprime/requirements.txt -e {{WSTS_REL}}/python`).

{{COMMANDS}}

`wsts/` is generated: change the manifests and regenerate, never edit it.

## What is simulated

{{SIMULATED}}

## Not simulated yet

{{NOT_YET}}
