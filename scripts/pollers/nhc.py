"""
NHC hurricane advisory poller.

Polls https://www.nhc.noaa.gov/CurrentStorms.json (free, no auth).
On detection of a new advisory or change in cone/intensity:
  * log structured event
  * search Polymarket for storm-name and "hurricane"/"named storm" markets
  * alert via Telegram

Atlantic hurricane season runs 1 Jun - 30 Nov. During off-season the JSON
returns an empty list — poller still runs (cheap) and produces a heartbeat.

Usage:
    python -m pollers.nhc                  # one-shot
    python -m pollers.nhc --watch 600      # poll every 10 min
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notify import alert, fyi, event
from pollers.state import is_new
from validator_core import gamma_search

UA = {"User-Agent": "Mozilla/5.0 (odds-nhc-poller; r.ingham@live.co.uk)"}
NHC_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"


def fetch_storms() -> list[dict]:
    try:
        r = requests.get(NHC_URL, headers=UA, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        sys.stderr.write(f"[nhc] fetch failed: {e}\n")
        return []
    storms = data.get("activeStorms") or data.get("storms") or []
    if isinstance(storms, dict):
        storms = list(storms.values())
    return storms


def storm_summary(s: dict) -> str:
    name = s.get("name") or s.get("stormName") or "(unnamed)"
    classification = s.get("classification") or s.get("intensity") or ""
    basin = s.get("binNumber") or s.get("basin") or ""
    advnum = s.get("advisoryNumber") or s.get("advNum") or ""
    pos = s.get("centerLocation") or s.get("position") or ""
    winds = s.get("intensity") or s.get("intensityMph") or ""
    return f"{classification} {name} ({basin}) — adv #{advnum} — {pos} — {winds} mph"


def find_pm_markets(name: str) -> list[dict]:
    if not name:
        return []
    res = gamma_search(f"hurricane {name}", limit=5)
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
    storms = fetch_storms()
    new_advisories = []
    for s in storms:
        name = (s.get("name") or "")
        adv = (s.get("advisoryNumber") or s.get("advNum") or "")
        item_id = f"{name}-{adv}"
        # Hash key fields (intensity, position) so an INTERMEDIATE update
        # also registers as a new advisory.
        payload = json.dumps({
            "intensity": s.get("intensity"),
            "position": s.get("centerLocation") or s.get("position"),
            "movement": s.get("movement"),
        }, sort_keys=True)
        h = hashlib.sha1(payload.encode()).hexdigest()[:16]
        if is_new("nhc", item_id, h):
            new_advisories.append(s)
    fyi(f"nhc: {len(storms)} active storms, {len(new_advisories)} new/updated advisories")
    for s in new_advisories:
        summary = storm_summary(s)
        markets = find_pm_markets(s.get("name") or "")
        body = f"NHC advisory — {summary}"
        if s.get("publicAdvisory"):
            body += f"\nPublic Advisory: {s['publicAdvisory']}"
        if s.get("forecastAdvisory"):
            body += f"\nForecast: {s['forecastAdvisory']}"
        if markets:
            body += "\n\nRelated PM markets:"
            for m in markets:
                body += (f"\n  • {m['title']}  bid {m['best_bid']} / "
                         f"ask {m['best_ask']}  v24 ${m['vol_24h']}")
        alert(body)
        event("nhc.advisory", {
            "name": s.get("name"),
            "advisory": s.get("advisoryNumber"),
            "intensity": s.get("intensity"),
            "position": s.get("centerLocation") or s.get("position"),
            "markets": markets,
        })
    return new_advisories


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0)
    args = ap.parse_args()
    while True:
        try:
            scan()
        except Exception as e:
            sys.stderr.write(f"[nhc] error: {e}\n")
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
