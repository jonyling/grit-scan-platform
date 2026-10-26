# 42. Daily lane and nightly index

**Status:** Built (Step 4 of the merged plan), 2026-10-20.
**Trigger:** The merged plan in [docs/design/reader-notes.md](../docs/design/reader-notes.md), section 11, and the Co-Pilot design, section 5 ([docs/design/copilot-architecture.md](../docs/design/copilot-architecture.md)).
**Builds on:** specs 40 and 41.

## 1. What it does

Once a day, GRIT-Scan reads the primary-source feeds that readers named, and sends one Currents query for each Group standing condition and each active management request. Once a week, it sweeps the named sources that have no usable feed. Every page goes through the same deterministic assessment as a weekly scan. A nightly index then groups pages into events and writes a digest per pack, so the Co-Pilot can answer "what's new". The weekly and monthly scans are unchanged. The daily lane uses no Brave queries, apart from the weekly source sweep.

To run it once a day, for example at 05:30 Singapore time from `cron`:

```bash
.venv/bin/grit daily
```

A crontab line for the host:

```text
30 5 * * * cd /home/khengaikjony.ling/grit-scan-platform && .venv/bin/grit daily >> output/daily.log 2>&1
```

`--lookback-days 7` catches up after a gap; the default reads two days. `--no-currents` sends no Currents query, `--no-sweep` skips the source sweep, and `--dry-run` prints the packs, feeds and due sweeps without fetching.

## 2. The feed register

`grit/daily/feeds.py` lists every feed with its packs, its status, and the reader's note that names the source. A feed enters the register only when a reader's note names its source (Co-Pilot decision 4). On 20 October 2026, every feed came from Wilson's note N1.

| Status | Meaning | Feeds on 20 October 2026 |
|---|---|---|
| ACTIVE | Read every day | Atlantic Council, Defense News, SemiAnalysis, Tom's Hardware, Epoch AI, METR, UK AI Security Institute, NIST, The Quantum Insider, and the Federal Register notices of BIS and OFAC |
| STALE | The feed answers, but its newest item is months old | TrendForce (July 2026), CSIS (2016) |
| BLOCKED | The site refuses automated readers, or has no feed | ISW, RUSI, Breaking Defense, Janes, TSMC, PIF, NEOM, u.ae, Anthropic, IBM Quantum, World Bank, IMF, CISA |

BIS and OFAC block automated readers, and OFAC's own feed stopped in February 2025. Both publish their rules and notices in the Federal Register, whose feeds are read instead. The Federal Register has no separate CISA agency, so CISA stays blocked. An unknown agency filter makes the Federal Register return every agency's documents, so `grit feeds --check` checks each agency first.

To list the register, or to read every feed now and report its newest item:

```bash
.venv/bin/grit feeds
.venv/bin/grit feeds --check
```

### Cyber sources (added 23 October 2026)

CSA and CISA are rated sources in the DS&C and URS source registers, but the feed register only carried sources Wilson named in N1, so neither was read until 23 October 2026. Both now serve the Group, DS&C and URS daily runs:

| Source | How it is read |
|---|---|
| CISA Known Exploited Vulnerabilities | The catalogue's JSON. Each vulnerability CISA adds becomes an item linked to its NVD record, with the required action and due date. |
| CISA advisories | Blocked (HTTP 403). One daily Currents query, "CISA advisory", for the news that reports them. |
| Cyber Security Agency of Singapore | No feed. One daily Currents query, "Cyber Security Agency of Singapore", for the news that reports its advisories. |

A register entry with a `currents` query is read this way every day, whatever its status.

### URS Taiwan official hazard and cross-strait sources (added 26 October 2026)

Improvement point 14 gives URS Taiwan its own daily run, fed by official sources. Each source is read in its own format, and each report becomes one item:

| Source | How it is read |
|---|---|
| USGS earthquakes | The past-week GeoJSON feed. One item per earthquake of magnitude 5.0 or more (`USGS_MIN_MAG`) in the Taiwan region (longitude 118 to 123.5, latitude 21 to 26.5), with depth, PAGER alert level and tsunami flag. |
| Taiwan NCDR official alerts | NCDR's Atom feed, which is public domain and carries the Central Weather Administration's warnings. One item per typhoon, tsunami, heavy-rain, flooding, debris-flow, work-and-school-closure, power, water or air-raid alert. The title gives the category in English; the alert text stays in Chinese as issued. Earthquakes come from USGS, which gives magnitudes. |
| Taiwan Ministry of National Defense | The English PLA Activities list. One item per daily report, titled with its counts, for example "Taiwan MND: 3 PLA aircraft sorties, 6 PLAN ships, 9 official ships around Taiwan; 1 crossed the Strait median line". A report is dated 06:00 Taiwan time on its issue date. |
| NASA FIRMS | The VIIRS area API for Taiwan. One item per day with 5 or more high-confidence fire detections (`FIRMS_MIN_DETECTIONS`), giving the strongest detection's location. It needs a free `FIRMS_MAP_KEY`; until one is set, each daily run reports the feed as failed. Error messages never show the key. |
| Central Weather Administration | Blocked. Its RSS terms forbid parsing it into derived products, and its open-data API needs a key. Its warnings arrive through NCDR. |

An official daily series repeats its wording every day, so the near-duplicate rule would merge a week of MND reports into one. Reports from the same series publisher (`SERIES_PUBLISHERS` in `grit/core/reader_filter.py`: MND, USGS, NCDR, NASA and CISA) dated on different days are therefore never merged. Other outlets' pages still merge into them as corroboration.

On 26 October 2026, a dry run read three MND reports, and the URS Taiwan rules kept all three under the Taiwan Strait pressure condition. USGS had no earthquake of magnitude 5.0 or more that week, and NCDR's alerts were wind, road and reservoir notices, which GRIT-Scan does not read.

### Source sweep for stale and blocked sources

Each stale or blocked source has a sweep query, such as `site:understandingwar.org`. Once every 28 days (`SWEEP_DAYS`), `grit daily` sends one Brave News query per source for the last week: about 16 queries a month, within Brave's free credit. A source whose query failed is tried again the next day. The sweep is skipped without a Brave key, and when Brave is within 10% of its monthly limit; `--no-sweep` turns it off.

Only the source's own pages are kept from the results. They enter the daily runs of every pack the source serves, so the same rules decide on them and kept pages are queued for the next weekly or monthly scan. On 20 October 2026, 11 of the 15 sources returned 115 pages, and the rules kept 18. CISA, RUSI, PIF and u.ae returned nothing that week.

A page from a source not in the register, such as an article a reader forwards, can reach the pool through **Add to pool** under a web citation in the Co-Pilot, or with `grit pool-add` (spec 41).

### Topic keywords in Title Case headlines

The sweep found a fault in the matching rule. A name stops at another capitalised word, so "Leonardo" does not match "Leonardo DiCaprio". That guard also applied to topic keywords, so a condition keyword never matched a Title Case headline: "Houthi" did not count in "Houthi Attacks in Saudi Arabia". Condition, hazard and watchpoint keywords now skip the name guard. Names keep it. The fix applies to runs assessed from 20 October 2026. Earlier weekly runs were not reassessed, because their briefs are already written.

## 3. The daily run

For each pack an active feed serves, and the Group pack:

1. The run `DATE-PACK-daily` starts, with kind `DAILY`.
2. Each active feed's items from the lookback window become signals with a sighting in the run. An item without a date is skipped, because its age cannot be checked.
3. The week's source-sweep pages for the pack's stale and blocked sources join the run.
4. Currents receives one query per standing condition and per active management request, from the pack's scan plan. The sightings carry the query's lane and target, as in a weekly scan. `CURRENTS_DAILY_MAX` caps the queries per pack and day (default 20). A query already answered that day is not sent again.
5. New pages are embedded. The run's deterministic assessment then applies the link ladder, keep rule, tier, conditions, harm scorer and RED rule.
6. Every kept page is queued for the pack's next weekly or monthly scan (`pool_additions`, added by `daily-lane`). That scan's rules assess it again before it can reach a brief.
7. RED drafts are written for a named person to send, as the weekly report does. A page gets one draft per pack, whichever run reads it first. Drafts from daily runs show in the dashboard's RED tab beside the selected run's own.
8. The run's kept signals are written to `output/runs/DATE-PACK-daily/DATE-PACK-daily-signals.csv` (improvement point 9), in the brief's order: tier, RED first, newest first. The columns are published time, tier, RED, risk level, trend, harm, link to STE, independent publishers, title, publisher, the feed or query that found it, why it was kept, and URL. The file is UTF-8 with a byte-order mark, so Excel shows Chinese titles correctly.

Weekly and monthly code ignores `DAILY` runs. This covers the previous-run comparison, the dashboard's run list, the Co-Pilot's `get_state`, and the fit report. The Co-Pilot's signal search includes daily pages.

## 4. The nightly index

`grit daily` ends with the nightly index. To run the index alone:

```bash
.venv/bin/grit index
```

Each step is deterministic and safe to repeat.

| Step | What it does |
|---|---|
| Full text | Adds new signals to the FTS5 index. |
| Events | Joins each newly kept signal to an event when it tells the same story: the same title within 8 days, or cosine 0.90 or more with a member published within 8 days. Otherwise it starts an event. The lead is the best-labelled member: lowest tier, then earliest. The Co-Pilot's search collapses pages of one event into one result. |
| Digests | For each pack and day, from the runs dated that day: the newly kept pages, the new tier 1 and 2 events, the events that match an active management request (whatever their tier), RED patterns and drafts, moved standing items, and watchpoint updates. A page kept on an earlier day is not new again. Each night rebuilds the last three days, and `grit report` rebuilds its run date's digest, so a weekly run that finishes after the night's index still reaches its day. |
| Retention | After 90 days, a page that no run kept keeps its title and the first 500 characters of its text. Kept pages and all vectors stay whole. |

## 5. whats_new and the Co-Pilot

The Co-Pilot's `whats_new` tool reads the digests since a date (default: the last 7 days) for the packs in the reader's reach. Each day's digest is a citable brief-state source (`B`) whose text states its counts. Each new event is a signal source (`S`). The drawer has a **What's new since yesterday?** button.

The answer checks also changed:

- Codes in capitals that carry a digit, such as `STE-GC-01`, `H200` or `HBM3E`, are not read as numbers. Amounts and units such as `$12bn`, `3x` or `40-year` are still checked.
- An answer may name today's or yesterday's date ("since 19 October") without a source. The bare numbers of those dates are still checked elsewhere in a sentence.

## 6. Live result, 20 October 2026

A 7-day catch-up read 274 pages from 11 feeds and 28 Currents queries. The rules kept 46, and all 46 were queued for the next scans. The nightly index grouped 1,421 kept signals into 1,361 events, 48 of them with more than one outlet.

## 7. Not yet built

- A scheduler on the host. The crontab line in section 1 is ready; installing it is the operator's decision.
- Feeds for readers other than Wilson, who have no notes yet.
