"""Code that waits on the host: what may keep stepped time from working, or from repeating.

Stepped time (docs/stepped-time.md) sees what flight software does through the OSAL: its
queues, its tasks and `Os::RawTime`. A component that sleeps, reads the host's clock,
starts a thread of its own, waits for another thread or blocks on a device does part of
its work where the stepper does not look. This module reads the sources of the components
a simulated deployment builds and says which do any of that, and what each costs in
stepped time. It reads text only, so the report is the same on every machine.

Framework components are not read: they go through the OSAL, and the few that do not are
listed in `FRAMEWORK` with what is known of them. The toolkit's own components are right
by construction and are not read either.
"""

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import flight_cmake

#: What a kind of call does in stepped time, from least to most serious. `effect` is what
#: it costs: `slower` (the same result each time, later), `not_repeatable` (two stepped
#: runs no longer record the same bytes), `may_stall` (a tick may never finish).
KINDS: Dict[str, dict] = {
    "sleeps": {
        "title": "sleeps on the wall clock",
        "effect": "slower",
        "stepped": "It waits for the host's clock, not for simulated time, and keeps the turn meanwhile: two "
                   "runs still give the same result, each as slowly as the sleeps add up. `Os::Task::delay` "
                   "is no different yet.",
    },
    "host_clock": {
        "title": "reads the host's clock",
        "effect": "not_repeatable",
        "stepped": "What is computed from it (time stamps, time-outs, rates) follows the host, not the "
                   "simulated time flight software is given, and differs from run to run: two stepped runs "
                   "no longer record the same bytes. Simulated time comes from `Os::RawTime` or the time "
                   "port; simulation code can ask `Wsts::Control::flightTime()`.",
    },
    "host_state": {
        "title": "reads the state of the host",
        "effect": "not_repeatable",
        "stepped": "What it reports (processor load, memory, files) is the host's and differs from run to "
                   "run, as it does from machine to machine.",
    },
    "waits_on_io": {
        "title": "waits on a device or socket",
        "effect": "not_repeatable",
        "stepped": "What arrives comes from outside the simulation, at a moment of its own: it is handled in "
                   "whichever tick is being taken, a different one in each run (`busy_ticks` in `status` "
                   "counts them). Waiting in a task of its own, between messages, does not keep a tick from "
                   "finishing.",
    },
    "own_thread": {
        "title": "starts a thread outside Os::Task",
        "effect": "not_repeatable",
        "stepped": "The thread is not one of flight software's tasks to the stepper: what it does is not "
                   "counted as work and does not wait for the turn, so a tick can be taken while it runs, "
                   "and what it does falls in a different place in each run.",
    },
    "waits_for_thread": {
        "title": "waits for another thread",
        "effect": "may_stall",
        "stepped": "In stepped time one thread runs at a time. A handler that waits for another task to give "
                   "it something waits for ever: the tick does not finish, and is reported stuck after "
                   "`WSTS_STEP_LIMIT_MS`. A blocking send to a full queue is the one wait that lets go of "
                   "the turn.",
    },
}
EFFECTS = ("slower", "not_repeatable", "may_stall")

# A call of a free function: not a member (`.x(`, `->x(`) and not another scope's (`Foo::x(`),
# though `::x(` is the global one
_FREE = r"(?:^|(?<=[^\w.>:])|(?<=[^\w:]::)|(?<=^::))"

#: kind -> patterns, each with one group: the call as the report shows it
PATTERNS: Dict[str, List[re.Pattern]] = {
    "sleeps": [
        re.compile(_FREE + r"(sleep|usleep|nanosleep|clock_nanosleep)\s*\("),
        re.compile(r"\b((?:std::)?this_thread::sleep_(?:for|until))\b"),
        re.compile(r"\b(taskDelay)\s*\("),
        re.compile(r"\b((?:Os::)?Task::delay)\s*\("),
    ],
    "host_clock": [
        re.compile(_FREE + r"(clock_gettime|gettimeofday|tickGet|ftime)\s*\("),
        re.compile(_FREE + r"((?:std::)?time)\s*\(\s*(?:NULL|nullptr|0|&\s*\w+)\s*\)"),
        re.compile(r"\b((?:std::chrono::)?(?:system_clock|steady_clock|high_resolution_clock)\s*::\s*now)\b"),
    ],
    "own_thread": [
        re.compile(r"\b(pthread_create|taskSpawn)\s*\("),
        re.compile(r"\b(std::j?thread)\b(?!\s*::)"),
        re.compile(r"\b(std::async)\s*\("),
    ],
    "waits_for_thread": [
        re.compile(_FREE + r"(sem_wait|sem_timedwait|semTake|pthread_cond_wait|pthread_cond_timedwait|msgQReceive)\s*\("),
        re.compile(r"\b(std::condition_variable(?:_any)?)\b"),
        re.compile(r"\b(Os::ConditionVariable)\b"),
    ],
    "waits_on_io": [
        re.compile(_FREE + r"(select|poll|epoll_wait|recv|recvfrom|recvmsg|accept)\s*\("),
    ],
}

#: Framework components that do not go through the OSAL for all they do: component ->
#: (kind, what it does). Read from F Prime v4.4.2.
FRAMEWORK: Dict[str, Tuple[str, str]] = {
    "Drv.TcpClient": ("waits_on_io", "a socket driver: its receive task waits in recv() between messages and "
                      "sleeps on the wall clock (Os::Task::delay) while it reconnects"),
    "Drv.TcpServer": ("waits_on_io", "a socket driver: its receive task waits in recv() between messages and "
                      "sleeps on the wall clock (Os::Task::delay) while no client is connected"),
    "Drv.Udp": ("waits_on_io", "a socket driver: its receive task waits in recv() between messages"),
    "Drv.PosixUartDriver": ("waits_on_io", "a serial driver: its receive task waits in read() on the device"),
    "Drv.LinuxGpioDriver": ("waits_on_io", "a GPIO driver: its interrupt task waits in poll() on the line"),
    "Svc.PosixTime": ("host_clock", "time stamps from clock_gettime(), in either mode"),
    "Svc.ChronoTime": ("host_clock", "time stamps from std::chrono::system_clock, in either mode"),
    "Svc.LinuxTimer": ("sleeps", "ticks by the wall clock, and does not take its ticks from the stepper"),
    "Svc.SystemResources": ("host_state", "processor and memory use, read from the host"),
}

SOURCE_FILES = (*flight_cmake.SOURCE_SUFFIXES, *flight_cmake.HEADER_SUFFIXES)


@dataclass
class Finding:
    kind: str
    call: str
    file: Path
    line: int


@dataclass
class Entry:
    """What one module of the simulated build does that waits on the host"""
    role: str  # "flight", "surrogate" (of the simulation project), "simulator" or "framework"
    components: List[str]
    instances: List[str]
    module: Optional[Path] = None
    findings: List[Finding] = field(default_factory=list)
    known: Optional[Tuple[str, str]] = None  # for a framework component: FRAMEWORK's entry

    def kinds(self) -> List[str]:
        found = {f.kind for f in self.findings} | ({self.known[0]} if self.known else set())
        return [kind for kind in KINDS if kind in found]

    def effect(self) -> str:
        return max((KINDS[k]["effect"] for k in self.kinds()), key=EFFECTS.index)


def _mask(text: str) -> str:
    """Comments, string and character literals replaced by spaces, newlines kept: what is
    left is code, at the same line numbers"""
    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', blank, text, flags=re.S)


def scan_text(text: str, file: Path) -> List[Finding]:
    """Calls that wait on the host in one C or C++ source, in the order of the text"""
    code = _mask(text)
    starts = [0] + [m.end() for m in re.finditer(r"\n", code)]
    found = []
    for kind, patterns in PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(code):
                line = bisect.bisect_right(starts, match.start(1))
                call = re.sub(r"\s+", "", match.group(1))
                found.append(Finding(kind, call, file, line))
    found.sort(key=lambda f: (f.line, list(KINDS).index(f.kind), f.call))
    # One finding per call on a line
    unique: Dict[Tuple[int, str], Finding] = {}
    for finding in found:
        unique.setdefault((finding.line, finding.call), finding)
    return list(unique.values())


def module_files(directory: Path, testing: bool = False) -> List[Path]:
    """The C and C++ files a module's CMakeLists.txt names literally, in the variant the
    build takes; every such file in the directory when it names none"""
    cmake_file = directory / "CMakeLists.txt"
    files: List[Path] = []
    if cmake_file.is_file():
        text = flight_cmake._strip_comments(cmake_file.read_text(errors="replace"))
        for command in flight_cmake.read_commands(text):
            if command.name == "register_fprime_ut" or not flight_cmake.in_variant(command, testing):
                continue
            if command.name.startswith("register_fprime_") or command.name in flight_cmake.SOURCE_COMMANDS or (
                    command.name in ("set", "list") and command.args and "SOURCE" in command.args[0].upper()):
                files += [f for f in flight_cmake._files_named(command, directory, SOURCE_FILES) if f.is_file()]
    if not files:
        files = [f for f in sorted(directory.iterdir()) if f.is_file() and f.suffix in SOURCE_FILES] \
            if directory.is_dir() else []
    return list(dict.fromkeys(files))


def scan_module(directory: Path, testing: bool = False) -> List[Finding]:
    found = []
    for file in module_files(directory, testing):
        found += scan_text(file.read_text(errors="replace"), file)
    return found


def describe(entries: Iterable[Entry], rel: Callable[[Path], str]) -> List[dict]:
    """The entries for the report, those with something to say only"""
    listed = []
    for entry in entries:
        if not entry.findings and not entry.known:
            continue
        item = {
            "role": entry.role,
            "components": entry.components,
            "instances": entry.instances,
            **({"module": rel(entry.module)} if entry.module is not None else {}),
            "kinds": entry.kinds(),
            "effect": entry.effect(),
        }
        if entry.known:
            item["known"] = entry.known[1]
        if entry.findings:
            item["calls"] = [{"kind": f.kind, "call": f.call, "file": rel(f.file), "line": f.line} for f in entry.findings]
        listed.append(item)
    order = {"flight": 0, "surrogate": 1, "simulator": 2, "framework": 3}
    listed.sort(key=lambda i: (-EFFECTS.index(i["effect"]), order[i["role"]], i["components"]))
    return listed


ROLE_NAMES = {"flight": "flight code", "surrogate": "a surrogate of the simulation project",
              "simulator": "a simulator", "framework": "framework"}
EFFECT_NAMES = {"slower": "slower", "not_repeatable": "not repeatable", "may_stall": "may stall a tick"}


def _count(number: int) -> str:
    return "one" if number == 1 else str(number)


def verdict(listed: List[dict]) -> str:
    """One sentence on whether stepped time will work for the deployment"""
    if not listed:
        return ("Nothing in the sources of the components this deployment builds waits on the host, as far as "
                "their text shows: stepped time should work, and two stepped runs record the same bytes.")
    counts = {effect: sum(1 for i in listed if i["effect"] == effect) for effect in EFFECTS}
    parts = []
    if counts["may_stall"]:
        parts.append(f"{_count(counts['may_stall'])} may keep a tick from finishing")
    if counts["not_repeatable"]:
        parts.append(f"{_count(counts['not_repeatable'])} keep{'s' if counts['not_repeatable'] == 1 else ''} two "
                     "stepped runs from recording the same bytes")
    if counts["slower"]:
        parts.append(f"{_count(counts['slower'])} only make{'s' if counts['slower'] == 1 else ''} stepped runs slower")
    many = len(listed) != 1
    return (f"{_count(len(listed)).capitalize()} module{'s' if many else ''} of this deployment do{'' if many else 'es'} something that "
            "waits on the host: "
            + ", ".join(parts) + ". Read from the text: a call that is never reached in a run costs nothing.")


def warning(item: dict) -> str:
    """A warning of the generator, for a deployment whose manifest asks for stepped time"""
    who = ", ".join(item["instances"]) or ", ".join(item["components"])
    calls = item.get("calls", [])
    shown = "; ".join(f"{c['call']} at {c['file']}:{c['line']}" for c in calls[:3])
    shown += f" and {len(calls) - 3} more" if len(calls) > 3 else ""
    what = item.get("known") or shown
    consequences = " ".join(KINDS[kind]["stepped"] for kind in item["kinds"] if KINDS[kind]["effect"] != "slower") \
        or KINDS["sleeps"]["stepped"]
    return (f"time.mode is stepped, and {who} ({', '.join(item['components'])}, {ROLE_NAMES[item['role']]}) "
            f"{' and '.join(KINDS[k]['title'] for k in item['kinds'])}: {what}. {consequences} Substitute it, "
            "or run this deployment in real time (see 'Time' in wsts-report.md).")


def render(listed: List[dict]) -> List[str]:
    """The part of the report's 'Time' section on code that waits on the host"""
    lines = ["**Code that waits on the host** (read from the sources of the components this deployment builds; "
             "what each costs in stepped time):", "", verdict(listed)]
    if not listed:
        return lines
    lines += ["", "| Instances | Component | Module | What it does | In stepped time |", "|---|---|---|---|---|"]
    for item in listed:
        if item.get("known"):
            what = item["known"]
        else:
            calls = item["calls"]
            what = "; ".join(f"`{c['call']}` at `{c['file']}:{c['line']}`" for c in calls[:4])
            what += f"; {len(calls) - 4} more" if len(calls) > 4 else ""
        instances = ", ".join(f"`{i}`" for i in item["instances"]) or "(none)"
        components = ", ".join(f"`{c}`" for c in item["components"]) + f" ({ROLE_NAMES[item['role']]})"
        module = f"`{item['module']}`" if item.get("module") else ""
        lines.append(f"| {instances} | {components} | {module} | {what} | {EFFECT_NAMES[item['effect']]} |")
    kinds = [kind for kind in KINDS if any(kind in item["kinds"] for item in listed)]
    lines += [""] + [f"- **{KINDS[kind]['title'].capitalize()}.** {KINDS[kind]['stepped']}" for kind in kinds]
    return lines
