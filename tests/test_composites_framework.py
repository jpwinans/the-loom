"""Unit tests for the composite framework primitives.

Every section runs inside `time_section`, which never throws — a failing
section yields `{data: None, durationMs, error}` so sibling sections still
execute. The overall `build_composite_result` tallies successes/failures and
stamps timing.
"""

from __future__ import annotations

import time

from theloom.composites import framework


class TestTimeSection:
    def test_success_wraps_data(self) -> None:
        result = framework.time_section(lambda: {"n": 1})
        assert result["data"] == {"n": 1}
        assert result["error"] is None
        assert isinstance(result["durationMs"], int)
        assert result["durationMs"] >= 0

    def test_failure_captures_message_never_raises(self) -> None:
        def boom() -> int:
            raise ValueError("kaboom")

        result = framework.time_section(boom)
        assert result["data"] is None
        assert result["error"] == "kaboom"
        assert isinstance(result["durationMs"], int)

    def test_non_error_exception_stringified(self) -> None:
        def boom() -> int:
            raise RuntimeError("plain text")

        assert framework.time_section(boom)["error"] == "plain text"


class TestFailedSection:
    def test_shape(self) -> None:
        result = framework.failed_section("no store")
        assert result == {"data": None, "durationMs": 0, "error": "no store"}


class TestBuildCompositeResult:
    def test_tallies_and_metadata(self) -> None:
        sections = {
            "stats": {"data": {"x": 1}, "durationMs": 2, "error": None},
            "loops": {"data": None, "durationMs": 1, "error": "boom"},
        }
        composite = framework.build_composite_result(sections, total_duration_ms=5)
        assert composite["result"] is sections
        meta = composite["metadata"]
        assert meta["totalDurationMs"] == 5
        assert meta["sectionsSucceeded"] == 1
        assert meta["sectionsFailed"] == 1
        # executedAt is an ISO-8601 UTC stamp.
        assert meta["executedAt"].endswith("Z") or "+00:00" in meta["executedAt"]

    def test_all_success(self) -> None:
        sections = {"a": framework.time_section(lambda: 1)}
        composite = framework.build_composite_result(sections, total_duration_ms=0)
        assert composite["metadata"]["sectionsSucceeded"] == 1
        assert composite["metadata"]["sectionsFailed"] == 0


class TestRunComposite:
    """The runner: executes a declared list of (name, section) pairs and stamps
    the envelope. Each spec is either a zero-arg callable (run through
    `time_section`) or an already-built SectionResult (used as-is — the
    pattern `failed_section`-style short-circuits rely on)."""

    def test_runs_callables_in_order_and_builds_envelope(self) -> None:
        calls: list[str] = []

        def first() -> dict[str, int]:
            calls.append("first")
            return {"n": 1}

        def second() -> dict[str, int]:
            calls.append("second")
            return {"n": 2}

        composite = framework.run_composite([("first", first), ("second", second)])

        assert calls == ["first", "second"], "sections run in declared order"
        assert composite["result"]["first"]["data"] == {"n": 1}
        assert composite["result"]["second"]["data"] == {"n": 2}
        assert list(composite["result"].keys()) == ["first", "second"]
        assert composite["metadata"]["sectionsSucceeded"] == 2
        assert composite["metadata"]["sectionsFailed"] == 0
        assert isinstance(composite["metadata"]["totalDurationMs"], int)
        assert composite["metadata"]["totalDurationMs"] >= 0

    def test_a_failing_callable_still_lets_later_sections_run(self) -> None:
        def boom() -> None:
            raise ValueError("kaboom")

        def ok() -> int:
            return 42

        composite = framework.run_composite([("bad", boom), ("good", ok)])

        assert composite["result"]["bad"]["error"] == "kaboom"
        assert composite["result"]["bad"]["data"] is None
        assert composite["result"]["good"]["data"] == 42
        assert composite["metadata"]["sectionsSucceeded"] == 1
        assert composite["metadata"]["sectionsFailed"] == 1

    def test_an_already_built_section_result_passes_through_unexecuted(self) -> None:
        """A pre-built SectionResult (e.g. from `failed_section`, for a
        conditional short-circuit decided before the runner is called) is used
        as-is rather than being treated as a callable."""
        prebuilt = framework.failed_section("no prerequisite data")

        composite = framework.run_composite([("skipped", prebuilt)])

        assert composite["result"]["skipped"] is prebuilt
        assert composite["metadata"]["sectionsFailed"] == 1

    def test_start_override_extends_totalDurationMs_to_include_prior_work(self) -> None:
        """A caller that already captured `time.perf_counter()` before doing
        pre-section setup can pass that timestamp in so the setup counts
        toward totalDurationMs."""
        earlier = time.perf_counter() - 0.05  # pretend 50ms of setup already happened

        composite = framework.run_composite([("a", lambda: 1)], start=earlier)

        assert composite["metadata"]["totalDurationMs"] >= 45

    def test_no_start_override_times_from_the_call_itself(self) -> None:
        composite = framework.run_composite([("a", lambda: 1)])
        assert composite["metadata"]["totalDurationMs"] < 1000

    def test_empty_spec_list_builds_an_empty_but_valid_envelope(self) -> None:
        composite = framework.run_composite([])
        assert composite["result"] == {}
        assert composite["metadata"]["sectionsSucceeded"] == 0
        assert composite["metadata"]["sectionsFailed"] == 0
