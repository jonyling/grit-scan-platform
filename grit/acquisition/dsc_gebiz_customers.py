"""
DS&C customer finder - GeBIZ awarded contracts (via data.gov.sg)
Pulls every Singapore government tender awarded to the DS&C entities,
then rolls up a customer (agency) list.

Run:   pip install requests pandas openpyxl
       python dsc_gebiz_customers.py
Out:   dsc_gebiz_awards.xlsx  (sheet "Awards" = every award, sheet "Customers" = rollup)

Dataset: "Government Procurement via GeBIZ" on data.gov.sg.
If the resource ID below stops working, open the dataset page on data.gov.sg,
copy the new resource/dataset ID, and paste it into RESOURCE_ID.
"""
import re, time
import requests
import pandas as pd

RESOURCE_ID = "d_acde1106003906a75c3fa052592f2fcb"
API = "https://data.gov.sg/api/action/datastore_search"

# Current legal names (from DS&C entity list)
ENTITIES = [
    "ST Engineering Digital Systems Pte. Ltd.",
    "Codex Solutions Pte. Ltd.",
    "ST Engineering Mission Software & Services Pte. Ltd.",
    "ST Engineering Training & Simulation Systems Pte. Ltd.",
    "ST Engineering Advanced Networks & Sensors Pte. Ltd.",
    "ST Engineering Cloud and Data Centre Solutions Pte. Ltd.",
    "ST Engineering Unmanned & Integrated Systems Pte. Ltd.",
    "ST Engineering E-Services Pte. Ltd.",
    "ST Engineering Geo-Insights Pte. Ltd.",
    "ST Engineering Satellite Systems Pte. Ltd.",
    "InnoEdge Labs Pte. Ltd.",
    "D'Crypt Pte. Ltd.",
    "ST Engineering Info-Security Pte. Ltd.",
]
# Older awards may sit under former names (e.g. "ST Electronics (...)").
# These are UNVERIFIED guesses - confirm with Finance/Legal and edit as needed.
FORMER_NAME_HINTS = [
    "ST Electronics", "Singapore Technologies Electronics",
    "ST Engineering Electronics", "ST Engineering Info-Software",
    "ST Electronics (Info-Software", "ST Electronics (Training",
    "ST Electronics (Info-Security", "ST Electronics (e-Services",
    "ST Electronics (Satellite", "ST Electronics (Data Centre",
]

def norm(s):
    s = s.lower().replace("&", "and")
    s = re.sub(r"\bpte\.?\s*ltd\.?|\blimited\b|\bltd\.?\b", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

TARGETS = {norm(e): e for e in ENTITIES}
HINTS = [norm(h) for h in FORMER_NAME_HINTS]

def fetch_all():
    rows, offset, limit = [], 0, 5000
    while True:
        r = requests.get(API, params={"resource_id": RESOURCE_ID,
                                      "limit": limit, "offset": offset}, timeout=60)
        r.raise_for_status()
        res = r.json()["result"]
        rows += res["records"]
        print(f"fetched {len(rows)} / {res.get('total')}")
        if len(res["records"]) < limit: break
        offset += limit
        time.sleep(1)
    return pd.DataFrame(rows)

def classify(supplier):
    n = norm(str(supplier))
    for k, v in TARGETS.items():
        if k == n or k in n: return v, "exact"
    for h in HINTS:
        if h in n: return supplier, "former-name (verify)"
    return None, None

def main():
    df = fetch_all()
    sup_col = next(c for c in df.columns if "supplier" in c.lower())
    df[["dsc_entity", "match_type"]] = df[sup_col].apply(lambda s: pd.Series(classify(s)))
    hits = df[df["dsc_entity"].notna()].copy()

    amt = next((c for c in hits.columns if "amt" in c.lower() or "amount" in c.lower()), None)
    if amt: hits[amt] = pd.to_numeric(hits[amt], errors="coerce")
    agency = next(c for c in hits.columns if "agency" in c.lower())
    date = next((c for c in hits.columns if "date" in c.lower()), None)

    agg = {"tenders": (sup_col, "size"), "entities": ("dsc_entity", lambda x: "; ".join(sorted(set(x))))}
    if amt: agg["total_awarded_sgd"] = (amt, "sum")
    if date: agg["first_award"] = (date, "min"); agg["last_award"] = (date, "max")
    cust = hits.groupby(agency).agg(**agg).sort_values("tenders", ascending=False).reset_index()

    with pd.ExcelWriter("dsc_gebiz_awards.xlsx") as xw:
        cust.to_excel(xw, sheet_name="Customers", index=False)
        hits.drop(columns=["_id"], errors="ignore").to_excel(xw, sheet_name="Awards", index=False)
    print(f"\n{len(hits)} awards, {len(cust)} distinct customers -> dsc_gebiz_awards.xlsx")
    print(cust.head(40).to_string(index=False))

if __name__ == "__main__":
    main()
