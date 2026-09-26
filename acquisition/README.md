# Acquisition

Adapters live here. They may propose candidates. They do not score, and they
must not import `assess_impact`.

Lanes: `APPROVED`, `WILD`, `SPHERA`, `AGILE`, `ISOS`. ISOS returns an empty
list until a feed exists. Sphera and AGILE scores are dropped on the way in.

The live wild scan is GDELT, not a search engine. `collect_wild_scan()`
queries the DOC artlist once per supplied condition, then `wild_scan()`
emits `WILD` candidates. Tone is dropped. `seendate` is `seen_at`, not a
publication date. A failed fetch is an error, not an empty week.

Do not copy `.env`, `secrets/`, `output/`, or credential files from GCP_Gemini.
