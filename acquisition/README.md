# Acquisition

Adapters live here. They may propose candidates. They do not score, and they
must not import `assess_impact`.

Lanes: `APPROVED`, `WILD`, `SPHERA`, `AGILE`, `ISOS`. ISOS returns an empty
list until a feed exists. Sphera and AGILE scores are dropped on the way in.

The live wild scan is GDELT, not a search engine. The scan loop calls
`collect_export_scan()`: one 15-minute events file, then a local filter
per condition. It does not poll the DOC API. Tone is not read. `DATEADDED`
is `seen_at`, not a publication date. The file has no headline, so those
rows are not Dataverse signals. `acquisition/currents.py` is the headline stream. It calls
currentsapi.services, not GDELT, and allows one or two pulls. The key stays
in `CURRENTS_API_KEY`. A failed fetch is an error, not an empty week.

Do not copy `.env`, `secrets/`, `output/`, or credential files from GCP_Gemini.
