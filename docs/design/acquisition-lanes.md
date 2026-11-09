# Acquisition lanes

This page describes the adapters in `grit/acquisition/`: what each one fetches, what it returns, and which command uses it.

## Rules for every adapter

An adapter fetches pages and returns `Candidate` records. It does not score, rank, or link a page to the STE register. It must not import the harm scorer (`assess_impact`); a contract test checks this.

A failed fetch raises an error. It never returns an empty list, because an empty list means "nothing happened this week".

Each candidate carries one source lane:

| Lane | Source | Status |
|---|---|---|
| `APPROVED` | Pages from an approved source list. | Adapter only. No command uses it. |
| `WILD` | Search results from Brave News, Currents, or GDELT. | In use. |
| `SPHERA` | Sphera supplier-risk alerts. Sphera's own scores are dropped. | In use on the daily lane. `secrets/sphera.env` holds the OAuth password grant. |
| `AGILE` | AGILE alerts. AGILE's own scores are dropped. | Adapter only. |
| `ISOS` | International SOS alerts. | Returns an empty list until a feed exists. |

## Brave News

Brave News is the main source. The weekly reader scan (`grit scan` and `grit weekly`) sends each reader pack's queries through `grit/acquisition/brave_plan.py`.

- Each query is sent once per run. A query already answered in that run is not sent again, so an interrupted scan resumes.
- Results collapse to one candidate per page. The URL loses tracking parameters and a trailing slash.
- The candidate's text is Brave's description plus its extra snippets. A snippet that is a list of other headlines (two or more separators such as `·`) is dropped.
- The run stops, and records why, at the monthly limit (`BRAVE_MONTHLY_LIMIT`, default 2000, counted in `output/brave-usage.json`) or when Brave keeps answering HTTP 429.

The key is `BRAVE_SEARCH_API_KEY`.

`grit/acquisition/brave.py` holds the shared result mapping. It also has a small command that prints one Boolean query per active LOB and stores nothing:

```bash
.venv/bin/python -m grit.acquisition.brave --pack urs-tw --plan-only
```

## Currents

`grit/acquisition/currents.py` calls currentsapi.services. It allows at most two pulls per run and 20 records per pull, and it keeps only records with a title and a URL. To add Currents results to a reader scan, pass up to two queries:

```bash
.venv/bin/python -m grit scan --pack urs-tw --currents "Taiwan Strait" "Kaohsiung MRT"
```

The key is `CURRENTS_API_KEY`.

## GDELT

`grit/acquisition/gdelt.py` reads GDELT's public 15-minute events export: it downloads the latest file listed in `lastupdate.txt` and filters it locally for each query. That path does not poll the DOC API. The export has a URL and a date added, but no headline. `DATEADDED` is stored as `seen_at`, not as a publication date, and tone and Goldstein scores are not read. A bad checksum raises `GdeltFetchError`. The export is an index. It is not written as a signal.

The daily lane does call the DOC artlist, because a page needs a title before the keep rule can assess it. One query per standing condition and active management request, up to `GDELT_DAILY_MAX` (default 8), with a 30-second timeout. GDELT's seen time is stored as the page date so the keep rule can drop a page from after the run date or from outside the window. A failed request is an error, not an empty day. `--no-gdelt` skips the lane. The weekly Brave scan does not call GDELT.

## Sphera

`grit/acquisition/sphera_lane.py` reads `secrets/sphera.env`. The file holds `SPHERA_AUTH_URL`, `SPHERA_CLIENT_ID`, `SPHERA_CLIENT_SECRET`, `SPHERA_USERNAME`, `SPHERA_PASSWORD`, and one `SPHERA_API_URL` per resource. A live `grit daily` asks the token endpoint, then reads `event_messages` newest first for the lookback window. Directly affected supplier names come from `risk_objects`. `score_cards` and `total_risk_score` are not read. The same pages are added to each pack in that run. `--no-sphera` skips the lane. A failed auth or a failed read is reported and is not treated as an empty day. The status line never includes a value from the file.

## Credentials

Keys live in the gitignored `.env`. Do not copy `.env`, `secrets/`, `output/`, or credential files from another repository, such as the `GCP_Gemini` project.
