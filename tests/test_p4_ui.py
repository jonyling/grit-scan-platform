from pathlib import Path

from grit.ui.actions import submit_from_screen
from grit.ui.seed import seed_pending_package
from grit.ui.view_model import NOT_ESTABLISHED, acquired_detail, package_view, signal_detail, signal_list


def test_reviewer_approves_the_exact_package_version_and_hash() -> None:
    supervisor, package_id, _assessments = seed_pending_package()
    package = supervisor.package(package_id)
    result = submit_from_screen(
        supervisor,
        package_id=package_id,
        reviewer_id="reviewer-01",
        reviewer_intent="APPROVE",
        findings=[],
        correlation_id="ui-1",
        accepted_at="2026-09-25T01:00:00Z",
    )
    approval = result["approval"]
    assert result["decision"]["review_outcome"] == "APPROVE"
    assert approval["package_id"] == package_id
    assert approval["package_version"] == package.version
    assert approval["package_hash"] == package.content_hash
    assert approval["membership_hash"] == package.membership_hash
    assert supervisor.current_state("SIG-1", "EXEC-1") == "APPROVED"
    assert supervisor.package(package_id).status == "APPROVED"


def test_return_for_rework_creates_no_approval() -> None:
    supervisor, package_id, _assessments = seed_pending_package()
    result = submit_from_screen(
        supervisor,
        package_id=package_id,
        reviewer_id="reviewer-01",
        reviewer_intent="REQUEST_REWORK",
        findings=[
            {
                "finding_id": "F-1",
                "finding_type": "MISSING_EVIDENCE",
                "severity": "MATERIAL",
                "description": "Source missing",
                "resolution_required": True,
            }
        ],
        correlation_id="ui-1",
        accepted_at="2026-09-25T01:00:00Z",
    )
    assert result["decision"]["review_outcome"] == "RETURN_FOR_REWORK"
    assert result["approval"] is None
    assert supervisor.approvals == []
    assert supervisor.current_state("SIG-1", "EXEC-1") == "ASSESSED"


def test_screen_shows_absence_and_hides_empty_fields() -> None:
    _supervisor, package_id, assessments = seed_pending_package()
    detail = dict(signal_detail(assessments["SIG-1"]))
    assert detail["Severity"] == NOT_ESTABLISHED
    assert detail["Financial basis"] == NOT_ESTABLISHED
    assert detail["Opportunity cost"] == NOT_ESTABLISHED
    assert "Entity" not in detail
    assert "Driving dimension" not in detail
    assert detail["Coverage gaps"] == "value_at_risk"
    package = dict(package_view(_supervisor, package_id, weekly_delta=None))
    assert package["Version"] == "1"
    assert "Weekly delta" not in package
    assert signal_list(_supervisor) == [{"signal_id": "SIG-1", "execution_id": "EXEC-1", "state": "PENDING_REVIEW"}]


def test_acquired_headline_does_not_invent_a_score() -> None:
    detail = dict(
        acquired_detail(
            {
                "title": "Strait traffic slows",
                "publisher": "reuters.com",
                "url": "https://www.reuters.com/world/strait",
                "published_at": None,
                "evidence": "LOW",
            }
        )
    )
    assert detail["Headline"] == "Strait traffic slows"
    assert detail["Severity"] == NOT_ESTABLISHED
    assert detail["Financial basis"] == NOT_ESTABLISHED
    assert "Published" not in detail


def test_screen_modules_do_not_write_the_ledger() -> None:
    root = Path(__file__).parents[1] / "grit" / "ui"
    for name in ("actions.py", "app.py", "view_model.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "ledger.append" not in text
        assert "WorkflowRow(" not in text
    assert "review_gate(" in (root / "actions.py").read_text(encoding="utf-8")
