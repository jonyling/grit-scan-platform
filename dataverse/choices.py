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
