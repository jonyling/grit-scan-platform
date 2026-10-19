"""The STE Group pack seed (spec 37). `grit pack-init --pack ste` writes it once; after that, config/ste/ is the source.

The content comes from Wilson Yeo's email of 9 October 2026: his 12 dashboard themes become the standing conditions, his
eight strategic shifts the themes, and his tier 1 to 7 sources the source register. Every judgement here is proposed: the
business-area relevance, the source ratings, and how each condition could reach STE. No condition carries a current
assessment. `grit baseline-draft` drafts it from cited evidence, and Wilson signs it off.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grit.settings import CONFIG_DIR

PACK = "ste"
PREFIX = "STE"
LABEL = "ST Engineering Group"
STATUS = "PROPOSED_PENDING_WILSON_SIGN_OFF"


def _condition(number: int, name: str, domain: str, transmission: str, indicators: list[str], shifts: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "id": f"STE-GC-{number:02d}", "name": name, "domain": domain, "currentAssessment": None, "transmission": transmission,
        "watchIndicators": indicators, "steThemes": shifts, "referenceSources": sources,
    }


CONDITIONS = [
    _condition(1, "Saudi, UAE and Qatar investment signals", "Markets",
               "Where Gulf state money flows (PIF, Vision 2030, UAE 2031, Qatar programmes) could open or close defence localisation, smart-city, "
               "mobility, cyber and aviation-services work, subject to localisation, technology-transfer and payment terms.",
               ["PIF investment announcements", "Vision 2030 programme funding", "UAE 2031 digital and smart-city programmes", "Qatar infrastructure sustainment awards",
                "Local-content and Saudisation thresholds"],
               ["Middle East defence and infrastructure spending", "Sovereign AI and digital sovereignty"],
               ["Saudi Vision 2030 Annual Report", "We the UAE 2031", "Public Investment Fund", "World Bank MENAAP Economic Update", "IMF Regional Economic Outlook"]),
    _condition(2, "Middle East security developments", "Security",
               "Conflict could raise demand for counter-UAS, border and maritime surveillance and secure communications, and could also put staff, "
               "sites, shipping routes and payment timing at risk in Gulf markets.",
               ["Attacks on Gulf cities or energy infrastructure", "Red Sea and Gulf shipping disruption", "Air-defence and counter-UAS procurement",
                "Travel advisories for staff"],
               ["Middle East defence and infrastructure spending", "AI + autonomous systems"],
               ["Institute for the Study of War", "Atlantic Council Middle East Programs", "CSIS"]),
    _condition(3, "Defence budgets globally", "Markets",
               "Changes in national defence budgets and procurement priorities could move demand for C4ISR, autonomous systems, electronic warfare, "
               "training and simulation, and MRO.",
               ["National defence budget announcements", "NATO spending commitments", "Procurement priorities for drones and counter-drone systems",
                "Major defence contract awards"],
               ["Human-machine teaming in defence and critical infrastructure", "AI + autonomous systems"],
               ["SIPRI", "Defense News", "Breaking Defense", "Janes", "RUSI"]),
    _condition(4, "Ukraine reconstruction", "Markets",
               "Reconstruction funding could open rail, transport command-and-control, border surveillance, secure communications, airport and "
               "critical-infrastructure work, subject to eligibility, financing and security conditions. Russia stays a sanctions and compliance "
               "watchpoint, not a market.",
               ["Reconstruction financing commitments", "World Bank and IFC programme awards", "Rail, energy and border-security tenders",
                "Ceasefire or escalation", "Sanctions changes affecting Russia"],
               ["Ukraine reconstruction", "Export controls and technology blocs"],
               ["World Bank RDNA5", "World Bank and IFC sector assessments", "US Country Commercial Guide: Ukraine"]),
    _condition(5, "China-Taiwan tensions", "Security",
               "Cross-strait escalation could disrupt Taiwan operations and Asian supply chains, tighten technology controls, and change defence "
               "demand across the region.",
               ["PLA activity around Taiwan", "Blockade or quarantine drills", "US arms sales to Taiwan", "Semiconductor supply disruption from Taiwan"],
               ["Semiconductor/GPU access", "Export controls and technology blocs"],
               ["CSIS China Power", "Institute for the Study of War", "Taiwan Ministry of National Defense"]),
    _condition(6, "Semiconductor and HBM prices", "Technology and supply",
               "Memory and chip price rises could squeeze project margins and bid validity, and raise AI-infrastructure and product costs across "
               "the business areas.",
               ["DRAM and NAND contract prices", "HBM spot versus contracted price", "Advanced packaging capacity", "Foundry utilisation",
                "Supplier prepayments and allocation"],
               ["Semiconductor/GPU access"],
               ["TrendForce", "TSMC earnings calls", "PwC Semiconductor and Beyond"]),
    _condition(7, "GPU availability", "Technology and supply",
               "Constrained GPU supply or allocation could delay AI-enabled products and data-centre projects even where budget exists, and favour "
               "smaller models and edge designs.",
               ["GPU lead times", "Cloud GPU prices", "Data-centre power and cooling costs", "Vendor concentration across GPU, memory and packaging"],
               ["Semiconductor/GPU access", "Sovereign AI and digital sovereignty"],
               ["SemiAnalysis", "Tom's Hardware", "TSMC earnings calls"]),
    _condition(8, "Frontier AI capability jumps", "Technology",
               "A step change in model capability could speed up AI-enabled cyber attacks and product cycles, and bring new competitors, while "
               "opening work in secure, governed autonomy and AI assurance.",
               ["Frontier model releases", "Autonomous-agent capability evaluations", "AI-enabled cyber incidents", "Compute growth"],
               ["AI + autonomous systems"],
               ["Epoch AI", "METR", "UK AI Security Institute", "Anthropic Research", "UK Government AI Scenarios 2030"]),
    _condition(9, "AI regulation", "Regulation",
               "New AI rules could add assurance and liability requirements to AI-enabled products and services, and could favour sovereign or "
               "certified deployments.",
               ["EU AI Act milestones", "US federal and state AI rules", "Singapore AI governance frameworks", "Sovereign AI procurement restrictions"],
               ["Sovereign AI and digital sovereignty", "AI + autonomous systems"],
               ["UK AI Security Institute", "OECD AI policy", "CSIS AI and Emerging Technology"]),
    _condition(10, "Quantum computing milestones", "Technology",
               "Progress toward a cryptographically relevant quantum computer could shorten the window to migrate long-life defence, aviation and "
               "infrastructure systems to post-quantum cryptography, and opens quantum-safe and quantum-sensing work.",
               ["Quantum hardware roadmap milestones", "NIST post-quantum cryptography standards", "Government PQC migration deadlines",
                "Harvest-now-decrypt-later warnings"],
               ["Quantum-safe security migration"],
               ["NIST PQC", "The Quantum Insider", "IBM Quantum", "Global Risk Institute Quantum Threat Timeline", "GFMA Quantum Migration"]),
    _condition(11, "Export controls", "Regulation",
               "Changes to export controls, entity lists and sanctions could restrict access to chips, components and markets, and decide which "
               "bids STE can make.",
               ["BIS Entity List additions", "AI chip export rules", "OFAC sanctions designations", "Technology-transfer rules", "China export restrictions"],
               ["Export controls and technology blocs", "Semiconductor/GPU access"],
               ["US Bureau of Industry and Security", "OFAC", "EU Sanctions Map"]),
    _condition(12, "Competitor strategic moves", "Competition",
               "Competitors' acquisitions, partnerships and wins could change STE's position in defence, smart-city, cyber and aerospace markets.",
               ["Competitor acquisitions and partnerships", "Major competitor contract wins in STE markets", "Competitor entry into Middle East localisation"],
               ["AI + autonomous systems", "Middle East defence and infrastructure spending"],
               ["Defense News", "Breaking Defense", "Company announcements"]),
]

# Theme keywords need six or more characters or a space (grit/ontology/link.py), so an acronym such as HBM is written as a phrase.
SHIFTS = [
    ("AI + autonomous systems", ["autonomous systems", "agentic AI", "AI agents", "autonomous vehicles", "unmanned systems", "drone swarm"]),
    ("Sovereign AI and digital sovereignty", ["sovereign AI", "digital sovereignty", "sovereign cloud", "data localisation", "data localization"]),
    ("Semiconductor/GPU access", ["GPU shortage", "chip shortage", "HBM demand", "HBM supply", "HBM prices", "memory prices", "memory supply", "DRAM price", "AI chips", "semiconductor supply"]),
    ("Middle East defence and infrastructure spending", ["Vision 2030", "Public Investment Fund", "Gulf defence spending", "Saudi defence", "UAE defence", "Qatar infrastructure"]),
    ("Ukraine reconstruction", ["Ukraine reconstruction", "rebuild Ukraine", "Ukraine recovery"]),
    ("Export controls and technology blocs", ["export controls", "Entity List", "technology bifurcation", "decoupling", "chip export"]),
    ("Quantum-safe security migration", ["post-quantum", "quantum-safe", "post-quantum cryptography", "harvest now decrypt later", "quantum computing"]),
    ("Human-machine teaming in defence and critical infrastructure", ["human-machine teaming", "manned-unmanned teaming", "loyal wingman"]),
]

SCAN = {
    "STE-GC-01": (["Saudi PIF investment defence technology 2026", "UAE sovereign fund AI infrastructure investment", "Qatar infrastructure investment programme 2026",
                   "Saudi Vision 2030 localisation defence contract"],
                  ["Public Investment Fund", "Vision 2030", "PIF invest", "Mubadala", "Qatar Investment Authority", "Saudi localisation"], ["urs", "dsc"]),
    "STE-GC-02": (["Middle East security escalation Gulf", "Houthi attack Saudi Arabia", "Gulf air defence counter-drone", "Red Sea shipping attack"],
                  ["Houthi", "missile attack", "drone attack", "Red Sea", "Gulf security", "air defence"], ["dsc", "urs"]),
    "STE-GC-03": (["defence budget increase 2027", "NATO defence spending target", "Asia defence budget rise", "Pentagon budget drones counter-UAS"],
                  ["defence budget", "defense budget", "military spending", "defence spending", "defense spending"], ["dsc"]),
    "STE-GC-04": (["Ukraine reconstruction contracts", "Ukraine recovery conference financing", "World Bank Ukraine reconstruction", "Ukraine rail energy rebuild tender"],
                  ["Ukraine reconstruction", "Ukraine recovery", "rebuild Ukraine", "reconstruction of Ukraine"], ["urs", "dsc"]),
    "STE-GC-05": (["China Taiwan military pressure", "PLA drills Taiwan", "Taiwan Strait blockade", "US arms sale Taiwan"],
                  ["Taiwan Strait", "PLA", "cross-strait", "Taiwan blockade"], ["urs", "dsc"]),
    "STE-GC-06": (["DRAM contract prices", "HBM prices 2026", "memory chip price increase", "TrendForce memory forecast"],
                  ["DRAM price", "HBM", "NAND price", "memory prices", "memory chip"], ["dsc", "urs"]),
    "STE-GC-07": (["GPU shortage lead times", "Nvidia GPU allocation", "cloud GPU prices", "AI data centre GPU supply"],
                  ["GPU shortage", "GPU supply", "GPU lead time", "Nvidia allocation", "GPU prices"], ["dsc"]),
    "STE-GC-08": (["frontier AI model release capability", "AI agents autonomous capability evaluation", "AI cyber attack capability", "Epoch AI compute trends"],
                  ["frontier model", "frontier AI", "AI capabilities", "autonomous agents", "AI agent"], ["dsc"]),
    "STE-GC-09": (["AI regulation enforcement 2026", "EU AI Act implementation", "AI law government", "AI safety institute"],
                  ["AI Act", "AI regulation", "AI law", "AI rules", "AI governance"], ["dsc", "urs"]),
    "STE-GC-10": (["quantum computing milestone", "post-quantum cryptography migration deadline", "NIST post-quantum standard", "quantum computer error correction breakthrough"],
                  ["quantum computer", "quantum computing", "post-quantum", "PQC", "quantum-safe"], ["dsc"]),
    "STE-GC-11": (["BIS Entity List additions", "US export controls AI chips", "OFAC sanctions designations technology", "China export restrictions critical minerals"],
                  ["export controls", "Entity List", "export restrictions", "sanctions", "export ban"], ["dsc", "urs"]),
    "STE-GC-12": (["Lockheed Martin acquisition", "Thales contract Asia", "Saab Elbit defence order", "Hanwha Aerospace export deal"],
                  ["Lockheed Martin", "Thales", "Saab", "Elbit", "Hanwha", "Leidos", "Rheinmetall"], ["dsc", "urs"]),
}

SOURCES = [
    ("Institute for the Study of War", "understandingwar.org", "THINK_TANK", "MEDIUM"),
    ("CSIS", "csis.org", "THINK_TANK", "MEDIUM"),
    ("Atlantic Council", "atlanticcouncil.org", "THINK_TANK", "MEDIUM"),
    ("RUSI", "rusi.org", "THINK_TANK", "MEDIUM"),
    ("UAE Government", "u.ae", "GOVERNMENT", "HIGH"),
    ("NEOM", "neom.com", "COMPANY", "MEDIUM"),
    ("Public Investment Fund", "pif.gov.sa", "GOVERNMENT", "HIGH"),
    ("Saudi Vision 2030", "vision2030.gov.sa", "GOVERNMENT", "HIGH"),
    ("TrendForce", "trendforce.com", "INDUSTRY_RESEARCH", "MEDIUM"),
    ("SemiAnalysis", "semianalysis.com", "INDUSTRY_RESEARCH", "MEDIUM"),
    ("Tom's Hardware", "tomshardware.com", "TRADE_PRESS", "LOW"),
    ("TSMC Investor Relations", "investor.tsmc.com", "COMPANY", "HIGH"),
    ("Janes", "janes.com", "TRADE_PRESS", "HIGH"),
    ("Defense News", "defensenews.com", "TRADE_PRESS", "MEDIUM"),
    ("Breaking Defense", "breakingdefense.com", "TRADE_PRESS", "MEDIUM"),
    ("Epoch AI", "epoch.ai", "RESEARCH", "MEDIUM"),
    ("METR", "metr.org", "RESEARCH", "MEDIUM"),
    ("UK AI Security Institute", "aisi.gov.uk", "GOVERNMENT", "HIGH"),
    ("Anthropic Research", "anthropic.com", "COMPANY", "MEDIUM"),
    ("NIST", "nist.gov", "GOVERNMENT", "HIGH"),
    ("The Quantum Insider", "thequantuminsider.com", "TRADE_PRESS", "MEDIUM"),
    ("IBM Quantum", "ibm.com", "COMPANY", "MEDIUM"),
    ("US Bureau of Industry and Security", "bis.gov", "GOVERNMENT", "HIGH"),
    ("OFAC", "ofac.treasury.gov", "GOVERNMENT", "HIGH"),
    ("CISA", "cisa.gov", "GOVERNMENT", "HIGH"),
    ("World Bank", "worldbank.org", "INTERGOVERNMENTAL", "HIGH"),
    ("IMF", "imf.org", "INTERGOVERNMENTAL", "HIGH"),
    ("IFC", "ifc.org", "INTERGOVERNMENTAL", "HIGH"),
    ("Deloitte", "deloitte.com", "ADVISORY", "MEDIUM"),
    ("KPMG", "kpmg.com", "ADVISORY", "MEDIUM"),
    ("PwC", "pwc.com", "ADVISORY", "MEDIUM"),
    ("US International Trade Administration", "trade.gov", "GOVERNMENT", "HIGH"),
    ("UK Government", "gov.uk", "GOVERNMENT", "HIGH"),
    ("Global Risk Institute", "globalriskinstitute.org", "RESEARCH", "MEDIUM"),
    ("GFMA", "gfma.org", "INDUSTRY_BODY", "MEDIUM"),
    ("SIPRI", "sipri.org", "RESEARCH", "HIGH"),
    ("Reuters", "reuters.com", "WIRE", "HIGH"),
    ("Associated Press", "apnews.com", "WIRE", "HIGH"),
    ("Bloomberg", "bloomberg.com", "WIRE", "HIGH"),
    ("Financial Times", "ft.com", "NEWSPAPER", "HIGH"),
]


def radar_brief(name: str, role: str) -> dict[str, Any]:
    """The monthly Group Strategic Radar brief. A reader profile can add it for a reader whose JSON profile has none."""
    return {
        "title": "GRIT-Scan: ST Engineering Group Strategic Radar", "subtitle": "Monthly geopolitical and technology radar",
        "preparedFor": f"{name}, {role}", "sections": ["BOTTOM_LINE", "CONDITIONS", "RESPONSE_OPTIONS", "PRIORITY_FINDINGS", "WATCHPOINTS"],
        "maxFindings": 5, "formats": ["docx", "pptx"],
    }


def _profile(profile_id: str, name: str, role: str, note: str) -> dict[str, Any]:
    return {
        "profileId": profile_id, "name": name, "role": role, "aperture": "MATERIAL_APERTURE", "packs": [PACK],
        "businessScope": {"level": "GROUP"}, "reviewRole": "READER", "redAlerts": {"receive": False}, "email": None,
        "briefs": {PACK: radar_brief(name, role)}, "note": note,
    }


PROFILES = {
    "vschong": _profile("vschong", "Vincent S. Chong", "Chief Executive Officer", "Reader. Receives the monthly Group Strategic Radar after Wilson approves it."),
    "mkteo": _profile("mkteo", "MK Teo", "Chairman", "Reader. Receives the monthly Group Strategic Radar after Wilson approves it, and conveys his views to Wilson."),
}


def documents(children: list[str]) -> dict[str, Any]:
    """The pack's files, keyed by file name. children are the business-area packs whose registers make up the footprint."""
    names = {key: f"{PREFIX}-{value}.json" for key, value in {
        "source_register": "Source-Register", "exposure": "Exposure-Ontology", "lob_profiles": "LOB-Profiles", "themes": "Group-Themes",
        "baseline": "Global-Baseline", "scan_plan": "Scan-Plan",
    }.items()}
    manifest = {
        "pack": PACK, "level": "GROUP", "label": LABEL, "shortLabel": PREFIX, "parent": None, "children": children,
        "note": "The Group pack (spec 37). Its footprint, entities, customers, suppliers and competitors come from its business-area children at run time.",
        "registers": {key: names[key] for key in ("source_register", "exposure", "lob_profiles", "themes", "baseline")},
        "optional": {"scan_plan": names["scan_plan"]},
    }
    derived = "The Group pack joins its children's registers at run time. This file stays empty."
    return {
        "pack.json": manifest,
        names["source_register"]: {
            "registerId": f"{PREFIX}-SOURCES", "status": STATUS,
            "absence": "Ratings are proposed by source class: official and intergovernmental sources and wires HIGH; think tanks, research and trade press MEDIUM or LOW. R&A confirms them.",
            "sources": [
                {"sourceName": name, "sourceDomain": domain, "sourceUrl": f"https://{domain}", "sourceClass": cls, "credibilityRating": rating, "active": True, "ratingStatus": "PROPOSED"}
                for name, domain, cls, rating in SOURCES
            ],
        },
        names["exposure"]: {"registerId": f"{PREFIX}-EXPOSURE", "derivedFrom": children, "absence": derived, "entities": [], "customers": [], "suppliers": [], "projects": [], "sites": []},
        names["lob_profiles"]: {"registerId": f"{PREFIX}-LOB-PROFILES", "derivedFrom": children, "absence": derived, "lobs": []},
        names["themes"]: {
            "registerId": f"{PREFIX}-STRATEGIC-SHIFTS", "status": STATUS, "source": "Wilson Yeo, email of 9 October 2026",
            "themes": [{"themeId": f"{PREFIX}-SHIFT-{number:02d}", "themeName": name, "tier": "KEY", "keywords": keywords} for number, (name, keywords) in enumerate(SHIFTS, start=1)],
        },
        names["baseline"]: {
            "baselineId": f"{PREFIX}-GROUP-BASELINE", "readerId": PREFIX, "readerName": LABEL, "revisionPrefix": PREFIX, "deltaTitle": "STE GROUP MONTHLY STRATEGIC DELTA",
            "effectiveDate": None, "status": "DRAFT_PENDING_SIGN_OFF", "signedOffBy": None, "signedOffOn": None,
            "absence": "No condition has a signed-off current assessment yet. Run grit baseline-draft and have Wilson sign it off.",
            "posture": "The Group radar measures each standing condition against its signed-off baseline. Until Wilson signs it off, movement is judged against the previous month only.",
            "idleLine": "No standing condition moved materially this month.",
            "conditions": CONDITIONS,
        },
        names["scan_plan"]: {
            "registerId": f"{PREFIX}-SCAN-PLAN", "status": STATUS, "standingSet": "conditions", "windowDays": 31,
            "audience": "Vincent S. Chong (CEO) and MK Teo (Chairman), ST Engineering, released by Wilson Yeo (Group Head, Risk & Assurance)",
            "sectionTitles": {"CONDITIONS": "Group Strategic Radar: the 12 standing conditions", "PRIORITY_FINDINGS": "Signals to read in full"},
            "conditions": [
                {"conditionId": condition_id, "queries": queries, "keywords": keywords, "businessAreas": [child for child in areas if child in children], "businessAreaStatus": "PROPOSED"}
                for condition_id, (queries, keywords, areas) in SCAN.items()
            ],
            "watchpoints": [],
        },
    }


def write_pack(config_dir: Path = CONFIG_DIR, children: list[str] | None = None) -> dict[str, list[str]]:
    """Write the pack and the two reader profiles. A file that exists is left alone, so a signed-off register is never replaced."""
    from grit.ontology.pack import manifests

    known = manifests(config_dir)
    chosen = children if children is not None else sorted(name for name, manifest in known.items() if manifest.get("level") == "BA")
    written: list[str] = []
    kept: list[str] = []
    targets = {Path(config_dir) / PACK / name: body for name, body in documents(chosen).items()}
    targets |= {Path(config_dir) / "profiles" / f"{profile_id}.json": body for profile_id, body in PROFILES.items()}
    for path, body in targets.items():
        if path.exists():
            kept.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(str(path))
    return {"written": written, "kept": kept, "children": chosen}


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Write a Group pack's starting registers and its reader profiles. Existing files are kept.")
    parser.add_argument("--pack", default=PACK, choices=[PACK])
    parser.add_argument("--children", nargs="*", help="Business-area packs for the footprint. Default: every BA pack.")
    args = parser.parse_args()
    result = write_pack(children=args.children)
    print(f"Children: {', '.join(result['children'])}")
    for path in result["written"]:
        print(f"written: {path}")
    for path in result["kept"]:
        print(f"kept (exists): {path}")


if __name__ == "__main__":
    _main()
