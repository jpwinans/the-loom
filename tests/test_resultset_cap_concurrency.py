"""Proves ``tests/conftest.py::small_resultset_cap`` cannot strand the
server's RESULTSET_SIZE under concurrent pytest runs (desire 6).

The original bug: two concurrent ``pytest`` invocations both reading the
"original" RESULTSET_SIZE before either restored it, so the second restore
clobbered the first's already-lowered value back to the test cap (40)
instead of whatever the server was actually configured to — permanently
stranding it at the cap.

This test reproduces the concurrency shape directly: it spawns two real
``pytest`` subprocesses, both pointed at
``concurrency_target_resultset_cap.py``'s one test (which holds the fixture
for 1.5s to widen the race window), started as close together as
``subprocess.Popen`` allows. Self-contained and scoped to one dedicated
target file — never the full suite — so it is safe to run standalone or
from inside a parent pytest process.

The strand proof is value-agnostic: it only asserts the server's
RESULTSET_SIZE is unchanged before vs. after the concurrent runs, whatever
that value is. It does not assume any particular configured default — the
live default is currently the compose-configured ``-1`` (uncapped; it used
to read back 10000 before that config took effect on the container).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from falkordb import FalkorDB

from tests.conftest import _RESULTSET_CAP

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TARGET = Path(__file__).resolve().parent / "concurrency_target_resultset_cap.py"

# The target test holds the fixture's critical section for ~1.5s (see
# concurrency_target_resultset_cap.py). Launching the second run this far
# after the first guarantees its fixture setup lands *inside* the first
# run's still-open window instead of leaving the actual overlap to OS
# scheduler luck (which, empirically, is not reliable enough to exercise the
# historical bug on every run — the corrupted read only propagates to the
# final state when the *later* finisher is also the one that captured it,
# so an uncontrolled simultaneous launch reproduces the strand only about
# half the time). A deliberate stagger makes the overlap — and, unfixed, the
# strand — deterministic, while still proving exactly the scenario in the
# pass criterion: two concurrent runs of the fixture's file.
_STAGGER_SECONDS = 0.4


def _spawn_target() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(_TARGET)],
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def test_concurrent_runs_leave_resultset_size_at_default(db: FalkorDB) -> None:
    before = db.config_get("RESULTSET_SIZE")
    assert int(before) != _RESULTSET_CAP, (
        f"RESULTSET_SIZE is already at the test cap ({_RESULTSET_CAP}) before this "
        "test ran — some other run left the server stranded already; restore it "
        "(GRAPH.CONFIG SET RESULTSET_SIZE -1, the compose-configured default) before "
        "re-running this test, which otherwise cannot prove anything from a dirty "
        "baseline."
    )

    first = _spawn_target()
    time.sleep(_STAGGER_SECONDS)
    second = _spawn_target()
    procs = [first, second]
    outputs = [proc.communicate(timeout=90)[0].decode(errors="replace") for proc in procs]

    for output, proc in zip(outputs, procs, strict=True):
        assert proc.returncode == 0, f"concurrent target run failed:\n{output}"

    after = db.config_get("RESULTSET_SIZE")
    assert int(after) == int(before), (
        f"RESULTSET_SIZE ended at {after!r} instead of the value it started at "
        f"({before!r}) — the fixture stranded the server under concurrent runs.\n\n"
        f"run 1 output:\n{outputs[0]}\n\nrun 2 output:\n{outputs[1]}"
    )
