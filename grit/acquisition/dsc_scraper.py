"""
DS&C public-web scraper for stengg.com (+ innovd.stengg.com)
Collects DS&C-relevant pages and pulls customers / applications / projects.

Run:  pip install requests beautifulsoup4 lxml
      python dsc_scraper.py
Output: dsc_pages.csv (page-level), dsc_mentions.csv (customer/project hits)

Polite: honors robots.txt, 1.5s delay, identifies itself. Check STE's
website terms of use before running this at scale.
"""
import csv, re, time, urllib.robotparser
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (STE-RA internal research; contact: jony)"
DELAY = 1.5
SITEMAPS = ["https://www.stengg.com/sitemap.xml"]
SEEDS = [
    "https://www.stengg.com/en/cybersecurity/",
    "https://www.stengg.com/en/cybersecurity/solutions",
    "https://www.stengg.com/en/cybersecurity/products/",
    "https://www.stengg.com/en/digital-tech/",
    "https://www.stengg.com/en/newsroom/news-releases/",
    "https://innovd.stengg.com/articles",
]
# URL paths considered DS&C-relevant
URL_KEEP = re.compile(r"/(cybersecurity|digital-tech|defence-and-public-security|"
                      r"public-security|c5isr|training-and-simulation|newsroom|"
                      r"case-stud|success-stor|articles)", re.I)
# Newsroom pages are only kept if they mention DS&C themes
DSC_TERMS = re.compile(r"cyber|C5ISR|command and control|simulat|encrypt|SOC\b|"
                       r"video analytics|AI|cloud|data centre|homeland|public safety|"
                       r"digital systems", re.I)

# Seed customer list (from DS&C Entity Info) + generic patterns
CUSTOMERS = ["MHA", "Ministry of Home Affairs", "HTX", "Home Team Science",
    "Singapore Police Force", "SPF", "SCDF", "Civil Defence", "MINDEF",
    "Ministry of Defence", "DSTA", "DSO", "CAAS", "MPA", "Maritime and Port Authority",
    "Changi Airport", "HDB", "PUB", "NEA", "Elections Department", "Science Centre",
    "Tower Transit", "DXC", "Royal Thai Air Force", "Ministry of Public Security",
    "UK MOD", "NP Aerospace", "BNPB", "Hong Kong Police", "Estonia", "Samoa",
    "Qatar", "UAE", "Pakistan", "India", "GovTech", "CSA", "Cyber Security Agency"]
CUST_RE = re.compile(r"\b(" + "|".join(map(re.escape, CUSTOMERS)) + r")\b")
CONTRACT_RE = re.compile(r"[^.]*\b(contract|awarded|secured|deployed|deliver(?:ed|s)|"
                         r"partner(?:ed|ship)|MOU|selected by|for the)\b[^.]*\.", re.I)

s = requests.Session(); s.headers["User-Agent"] = UA
robots = {}

def allowed(url):
    host = urlparse(url).scheme + "://" + urlparse(url).netloc
    if host not in robots:
        rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
        try: rp.read()
        except Exception: pass
        robots[host] = rp
    return robots[host].can_fetch(UA, url)

def get(url):
    if not allowed(url): return None
    time.sleep(DELAY)
    try:
        r = s.get(url, timeout=20)
        return r.text if r.ok else None
    except requests.RequestException:
        return None

def sitemap_urls():
    out, queue = set(), list(SITEMAPS)
    while queue:
        xml = get(queue.pop())
        if not xml: continue
        for loc in re.findall(r"<loc>(.*?)</loc>", xml):
            if loc.endswith(".xml"): queue.append(loc)
            else: out.add(loc)
    return out

def crawl():
    urls = {u for u in sitemap_urls() if URL_KEEP.search(u)} | set(SEEDS)
    seen, pages = set(), []
    queue = list(urls)
    while queue and len(seen) < 1500:
        url = queue.pop(0).split("#")[0]
        if url in seen: continue
        seen.add(url)
        html = get(url)
        if not html: continue
        soup = BeautifulSoup(html, "lxml")
        for t in soup(["script", "style", "nav", "footer", "header"]): t.decompose()
        title = (soup.title.string or "").strip() if soup.title else ""
        text = " ".join(soup.get_text(" ").split())
        if "/newsroom/" in url and not DSC_TERMS.search(text):
            continue
        pages.append({"url": url, "title": title, "text": text})
        for a in soup.find_all("a", href=True):
            nxt = urljoin(url, a["href"]).split("#")[0]
            if "stengg.com" in nxt and URL_KEEP.search(nxt) and nxt not in seen:
                queue.append(nxt)
        print(f"[{len(pages)}] {title[:80]}")
    return pages

def main():
    pages = crawl()
    with open("dsc_pages.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["url", "title", "text"]); w.writeheader(); w.writerows(pages)
    with open("dsc_mentions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["url", "title", "customer", "sentence"])
        for p in pages:
            for m in CONTRACT_RE.finditer(p["text"]):
                sent = m.group(0).strip()
                custs = set(CUST_RE.findall(sent)) or {""}
                for c in custs:
                    w.writerow([p["url"], p["title"], c, sent[:500]])
    print(f"Done: {len(pages)} pages -> dsc_pages.csv, dsc_mentions.csv")

if __name__ == "__main__":
    main()
