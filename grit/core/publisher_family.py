"""Publisher families: corroboration counts independent owners, not domains (item 13, 26 Oct 2026).

Three pages from sfgate.com, timesunion.com and houstonchronicle.com are one Hearst story, not three corroborations. A
page on yahoo.com or msn.com usually reposts another outlet's story, so an aggregator adds no corroboration of its own.

family(publisher):
1. A press-release wire (PR Newswire, GlobeNewswire, ...) is one family, "wire services".
2. The host is cut to its registrable domain: news.yahoo.co.jp -> yahoo.co.jp, edition.cnn.com -> cnn.com.
3. A registrable domain listed under FAMILIES takes that family's name; any other domain is its own family.

FAMILIES is a starting list, not a complete ownership register. Add an owner when two of its titles show up as separate
corroboration.
"""

from __future__ import annotations

from typing import Iterable

from grit.core.doc_type import is_wire

WIRE_OUTLET = "wire services"
AGGREGATOR = "aggregator"

# Second-level suffixes under which the registrable domain has three labels (news.example.co.uk -> example.co.uk).
_SECOND_LEVEL = frozenset(
    "co.uk org.uk ac.uk gov.uk com.sg edu.sg gov.sg org.sg net.sg com.au net.au org.au gov.au co.jp or.jp ne.jp go.jp com.tw org.tw gov.tw "
    "com.hk org.hk gov.hk com.my org.my gov.my co.kr or.kr co.in gov.in com.cn gov.cn co.nz com.br com.ph co.id com.vn co.th "
    "com.sa gov.sa com.qa gov.qa co.il com.tr com.mx co.za".split()
)

FAMILIES: dict[str, tuple[str, ...]] = {
    "Hearst": ("sfgate.com", "timesunion.com", "houstonchronicle.com", "chron.com", "seattlepi.com", "ctpost.com", "expressnews.com", "mysanantonio.com",
               "nhregister.com", "stamfordadvocate.com", "greenwichtime.com", "esquire.com", "cosmopolitan.com", "popularmechanics.com"),
    "Gannett": ("usatoday.com", "freep.com", "detroitnews.com", "indystar.com", "tennessean.com", "azcentral.com", "desertsun.com", "jsonline.com"),
    "Dow Jones": ("wsj.com", "barrons.com", "marketwatch.com", "dowjones.com", "mansionglobal.com"),
    "Nikkei": ("nikkei.com", "asia.nikkei.com", "ft.com"),
    "SPH Media": ("straitstimes.com", "businesstimes.com.sg", "zaobao.com.sg", "zaobao.com", "tnp.sg", "beritaharian.sg"),
    "Mediacorp": ("channelnewsasia.com", "cna.asia", "todayonline.com", "8world.com", "mothership.sg"),
    "Sightline Media": ("defensenews.com", "c4isrnet.com", "militarytimes.com", "federaltimes.com", "armytimes.com", "navytimes.com", "airforcetimes.com", "marinecorpstimes.com"),
    "Future plc": ("tomshardware.com", "techradar.com", "pcgamer.com", "anandtech.com", "livescience.com", "space.com", "itpro.com", "windowscentral.com"),
    "Ziff Davis": ("pcmag.com", "mashable.com", "zdnet.com", "ign.com", "extremetech.com", "lifehacker.com"),
    "Vox Media": ("theverge.com", "vox.com", "nymag.com", "polygon.com"),
    "Conde Nast": ("wired.com", "arstechnica.com", "newyorker.com", "vanityfair.com"),
    "Axel Springer": ("politico.com", "politico.eu", "businessinsider.com", "insider.com"),
    "Bloomberg": ("bloomberg.com", "bloomberglaw.com", "bnnbloomberg.ca"),
    "Thomson Reuters": ("reuters.com", "thomsonreuters.com"),
    "News Corp": ("nypost.com", "thetimes.co.uk", "thesun.co.uk", "theaustralian.com.au", "news.com.au", "foxnews.com", "foxbusiness.com"),
    "Informa": ("darkreading.com", "informationweek.com", "networkcomputing.com", "lloydslist.com", "aviationweek.com"),
    "Tech Insider Media": ("digitimes.com", "digitimes.com.tw"),
    "Taipei Times / Liberty Times": ("taipeitimes.com", "ltn.com.tw"),
    "Central News Agency (Taiwan)": ("focustaiwan.tw", "cna.com.tw"),
    "Times Internet": ("timesofindia.indiatimes.com", "indiatimes.com", "economictimes.com"),
}

# Sites that mostly repost other outlets' stories.
AGGREGATORS = frozenset(
    "yahoo.com yahoo.co.jp yahoo.com.tw msn.com newsbreak.com flipboard.com ground.news inkl.com smartnews.com headtopics.com newsnow.co.uk "
    "biztoc.com futunn.com marketscreener.com newsbytesapp.com devdiscourse.com".split()
)

_OWNER = {domain: owner for owner, domains in FAMILIES.items() for domain in domains}


def registrable(host: str) -> str:
    host = (host or "").casefold().strip().removeprefix("www.").split("/")[0].split(":")[0]
    if "://" in host:
        host = host.split("://", 1)[1]
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return ".".join(parts)
    keep = 3 if ".".join(parts[-2:]) in _SECOND_LEVEL else 2
    return ".".join(parts[-keep:])


def family(publisher: str | None) -> str | None:
    """The publisher family of a host or URL, or None for a blank one."""
    raw = str(publisher or "").strip()
    if not raw:
        return None
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    host = raw.casefold().removeprefix("www.").split("/")[0]
    if is_wire(host):
        return WIRE_OUTLET
    if host in _OWNER:
        return _OWNER[host]
    domain = registrable(host)
    if domain in AGGREGATORS:
        return AGGREGATOR
    return _OWNER.get(domain, domain)


def families(publishers: Iterable[str | None]) -> list[str]:
    """Distinct families, in order of first appearance. An aggregator counts only when nothing else carries the story."""
    found: list[str] = []
    for publisher in publishers:
        name = family(publisher)
        if name and name not in found:
            found.append(name)
    independent = [name for name in found if name != AGGREGATOR]
    return independent or found
