# Acquisition

Adapters live here. They may propose candidates. They do not score, and they
must not import `assess_impact`.

Lanes: `APPROVED`, `WILD`, `SPHERA`, `AGILE`, `ISOS`. ISOS returns an empty
list until a feed exists. Sphera and AGILE scores are dropped on the way in.

The live wild scan is GDELT, not a search engine. The scan loop calls
`collect_export_scan()`: one 15-minute events file, then a local filter
per condition. It does not poll the DOC API. Tone is not read. `DATEADDED`
is `seen_at`, not a publication date. The file has no headline, so those
rows are not Dataverse signals. `grit/acquisition/currents.py` is the headline stream. It calls
currentsapi.services, not GDELT, and allows one or two pulls. The key stays
in `CURRENTS_API_KEY`. A failed fetch is an error, not an empty week.

`grit/acquisition/brave.py` is a second headline stream, from the Brave News Search
API. Its queries are not typed by hand. `plan_queries()` builds one Boolean
string per active LOB in a named pack, from that LOB's governed keywords:
`"railway signalling" OR "CBTC" OR ...`. A long list is split, not cut, and
inactive LOBs are skipped. A keyword only widens the pull. It is not an STE
link, so every row still goes through the bind. The key stays in
`BRAVE_SEARCH_API_KEY`. A failed fetch is an error, not an empty week. One run
makes at most six pulls. Print the plan with
`python -m grit.acquisition.brave --pack urs-tw --plan-only`, and narrow a large
pack with `--lob`. The command stores nothing.

`grit/acquisition/brave_plan.py` sends the weekly query plan from `grit/ontology/query_plan.py` to the same
News Search API. It reuses this module's mapping, logs each search as it returns, collapses the
results to one candidate per page, and stops with a recorded reason at a monthly or rate limit. It
stores its run under `output/brave-plan/`. It does not fetch a page or score a result.

Do not copy `.env`, `secrets/`, `output/`, or credential files from GCP_Gemini.
