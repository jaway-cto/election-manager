"""
FDA press-release poller.

Source: https://www.fda.gov/.../rss-feeds/press-releases/rss.xml

Watches for new FDA press releases (drug approvals, AdComm announcements,
recalls, enforcement actions). On new item:
  * extract the drug/sponsor name where possible
  * search Polymarket for FDA / drug-name markets
  * alert via Telegram

Coverage caveats per Stage 2b:
  * No official PDUFA calendar feed — this poller catches approvals AT
    announcement, not in advance. To trade ahead you also want
    BioPharmaCatalyst's PDUFA scrape (see future addition).
  * Press releases lag the actual approval announcement by 1-10 minutes.

Usage:
    python -m pollers.fda
    python -m pollers.fda --watch 60
"""
from __future__ import annotations
import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notify import alert, fyi, event
from pollers.state import is_new
from validator_core import gamma_search

UA = {"User-Agent": "Mozilla/5.0 (odds-fda-poller; r.ingham@live.co.uk)"}
FDA_RSS = ("https://www.fda.gov/about-fda/contact-fda/stay-informed/"
           "rss-feeds/press-releases/rss.xml")


def fetch_feed() -> list[dict]:
    """Return list of {title, link, pubDate, description}."""
    try:
        r = requests.get(FDA_RSS, headers=UA, timeout=20)
        r.raise_for_status()
        xml = r.text
    except Exception as e:
        sys.stderr.write(f"[fda] fetch failed: {e}\n")
        return []
    items = []
    # Simple regex parse — RSS XML structure is stable
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.DOTALL):
        body = m.group(1)
        def field(tag):
            m2 = re.search(fr"<{tag}>(.*?)</{tag}>", body, re.DOTALL)
            if not m2:
                return ""
            v = m2.group(1).strip()
            v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.DOTALL)
            return v.strip()
        items.append({
            "title": field("title"),
            "link": field("link"),
            "pubDate": field("pubDate"),
            "description": re.sub(r"<[^>]+>", "", field("description"))[:500],
            "guid": field("guid"),
        })
    return items


# Keywords that suggest tradeable / market-relevant news
TRADEABLE_KEYWORDS = [
    "approves", "approval", "authorize", "authorization",
    "advisory committee", "AdComm",
    "complete response letter", "CRL",
    "warning letter", "recall",
    "PDUFA", "BLA", "NDA",
    "breakthrough", "fast track", "priority review",
]


def is_tradeable(title: str, desc: str) -> bool:
    text = (title + " " + desc).lower()
    return any(k.lower() in text for k in TRADEABLE_KEYWORDS)


# Extract drug/sponsor candidates from headline
DRUG_RE = re.compile(r"(?:Approves|Authorizes|Issues|Grants)\s+(.+?)(?:\s+for|\s+to|$)",
                     re.I)


def extract_drug(title: str) -> str:
    m = DRUG_RE.search(title)
    return (m.group(1).strip() if m else "")[:80]


def find_pm_markets(query: str) -> list[dict]:
    if not query:
        return []
    res = gamma_search(query, limit=8)
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


def scan() -> list[dict]:
    items = fetch_feed()
    fyi(f"fda: {len(items)} press-release items in feed")
    new_items = []
    for it in items:
        guid = it.get("guid") or it.get("link") or it["title"]
        h = hashlib.sha1((it["title"] + it["pubDate"]).encode()).hexdigest()[:16]
        if is_new("fda", guid, h):
            new_items.append(it)
    if new_items:
        fyi(f"fda: {len(new_items)} new releases")
    for it in new_items:
        if not is_tradeable(it["title"], it["description"]):
            event("fda.skipped", {"title": it["title"]})
            continue
        drug = extract_drug(it["title"])
        markets = find_pm_markets(drug or it["title"][:60])
        body = (
            f"FDA press release — {it['pubDate']}\n"
            f"Title: {it['title']}\n"
            f"Link: {it['link']}\n"
            f"Drug/Action: {drug or '?'}\n"
        )
        if it["description"]:
            body += f"\nSummary: {it['description'][:300]}\n"
        if markets:
            body += "\nRelated PM markets:\n"
            for m in markets:
                body += (f"  • {m['title']}  bid {m['best_bid']} / "
                         f"ask {m['best_ask']}  v24 ${m['vol_24h']}\n")
        alert(body)
        event("fda.release", {
            "title": it["title"], "link": it["link"],
            "drug": drug, "pubDate": it["pubDate"],
            "markets": markets,
        })
    return new_items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0)
    args = ap.parse_args()
    while True:
        try:
            scan()
        except Exception as e:
            sys.stderr.write(f"[fda] error: {e}\n")
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
