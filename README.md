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
- `tests/` and `run-impact-contract-tests.py`: the contract runner.
- `specs/`: governing specifications. Spec 34 is the build order.
- `URS Entity Info.xlsx` and `config/registry_aliases.json`: the URS ontology inputs the tests require.
- `acquisition/`: empty on purpose. GCP_Gemini collectors are not copied yet.

## Left behind on purpose

PowerShell repair scripts, demo output, mail exports, Power Automate flows,
agent environment exports, and GCP_Gemini secrets. Do not import them.

## Check

```bash
python3 run-impact-contract-tests.py
```
