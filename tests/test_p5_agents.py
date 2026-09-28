from pathlib import Path

from agents.gemini import gemini_proposer
from agents.layer import run_agent
from agents.tools import AgentTools
from supervisor.service import Supervisor


def _tools(**kwargs) -> AgentTools:
    return AgentTools(known_entity_ids=frozenset({"ENT-KMRT"}), **kwargs)


def test_unknown_entity_is_logged_and_not_accepted() -> None:
    supervisor = Supervisor(actor_id="supervisor-01", known_entity_ids=frozenset({"ENT-KMRT"}))
    supervisor.accept_signal("SIG-1", "EXEC-1", "corr-1", accepted_at="2026-09-25T00:00:00Z")
    before = len(supervisor.ledger.rows())

    def proposer(agent_name: str, prompt: str, task: dict) -> dict:
        return {
            "cited_entity_ids": ["ENT-DOES-NOT-EXIST"],
            "evidence_references": ["https://www.reuters.com/story"],
            "claims": [{"statement": "A named unit is affected.", "established": True}],
            "rationale": "Model prose",
        }

    proposal = run_agent("Assessment", {"query": "Taiwan rail"}, proposer, _tools())
    result = supervisor.submit_agent_proposal(proposal, signal_id="SIG-1", execution_id="EXEC-1", correlation_id="corr-1")
    assert result["accepted"] is False
    assert "UNKNOWN_ENTITY" in result["reason_codes"]
    assert result["prompt_version"] == "p5-assessment-1"
    assert result["response_hash"]
    assert supervisor.accepted_proposals == []
    assert len(supervisor.ledger.rows()) == before
    assert supervisor.agent_log[-1]["record_id"] == result["record_id"]


def test_critic_reports_not_verifiable_and_does_not_fill_the_gap() -> None:
    def proposer(agent_name: str, prompt: str, task: dict) -> dict:
        return {
            "cited_entity_ids": ["ENT-KMRT"],
            "evidence_references": ["https://www.reuters.com/only-one"],
            "claims": [{"statement": "A second outlet confirmed the figure.", "established": True}],
            "severity": "SEVERE",
        }

    proposal = run_agent("Critic", {}, proposer, _tools())
    assert [item["check"] for item in proposal["tool_results"]] == ["entity", "corroboration", "url"]
    assert any(item["status"] == "NOT_VERIFIABLE" for item in proposal["not_verifiable"])
    assert proposal["claims"][0]["established"] is False
    assert "severity" not in proposal
    assert proposal["forbidden_fields_present"] == ["severity"]
    supervisor = Supervisor(actor_id="supervisor-01", known_entity_ids=frozenset({"ENT-KMRT"}))
    result = supervisor.submit_agent_proposal(proposal, signal_id="SIG-1", execution_id="EXEC-1", correlation_id="corr-1")
    assert result["accepted"] is False
    assert "NOT_VERIFIABLE" in result["reason_codes"]
    assert "AGENT_COMPUTED_CONTROLLED_VALUE" in result["reason_codes"]


def test_researcher_cannot_invent_a_source_when_search_is_empty() -> None:
    def proposer(agent_name: str, prompt: str, task: dict) -> dict:
        return {
            "cited_entity_ids": ["ENT-KMRT"],
            "evidence_references": ["https://invented.example/story"],
            "claims": [{"statement": "Invented article", "established": True}],
        }

    tools = _tools(search_results=[])
    proposal = run_agent("Researcher", {"query": "Vietnam rail"}, proposer, tools)
    assert proposal["evidence_references"] == []
    assert proposal["not_verifiable"][0]["check"] == "search"
    assert proposal["claims"][0]["established"] is False
    supervisor = Supervisor(actor_id="supervisor-01", known_entity_ids=frozenset({"ENT-KMRT"}))
    result = supervisor.submit_agent_proposal(
        proposal,
        signal_id="SIG-1",
        execution_id="EXEC-1",
        correlation_id="corr-1",
        permitted_urls=set(),
    )
    assert result["accepted"] is False
    assert "UNEVIDENCED" in result["reason_codes"]


def test_evidenced_known_entity_is_accepted_without_a_workflow_write() -> None:
    def proposer(agent_name: str, prompt: str, task: dict) -> dict:
        return {
            "cited_entity_ids": ["ENT-KMRT"],
            "evidence_references": ["https://www.reuters.com/a", "https://www.taipeitimes.com/b"],
            "claims": [{"statement": "KMRT is named.", "established": True}],
            "rationale": "Both outlets name the registered entity.",
        }

    tools = _tools(url_checks={"https://www.reuters.com/a": True, "https://www.taipeitimes.com/b": True})
    proposal = run_agent("Critic", {}, proposer, tools)
    assert proposal["not_verifiable"] == []
    assert proposal["claims"][0]["established"] is True
    supervisor = Supervisor(actor_id="supervisor-01", known_entity_ids=frozenset({"ENT-KMRT"}))
    before = len(supervisor.ledger.rows())
    result = supervisor.submit_agent_proposal(proposal, signal_id="SIG-1", execution_id="EXEC-1", correlation_id="corr-1")
    assert result["accepted"] is True
    assert result["prompt_version"] == "p5-critic-1"
    assert len(result["response_hash"]) == 64
    assert supervisor.accepted_proposals[0]["response_hash"] == proposal["response_hash"]
    assert len(supervisor.ledger.rows()) == before


def test_same_proposal_has_the_same_response_hash() -> None:
    def proposer(agent_name: str, prompt: str, task: dict) -> dict:
        return {"cited_entity_ids": ["ENT-KMRT"], "evidence_references": ["https://www.reuters.com/a"], "rationale": "Named."}

    first = run_agent("Assessment", {}, proposer, _tools())
    second = run_agent("Assessment", {}, proposer, _tools())
    assert first["response_hash"] == second["response_hash"]


def test_agents_hold_no_dataverse_credential_and_gemini_refuses_without_a_key() -> None:
    root = Path(__file__).parents[1] / "agents"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "client_secret" not in text
        assert "DATAVERSE" not in text
        assert "ledger.append" not in text
        assert "assess_impact" not in text
        assert "BEGIN PRIVATE KEY" not in text
    raised = False
    try:
        gemini_proposer("Researcher", "prompt", {}, env={})
    except RuntimeError as exc:
        raised = "not configured" in str(exc) or "service-account file" in str(exc)
        assert "AIza" not in str(exc)
    assert raised


def test_private_key_text_is_not_accepted_as_an_api_key() -> None:
    raised = False
    try:
        gemini_proposer("Researcher", "prompt", {}, env={"GOOGLE_API_KEY": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----"})
    except RuntimeError as exc:
        raised = "not an API key" in str(exc)
        assert "not-a-real-key" not in str(exc)
    assert raised


def test_agent_log_body_records_a_proposal_and_not_a_score() -> None:
    from dataverse.signals import agent_log_body

    body = agent_log_body(
        agent_name="Critic",
        prompt_version="p5-critic-1",
        task="A headline",
        citations=["https://news.example/story"],
        response_hash="abc",
        rationale="One outlet only.",
        validation="REJECTED: NOT_VERIFIABLE",
        correlation_id="AGENTS-1",
        completed_at="2026-09-28T00:00:00Z",
        execution_id="exec-1",
        signal_id="sig-1",
    )
    assert body["grit_logstatus"] == 100000800
    assert body["grit_supervisorvalidationresult"] == "REJECTED: NOT_VERIFIABLE"
    assert "severity" not in body
    assert "score" not in body
    assert "grit_state" not in body


def test_injected_client_returns_a_proposal_without_a_credential() -> None:
    class _Response:
        text = '{"cited_entity_ids":["ENT-KMRT"],"claims":[{"statement":"Named.","established":true}],"rationale":"From the task."}'

    class _Models:
        def generate_content(self, **kwargs):
            assert kwargs["model"] == "gemini-2.5-flash"
            assert "score" in kwargs["config"]["system_instruction"]
            return _Response()

    class _Client:
        models = _Models()

    raw = gemini_proposer("Assessment", "prompt", {"query": "Taiwan"}, env={}, client=_Client())
    assert raw["cited_entity_ids"] == ["ENT-KMRT"]
    assert raw["claims"][0]["established"] is True
