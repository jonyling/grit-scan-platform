"""Dependency-free runner for the impact-scoring contract tests."""

from __future__ import annotations

import importlib
import traceback


MODULES = (
    "tests.test_impact_scoring_contract",
    "tests.test_severity_definition",
    "tests.test_registry_loader",
    "tests.test_lob_keyword_loader",
    "tests.test_risk_taxonomy",
    "tests.test_grade_evidence",
    "tests.test_classify_horizon",
    "tests.test_score_severity",
    "tests.test_resolve_transmission",
    "tests.test_is_high_negative_impact",
    "tests.test_assess_impact",
    "tests.test_classify_opportunity_cost",
    "tests.test_p2_matchers",
    "tests.test_p2_weekly_pipeline",
    "tests.test_p2_acquisition",
    "tests.test_p3_transition_guard",
    "tests.test_p3_review_gate",
    "tests.test_p3_supervisor",
    "tests.test_p4_ui",
)


def main() -> None:
    failures: list[str] = []
    passed = 0
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        for name in sorted(dir(module)):
            candidate = getattr(module, name)
            if not name.startswith("test_") or not callable(candidate):
                continue
            try:
                candidate()
                print(f"PASS {module_name}:{name}")
                passed += 1
            except Exception:
                failures.append(f"FAIL {module_name}:{name}\n{traceback.format_exc()}")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"PASS total={passed}")


if __name__ == "__main__":
    main()
