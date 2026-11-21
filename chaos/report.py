"""summary.md of a chaos run (D-EXT-15): PASS/FAIL per invariant, counts, alarm quality."""

import platform
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from chaos.invariants import Result
from gs.alarms.quality import Quality

INVARIANTS = ("I1", "I2", "I3", "I5", "I6", "I7", "I8")


@dataclass
class Tally:
    """Per invariant: samples taken, samples skipped (a source was down), failures."""

    taken: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    failed: dict[str, str] = field(default_factory=dict)

    def add(self, r: Result, skipped: bool = False) -> None:
        (self.skipped if skipped else self.taken)[r.name] += 1
        if not r.ok and not skipped:
            self.failed.setdefault(r.name, r.detail)


def image_digests() -> list[str]:
    """`service image-id` of every container of the compose project."""
    ids = subprocess.run(
        ["docker", "compose", "ps", "-q"], capture_output=True, text=True, check=False
    ).stdout.split()
    fmt = '{{index .Config.Labels "com.docker.compose.service"}} {{.Image}}'
    out = subprocess.run(
        ["docker", "inspect", "-f", fmt, *ids], capture_output=True, text=True, check=False
    ).stdout
    return sorted(set(out.splitlines()))


def render(
    seed: int,
    minutes: float,
    started: datetime,
    tally: Tally,
    counts: Mapping[str, Mapping[str, int]],
    quality: Quality | None,
    violation: str | None,
    recoveries: Sequence[float],
    restores: tuple[Sequence[str], Sequence[str], bool] = ((), (), False),
    i4: str = "NOT RUN (no watcher)",
    incomplete: Sequence[str] = (),
) -> str:
    """`restores`: start-up repair lines, end repair lines, whether the end restore failed.
    `incomplete`: why the run did not do what it set out to (a FAIL, not a PASS)."""
    start, end, end_failed = restores
    repairs = "".join(
        f"\n## {title}\n\n" + ("".join(f"- {ln}\n" for ln in lines) or "- none\n")
        for title, lines in (("Start-up repairs", start), ("End repairs", end))
    )
    not_clean = (
        "**NOT CLEAN: the end restore failed, the stack may be left broken (see End repairs).**\n\n"
        if end_failed
        else ""
    )
    rows = "".join(
        f"| {i} | {'FAIL' if i in tally.failed else 'PASS' if tally.taken[i] else 'NOT RUN'} "
        f"| {tally.taken[i]} | {tally.skipped[i]} | {tally.failed.get(i, '')} |\n"
        for i in INVARIANTS
    )
    unjudged = [i for i in INVARIANTS if not tally.taken[i]]  # NOT RUN proves nothing
    reasons = [*incomplete, *(f"{i} was never judged" for i in unjudged)]
    verdict = "FAIL" if tally.failed or violation or end_failed or reasons else "PASS"
    why = "".join(f"**INCOMPLETE: {r}.**\n\n" for r in reasons)
    q = (
        f"- RAISEs {quality.raises}, false raises {quality.false_raises}, "
        f"chattering: {', '.join(quality.chattering) or 'none'}\n"
        f"- Peak new alarms in 10 min: {dict(quality.peak_10min)}; time in flood: "
        f"{quality.time_in_flood_s:.0f} s\n"
        if quality
        else "- (no alarm-quality sample)\n"
    )
    cnt = "".join(f"- {k}: {dict(sorted(v.items()))}\n" for k, v in counts.items())
    digests = "".join(f"  - {d}\n" for d in image_digests())
    return (
        f"# Chaos soak, seed {seed}, {minutes:g} min, started {started:%Y-%m-%d %H:%M:%S}Z\n\n"
        f"**{verdict}**{f' (stopped: {violation})' if violation else ''}\n\n"
        f"{not_clean}{why}"
        f"Properties: P1-P5 and P7 run in `tests/property`; P6 skipped (the derived evaluator, "
        f"EXT-01 later parts, is not built): this evidence does not claim P6.\n\n"
        f"I4 (console watcher): {i4}\n\n"
        "| Invariant | Result | Samples | Skipped | First failure |\n|---|---|---|---|---|\n"
        f"{rows}\n"
        f"- Recovery after each action (I5): max {max(recoveries, default=0):.1f} s over "
        f"{len(recoveries)} actions\n{q}\n{cnt}\n"
        f"Machine: {platform.platform()}, {platform.machine()}\n{repairs}\nImages:\n{digests}"
    )
