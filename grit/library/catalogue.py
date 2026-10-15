"""The report library and the State of the Situation topics (spec 38).

REPORTS is the register of thought-leadership reports the library reads. Each entry names its file under secrets/reports/,
its publisher, its edition, and whether it is public or internal. The files are gitignored: an internal report never leaves
secrets/. TOPICS are the 11 State of the Situation documents: Wilson's eight strategic shifts, plus the three dashboard
themes that sit under no shift. Each of the 12 dashboard themes takes its monitoring baseline from one topic.

A topic reads only the reports listed for it. A topic with no report states that the library does not cover it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PUBLIC, INTERNAL = "PUBLIC", "INTERNAL"


@dataclass(frozen=True)
class Report:
    report_id: str
    file: str  # relative to secrets/reports/
    title: str
    publisher: str
    edition: str
    access: str
    url: str | None = None
    aliases: tuple[str, ...] = ()  # how a sentence may name the publisher when it attributes a forecast
    note: str | None = None
    short: str = ""  # the in-text citation name, such as "PwC Semiconductor"


@dataclass(frozen=True)
class Topic:
    topic_id: str
    name: str
    question: str  # what the document must explain
    themes: tuple[str, ...]  # the Group dashboard themes (STE-GC-nn) and strategic shift (STE-SHIFT-nn) it covers
    reports: tuple[str, ...]
    countries: tuple[str, ...] = ()  # register country names that put STE entities in scope
    keywords: tuple[str, ...] = ()  # matched against the business areas' own risk themes
    coverage: str = "GOOD"  # GOOD, THIN, or GAP, as judged when the library was assembled
    gap_sources: tuple[str, ...] = field(default_factory=tuple)  # proposed sources to close a gap, for Wilson's approval
    counterparties: tuple[str, ...] = ()  # name fragments that pick the topic's suppliers and customers out of the registers


REPORTS = [
    Report("IMF-REO-MCD-2025-05", "IMF.pdf", "Regional Economic Outlook: Middle East and Central Asia. Charting a Path through the Haze",
           "International Monetary Fund", "May 2025", PUBLIC, "https://www.imf.org/en/Publications/REO/MECA", ("IMF", "International Monetary Fund"), short="IMF Regional Outlook"),
    Report("EY-GEO-2025", "2025_Geostrategic_Risks__1736989373.pdf", "2025 Geostrategic Outlook: How geopolitics is driving transformation",
           "EY-Parthenon", "December 2024", PUBLIC, "https://www.ey.com/en_gl/insights/geostrategy/geostrategic-outlook", ("EY", "EY-Parthenon"), short="EY Geostrategic Outlook"),
    Report("WEF-GRR-2026", "WEF_Global_Risks_Report_2026.pdf", "Global Risks Report 2026", "World Economic Forum", "January 2026", PUBLIC,
           "https://www.weforum.org/publications/global-risks-report-2026/", ("WEF", "World Economic Forum", "the Forum"), short="WEF Global Risks"),
    Report("MCK-TTO-2026", "mckinsey technology trends outlook 2026 (1).pdf", "Technology Trends Outlook 2026", "McKinsey & Company", "September 2026", PUBLIC,
           "https://www.mckinsey.com/capabilities/mckinsey-digital/our-insights/the-top-trends-in-tech", ("McKinsey",), short="McKinsey Tech Trends"),
    Report("PWC-SEMI-2026", "pwc-semiconductor-and-beyond-2026-full-report.pdf", "Semiconductor and Beyond 2026", "PwC", "November 2025", PUBLIC,
           "https://www.pwc.com/gx/en/industries/technology/semiconductor-and-beyond.html", ("PwC",), short="PwC Semiconductor"),
    Report("DSC-RISK-REFRESH-2026", "DS&C Risk Refresh 2026.pptx", "Y2026 Refresh of DS&C Key Risk Heatmap and Emerging Risks (98th Risk and Sustainability Committee)",
           "ST Engineering DS&C", "5 August 2026", INTERNAL, None, ("DS&C", "the DS&C risk refresh"), "Co-Confidential. Cite inside STE only.", short="DS&C Risk Refresh"),
    Report("WB-RDNA5-2026", "public/WorldBank_Ukraine_RDNA5_2026-02.pdf", "Ukraine Fifth Rapid Damage and Needs Assessment (RDNA5), February 2022 to December 2025",
           "World Bank, Government of Ukraine, European Commission and United Nations", "February 2026", PUBLIC,
           "https://documents1.worldbank.org/curated/en/099022026094036395/pdf/P514499-22f93f3a-4278-42bc-b907-db9553d12069.pdf", ("World Bank", "RDNA5", "the assessment"), short="World Bank RDNA5"),
    Report("WB-MPO-SAU-2026-10", "public/WorldBank_MPO_sau.pdf", "Macro Poverty Outlook: Saudi Arabia", "World Bank", "October 2026", PUBLIC,
           "https://thedocs.worldbank.org/en/doc/65cf93926fdb3ea23b72f277fc249a72-0500042021/related/mpo-sau.pdf", ("World Bank",), short="World Bank, Saudi Arabia"),
    Report("WB-MPO-ARE-2026-10", "public/WorldBank_MPO_are.pdf", "Macro Poverty Outlook: United Arab Emirates", "World Bank", "October 2026", PUBLIC,
           "https://thedocs.worldbank.org/en/doc/65cf93926fdb3ea23b72f277fc249a72-0500042021/related/mpo-are.pdf", ("World Bank",), short="World Bank, UAE"),
    Report("WB-MPO-QAT-2026-10", "public/WorldBank_MPO_qat.pdf", "Macro Poverty Outlook: Qatar", "World Bank", "October 2026", PUBLIC,
           "https://thedocs.worldbank.org/en/doc/65cf93926fdb3ea23b72f277fc249a72-0500042021/related/mpo-qat.pdf", ("World Bank",), short="World Bank, Qatar"),
    Report("GRI-QTT-2024", "public/GRI_Quantum_Threat_Timeline_2024.pdf", "Quantum Threat Timeline Report 2024", "Global Risk Institute and evolutionQ", "December 2024", PUBLIC,
           "https://globalriskinstitute.org/publication/2024-quantum-threat-timeline-report/", ("Global Risk Institute", "GRI", "the expert survey", "evolutionQ"), short="GRI Quantum Timeline"),
    Report("GRI-QTT-2025-EXEC", "public/GRI_Quantum_Threat_Timeline_2025_Executive_Perspectives.pdf", "Quantum Threat Timeline 2025: Executive Perspectives on Barriers to Action",
           "Global Risk Institute", "February 2025", PUBLIC,
           "https://globalriskinstitute.org/publication/quantum-threat-timeline-2025-executive-perspectives-on-barriers-to-action/", ("Global Risk Institute", "GRI"), short="GRI Executive Perspectives"),
    Report("UKGOV-AI2030-2024", "public/UKGov_AI_2030_Scenarios.pdf", "AI 2030 Scenarios: Helping policy makers plan for the future of AI",
           "UK Government Office for Science", "January 2024", PUBLIC, "https://assets.publishing.service.gov.uk/media/6808fc002a86d6dfb2b52772/AI_2030_Scenarios_Report.pdf",
           ("UK Government", "Government Office for Science", "GO-Science"), short="GO-Science AI 2030"),
    Report("UKGOV-FRONTIER-2023", "public/UKGov_Future_Risks_of_Frontier_AI.pdf", "Future Risks of Frontier AI", "UK Government Office for Science", "October 2023", PUBLIC,
           "https://assets.publishing.service.gov.uk/media/653bc393d10f3500139a6ac5/future-risks-of-frontier-ai-annex-a.pdf", ("UK Government", "Government Office for Science", "GO-Science"), short="GO-Science Frontier AI"),
]

# Held in secrets/reports/ but not read: another tool's output is not evidence.
EXCLUDED = {"GRIT_Scan_URS_BA-wide_1-8Oct2026_v3.docx": "Copilot's URS brief for 1 to 8 October 2026. Kept for comparison with GRIT-Scan's own brief, not as a source."}

TOPICS = [
    Topic("SOTS-MIDEAST", "Middle East defence and infrastructure spending",
          "How the Gulf states' fiscal position, diversification plans and security environment shape defence, infrastructure and digital spending in Saudi Arabia, the UAE and Qatar.",
          ("STE-SHIFT-04", "STE-GC-01", "STE-GC-02"), ("IMF-REO-MCD-2025-05", "WB-MPO-SAU-2026-10", "WB-MPO-ARE-2026-10", "WB-MPO-QAT-2026-10", "EY-GEO-2025", "WEF-GRR-2026"),
          ("Saudi Arabia", "United Arab Emirates", "UAE", "Qatar"), ("Middle East", "Saudi", "UAE", "Qatar", "geopolitical", "localisation")),
    Topic("SOTS-UKRAINE", "Ukraine reconstruction",
          "The scale, sectors, financing and conditions of Ukraine's recovery and reconstruction, and how Russia's war and sanctions bound it.",
          ("STE-SHIFT-05", "STE-GC-04"), ("WB-RDNA5-2026", "EY-GEO-2025", "WEF-GRR-2026"), ("Ukraine",), ("Ukraine", "Russia", "sanctions", "geopolitical")),
    Topic("SOTS-SEMIS", "Semiconductor and GPU access",
          "How AI demand, supply concentration, memory and HBM pricing, and trade restrictions shape access to semiconductors, memory and GPUs.",
          ("STE-SHIFT-03", "STE-GC-06", "STE-GC-07"), ("PWC-SEMI-2026", "MCK-TTO-2026", "DSC-RISK-REFRESH-2026", "EY-GEO-2025", "WEF-GRR-2026"),
          (), ("semiconductor", "memory", "chip", "supply chain", "component"),
          counterparties=("NVIDIA", "Micron", "Xilinx", "Advanced Micro Devices", "NXP", "Axiomtek", "Ubiqconn")),
    Topic("SOTS-QUANTUM", "Quantum-safe security migration",
          "How progress toward a cryptographically relevant quantum computer, post-quantum standards and migration readiness shape the security of long-life systems.",
          ("STE-SHIFT-07", "STE-GC-10"), ("GRI-QTT-2024", "GRI-QTT-2025-EXEC", "WEF-GRR-2026", "MCK-TTO-2026"), (), ("quantum", "cryptograph", "encryption", "cyber")),
    Topic("SOTS-AI-AUTONOMY", "AI and autonomous systems",
          "How frontier AI capability, agentic and autonomous systems, and their risks are developing, and what that means for defence, infrastructure and engineering.",
          ("STE-SHIFT-01", "STE-GC-08"), ("UKGOV-AI2030-2024", "UKGOV-FRONTIER-2023", "MCK-TTO-2026", "WEF-GRR-2026"), (), ("AI", "artificial intelligence", "autonomous", "emerging technolog")),
    Topic("SOTS-SOVEREIGN-AI", "Sovereign AI and digital sovereignty",
          "How governments' push for control over AI, data, cloud and compute, and AI regulation, are reshaping technology markets and procurement.",
          ("STE-SHIFT-02", "STE-GC-09"), ("EY-GEO-2025", "MCK-TTO-2026", "WEF-GRR-2026", "UKGOV-AI2030-2024"), (), ("AI", "data", "cloud", "regulation"), "THIN"),
    Topic("SOTS-TECH-BLOCS", "Export controls and technology blocs",
          "How export controls, sanctions and technology decoupling divide markets, suppliers and technology stacks.",
          ("STE-SHIFT-06", "STE-GC-11"), ("EY-GEO-2025", "PWC-SEMI-2026", "WEF-GRR-2026", "DSC-RISK-REFRESH-2026"), ("China", "USA"), ("export", "trade compliance", "sanction", "bifurcation"), "THIN"),
    Topic("SOTS-HMT", "Human-machine teaming in defence and critical infrastructure",
          "How human-machine teaming and manned-unmanned operations are developing in defence and critical infrastructure.",
          ("STE-SHIFT-08",), ("MCK-TTO-2026", "WEF-GRR-2026"), (), ("unmanned", "autonomous", "robot"), "GAP",
          ("RUSI and CSIS papers on human-machine teaming and military autonomy",)),
    Topic("SOTS-CHINA-TAIWAN", "China-Taiwan tensions",
          "The military, economic and technology dimensions of cross-strait tension and how they reach regional supply chains.",
          ("STE-GC-05",), ("EY-GEO-2025", "WEF-GRR-2026", "PWC-SEMI-2026"), ("Taiwan", "China"), ("Taiwan", "geopolitical", "concentration"), "THIN",
          ("CSIS China Power", "US Department of Defense, Military and Security Developments Involving the People's Republic of China")),
    Topic("SOTS-DEFENCE-BUDGETS", "Defence budgets globally",
          "How national defence budgets and procurement priorities are moving, and where demand for defence technology is shifting.",
          ("STE-GC-03",), ("EY-GEO-2025", "WEF-GRR-2026"), (), ("defence", "defense", "budget"), "GAP",
          ("SIPRI, Trends in World Military Expenditure", "NATO defence expenditure reports")),
    Topic("SOTS-COMPETITORS", "Competitor strategic moves",
          "How STE's main competitors are positioning through acquisitions, partnerships and market entry.",
          ("STE-GC-12",), (), (), ("competition", "competitor"), "GAP",
          ("Competitors' annual reports and investor-day materials",)),
]

PILOT = ("SOTS-MIDEAST", "SOTS-UKRAINE", "SOTS-SEMIS", "SOTS-QUANTUM")


def report(report_id: str) -> Report:
    return next(item for item in REPORTS if item.report_id == report_id)


def short_name(item: Report) -> str:
    return item.short or (item.aliases[0] if item.aliases else item.publisher)


def topic(topic_id: str) -> Topic:
    found = next((item for item in TOPICS if item.topic_id == topic_id), None)
    if found is None:
        raise KeyError(f"Unknown topic {topic_id}. Known: {', '.join(item.topic_id for item in TOPICS)}")
    return found
