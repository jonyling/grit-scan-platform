# Prepare the weekly exposure snapshot with M365 Copilot

GRIT-Scan cannot read internal email, GRIT and ISOS alerts, the travel tracker,
or project papers. Each week, use M365 Copilot, which can, to prepare the
exposure snapshot. Then a named reviewer or BA head approves it, and
`grit weekly` admits it.

## Prepare the snapshot

1. Open M365 Copilot and paste the prompt from the next section. Replace
   `BUSINESS_AREA` and `DATE`.
2. Check each entry against its source. Delete any entry you cannot verify.
3. Save the JSON as `secrets/internal/PACK/DATE.json`, for example
   `secrets/internal/urs/2026-11-09.json`. Git ignores `secrets/`.
4. Run the weekly scan with the snapshot and the approver's profile:

   ```bash
   .venv/bin/python -m grit weekly --pack urs --date 2026-11-09 --reasoned --fast \
     --internal-evidence secrets/internal/urs/2026-11-09.json --evidence-approved-by PROFILE
   ```

   Replace `PROFILE` with the approver's GRIT-Scan profile ID. The profile must
   be a reviewer or BA head.

## Prompt

```text
Prepare the GRIT-Scan exposure snapshot for BUSINESS_AREA as of DATE, from
internal sources only: GRIT and ISOS alert emails, GRIT impact assessments, the
travel tracker, HR safety confirmations, project registers and the latest risk
papers. Return JSON in exactly the format of
docs/templates/exposure-snapshot.example.json.

Rules:
- Give counts of staff and travellers, never names.
- Give each entry a "reference": the document or email it comes from, with its date.
- "safetyConfirmed" is the date staff safety was last confirmed; leave it out if not found.
- For each alert in the last 7 days, list the nearest sites with their distance in km,
  as stated in the GRIT impact assessment. Do not estimate a distance.
- Leave out anything you cannot find. Never estimate or round up a number.
```
