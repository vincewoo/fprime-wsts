# A workstation for fprime-wsts: what CI's ubuntu-24.04 runner has and the toolkit needs.
# CMake, Ninja and the FPP tools (a native binary, no Java) come from pip, in post-create.sh.
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        git \
        python3 \
        python3-dev \
        python3-venv \
        g++-multilib \
    && rm -rf /var/lib/apt/lists/*

# The Python tools live in a virtual environment first on PATH (devcontainer.json), so the
# python3 that CMake runs for F Prime is the one that has them
RUN python3 -m venv /opt/venv && chown -R vscode:vscode /opt/venv
