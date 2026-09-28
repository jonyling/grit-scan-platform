from acquisition.candidate import Candidate
from dataverse.choices import EVIDENCE_LOW, RISK_LEVEL_LOW, TREND_ESCALATING
from dataverse.client import DataverseError, organization_url, require_env
from dataverse.signals import ranked_finding_body, ranked_finding_content_hash, signal_body
from supervisor.service import Supervisor


class FakeDataverse:
    def __init__(self, hashes: dict[str, str] | None = None) -> None:
        self.created: list[tuple[str, dict]] = []
        self.patched: list[tuple[str, str, dict]] = []
        self.hashes = dict(hashes or {})

    def content_hashes(self) -> dict[str, str]:
        return dict(self.hashes)

    def create(self, entity_set: str, body: dict) -> str:
        record_id = f"{entity_set}-{len(self.created) + 1}"
        self.created.append((entity_set, body))
        if entity_set == "grit_signals":
            self.hashes[body["grit_contenthash"]] = record_id
        return record_id

    def patch(self, entity_set: str, record_id: str, body: dict) -> None:
        self.patched.append((entity_set, record_id, body))


def _candidate(title: str = "Vietnam rail note", url: str = "https://www.reuters.com/rail") -> Candidate:
    return Candidate(
        source_lane="WILD",
        title=title,
        text="",
        url=url,
        publisher="reuters.com",
        published_at=None,
        fetched_at="2026-09-26T00:00:00Z",
        content_hash="a" * 64,
        seen_at="2026-09-25T15:30:00Z",
    )


def test_signal_body_does_not_invent_a_score_or_publication_date() -> None:
    body = signal_body(_candidate(), execution_id="exec-1", correlation_id="GDELT-1")
    assert body["grit_evidenceconfidence"] == EVIDENCE_LOW
    assert body["grit_sourceurl"] == "https://www.reuters.com/rail"
    assert "grit_sourcepublicationdateutc" not in body
    assert "grit_trend" not in body
    assert "tone" not in body
    assert "severity" not in body


def _ranked_item(**overrides) -> dict:
    item = {
        "developmentId": "DEV-C01-01",
        "conditionId": "C01",
        "condition": "Taiwan Strait pressure",
        "domain": "Security",
        "movement": "ESCALATING",
        "evidenceConfidence": "HIGH",
        "headline": "Strait pressure",
        "summary": "Pressure increased.",
        "sources": ["https://www.reuters.com/world/asia-pacific/story"],
        "steThemes": ["Geopolitical volatility"],
        "descentStatus": "NOT_JUSTIFIED",
        "descent": None,
        "exposureEntities": [],
        "priorityLabel": "HIGH",
        "trend": "ESCALATING",
        "riskLevel": "HIGH",
        "businessRelevanceBasis": "Security condition tracked under STE themes: Geopolitical volatility.",
    }
    item.update(overrides)
    return item


def test_ranked_finding_body_writes_trend_risklevel_and_relevance_basis() -> None:
    body = ranked_finding_body(_ranked_item(), execution_id="exec-1", correlation_id="URS-WEEKLY-1")
    assert body["grit_trend"] == TREND_ESCALATING
    assert body["grit_risklevel"] != RISK_LEVEL_LOW
    assert body["grit_businessrelevancebasis"] == "Security condition tracked under STE themes: Geopolitical volatility."
    assert body["grit_sourceurl"] == "https://www.reuters.com/world/asia-pacific/story"
    assert "severity" not in body
    assert "grit_state" not in body


def test_ranked_finding_body_needs_a_condition() -> None:
    try:
        ranked_finding_body({}, execution_id="exec-1", correlation_id="URS-WEEKLY-1")
    except ValueError as exc:
        assert "condition" in str(exc)
    else:
        raise AssertionError("a ranked finding with no condition was accepted")


def test_ranked_finding_content_hash_falls_back_to_development_id_without_a_source() -> None:
    with_source = ranked_finding_content_hash(_ranked_item())
    no_source = ranked_finding_content_hash(_ranked_item(sources=[]))
    other_development = ranked_finding_content_hash(_ranked_item(sources=[], developmentId="DEV-C02-01"))
    assert with_source != no_source
    assert no_source != other_development


def test_persist_ranked_findings_creates_then_refreshes_without_a_second_workflow_row() -> None:
    client = FakeDataverse()
    supervisor = Supervisor(actor_id="supervisor-urs-weekly")
    first = supervisor.persist_ranked_findings(
        [_ranked_item()],
        client,
        correlation_id="URS-WEEKLY-2026-09-21",
        accepted_at="2026-09-21T00:00:00Z",
    )
    assert len(first["created"]) == 1
    assert first["updated"] == []
    kinds = [kind for kind, _ in client.created]
    assert kinds.count("grit_workflowstates") == 1
    assert kinds.count("grit_signals") == 1

    second = supervisor.persist_ranked_findings(
        [_ranked_item(riskLevel="LOW", trend="EASING")],
        client,
        correlation_id="URS-WEEKLY-2026-09-28",
        accepted_at="2026-09-28T00:00:00Z",
    )
    assert second["created"] == []
    assert len(second["updated"]) == 1
    kinds_after = [kind for kind, _ in client.created]
    assert kinds_after.count("grit_workflowstates") == 1
    patched_signal = next(body for kind, _, body in client.patched if kind == "grit_signals" and "grit_trend" in body)
    assert patched_signal["grit_risklevel"] == RISK_LEVEL_LOW


def test_repeated_content_hash_is_not_created_again() -> None:
    client = FakeDataverse({"a" * 64: "already-there"})
    supervisor = Supervisor(actor_id="supervisor-gdelt")
    first = supervisor.persist_acquired_signals(
        [_candidate(), _candidate(title="No url", url="")],
        client,
        correlation_id="GDELT-1",
        accepted_at="2026-09-26T00:00:00Z",
    )
    assert first["reused"] == ["already-there"]
    assert first["skipped"] == ["a" * 64]
    assert first["created"] == []
    assert [item[0] for item in client.created] == ["grit_executions"]
    fresh = _candidate(title="Second", url="https://www.example.com/story")
    fresh = Candidate(
        source_lane="WILD",
        title="Second",
        text="",
        url="https://www.example.com/story",
        publisher="example.com",
        published_at=None,
        fetched_at="2026-09-26T00:00:00Z",
        content_hash="b" * 64,
    )
    second = supervisor.persist_acquired_signals([fresh], client, correlation_id="GDELT-2", accepted_at="2026-09-26T00:00:00Z")
    assert len(second["created"]) == 1
    signal = next(body for kind, body in client.created if kind == "grit_signals")
    assert signal["grit_evidenceconfidence"] == EVIDENCE_LOW
    assert client.patched[0][0] == "grit_signals"
    assert len(supervisor.ledger.rows()) == 1


def test_organization_url_drops_a_copied_api_path() -> None:
    assert organization_url("https://contoso.crm5.dynamics.com/api/data/v9.2/") == "https://contoso.crm5.dynamics.com"
    assert organization_url("https://contoso.api.crm.dynamics.com/.default") == "https://contoso.crm.dynamics.com"
    try:
        organization_url("https://make.powerapps.com/environments/abc")
    except DataverseError as exc:
        assert "must not include a path" in str(exc)
    else:
        raise AssertionError("a Power Apps page URL was accepted as the environment root")


def test_missing_dataverse_env_fails_before_a_write() -> None:
    import os

    from dataverse.client import ENV_VARS

    saved = {name: os.environ.pop(name, None) for name in ENV_VARS}
    try:
        require_env()
    except DataverseError as exc:
        assert "DATAVERSE_CLIENT_SECRET" in str(exc)
        assert "BEGIN PRIVATE KEY" not in str(exc)
    else:
        raise AssertionError("missing Dataverse environment was accepted")
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value
