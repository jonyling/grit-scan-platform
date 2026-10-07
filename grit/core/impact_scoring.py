"""Typed contracts for deterministic GRIT-Scan impact scoring.

Rules are governed by ``15_Severity_Definition.md``. Scoring bodies are added
one at a time and reviewed before the next is written.

``SeverityBand`` is an ``IntEnum`` and ``INSIGNIFICANT == 0``. Never truth-test
an instance; compare it explicitly with another ``SeverityBand`` instead.

Absence of a rating is expressed with exactly one sentinel per cause, never
both: ``ValueAtRiskBasis.INSUFFICIENT_DATA`` is reserved for the FINANCIAL
dimension only (it names *why* value-at-risk could not be computed).
``DimensionAssessment.NOT_ASSESSED`` is used for HEALTH_SAFETY,
REGULATORY_COMPLIANCE, and REPUTATION, and for ``SeverityResult.max_band``
in the degenerate case where every dimension is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum, IntEnum
from typing import Mapping, Protocol, Sequence

SCORING_MODULE_VERSION = "0.5.0"


class SeverityBand(IntEnum):
    INSIGNIFICANT = 0
    MINOR = 1
    MODERATE = 2
    MAJOR = 3
    SEVERE = 4


class SeverityDimension(str, Enum):
    FINANCIAL = "FINANCIAL"
    HEALTH_SAFETY = "HEALTH_SAFETY"
    REGULATORY_COMPLIANCE = "REGULATORY_COMPLIANCE"
    REPUTATION = "REPUTATION"

    @property
    def display_name(self) -> str:
        return {
            SeverityDimension.FINANCIAL: "Financial",
            SeverityDimension.HEALTH_SAFETY: "Health & Safety",
            SeverityDimension.REGULATORY_COMPLIANCE: "Regulatory Compliance",
            SeverityDimension.REPUTATION: "Reputation",
        }[self]


class DimensionAssessment(str, Enum):
    """Absence sentinel for non-financial dimensions and the overall result."""

    NOT_ASSESSED = "NOT_ASSESSED"


class TransmissionRung(str, Enum):
    DIRECT = "DIRECT"
    COUNTERPARTY = "COUNTERPARTY"
    PERIMETER = "PERIMETER"
    SECTOR = "SECTOR"
    AMBIENT = "AMBIENT"

    @property
    def factor(self) -> float | None:
        return {
            TransmissionRung.DIRECT: 1.0,
            TransmissionRung.COUNTERPARTY: 0.7,
            TransmissionRung.PERIMETER: 0.4,
            TransmissionRung.SECTOR: 0.2,
            TransmissionRung.AMBIENT: None,
        }[self]


class TransmissionSource(str, Enum):
    AUTO = "auto"
    PROPOSED = "proposed"
    HUMAN = "human"


class EvidenceConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Horizon(str, Enum):
    MATERIALISED = "MATERIALISED"
    IMMINENT = "IMMINENT"
    DEVELOPING = "DEVELOPING"
    LATENT = "LATENT"


class ImpactVerdict(str, Enum):
    HIGH_NEGATIVE_IMPACT = "HIGH_NEGATIVE_IMPACT"
    POTENTIAL_HIGH = "POTENTIAL_HIGH"
    NOT_HIGH = "NOT_HIGH"


class OpportunityCostStatus(str, Enum):
    """Strategic white-space class. Never a harm score and never a guessed loss."""

    NOT_ASSESSED = "NOT_ASSESSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_LINKED = "NOT_LINKED"
    PRESENT = "PRESENT"
    OPPORTUNITY_COST = "OPPORTUNITY_COST"


class Presence(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class SourceTier(str, Enum):
    TIER_0 = "TIER_0"
    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"
    TIER_4 = "TIER_4"
    UNRATED = "UNRATED"

    @property
    def rank(self) -> int:
        """Lower is more credible. TIER_3/TIER_4 exist to carry the SOP's
        mandatory caveat rule; see grade_evidence."""
        return {
            SourceTier.TIER_0: 0,
            SourceTier.TIER_1: 1,
            SourceTier.TIER_2: 2,
            SourceTier.TIER_3: 3,
            SourceTier.TIER_4: 4,
            SourceTier.UNRATED: 5,
        }[self]


class RegistryKind(str, Enum):
    ENTITY = "ENTITY"
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    PROJECT = "PROJECT"


class KeywordTier(str, Enum):
    """Matches the source sheet's own two watch-list tiers, nothing invented."""

    KEY_TIER_2 = "KEY_TIER_2"
    EMERGING = "EMERGING"


class MediaReach(str, Enum):
    LOCAL = "LOCAL"
    NATIONAL = "NATIONAL"
    INTERNATIONAL = "INTERNATIONAL"


class ValueAtRiskBasis(str, Enum):
    """Financial-dimension basis only. Never used for other dimensions."""

    MARGIN_AT_RISK = "MARGIN_AT_RISK"
    LD_PENALTY_EXPOSURE = "LD_PENALTY_EXPOSURE"
    UNRECOVERABLE_COST_ESCALATION = "UNRECOVERABLE_COST_ESCALATION"
    UNBILLED_RECEIVABLE = "UNBILLED_RECEIVABLE"
    REMEDIATION_COST = "REMEDIATION_COST"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ProjectStatus(str, Enum):
    LIVE = "LIVE"
    PIPELINE = "PIPELINE"


@dataclass(frozen=True)
class SourceEvidence:
    """Acquisition-supplied facts; deterministic scoring performs no I/O.

    Known limitation, not fixed here: ``independent_outlet`` can be inflated
    by wire syndication (one wire story carried by three papers counts as
    three distinct outlets and alone clears grade_evidence's >=2 bar).
    Acquisition must tag syndicated pickups against their originating wire
    before this reaches scoring; this module has no way to detect syndication
    on its own.
    """

    url: str
    source_tier: SourceTier
    independent_outlet: str
    url_check_passed: bool
    dates_cross_checked: bool
    figures_cross_checked: bool


@dataclass(frozen=True)
class RiskObservations:
    """Structured, observable facts for non-financial severity scoring."""

    fatalities: int | None
    permanent_incapacity: bool | None
    debarment_months: int | None
    work_suspension_days: int | None
    criminal_offence_or_censure: bool | None
    media_reach: MediaReach | None


@dataclass(frozen=True)
class Signal:
    """Normalized acquisition payload passed into deterministic scoring."""

    signal_id: str
    scan_date: date
    published_date: date | None
    event_date: date | None
    expected_impact_date: date | None
    title: str
    body: str
    country: str
    entity: str | None
    lob: str | None
    sources: tuple[SourceEvidence, ...]
    observations: RiskObservations
    value_at_risk: float | None
    value_at_risk_basis: ValueAtRiskBasis
    proposed_transmission: TransmissionRung | None = None
    proposed_horizon: Horizon | None = None
    risk_taxonomy_ref: str | None = None
    participation_question: bool | None = None

    @property
    def source_text(self) -> str:
        return f"{self.title}\n{self.body}"


@dataclass(frozen=True)
class RegistryEntry:
    """Primary, canonical entry seeded from URS Entity Info.xlsx.

    ``entity_id`` is derived from a slug of ``canonical_name``, not workbook row
    position, so inserting a row upstream cannot silently re-point an existing
    alias at the wrong entity.
    """

    entity_id: str
    canonical_name: str
    registry_kind: RegistryKind
    ba: str | None
    country: str | None


@dataclass(frozen=True)
class AliasRecord:
    """Optional explicit overlay, keyed to an existing RegistryEntry entity_id."""

    entity_id: str
    alias: str


@dataclass(frozen=True)
class LobKeyword:
    """One governed SECTOR/PERIMETER keyword, seeded from '6. LOB_Keywords'."""

    lob: str
    keyword: str
    tier: KeywordTier


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    project_name: str
    entity: str
    lob: str
    country: str
    contract_value: float | None
    margin_at_risk: float | None
    status: ProjectStatus
    named_counterparties: tuple[str, ...]
    named_assets: tuple[str, ...]


class ProjectRegistry(Protocol):
    def lookup(self, entity: str, country: str) -> Sequence[ProjectRecord]: ...
    def coverage_gaps(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class NullProjectRegistry:
    """v0 register: declares absent contract data instead of inventing it."""

    def lookup(self, entity: str, country: str) -> Sequence[ProjectRecord]:
        return ()

    def coverage_gaps(self) -> tuple[str, ...]:
        return (
            "project/contract register unavailable: financial value-at-risk cannot be assessed",
            "project/contract register unavailable: PERIMETER and SECTOR require human confirmation",
        )


@dataclass(frozen=True)
class TransmissionRegistry:
    """Canonical register plus explicit alias overlay and project-register projection."""

    entries: tuple[RegistryEntry, ...]
    aliases: tuple[AliasRecord, ...]
    project_registry: ProjectRegistry
    snapshot_hash: str
    lob_keywords: tuple[LobKeyword, ...] = ()


@dataclass(frozen=True)
class SeverityBandBracket:
    """One row of the source table, with comparators parsed explicitly.

    ``lower_percent``/``lower_amount`` come from the row's ``>`` figure
    (``None`` amount only for INSIGNIFICANT, which has no lower bracket).
    ``upper_percent``/``upper_amount`` come from the row's ``<`` figure
    (``None`` for SEVERE, which is open-ended).
    """

    lower_percent: float
    lower_amount: float | None
    upper_percent: float | None
    upper_amount: float | None


@dataclass(frozen=True)
class SeverityDefinition:
    """Parsed definition. ``preceding_year_ebit`` is an explicit config input,
    never derived from the source table."""

    version: str
    preceding_year_ebit: float
    brackets: Mapping[SeverityBand, SeverityBandBracket]

    @property
    def financial_lower_bounds(self) -> Mapping[SeverityBand, float]:
        return {band: bracket.lower_percent for band, bracket in self.brackets.items()}


@dataclass(frozen=True)
class SeverityDimensionResult:
    """A band, or exactly one absence sentinel appropriate to the dimension."""

    band: SeverityBand | ValueAtRiskBasis | DimensionAssessment
    basis: str

    @property
    def is_absent(self) -> bool:
        return self.band is ValueAtRiskBasis.INSUFFICIENT_DATA or self.band is DimensionAssessment.NOT_ASSESSED


@dataclass(frozen=True)
class TransmissionResult:
    rung: TransmissionRung
    matched_entity: str | None
    matched_alias: str | None
    registry_kind: RegistryKind | None
    character_offset: int | None
    source: TransmissionSource
    rationale: str
    matched_keyword: str | None = None

    @property
    def factor(self) -> float | None:
        return self.rung.factor


@dataclass(frozen=True)
class SeverityResult:
    max_band: SeverityBand | ValueAtRiskBasis | DimensionAssessment
    driving_dimension: SeverityDimension | None
    dimensions: Mapping[SeverityDimension, SeverityDimensionResult]
    severity_definition_version: str
    rationale: str


@dataclass(frozen=True)
class EvidenceResult:
    confidence: EvidenceConfidence
    source_tier: SourceTier
    corroborating_outlet_count: int
    gating_urls_resolved: bool
    weak_source_present: bool
    caveat_required: bool
    caveat_text: str | None
    rationale: str


@dataclass(frozen=True)
class VerdictResult:
    verdict: ImpactVerdict
    rationale: str


@dataclass(frozen=True)
class OpportunityCostResult:
    """Ontology-bound white space. Financial cost stays absent until supplied."""

    status: OpportunityCostStatus
    confirmation_required: bool
    presence: Presence
    matched_lob: str | None
    matched_keyword: str | None
    country: str | None
    financial_basis: ValueAtRiskBasis
    rationale: str


@dataclass(frozen=True)
class ImpactAssessment:
    signal_id: str
    scan_date: date
    entity: str | None
    lob: str | None
    severity: SeverityResult
    transmission: TransmissionResult
    evidence: EvidenceResult
    horizon: Horizon
    verdict: VerdictResult
    opportunity_cost: OpportunityCostResult
    value_at_risk_basis: ValueAtRiskBasis
    risk_taxonomy_ref: str | None
    scoring_module_version: str
    registry_snapshot_hash: str
    coverage_gaps: tuple[str, ...]


_DIRECT_KINDS = (RegistryKind.ENTITY, RegistryKind.PROJECT)
_COUNTERPARTY_KINDS = (RegistryKind.CUSTOMER, RegistryKind.SUPPLIER)


def _name_matches(signal: Signal, registry: TransmissionRegistry) -> list[tuple[RegistryEntry, str | None, int]]:
    """Entries whose canonical name or an alias appears in the signal text."""
    haystack = signal.source_text.casefold()
    aliases_by_entity: dict[str, list[str]] = {}
    for alias in registry.aliases:
        aliases_by_entity.setdefault(alias.entity_id, []).append(alias.alias)

    matches: list[tuple[RegistryEntry, str | None, int]] = []
    for entry in registry.entries:
        offset = haystack.find(entry.canonical_name.casefold())
        if offset != -1:
            matches.append((entry, None, offset))
            continue
        for alias in aliases_by_entity.get(entry.entity_id, ()):
            offset = haystack.find(alias.casefold())
            if offset != -1:
                matches.append((entry, alias, offset))
                break
    return matches


def _earliest_match(
    matches: list[tuple[RegistryEntry, str | None, int]], kinds: tuple[RegistryKind, ...]
) -> tuple[RegistryEntry, str | None, int] | None:
    candidates = [match for match in matches if match[0].registry_kind in kinds]
    if not candidates:
        return None
    # Earliest mention wins; entity_id breaks ties for determinism.
    return min(candidates, key=lambda match: (match[2], match[0].entity_id))


def _keyword_matches(signal: Signal, registry: TransmissionRegistry) -> list[tuple[LobKeyword, int]]:
    haystack = signal.source_text.casefold()
    matches: list[tuple[LobKeyword, int]] = []
    for keyword in registry.lob_keywords:
        offset = haystack.find(keyword.keyword.casefold())
        if offset != -1:
            matches.append((keyword, offset))
    return matches


def resolve_transmission(signal: Signal, registry: TransmissionRegistry) -> TransmissionResult:
    """Resolve the highest transmission rung from name matches only.

    DIRECT and COUNTERPARTY are the only rungs resolved automatically, from
    an explicit canonical-name or alias match against the registry's
    ENTITY/PROJECT and CUSTOMER/SUPPLIER records respectively. DIRECT
    outranks COUNTERPARTY when a signal names both -- naming one of our own
    entities always dominates naming a customer or supplier alongside it.

    Below that, PERIMETER and SECTOR are proposed only, never auto-resolved:
    a human must confirm before either is acted on. Both are driven by the
    governed LOB keyword taxonomy ('6. LOB_Keywords'), not by a generic
    business-area/country guess: a keyword match alone is SECTOR (a provable
    link to one of our industry segments, unconfirmed); a keyword match
    combined with the signal's country matching a registered entity's
    country is PERIMETER (same segment, same footprint). No keyword match at
    all -- even where the country matches -- is AMBIENT, resolved
    automatically as a rejection: a country mention by itself says nothing
    about relevance to us.
    """
    matches = _name_matches(signal, registry)

    direct = _earliest_match(matches, _DIRECT_KINDS)
    if direct is not None:
        entry, alias, offset = direct
        return TransmissionResult(
            rung=TransmissionRung.DIRECT,
            matched_entity=entry.entity_id,
            matched_alias=alias,
            registry_kind=entry.registry_kind,
            character_offset=offset,
            source=TransmissionSource.AUTO,
            rationale=(
                f"DIRECT: signal names our own registered entity '{entry.canonical_name}'"
                + (f" (matched via alias '{alias}')" if alias else "")
                + "."
            ),
        )

    counterparty = _earliest_match(matches, _COUNTERPARTY_KINDS)
    if counterparty is not None:
        entry, alias, offset = counterparty
        return TransmissionResult(
            rung=TransmissionRung.COUNTERPARTY,
            matched_entity=entry.entity_id,
            matched_alias=alias,
            registry_kind=entry.registry_kind,
            character_offset=offset,
            source=TransmissionSource.AUTO,
            rationale=(
                f"COUNTERPARTY: signal names a registered {entry.registry_kind.value.lower()} "
                f"'{entry.canonical_name}'" + (f" (matched via alias '{alias}')" if alias else "") + "."
            ),
        )

    keyword_matches = _keyword_matches(signal, registry)
    known_countries = {entry.country.casefold() for entry in registry.entries if entry.country}
    country_match = bool(signal.country) and signal.country.casefold() in known_countries

    if keyword_matches:
        keyword, offset = min(keyword_matches, key=lambda match: (match[1], match[0].lob, match[0].keyword))
        rung = TransmissionRung.PERIMETER if country_match else TransmissionRung.SECTOR
        country_clause = " and the signal's country matches a registered entity's country" if country_match else ""
        return TransmissionResult(
            rung=rung,
            matched_entity=None,
            matched_alias=None,
            registry_kind=None,
            character_offset=offset,
            source=TransmissionSource.PROPOSED,
            matched_keyword=keyword.keyword,
            rationale=(
                f"{rung.value}: no named entity/customer/supplier match, but the signal matches the "
                f"{keyword.tier.value} keyword '{keyword.keyword}' for {keyword.lob}{country_clause}; "
                "proposed for human confirmation."
            ),
        )

    return TransmissionResult(
        rung=TransmissionRung.AMBIENT,
        matched_entity=None,
        matched_alias=None,
        registry_kind=None,
        character_offset=None,
        source=TransmissionSource.AUTO,
        rationale="AMBIENT: no name or LOB-keyword link to the registry; rejected.",
    )


_FINANCIAL_BANDS_DESCENDING = (
    SeverityBand.SEVERE,
    SeverityBand.MAJOR,
    SeverityBand.MODERATE,
    SeverityBand.MINOR,
    SeverityBand.INSIGNIFICANT,
)

# Tie-break order when two or more dimensions share the max band.
_DIMENSION_TIE_PRIORITY = (
    SeverityDimension.HEALTH_SAFETY,
    SeverityDimension.REGULATORY_COMPLIANCE,
    SeverityDimension.FINANCIAL,
    SeverityDimension.REPUTATION,
)


def _score_financial(signal: Signal, definition: SeverityDefinition) -> SeverityDimensionResult:
    if signal.value_at_risk is None or signal.value_at_risk_basis is ValueAtRiskBasis.INSUFFICIENT_DATA:
        return SeverityDimensionResult(
            band=ValueAtRiskBasis.INSUFFICIENT_DATA,
            basis="value_at_risk is absent or its basis is INSUFFICIENT_DATA; never inferred from any other figure.",
        )
    ratio = signal.value_at_risk / definition.preceding_year_ebit
    for band in _FINANCIAL_BANDS_DESCENDING:
        lower_bound = definition.financial_lower_bounds[band]
        if ratio >= lower_bound:
            return SeverityDimensionResult(
                band=band,
                basis=(
                    f"value_at_risk={signal.value_at_risk:g} ({signal.value_at_risk_basis.value}) is "
                    f"{ratio:.4%} of preceding_year_ebit={definition.preceding_year_ebit:g}, "
                    f">= {band.name} lower bound of {lower_bound:.4%}"
                ),
            )
    raise AssertionError("ratio fell below the INSIGNIFICANT lower bound of 0%, which should be impossible")


def _score_health_safety(observations: RiskObservations) -> SeverityDimensionResult:
    fatalities = observations.fatalities
    permanent_incapacity = observations.permanent_incapacity
    if fatalities is None and permanent_incapacity is None:
        return SeverityDimensionResult(
            band=DimensionAssessment.NOT_ASSESSED,
            basis="no fatalities or permanent_incapacity observation reported",
        )
    if fatalities is not None and fatalities >= 2:
        return SeverityDimensionResult(band=SeverityBand.SEVERE, basis=f"fatalities={fatalities} (multiple deaths)")
    if (fatalities is not None and fatalities == 1) or permanent_incapacity is True:
        return SeverityDimensionResult(
            band=SeverityBand.MAJOR,
            basis=f"fatalities={fatalities}, permanent_incapacity={permanent_incapacity} (single death or permanent incapacity)",
        )
    return SeverityDimensionResult(
        band=SeverityBand.INSIGNIFICANT,
        basis=f"fatalities={fatalities}, permanent_incapacity={permanent_incapacity} (no death or permanent incapacity reported)",
    )


def _score_regulatory_compliance(observations: RiskObservations) -> SeverityDimensionResult:
    debarment_months = observations.debarment_months
    work_suspension_days = observations.work_suspension_days
    criminal_offence_or_censure = observations.criminal_offence_or_censure
    if debarment_months is None and work_suspension_days is None and criminal_offence_or_censure is None:
        return SeverityDimensionResult(
            band=DimensionAssessment.NOT_ASSESSED,
            basis="no debarment_months, work_suspension_days, or criminal_offence_or_censure observation reported",
        )
    if criminal_offence_or_censure is True:
        return SeverityDimensionResult(
            band=SeverityBand.SEVERE,
            basis="criminal_offence_or_censure=True (public censure or criminal offence)",
        )
    if debarment_months is not None and debarment_months >= 60:
        return SeverityDimensionResult(
            band=SeverityBand.SEVERE,
            basis=f"debarment_months={debarment_months} (debarment lasting 5 years or more)",
        )
    if work_suspension_days is not None and work_suspension_days > 30:
        return SeverityDimensionResult(
            band=SeverityBand.SEVERE,
            basis=f"work_suspension_days={work_suspension_days} (work suspension lasting more than 1 month)",
        )
    if debarment_months is not None and debarment_months > 0:
        return SeverityDimensionResult(
            band=SeverityBand.MAJOR,
            basis=f"debarment_months={debarment_months} (any form of debarment, under 5 years)",
        )
    if work_suspension_days is not None and work_suspension_days > 0:
        return SeverityDimensionResult(
            band=SeverityBand.MAJOR,
            basis=f"work_suspension_days={work_suspension_days} (work suspension lasting not more than 1 month)",
        )
    return SeverityDimensionResult(
        band=SeverityBand.INSIGNIFICANT,
        basis=(
            f"debarment_months={debarment_months}, work_suspension_days={work_suspension_days}, "
            f"criminal_offence_or_censure={criminal_offence_or_censure} "
            "(no debarment, work suspension, or censure reported)"
        ),
    )


def _score_reputation(observations: RiskObservations) -> SeverityDimensionResult:
    media_reach = observations.media_reach
    if media_reach is None:
        return SeverityDimensionResult(band=DimensionAssessment.NOT_ASSESSED, basis="no media_reach observation reported")
    if media_reach is MediaReach.INTERNATIONAL:
        return SeverityDimensionResult(band=SeverityBand.SEVERE, basis="media_reach=INTERNATIONAL (international coverage)")
    if media_reach is MediaReach.NATIONAL:
        return SeverityDimensionResult(
            band=SeverityBand.MAJOR,
            basis="media_reach=NATIONAL (serious public outcry or adverse media publicity)",
        )
    return SeverityDimensionResult(band=SeverityBand.MINOR, basis="media_reach=LOCAL (minimal impact; low media coverage)")


def score_severity(signal: Signal, definition: SeverityDefinition) -> SeverityResult:
    """Score all four dimensions independently from structured facts only,
    then return the maximum -- never an average, sum, or weighting.

    Non-financial dimensions read exactly one slice of ``RiskObservations``
    each (HEALTH_SAFETY: fatalities/permanent_incapacity; REGULATORY_COMPLIANCE:
    debarment_months/work_suspension_days/criminal_offence_or_censure;
    REPUTATION: media_reach). A dimension is ``NOT_ASSESSED`` only when every
    one of its fields is ``None``; an explicit ``0``/``False`` is a real fact
    (no harm occurred) and scores INSIGNIFICANT, never NOT_ASSESSED.

    FINANCIAL never falls back to any other figure: absence of
    ``value_at_risk`` (or its basis being INSUFFICIENT_DATA) always yields
    ``ValueAtRiskBasis.INSUFFICIENT_DATA`` for that dimension alone.

    If every dimension is absent, ``max_band`` is
    ``DimensionAssessment.NOT_ASSESSED`` and ``driving_dimension`` is
    ``None`` -- absence is never scored as INSIGNIFICANT.
    """
    dimension_results: dict[SeverityDimension, SeverityDimensionResult] = {
        SeverityDimension.FINANCIAL: _score_financial(signal, definition),
        SeverityDimension.HEALTH_SAFETY: _score_health_safety(signal.observations),
        SeverityDimension.REGULATORY_COMPLIANCE: _score_regulatory_compliance(signal.observations),
        SeverityDimension.REPUTATION: _score_reputation(signal.observations),
    }

    assessed = {
        dimension: result.band
        for dimension, result in dimension_results.items()
        if not result.is_absent
    }
    if not assessed:
        return SeverityResult(
            max_band=DimensionAssessment.NOT_ASSESSED,
            driving_dimension=None,
            dimensions=dimension_results,
            severity_definition_version=definition.version,
            rationale="NOT_ASSESSED: every dimension is absent (FINANCIAL is INSUFFICIENT_DATA; all others are NOT_ASSESSED).",
        )

    max_band = max(assessed.values())
    tied = [dimension for dimension in _DIMENSION_TIE_PRIORITY if assessed.get(dimension) == max_band]

    if len(tied) == 1:
        driving_dimension = tied[0]
        rationale = f"{max_band.name}: driven by {driving_dimension.value} ({dimension_results[driving_dimension].basis})."
    else:
        driving_dimension = tied[0]
        tied_names = ", ".join(dimension.value for dimension in tied)
        rationale = (
            f"{max_band.name}: tie between {tied_names}; broken in favour of {driving_dimension.value} "
            f"per fixed priority order ({dimension_results[driving_dimension].basis})."
        )

    return SeverityResult(
        max_band=max_band,
        driving_dimension=driving_dimension,
        dimensions=dimension_results,
        severity_definition_version=definition.version,
        rationale=rationale,
    )


_TIER_4_CAVEAT_TEXT = (
    "Based on available reporting; source credibility does not meet the SOP's "
    "primary evidentiary bar (Tier-0/Tier-1)."
)


def grade_evidence(signal: Signal) -> EvidenceResult:
    """Grade recorded acquisition evidence per the SOP credibility hierarchy.

    HIGH requires a non-empty TIER_0/TIER_1 corroborating set (the *only*
    set gated for cross-checking and URL resolution) with two or more
    distinct independent outlets overall. Gating exclusively on that set
    keeps grading monotonic: a weaker source can add to the outlet count via
    ``weak_source_present`` but can never drag a qualifying signal down --
    only a failure *within* the TIER_0/TIER_1 set caps the result at LOW.
    When no TIER_0/TIER_1 source exists at all, the full source set becomes
    the gating set by necessity, since there is no stronger subset to prefer;
    such a signal cannot reach HIGH regardless.

    Any signal whose best available tier is TIER_3 or TIER_4 carries the SOP's
    mandatory caveat, independent of the confidence grade itself.
    """
    sources = signal.sources
    if not sources:
        return EvidenceResult(
            confidence=EvidenceConfidence.LOW,
            source_tier=SourceTier.UNRATED,
            corroborating_outlet_count=0,
            gating_urls_resolved=False,
            weak_source_present=False,
            caveat_required=False,
            caveat_text=None,
            rationale="LOW: no sources were supplied for this signal.",
        )

    best_tier = min((source.source_tier for source in sources), key=lambda tier: tier.rank)
    caveat_required = best_tier.rank >= SourceTier.TIER_3.rank
    caveat_text = _TIER_4_CAVEAT_TEXT if caveat_required else None

    # Known limitation (not fixed here): wire syndication can inflate this
    # count; see SourceEvidence.independent_outlet.
    corroborating_outlet_count = len({source.independent_outlet for source in sources if source.independent_outlet})
    weak_source_present = any(source.source_tier.rank > SourceTier.TIER_1.rank for source in sources)

    primary_sources = tuple(source for source in sources if source.source_tier.rank <= SourceTier.TIER_1.rank)
    # No TIER_0/TIER_1 source exists: there is no stronger subset to prefer,
    # so the full (all-weak) source set is, by necessity, the gating set.
    gating_sources = primary_sources if primary_sources else sources
    gate_url_check_passed = all(source.url_check_passed for source in gating_sources)
    gate_cross_checked = all(source.dates_cross_checked and source.figures_cross_checked for source in gating_sources)

    if not gate_url_check_passed:
        return EvidenceResult(
            confidence=EvidenceConfidence.LOW,
            source_tier=best_tier,
            corroborating_outlet_count=corroborating_outlet_count,
            gating_urls_resolved=False,
            weak_source_present=weak_source_present,
            caveat_required=caveat_required,
            caveat_text=caveat_text,
            rationale="LOW: a source in the best-tier corroborating set has a URL that did not resolve; capped at LOW regardless of tier.",
        )

    meets_tier = bool(primary_sources)
    meets_corroboration = corroborating_outlet_count >= 2

    if meets_tier and meets_corroboration and gate_cross_checked:
        rationale = (
            f"HIGH: TIER_0/TIER_1 corroborating set present ({best_tier.value}), "
            f"{corroborating_outlet_count} independent outlet(s) overall, dates/figures "
            "cross-checked and URLs resolved across that set."
        )
        if weak_source_present:
            rationale += " Weaker corroborating source(s) also present but did not gate the grade."
        return EvidenceResult(
            confidence=EvidenceConfidence.HIGH,
            source_tier=best_tier,
            corroborating_outlet_count=corroborating_outlet_count,
            gating_urls_resolved=True,
            weak_source_present=weak_source_present,
            caveat_required=caveat_required,
            caveat_text=caveat_text,
            rationale=rationale,
        )

    unmet: list[str] = []
    if not meets_tier:
        unmet.append(f"no TIER_0/TIER_1 source is present (best available tier is {best_tier.value})")
    if not meets_corroboration:
        unmet.append(f"only {corroborating_outlet_count} independent outlet(s), fewer than 2")
    if not gate_cross_checked:
        unmet.append("dates and/or figures are not cross-checked across the best-tier corroborating set")

    return EvidenceResult(
        confidence=EvidenceConfidence.MEDIUM,
        source_tier=best_tier,
        corroborating_outlet_count=corroborating_outlet_count,
        gating_urls_resolved=True,
        weak_source_present=weak_source_present,
        caveat_required=caveat_required,
        caveat_text=caveat_text,
        rationale="MEDIUM: " + "; ".join(unmet),
    )


def _severity_label(severity: SeverityResult) -> str:
    if not isinstance(severity.max_band, SeverityBand):
        return severity.max_band.value
    label = severity.max_band.name.title()
    if severity.driving_dimension is not None:
        return f"{label} ({severity.driving_dimension.display_name})"
    return label


def is_high_negative_impact(severity: SeverityResult, transmission: TransmissionResult, evidence: EvidenceResult) -> VerdictResult:
    """Apply the published High Negative Impact rule table.

    HIGH_NEGATIVE_IMPACT requires severity >= MAJOR, transmission in
    (DIRECT, COUNTERPARTY), and HIGH evidence, all three at once. The same
    severity/transmission gate with MEDIUM or LOW evidence yields
    POTENTIAL_HIGH, never HIGH_NEGATIVE_IMPACT -- evidence only gates the
    verdict down, it never substitutes for severity or transmission. An
    absent severity (NOT_ASSESSED or INSUFFICIENT_DATA) never satisfies the
    severity gate, since it is not a SeverityBand at all.
    """
    severity_met = isinstance(severity.max_band, SeverityBand) and severity.max_band >= SeverityBand.MAJOR
    transmission_met = transmission.rung in (TransmissionRung.DIRECT, TransmissionRung.COUNTERPARTY)
    severity_label = _severity_label(severity)

    if not (severity_met and transmission_met):
        return VerdictResult(
            verdict=ImpactVerdict.NOT_HIGH,
            rationale=(
                f"NOT_HIGH: {severity_label} + {transmission.rung.value} + {evidence.confidence.value} evidence "
                "(severity >= MAJOR and transmission in DIRECT/COUNTERPARTY are both required)."
            ),
        )

    if evidence.confidence is EvidenceConfidence.HIGH:
        return VerdictResult(
            verdict=ImpactVerdict.HIGH_NEGATIVE_IMPACT,
            rationale=f"HIGH_NEGATIVE_IMPACT: {severity_label} + {transmission.rung.value} + High evidence.",
        )

    return VerdictResult(
        verdict=ImpactVerdict.POTENTIAL_HIGH,
        rationale=(
            f"POTENTIAL_HIGH: {severity_label} + {transmission.rung.value} + {evidence.confidence.value.title()} "
            "evidence (High evidence is required to reach HIGH_NEGATIVE_IMPACT; evidence gates, it does not upgrade)."
        ),
    )


_IMMINENT_WINDOW_DAYS = 90


def classify_horizon(signal: Signal) -> Horizon:
    """Classify action posture from ``expected_impact_date`` vs ``scan_date``.

    News signals almost always report events that have already occurred, so
    ``event_date`` alone carries no information about what to do next: a
    debarment announced yesterday but taking effect in six months is a
    materialised *event* with a developing *impact*. This function reads only
    the acquisition-supplied ``expected_impact_date`` -- when the impact
    lands, not when the event occurred.

    Does not read severity, transmission, or evidence, and is not read by
    them in return -- the horizon must never influence the impact score.

    The source spec numerically defines only IMMINENT (<90 days); the
    remaining mapping is this module's explicit, documented interpretation:

    - ``expected_impact_date`` is ``None``: LATENT -- a background/standing
      condition with nothing to anchor a horizon on.
    - ``expected_impact_date <= scan_date``: MATERIALISED -- the impact has
      already landed as of scan time.
    - ``0 <`` days until ``expected_impact_date < 90``: IMMINENT.
    - days until ``expected_impact_date >= 90``: DEVELOPING -- a known future
      date, not yet near-term.

    ``event_date`` and ``published_date`` are intentionally not read here;
    ``event_date`` feeds the SOP's separate recency filter, not the horizon.
    """
    if signal.expected_impact_date is None:
        return Horizon.LATENT
    if signal.expected_impact_date <= signal.scan_date:
        return Horizon.MATERIALISED
    days_until_impact = (signal.expected_impact_date - signal.scan_date).days
    if days_until_impact < _IMMINENT_WINDOW_DAYS:
        return Horizon.IMMINENT
    return Horizon.DEVELOPING


def _registered_presence(signal: Signal, registry: TransmissionRegistry) -> Presence:
    """Presence is a registered entity or project, never a guessed bid."""
    if _earliest_match(_name_matches(signal, registry), _DIRECT_KINDS) is not None:
        return Presence.PRESENT
    country = signal.country.strip()
    if not country:
        return Presence.UNKNOWN
    folded = country.casefold()
    for entry in registry.entries:
        if entry.registry_kind not in (RegistryKind.ENTITY, RegistryKind.PROJECT):
            continue
        if entry.country and entry.country.casefold() == folded:
            return Presence.PRESENT
    return Presence.ABSENT


def classify_opportunity_cost(signal: Signal, registry: TransmissionRegistry) -> OpportunityCostResult:
    """Classify white space without turning it into a harm score.

    A market opening is never inferred from prose. Acquisition must set
    ``participation_question``. The ontology then decides whether a governed
    line of business is linked and whether we are already present. Absence of
    a value-at-risk figure stays ``INSUFFICIENT_DATA``; it is not estimated.
    """
    country = signal.country.strip() or None
    financial_basis = signal.value_at_risk_basis
    presence = _registered_presence(signal, registry)

    def result(
        status: OpportunityCostStatus,
        confirmation_required: bool,
        matched_lob: str | None,
        matched_keyword: str | None,
        rationale: str,
    ) -> OpportunityCostResult:
        return OpportunityCostResult(
            status=status,
            confirmation_required=confirmation_required,
            presence=presence,
            matched_lob=matched_lob,
            matched_keyword=matched_keyword,
            country=country,
            financial_basis=financial_basis,
            rationale=rationale,
        )

    if signal.participation_question is None:
        return result(
            OpportunityCostStatus.NOT_ASSESSED,
            False,
            None,
            None,
            "NOT_ASSESSED: participation question was not supplied; a market opening is never inferred from prose.",
        )
    if signal.participation_question is False:
        return result(
            OpportunityCostStatus.NOT_APPLICABLE,
            False,
            None,
            None,
            "NOT_APPLICABLE: acquisition explicitly marked this as not a participation question.",
        )

    keyword_matches = _keyword_matches(signal, registry)
    if not keyword_matches:
        return result(
            OpportunityCostStatus.NOT_LINKED,
            False,
            None,
            None,
            "NOT_LINKED: participation question supplied, but no governed LOB keyword matches.",
        )
    keyword, _offset = min(keyword_matches, key=lambda match: (match[1], match[0].lob, match[0].keyword))

    if presence is Presence.UNKNOWN:
        return result(
            OpportunityCostStatus.NOT_ASSESSED,
            False,
            keyword.lob,
            keyword.keyword,
            "NOT_ASSESSED: LOB link exists, but country is absent, so white space cannot be established.",
        )
    if presence is Presence.PRESENT:
        return result(
            OpportunityCostStatus.PRESENT,
            False,
            keyword.lob,
            keyword.keyword,
            f"PRESENT: a registered entity or project is already in scope for {country}. This is not the cost of staying out.",
        )

    if financial_basis is not ValueAtRiskBasis.INSUFFICIENT_DATA and signal.value_at_risk is not None:
        money = "A value-at-risk figure was supplied and is recorded, but it is not converted into a harm score."
    else:
        money = "Cost of not participating is not established; financial severity remains INSUFFICIENT_DATA."
    return result(
        OpportunityCostStatus.OPPORTUNITY_COST,
        True,
        keyword.lob,
        keyword.keyword,
        (
            f"OPPORTUNITY_COST: '{keyword.keyword}' links this to {keyword.lob}, and no registered "
            f"entity or project is present in {country}. Human confirmation is required. {money}"
        ),
    )


def assess_impact(signal: Signal, transmission_registry: TransmissionRegistry, severity_definition: SeverityDefinition) -> ImpactAssessment:
    """Thin orchestrator: calls the pure functions, makes no judgement itself."""
    evidence = grade_evidence(signal)
    horizon = classify_horizon(signal)
    severity = score_severity(signal, severity_definition)
    transmission = resolve_transmission(signal, transmission_registry)
    verdict = is_high_negative_impact(severity, transmission, evidence)
    opportunity_cost = classify_opportunity_cost(signal, transmission_registry)

    return ImpactAssessment(
        signal_id=signal.signal_id,
        scan_date=signal.scan_date,
        entity=signal.entity,
        lob=signal.lob,
        severity=severity,
        transmission=transmission,
        evidence=evidence,
        horizon=horizon,
        verdict=verdict,
        opportunity_cost=opportunity_cost,
        value_at_risk_basis=signal.value_at_risk_basis,
        risk_taxonomy_ref=signal.risk_taxonomy_ref,
        scoring_module_version=SCORING_MODULE_VERSION,
        registry_snapshot_hash=transmission_registry.snapshot_hash,
        coverage_gaps=get_coverage_gaps(transmission_registry.project_registry),
    )


def get_coverage_gaps(registry: ProjectRegistry) -> tuple[str, ...]:
    """Expose explicit degraded-mode gaps for the weekly quality-check note."""
    return registry.coverage_gaps()
