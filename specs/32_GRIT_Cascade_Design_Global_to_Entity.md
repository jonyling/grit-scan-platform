# GRIT-Scan: Cascade Design (Global Affairs → Entity)

**Status:** Proposed design — supersedes the earlier perimeter-first URS TW design
**Audience:** CEO / CFO, via Group R&A and the BP
**Register:** Strategic narrative, not operational log

## 1. The Cascade

```text
1. Current State of Global Affairs
        |
        v
2. Major Risk Themes of STE
        |
        v
3. Business Areas                        e.g. Urban Solutions
        |
        v
4. Lines of Business                     e.g. MRT E&M Main Contracting
        |
        v
5. Specific Entities / Business Units     e.g. URS Taiwan
```

The report is written **top-down**, in the order the reader thinks. Each layer answers "so what" for the layer below it, and the reader can stop at any depth and still hold a coherent picture.

## 2. Why Top-Down, and Why the Perimeter-First Design Was Wrong

The earlier design started at Layer 5 — the URS Taiwan exposure perimeter — and worked upward, driven by the BA-specific SOP's ten-cell coverage matrix.

That was a misreading, for two reasons.

**The audience sits at the top.** The report reaches the CEO and CFO, and the CFO responds in broad strokes. That is the register of someone tracking a continuing narrative about the world, not someone auditing entity-level coverage. A report assembled upward from a supplier list will not produce that framing, however complete it is.

**The matrix was never report content.** SOP §6.1 says *retain* the filled matrix and self-challenge for audit trail. Retain, not publish. The coverage matrix is the **working paper** that makes a conclusion defensible; the report is the conclusion. The earlier design promoted the working paper into the deliverable.

Both views reconcile: coverage discipline survives as evidence beneath the report, and applies where it belongs — at Layer 5.

## 3. Layer 1 — The Current State of Global Affairs

This layer does not exist yet, and is the immediate build target. Nothing below it can be framed properly without it.

### 3.1 A standing picture, not a weekly discovery

The critical design decision. Layer 1 is **not** a fresh scan each week producing a list of global news. It is a **persistent assessment of a small number of global conditions**, revised weekly.

The consequence: the weekly news is the **delta** — what moved, what hardened, what eased — against an assessment the reader already holds. That is what makes a narrative continuous across weeks, and what a broad-strokes reader is actually tracking.

A rediscovered list each week has no memory and reads as disconnected. A revised standing picture accumulates.

### 3.2 Baseline conditions

Defined by the world, not by STE's risk register. The mapping to STE happens at Layer 2; if Layer 1 were defined by STE's themes, the cascade would already be collapsed and the outside-in view lost.

The approved working baseline is maintained as data in [config/URS-TW-Global-Baseline.json](config/URS-TW-Global-Baseline.json). It contains ten Taiwan-relevant conditions organised around **US-China strategic competition**. The set includes the US Federal Reserve's effect on global borrowing costs as a distinct condition because Fed decisions transmit into TWD, financing costs, supplier liquidity, project margins and cash flexibility.

The current ten conditions are:

1. US-China strategic competition
2. China political trajectory and Taiwan objective
3. Taiwan Strait military and maritime pressure
4. US political, alliance and Taiwan policy trajectory
5. US Federal Reserve policy and global borrowing costs
6. US-China technology and AI confrontation
7. Sanctions, export controls and selective economic pressure
8. Taiwan domestic, economic and financial resilience
9. Middle East and Russia-Ukraine conflicts affecting the Taiwan calculation
10. Taiwan whole-of-society and infrastructure resilience

This is a working baseline, not an immutable taxonomy. Changes to the condition set should be deliberate and versioned because the weekly product revises these standing assessments rather than rebuilding an unrelated news list.

### 3.3 What is held per condition

```text
condition            name and domain
currentState         2-3 sentences of narrative assessment
direction            Escalating | Stable | Easing
directionPriorWeek   for week-over-week movement
confidence           High | Medium | Low
assessmentSince      when the current reading was set
whatMovedThisWeek    the delta, or explicitly "no material change"
watchIndicators      what would shift this assessment
keySources           what informed it
```

`watchIndicators` matters more than it appears. It states in advance what would change our mind, which converts a standing assessment from an opinion into something falsifiable — and gives the CFO the natural question to ask back.

### 3.4 Register

Narrative prose. Assessed, not hedged into uselessness. Confidence stated where it is genuinely low. No matrices, no cell counts, no scores at this layer.

## 4. Layer 2 — Major Risk Themes of STE

The hinge of the cascade, and the only layer with a fixed vocabulary. Fourteen documented themes, already agreed with the business.

**Key Risks (6):** Competition · Financial volatility · Escalating costs of major projects · Cybersecurity · Geopolitical impact to operations · Fraud, bribery and corruption

**Tier-2 (5):** Taiwan business concentration · Supply chain disruption from natural disasters & pandemic · Regulatory compliance · Workplace health and safety · Climate change adaptation

**Emerging (3):** Geopolitical volatility affecting staff safety & supply chain · Emerging technologies disrupting business · Talent scarcity in emerging technologies

The question at this layer: **given the global picture, which themes are under pressure this week, and in which direction?**

The value is that the CEO already knows these fourteen. Connecting a global condition to a theme the board has agreed makes the point land without further explanation. Each theme carries a pressure reading — Increasing / Stable / Decreasing — and the global conditions driving it.

Themes not under pressure are simply not discussed. There is no obligation to say something about all fourteen.

## 5. Layers 3–4 — Business Areas and Lines of Business

Where a theme under pressure transmits into the business. Named BA, named LOB, and the mechanism — cost, delivery, cash, demand, staff, or compliance.

Not exhaustive. Covers where transmission is real, and says so where it is uncertain.

## 6. Layer 5 — Entities and Business Units

The specific end of the chain: URS Taiwan, TransCore US, and so on. Reached only when there is genuinely something entity-specific to say.

This is where the BA-specific SOPs apply, where the exposure perimeter matters, and where coverage assurance belongs — as retained working paper. A finding here should name a real asset, supplier, contract or cash line.

## 7. The Determinism Gradient

Determinism should **increase as the cascade descends**. This reconciles strategic register with defensibility.

| Layer | Nature | Control |
| --- | --- | --- |
| 1 Global affairs | Synthesis and judgement | Source quality; explicit confidence; week-over-week continuity; stated watch indicators |
| 2 STE themes | Mapping | Must map to one of the 14 documented themes |
| 3–4 BA / LOB | Attribution | Named BA and LOB; stated transmission mechanism |
| 5 Entity | Specific fact | Named asset, supplier or cash line; SOP coverage retained as working paper |

"What is the state of the world" is a synthesis question and cannot be made deterministic without destroying it. "Does this touch a named supplier" is a factual question and should never be left to judgement. Applying the wrong mode at either end is the failure.

## 8. Where the Report Stops

Most weeks the cascade will run strongly through Layers 1 and 2 and reach no further. That is a legitimate and complete result, and should be stated plainly:

> Global conditions moved as follows; these themes are under pressure; **no entity-level escalation this week.**

This is a far more natural expression of "no escalation" for an executive reader than a list of matrix cells returning nil. The claim is that the cascade was followed and did not reach the bottom — not that seven boxes were ticked.

Depth is earned by evidence. The report descends as far as the evidence supports and stops there.

## 9. Build Sequence

**Build Layer 1 first, alone, and get it in front of the reader.**

1. **Confirm the ten conditions** with the business.
2. **Establish the opening standing picture** — a baseline assessment of all ten. This is the one-off effort everything afterwards revises. It is the foundation and should not be rushed.
3. **Run the weekly revision cycle** for two or three weeks, producing the Layer 1 narrative only.
4. **Test the register against the actual reader.** Wilson sends it; the CFO's response is the signal. Adjust tone, length and condition set on that evidence before building anything downstream.
5. **Add Layer 2 mapping** once the Layer 1 register is right.
6. **Add Layers 3–4** where transmission is real.
7. **Add Layer 5** last, reusing the BA SOPs and the existing signal pipeline as feeders.

Layer 1 has standalone value. If the cascade stopped there permanently it would still be a useful executive product — which makes it a safe place to start and a poor place to over-engineer.

## 10. What Happens to the SOP Coverage Discipline

Not discarded — relocated.

- It applies at **Layer 5**, where BA-specific SOPs live.
- It is **retained as working paper**, per SOP §6.1, not published in the report.
- It answers the challenge question — "how do you know nothing happened in Taiwan?" — which is asked occasionally, not weekly.

Chuan's existing wildcard scan and the audited-signal pipeline become **feeders into Layers 4 and 5**, unchanged. They already produce entity- and LOB-level signals. What was missing was the framing above them: Layers 1 and 2.

## 11. Principal Risks

| Risk | Mitigation |
| --- | --- |
| Layer 1 becomes bland commentary with no edge | Every condition carries a direction and a confidence; "Stable" must be an assessment, not an evasion |
| Standing picture drifts and is never challenged | `watchIndicators` set in advance; direction changes must cite what moved |
| Cascade descends further than evidence supports | Report stops where evidence stops; depth is earned |
| Global narrative loses connection to STE | Layer 2 mapping to the 14 documented themes required for anything carried below Layer 1 |
| Reverts to a weekly news list | Layer 1 is revised, never rebuilt; the delta is the output |
| Executive register lost to detail creep | Layers 1 and 2 are prose; tables begin at Layer 3 |
