"""Choice values already accepted by the audited-signal import.

These are Dataverse option-set codes, not scores. Do not invent new ones.
"""

EVIDENCE_LOW = 100000502
EXECUTION_COMPLETE = 100000401
WORKFLOW_NEW = 100000000
TRANSITION_START = 100000100
ACTOR_SUPERVISOR = 100000200
TRIGGER_SYSTEM = 100000304
# Live grit_logstatus choice. Formatted label is SUCCEEDED. It means the call finished, not that the proposal was accepted.
AGENT_LOG_SUCCEEDED = 100000800

# grit_trend on grit_signals. Deployed once for the URS-TW cascade. The same
# choice column is reused for any reader pack, Urban Solutions included.
TREND_ESCALATING = 100002100
TREND_EASING = 100002101
TREND_NO_MATERIAL_CHANGE = 100002102

# grit_risklevel on grit_signals ("GRIT Risk Level" global choice).
RISK_LEVEL_HIGH = 100002300
RISK_LEVEL_MEDIUM = 100002301
RISK_LEVEL_LOW = 100002302
