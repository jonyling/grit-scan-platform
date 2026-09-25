# Acquisition

Adapters live here. They may propose candidates. They do not score, and they
must not import `assess_impact`.

Lanes: `APPROVED`, `WILD`, `SPHERA`, `AGILE`, `ISOS`. ISOS returns an empty
list until a feed exists. Sphera and AGILE scores are dropped on the way in.

Do not copy `.env`, `secrets/`, `output/`, or credential files from GCP_Gemini.
