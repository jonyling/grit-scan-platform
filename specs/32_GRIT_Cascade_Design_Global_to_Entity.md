# GRIT-Scan: Cascade Design (Global Affairs → Entity)

**Status:** Proposed design — supersedes the earlier perimeter-first URS TW design
**Audience:** CEO / CFO, via Group R&A and the BP
**Register:** Strategic narrative, not operational log

## 1. The cascade

The report runs through five layers, from the global picture down to a single entity:

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

The report is written **top-down**, in the order the reader thinks. Each layer answers "so what" for the layer below it. The reader can stop at any depth and still hold a coherent picture.

## 2. Why top-down, and why the perimeter-first design was wrong

The earlier design started at Layer 5, the URS Taiwan exposure perimeter, and worked upward. The BA-specific SOP's ten-cell coverage matrix drove it.

That was a misreading, for two reasons.

**The audience sits at the top.** The report reaches the CEO and CFO. The CFO responds in broad strokes. That is the register of someone tracking a continuing narrative about the world. It is not the register of someone auditing entity-level coverage. A report assembled upward from a supplier list cannot produce that framing, however complete it is.

**The matrix was never report content.** SOP §6.1 says *retain* the filled matrix and self-challenge for audit trail. Retain, not publish. The coverage matrix is the **working paper** that makes a conclusion defensible. The report is the conclusion. The earlier design promoted the working paper into the deliverable.

Both views reconcile. Coverage discipline survives as evidence beneath the report. It applies where it belongs, at Layer 5.

## 3. Layer 1 — The current state of global affairs

This layer is the immediate build target. It does not exist yet. Nothing below it can be framed properly without it.

### 3.1 A standing picture, not a weekly discovery

This is the critical design decision. Layer 1 is a **persistent assessment of a small number of global conditions**, revised weekly. It is **not** a fresh scan each week producing a list of global news.

As a result, the weekly news is the **delta** against an assessment the reader already holds. The delta is what moved, what hardened, and what eased. The delta makes the narrative continuous across weeks. It is also what a broad-strokes reader is actually tracking.

A rediscovered list each week has no memory and reads as disconnected. A revised standing picture accumulates.

### 3.2 Baseline conditions

The world defines the baseline conditions. STE's risk register does not. The mapping to STE happens at Layer 2. If STE's themes defined Layer 1, the cascade would already be collapsed. The outside-in view would be lost.

The approved working baseline is stored as data in the [URS Taiwan global baseline](../config/urs-tw/URS-TW-Global-Baseline.json) file. It contains ten Taiwan-relevant conditions organized around **US-China strategic competition**. The set includes the US Federal Reserve's effect on global borrowing costs as a distinct condition. Fed decisions transmit into TWD, financing costs, supplier liquidity, project margins and cash flexibility.

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

This is a working baseline. It is not an immutable taxonomy. Make changes to the condition set deliberately, and version them. The weekly product revises these standing assessments instead of rebuilding an unrelated news list.

### 3.3 What is held per condition

Each condition holds the following fields:

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

`watchIndicators` matters more than it appears. It states in advance what would change our mind. That turns a standing assessment from an opinion into something falsifiable. It also gives the CFO the natural question to ask back.

### 3.4 Register

Narrative prose. Assessed, not hedged into uselessness. Confidence stated where it is genuinely low. No matrices, no cell counts, no scores at this layer.

## 4. Layer 2 — Major risk themes of STE

Layer 2 is the hinge of the cascade. It is the only layer with a fixed vocabulary: fourteen documented themes, already agreed with the business.

**Key Risks (6):** Competition · Financial volatility · Escalating costs of major projects · Cybersecurity · Geopolitical impact to operations · Fraud, bribery and corruption

**Tier-2 (5):** Taiwan business concentration · Supply chain disruption from natural disasters & pandemic · Regulatory compliance · Workplace health and safety · Climate change adaptation

**Emerging (3):** Geopolitical volatility affecting staff safety & supply chain · Emerging technologies disrupting business · Talent scarcity in emerging technologies

The question at this layer: **given the global picture, which themes are under pressure this week, and in which direction?**

The CEO already knows these fourteen. Connecting a global condition to a theme the board has agreed makes the point land without further explanation. Each theme carries a pressure reading: Increasing, Stable, or Decreasing. It also lists the global conditions driving it.

The report does not discuss themes that are not under pressure. There is no obligation to say something about all fourteen.

## 5. Layers 3–4 — Business areas and lines of business

Layers 3–4 show where a theme under pressure transmits into the business. Each entry names the BA, the LOB, and the mechanism. The mechanism is cost, delivery, cash, demand, staff, or compliance.

These layers cover only where transmission is real. They are not exhaustive. Where transmission is uncertain, they say so.

## 6. Layer 5 — Entities and business units

Layer 5 is the specific end of the chain: URS Taiwan, TransCore US, and so on. The report reaches it only when there is something entity-specific to say.

The BA-specific SOPs apply at this layer. The exposure perimeter matters here. Coverage assurance also belongs here, as retained working paper. A finding here should name a real asset, supplier, contract or cash line.

## 7. The determinism gradient

Determinism must **increase as the cascade descends**. This reconciles strategic register with defensibility.

| Layer | Nature | Control |
| --- | --- | --- |
| 1 Global affairs | Synthesis and judgment | Source quality; explicit confidence; week-over-week continuity; stated watch indicators |
| 2 STE themes | Mapping | Must map to one of the 14 documented themes |
| 3–4 BA / LOB | Attribution | Named BA and LOB; stated transmission mechanism |
| 5 Entity | Specific fact | Named asset, supplier or cash line; SOP coverage retained as working paper |

"What is the state of the world" is a synthesis question. Making it deterministic would destroy it. "Does this touch a named supplier" is a factual question. It must never be left to judgment. Applying the wrong mode at either end is the failure.

## 8. Where the report stops

Most weeks, the cascade runs strongly through Layers 1 and 2 and reaches no further. That is a legitimate and complete result. State it plainly:

> Global conditions moved as follows; these themes are under pressure; **no entity-level escalation this week.**

For an executive reader, this expresses "no escalation" more naturally than a list of matrix cells returning nil. The claim is that the cascade was followed and did not reach the bottom. The claim is not that seven boxes were ticked.

Depth is earned by evidence. The report descends as far as the evidence supports and stops there.

## 9. Build sequence

**Build Layer 1 first, alone, and get it in front of the reader.**

1. **Confirm the ten conditions** with the business.
2. **Establish the opening standing picture** — a baseline assessment of all ten. This is the one-off effort that everything afterward revises. It is the foundation. Do not rush it.
3. **Run the weekly revision cycle** for two or three weeks, producing the Layer 1 narrative only.
4. **Test the register against the actual reader.** Wilson sends it. The CFO's response is the signal. Adjust tone, length and condition set on that evidence before building anything downstream.
5. **Add Layer 2 mapping** once the Layer 1 register is right.
6. **Add Layers 3–4** where transmission is real.
7. **Add Layer 5** last, reusing the BA SOPs and the existing signal pipeline as feeders.

Layer 1 has standalone value. If the cascade stopped there permanently, it would still be a useful executive product. That makes it a safe place to start. It also makes it a poor place to over-engineer.

## 10. What happens to the SOP coverage discipline

The SOP coverage discipline moves. It is not discarded.

- It applies at **Layer 5**, where BA-specific SOPs live.
- It is **retained as working paper**, per SOP §6.1. It is not published in the report.
- It answers the challenge question "how do you know nothing happened in Taiwan?". Readers ask that occasionally, not weekly.

Chuan's existing wildcard scan and the audited-signal pipeline become **feeders into Layers 4 and 5**, unchanged. They already produce entity- and LOB-level signals. What was missing was the framing above them: Layers 1 and 2.

## 11. Principal risks

| Risk | Mitigation |
| --- | --- |
| Layer 1 becomes bland commentary with no edge | Every condition carries a direction and a confidence. "Stable" must be an assessment, not an evasion |
| Standing picture drifts and is never challenged | `watchIndicators` set in advance. Direction changes must cite what moved |
| Cascade descends further than evidence supports | Report stops where evidence stops. Depth is earned |
| Global narrative loses connection to STE | Layer 2 mapping to the 14 documented themes required for anything carried below Layer 1 |
| Reverts to a weekly news list | Layer 1 is revised, never rebuilt. The delta is the output |
| Executive register lost to detail creep | Layers 1 and 2 are prose. Tables begin at Layer 3 |
