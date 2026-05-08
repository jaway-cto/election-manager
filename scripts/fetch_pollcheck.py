"""
Scrape PollCheck.co.uk per-council projections (English councils only) and cache to JSON.

PollCheck publishes pre-poll modelled projections — these don't update intra-day,
so this is a one-shot fetch. Re-run only if you suspect they've revised forecasts.

Output: pollcheck.json -- {council_name_in_workbook: {p_control_change, projection: [{party, central, low, high}], winner_central}}
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
OUT = ROOT / "pollcheck.json"
BASE = "https://www.pollcheck.co.uk/council-projections/{slug}/"
UA = {"User-Agent": "Mozilla/5.0 election-tracker"}

PARTY_TO_BUCKET = {
    "Conservative": "Con", "Labour": "Lab", "Liberal Democrats": "LD",
    "Green": "Grn", "Reform UK": "Ref", "SNP": "SNP", "Plaid Cymru": "PC",
    "Independent": "Ind", "Others": "Oth", "Other": "Oth",
}


def workbook_name_to_slug(name: str) -> str | None:
    """Map our council names to PollCheck URL slugs (lowercase, underscores)."""
    n = name
    n = re.sub(r"\s*\(Mayor\)\s*$", "", n)
    n = re.sub(r"\s*\(by-election\)\s*$", "", n)
    n = re.sub(r"\s*\(shadow\)\s*$", "", n)
    n = re.sub(r"\s*CC\s*$", "", n)
    n = n.replace("Hull (Kingston upon Hull)", "Kingston upon Hull")
    n = n.replace("&", "and").replace("'", "")
    n = re.sub(r"[^A-Za-z0-9 -]", " ", n)
    n = re.sub(r"\s+", "_", n.strip()).lower()
    n = n.replace("-", "_")
    return n or None


def fetch_council(slug: str) -> dict | None:
    url = BASE.format(slug=slug)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return {"_error": str(e), "_url": url}
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    out = {"_url": url}
    m = re.search(r"[Pp]robability of [Cc]ontrol [Cc]hange[^0-9]*([0-9]+)%", text)
    out["p_control_change"] = int(m.group(1)) / 100 if m else None
    m = re.search(r"([0-9]+)\s*of\s*([0-9]+)\s*seats", text)
    if m:
        out["majority_threshold"] = int(m.group(1))
        out["total_seats"] = int(m.group(2))
    rows = []
    for m in re.finditer(
        r"(Conservative|Labour|Liberal Democrats|Green|Reform UK|SNP|Plaid Cymru|Independent|Others?)\s+(\d+)\s*\((\d+)\s*[-–]\s*(\d+)\)",
        text,
    ):
        rows.append({
            "party_name": m.group(1),
            "party": PARTY_TO_BUCKET.get(m.group(1), "Oth"),
            "central": int(m.group(2)),
            "low": int(m.group(3)),
            "high": int(m.group(4)),
        })
    out["projection"] = rows
    if rows:
        winner = max(rows, key=lambda r: r["central"])
        out["winner_central"] = winner["party"]
    return out


def main() -> None:
    from councils_data import ENGLAND, WALES

    cache: dict = {}
    misses: list[str] = []
    for council in ENGLAND + WALES:
        name = council[0]
        slug = workbook_name_to_slug(name)
        if not slug:
            continue
        result = fetch_council(slug)
        if result and result.get("projection"):
            cache[name] = result
            print(f"  OK  {name:<35} -> {slug:<30} {result.get('winner_central')} (P_change={result.get('p_control_change')})")
        else:
            misses.append(name)
            print(f"  MISS {name:<35} -> {slug}  ({result.get('_error') if result else 'no rows'})")
        time.sleep(0.3)  # be polite

    OUT.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    print(f"\nFetched {len(cache)} of {len(ENGLAND)+len(WALES)} councils. Misses: {len(misses)}")
    if misses:
        print("Missed names:", misses[:20])
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
