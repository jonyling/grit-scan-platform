# grit-scan-platform

Clean-slate GRIT-Scan. The old private repository,
`khengaikjony-ling_stengg/GRIT-Scan`, remains the archive. This history starts here.

The SSH VM is AWS. The service it calls is GCP. Dataverse remains the system of record.

## Rule

Agents propose. Deterministic functions decide. A named human approves.
A missing fact stays missing. A missed market is an `OPPORTUNITY_COST` signal,
not a harm score.

## What is in this seed

- `grit_scan/`: scoring core, including opportunity-cost classification.
- `grit_scan/matchers/`: credibility, exposure, LOB, and theme matchers. They bind text. They do not score.
- `grit_scan/weekly_pipeline.py`: one weekly chain — import, revise, rank, write the review delta.
- `grit_scan/transition_guard.py` and `grit_scan/review_gate.py`: pure workflow decisions. They do not write state.
- `supervisor/`: the only writer of the workflow ledger. It persists a row only after `TransitionGuard()` returns `ALLOW`.
- `ui/`: the Streamlit review screen. It calls `ReviewGate()`. It does not write state.
- `agents/`: Researcher, Assessment, Critic, and Reporting. They propose. The Supervisor accepts or rejects. Gemini is not called.
- `tests/` and `run-impact-contract-tests.py`: the contract runner. P0 and P2 fixture tests are registered.
- `specs/`: governing specifications. Spec 34 is the build order.
- `URS Entity Info.xlsx` and `config/registry_aliases.json`: the URS ontology inputs the tests require.
- `acquisition/`: candidate-only adapters for approved web, wild scan, Sphera, AGILE, and ISOS. The wild scan reads the GDELT 15-minute events export. It does not score. Currents is a separate currentsapi.services headline stream, capped at two pulls.
- `grit_scan/theme_layer.py`: theme and event matches, metric calculation, escalation, and the register write gate. They do not write state.

## Left behind on purpose

PowerShell repair scripts, demo output, mail exports, Power Automate flows,
agent environment exports, and GCP_Gemini secrets. Do not import them.

## Check

```bash
python3 run-impact-contract-tests.py
streamlit run ui/app.py
python3 -m dataverse.run_wild_scan --query "Taiwan Strait" --max-records 10
python3 -m dataverse.run_wild_scan --currents --query "Taiwan Strait" --max-records 10
```

The live command needs `DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`, `DATAVERSE_CLIENT_SECRET`, and `DATAVERSE_ORG_URL` in the environment. The Currents command also needs `CURRENTS_API_KEY`. Do not commit them.
