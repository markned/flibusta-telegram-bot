from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.intent_router import route_intent
from app.services.search_logic import norm


CASES_PATH = Path(__file__).parent / "fixtures" / "search_golden_cases.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))
QUALITY_FLOOR = 1.0


def _case_matches(case: dict[str, object]) -> bool:
    decision = route_intent(str(case["query"]))
    if decision.kind.value != case["kind"]:
        return False
    expected_author = case.get("author")
    if expected_author and norm(decision.author_part or "") != norm(str(expected_author)):
        return False
    expected_title = case.get("title")
    return not expected_title or norm(decision.title_part or "") == norm(str(expected_title))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["query"])
def test_search_routing_regression_case(case: dict[str, object]) -> None:
    assert _case_matches(case), case


def test_search_quality_floor_covers_known_backlog() -> None:
    passed = sum(_case_matches(case) for case in CASES)
    score = passed / len(CASES)
    assert score >= QUALITY_FLOOR, f"search routing quality {score:.1%} is below {QUALITY_FLOOR:.0%}"


def test_golden_dataset_has_balanced_coverage() -> None:
    kinds = {case["kind"] for case in CASES}
    assert len(CASES) >= 50
    assert {
        "exact_search",
        "author_search",
        "author_title_search",
        "unsupported_topic",
    } <= kinds
