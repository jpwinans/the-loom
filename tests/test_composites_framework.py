"""Unit tests for the composite framework primitives.

Every section runs inside `time_section`, which never throws — a failing
section yields `{data: None, durationMs, error}` so sibling sections still
execute. The overall `build_composite_result` tallies successes/failures and
stamps timing.
"""

from __future__ import annotations

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
