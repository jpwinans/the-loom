"""Subprocess target for ``test_resultset_cap_concurrency.py`` (desire 6).

Deliberately named so pytest's default ``python_files`` glob
(``test_*.py``/``*_test.py``) never auto-collects it — the full suite must
never run this file on its own, only ``test_resultset_cap_concurrency.py``
does, by launching two concurrent ``pytest`` subprocesses pointed at this
file's one test by explicit node id. That is what proves
``small_resultset_cap`` serializes instead of racing: both subprocesses ask
for the same fixture, against the same live server, at (as near as
``subprocess.Popen`` gets) the same moment.
"""

from __future__ import annotations

import time

from falkordb import FalkorDB


def test_uses_capped_resultset(small_resultset_cap: int, db: FalkorDB) -> None:
    assert small_resultset_cap == 40
    assert int(db.config_get("RESULTSET_SIZE")) == 40
    # Held deliberately: widens the window during which a second concurrent
    # invocation of this same fixture — if it raced instead of serializing —
    # would read (and later restore) the wrong "original" value. This is
    # exactly the get/set/restore span the historical race lived in.
    time.sleep(1.5)
