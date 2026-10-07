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
    "tests.test_urs_pack",
    "tests.test_search_inputs",
    "tests.test_query_plan",
    "tests.test_brave_plan",
    "tests.test_retrieval",
    "tests.test_bind",
    "tests.test_ontology_graph",
    "tests.test_doc_type",
    "tests.test_link",
    "tests.test_scale_cues",
    "tests.test_priority",
    "tests.test_loop_card",
    "tests.test_loop_engine",
    "tests.test_loop_run",
    "tests.test_relationship_gaps",
    "tests.test_report_findings",
    "tests.test_signal_filter",
    "tests.test_report",
    "tests.test_p2_weekly_pipeline",
    "tests.test_relevance_scoring",
    "tests.test_wild_scan_relevance",
    "tests.test_p2_acquisition",
    "tests.test_wild_scan_gdelt",
    "tests.test_currents",
    "tests.test_p3_transition_guard",
    "tests.test_p3_review_gate",
    "tests.test_p3_supervisor",
    "tests.test_p4_ui",
    "tests.test_p5_agents",
    "tests.test_p6_theme_layer",
    "tests.test_dataverse_signals",
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
