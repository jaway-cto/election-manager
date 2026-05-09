"""
SCOTUS slip-opinion poller.

Watches https://www.supremecourt.gov/opinions/slipopinion/{term} for new
opinions. Poll cadence: 10s during 09:55-11:00 ET on opinion days; 5min
otherwise. On detection: alert via Telegram + log payload + (optionally)
search Polymarket for related markets and emit recommended action.

Strategy:
  * Pull the term's slip-opinion HTML index every poll
  * Diff against last-seen set (persisted to SQLite)
  * On new row: extract docket number, case name, PDF URL, opinion author
  * Pull PDF first paragraph (the "syllabus" / holding) for keyword match
  * Search Polymarket for case-name keywords; alert with current PM price

The actual PDF parsing is best-effort — if pdfminer/pypdf isn't installed
we just emit the case name and URL.

Usage:
    python -m pollers.scotus                    # one-shot
    python -m pollers.scotus --watch 30         # poll every 30s
    python -m pollers.scotus --term 25
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests

# Allow `python pollers/scotus.py` and `python -m pollers.scotus`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notify import alert, fyi, event
from pollers.state import is_new
from validator_core import gamma_search

UA = {"User-Agent": "Mozilla/5.0 (odds-scotus-poller; r.ingham@live.co.uk)"}
SLIP_URL = "https://www.supremecourt.gov/opinions/slipopinion/{term}"

# Row pattern: each opinion row in the slip-opinion table
ROW_RE = re.compile(
    r'<tr[^>]*>\s*<td[^>]*>([\d/]{8,12})</td>\s*'      # date
    r'<td[^>]*>(\d{2}-\d{3,4}|\d{2}M\d+|\d{2}A\d+)</td>\s*'  # docket
    r'<td[^>]*>\s*<a[^>]+href="(/opinions/[^"]+\.pdf)"[^>]*>'
    r'(.*?)</a>'                                        # case title
    r'.*?</tr>',
    re.DOTALL | re.IGNORECASE,
)


def fetch_index(term: int) -> str:
    url = SLIP_URL.format(term=term)
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.text


def parse_rows(html: str) -> list[dict]:
    rows = []
    for m in ROW_RE.finditer(html):
        date, docket, href, title = m.groups()
        title = re.sub(r"<[^>]+>", "", title).strip()
        rows.append({
            "date": date.strip(),
            "docket": docket.strip(),
            "url": "https://www.supremecourt.gov" + href,
            "title": title[:120],
        })
    return rows


def fetch_pdf_first_para(url: str) -> str:
    """Best-effort first-paragraph extraction. Returns empty on failure."""
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        return ""
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        path = Path("C:/Dev/odds/data/scotus_tmp.pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        text = extract_text(path, page_numbers=[0, 1, 2])
        path.unlink(missing_ok=True)
        # Find the first big paragraph after "Syllabus" or "Held" or similar
        for marker in ("Held:", "HELD:", "Syllabus", "SYLLABUS"):
            i = text.find(marker)
            if i >= 0:
                snippet = text[i:i + 600]
                snippet = re.sub(r"\s+", " ", snippet).strip()
                return snippet
        # Fallback: first 600 chars
        return re.sub(r"\s+", " ", text[:600]).strip()
    except Exception:
        return ""


def find_pm_markets(case_title: str) -> list[dict]:
    """Best-effort search Polymarket for related contracts."""
    res = gamma_search(case_title, limit=8)
    out = []
    for src in (res.get("markets") or [], res.get("events") or []):
        for it in src:
            if it.get("closed") or it.get("archived"):
                continue
            out.append({
                "title": it.get("question") or it.get("title"),
                "slug": it.get("slug"),
                "best_bid": it.get("bestBid"),
                "best_ask": it.get("bestAsk"),
                "vol_24h": it.get("volume24hr"),
            })
    return out[:5]


def scan(term: int = 25) -> list[dict]:
    try:
        html = fetch_index(term)
    except Exception as e:
        sys.stderr.write(f"[scotus] fetch failed: {e}\n")
        return []
    rows = parse_rows(html)
    new_opinions = []
    for r in rows:
        item_id = f"{term}-{r['docket']}-{r['url']}"
        h = hashlib.sha1(r["url"].encode()).hexdigest()[:16]
        if is_new("scotus", item_id, h):
            new_opinions.append(r)
    fyi(f"scotus: {len(rows)} opinions on {term}-term page, {len(new_opinions)} new")
    for r in new_opinions:
        snippet = fetch_pdf_first_para(r["url"])
        markets = find_pm_markets(r["title"])
        body = (
            f"SCOTUS opinion released — {r['date']}\n"
            f"Case: {r['title']}\n"
            f"Docket: {r['docket']}\n"
            f"PDF: {r['url']}\n"
        )
        if snippet:
            body += f"\nHolding (auto-extract): {snippet[:400]}\n"
        if markets:
            body += "\nRelated PM markets:\n"
            for m in markets:
                body += (f"  • {m['title']}  bid {m['best_bid']} / "
                         f"ask {m['best_ask']}  v24 ${m['vol_24h']}\n")
        alert(body)
        event("scotus.opinion", {
            "docket": r["docket"], "title": r["title"], "url": r["url"],
            "snippet": snippet[:300], "markets": markets,
        })
    return new_opinions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--term", type=int, default=25,
                    help="OT term (e.g., 25 for OT 2025-26)")
    ap.add_argument("--watch", type=int, default=0)
    args = ap.parse_args()
    while True:
        try:
            scan(args.term)
        except Exception as e:
            sys.stderr.write(f"[scotus] error: {e}\n")
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
