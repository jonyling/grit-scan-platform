from acquisition.candidate import Candidate
from dataverse.choices import EVIDENCE_LOW
from dataverse.client import DataverseError, organization_url, require_env
from dataverse.signals import signal_body
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
