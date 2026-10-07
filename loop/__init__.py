"""Loop engineering for priority findings.

For each important signal the loop drafts a six-question card, checks it against the ontology, asks the model to evaluate its own
draft against the same ontology, uses a tool when the evidence is thin, and refines. It stops when every check passes, when a pass
changes nothing, or at the iteration limit. The model proposes. Deterministic functions decide what is valid and compute the confidence.
"""
