from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.intent_router import route_intent
from app.services.search_logic import norm


CASES_PATH = ROOT / "tests" / "fixtures" / "search_golden_cases.json"


def case_matches(case: dict[str, object]) -> bool:
    decision = route_intent(str(case["query"]))
    if decision.kind.value != case["kind"]:
        return False
    if case.get("author") and norm(decision.author_part or "") != norm(str(case["author"])):
        return False
    if case.get("title") and norm(decision.title_part or "") != norm(str(case["title"])):
        return False
    return True


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    failed = [case for case in cases if not case_matches(case)]
    passed = len(cases) - len(failed)
    print(f"Search routing quality: {passed}/{len(cases)} ({passed / len(cases):.1%})")
    if failed:
        print("Known gaps:")
        for case in failed:
            decision = route_intent(case["query"])
            note = f" — {case['note']}" if case.get("note") else ""
            print(f"- {case['query']!r}: expected={case['kind']} actual={decision.kind.value}{note}")


if __name__ == "__main__":
    main()
