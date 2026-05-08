"""
Scrape BBC News election results pages (England, Scotland, Wales) for live
council declarations. BBC has direct correspondents at every count + PA wire,
so it leads Democracy Club's volunteer-entered API by 30-60 minutes.

Reads JSON embedded in `window.__INITIAL_DATA__` — public page, no auth.
Output: bbc_data.json with per-party councillor counts + councils-declared.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "bbc_data.json"
UA = {"User-Agent": "Mozilla/5.0 elections-tracker"}

PAGES = {
    "england": "https://www.bbc.co.uk/news/election/2026/england/results",
    "scotland": "https://www.bbc.co.uk/news/election/2026/scotland/results",
    "wales": "https://www.bbc.co.uk/news/election/2026/wales/results",
}

# Lighter wc-data endpoints (3.5KB JSON each, no HTML scrape)
WC_BASE = "https://www.bbc.co.uk/wc-data/container"
SCOREBOARD_PATHS = {
    "england":  "/news/election/2026/england/results",
    "scotland": "/news/election/2026/scotland/results",
    "wales":    "/news/election/2026/wales/results",
}
AZ_PATHS = {
    "england":  "/news/election/2026/england/councils",
    "scotland": "/news/election/2026/scotland/councils",
    "wales":    "/news/election/2026/wales/councils",
}

# BBC winnerPartyCode -> our 9-bucket party
BBC_CODE_TO_PARTY = {
    "CON": "Con", "LAB": "Lab", "LD": "LD", "GRN": "Grn",
    "REF": "Ref", "SNP": "SNP", "PC": "PC",
    "IND": "Ind", "RES": "Oth", "ASP": "Oth",
    "NOC": "NOC",  # special — no overall control
}

# Map BBC party labels -> our PARTIES code
BBC_TO_PARTY = {
    "Labour": "Lab",
    "Labour and Co-operative": "Lab",
    "Conservative": "Con",
    "Liberal Democrat": "LD",
    "Reform UK": "Ref",
    "Green": "Grn",
    "SNP": "SNP",
    "Plaid Cymru": "PC",
    "Independents and others": "Ind",  # BBC bundles, we'll bucket as Ind+Oth
    "Residents' Association": "Oth",
    "Aspire": "Oth",
}


def fetch_initial_data(url: str) -> dict | None:
    """Fetch a BBC results page and parse window.__INITIAL_DATA__."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  fetch error {url}: {e}", file=sys.stderr)
        return None

    idx = html.find("window.__INITIAL_DATA__=")
    if idx < 0:
        return None
    start = idx + len("window.__INITIAL_DATA__=")
    if html[start] != '"':
        return None
    # Walk to find closing unescaped quote
    i = start + 1
    while i < len(html):
        c = html[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            end = i + 1
            break
        i += 1
    else:
        return None
    body = html[start:end]
    try:
        inner = json.loads(body)
        return json.loads(inner)
    except json.JSONDecodeError as e:
        print(f"  JSON parse error {url}: {e}", file=sys.stderr)
        return None


def extract_scoreboard(initial: dict, scoreboard_id: str = "council-scoreboard") -> dict | None:
    """Return parsed scoreboard data."""
    data = initial.get("data", {})
    for k, v in data.items():
        if not k.startswith("scoreboard?"):
            continue
        sb = v.get("data", {})
        if sb.get("headingId") == scoreboard_id:
            return sb
    return None


def parse_status(status_msg: str) -> tuple[int | None, int | None]:
    """Extract (declared, total) from text like 'After 84 of 136 councils declared.'"""
    m = re.search(r"(\d+)\s+of\s+(\d+)\s+councils?", status_msg or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def fetch_region(region: str, url: str) -> dict | None:
    initial = fetch_initial_data(url)
    if not initial:
        return None
    sb = extract_scoreboard(initial)
    if not sb:
        return None

    declared, total = parse_status(sb.get("status", {}).get("message", ""))
    parties: dict[str, dict] = {}
    for grp in sb.get("groups", []):
        for sc in grp.get("scorecards", []):
            title = sc.get("title")
            score = sc.get("score", {})
            cols = score.get("dataColumns") or []
            # cols[0] = [councils_total, councils_change], cols[1] = [councillors_total, councillors_change]
            try:
                councils_total = cols[0][0] if cols and cols[0] else None
                councillors_total = cols[1][0] if len(cols) > 1 and cols[1] else None
                councils_change = cols[0][1] if cols and len(cols[0]) > 1 else None
                councillors_change = cols[1][1] if len(cols) > 1 and len(cols[1]) > 1 else None
            except (IndexError, TypeError):
                continue
            parties[title] = {
                "councils": councils_total,
                "councils_change": councils_change,
                "councillors": councillors_total,
                "councillors_change": councillors_change,
            }
    return {
        "region": region,
        "councils_declared": declared,
        "councils_total": total,
        "parties": parties,
        "url": url,
    }


def aggregate_to_buckets(regions: dict) -> dict:
    """Sum BBC tallies into our 9-party buckets."""
    PARTIES = ["Con", "Lab", "LD", "Grn", "Ref", "SNP", "PC", "Ind", "Oth"]
    seats = {p: 0 for p in PARTIES}
    seats_change = {p: 0 for p in PARTIES}
    councils = {p: 0 for p in PARTIES}
    councils_total = 0
    councils_decl = 0
    for r in regions.values():
        if not r:
            continue
        councils_total += r["councils_total"] or 0
        councils_decl += r["councils_declared"] or 0
        for label, vals in r["parties"].items():
            bucket = BBC_TO_PARTY.get(label, "Oth")
            if vals.get("councillors") is not None:
                seats[bucket] += vals["councillors"]
            if vals.get("councillors_change") is not None:
                seats_change[bucket] += vals["councillors_change"]
            if vals.get("councils") is not None:
                councils[bucket] += vals["councils"]
    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "councils_declared": councils_decl,
        "councils_total": councils_total,
        "seats_by_party": seats,
        "seats_change_by_party": seats_change,
        "councils_by_party": councils,
    }


def fetch_scoreboard_wc(region: str) -> dict | None:
    """Use the lightweight wc-data endpoint (~3.5KB) instead of full HTML scrape."""
    path = SCOREBOARD_PATHS.get(region)
    if not path:
        return None
    url = (f"{WC_BASE}/scoreboard?assetUri={path}"
           f"&dataProperty=scoreboard&service=news&year=2026")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read())
    except Exception as e:
        print(f"  wc-data scoreboard error {region}: {e}", file=sys.stderr)
        return None
    declared, total = parse_status(d.get("status", {}).get("message", ""))
    parties: dict[str, dict] = {}
    for grp in d.get("groups", []):
        for sc in grp.get("scorecards", []):
            cols = sc.get("score", {}).get("dataColumns") or []
            try:
                parties[sc["title"]] = {
                    "councils": cols[0][0] if cols else None,
                    "councils_change": cols[0][1] if cols and len(cols[0]) > 1 else None,
                    "councillors": cols[1][0] if len(cols) > 1 else None,
                    "councillors_change": cols[1][1] if len(cols) > 1 and len(cols[1]) > 1 else None,
                }
            except (IndexError, TypeError):
                continue
    return {
        "region": region, "councils_declared": declared, "councils_total": total,
        "parties": parties, "url": url,
    }


def fetch_az_list(region: str) -> list[dict]:
    """Per-council winner data via az-list endpoint."""
    path = AZ_PATHS.get(region)
    if not path:
        return []
    url = f"{WC_BASE}/az-list?assetUri={path}&entities=councils"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read())
    except Exception as e:
        print(f"  wc-data az-list error {region}: {e}", file=sys.stderr)
        return []
    out = []
    for g in d.get("groups", []):
        for c in g.get("cards", []):
            wf = c.get("winnerFlash")
            entry = {
                "name": c.get("title"),
                "gss": (c.get("href", "").rsplit("/", 1)[-1] if c.get("href") else None),
                "region": region,
                "declared": wf is not None,
            }
            if wf:
                code = wf.get("winnerPartyCode") or "OTH"
                entry["winner_party_code"] = code
                entry["winner_party"] = BBC_CODE_TO_PARTY.get(code, "Oth")
                entry["winner_party_name"] = wf.get("partyName")
                entry["flash"] = wf.get("flash")
                entry["prev_party_code"] = wf.get("prevWinnerPartyCode")
                entry["change_type"] = "hold" if "hold" in (wf.get("flash") or "").lower() else (
                    "gain" if "gain" in (wf.get("flash") or "").lower() else (
                    "loss" if "loss" in (wf.get("flash") or "").lower() else "noc"))
            out.append(entry)
    return out


def main() -> None:
    # Use the lighter wc-data endpoints
    regions = {name: fetch_scoreboard_wc(name) for name in PAGES}
    aggregate = aggregate_to_buckets(regions)
    aggregate["regions"] = regions
    # Per-council winners
    councils_all: list[dict] = []
    for region in PAGES:
        councils_all.extend(fetch_az_list(region))
    aggregate["councils"] = councils_all
    aggregate["councils_with_winners"] = sum(1 for c in councils_all if c.get("declared"))

    OUT.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    by_party_winners: dict[str, int] = {}
    for c in councils_all:
        if c.get("declared"):
            wp = c.get("winner_party", "?")
            by_party_winners[wp] = by_party_winners.get(wp, 0) + 1
    print(f"BBC: {aggregate['councils_declared']}/{aggregate['councils_total']} councils declared")
    print(f"  Per-council winners ({aggregate['councils_with_winners']}): {by_party_winners}")
    print(f"  Seats by party: {aggregate['seats_by_party']}")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
