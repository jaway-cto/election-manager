"""
French 2027 Presidential Election: Polymarket vs Smarkets cross-venue validator.

Sources (all free, no auth):
  - Polymarket Gamma API: https://gamma-api.polymarket.com/events?slug=next-french-presidential-election
  - Smarkets v3 API:      https://api.smarkets.com/v3/events/42702202/  (Winner market 23982189)
  - Wikipedia poll page:  scraped first-round rolling averages (best-effort)

Notes on Smarkets shape (verified 8 May 2026):
  - Event 42702202 has ONE market (id 23982189, "Winner"), not first-round odds.
  - Quotes endpoint returns prices in basis points (10000 = 100%); we divide by 10000.
  - Best-offer (ask) is conservative implied prob; many contracts have no bids at all.
  - Bardella & Attal contracts (created 2025-07) have empty bids/wide offers => illiquid.

Polymarket is also a Winner market (resolves to election winner including 2nd round).
So this is winner-vs-winner, not first-round-vs-first-round. Polling is first-round
share — kept as a directional reference only, not an apples-to-apples comparison.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.request
import urllib.parse
from typing import Dict, List, Optional, Tuple

from validator_core import gamma_event, get_quote, parse_clob_token_ids

UA = "Mozilla/5.0 (compatible; french-pres-validator/1.0)"
PM_SLUG = "next-french-presidential-election"
SM_EVENT = "42702202"
SM_MARKET = "23982189"
WIKI_URL = "https://en.wikipedia.org/wiki/Opinion_polling_for_the_2027_French_presidential_election"

# Per-candidate CLOB metadata captured during fetch_polymarket
_PM_QUOTES: Dict[str, dict] = {}


def http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]+", "", s).strip()
    return s


# ---------- Polymarket ----------
def fetch_polymarket() -> Dict[str, float]:
    """Per-candidate YES price from CLOB best ask (executable). Captures spread for filtering."""
    ev = gamma_event(PM_SLUG)
    if not ev:
        raise RuntimeError("Polymarket: empty response for slug")
    out: Dict[str, float] = {}
    _PM_QUOTES.clear()
    for m in ev.get("markets", []):
        name = m.get("groupItemTitle") or m.get("question") or ""
        if not name or name.lower().startswith("person "):
            continue
        yes_tok, _ = parse_clob_token_ids(m)
        quote = get_quote(yes_tok) if yes_tok else None
        p: Optional[float] = None
        if quote and quote.ask is not None:
            p = quote.ask
        elif quote and quote.mid is not None:
            p = quote.mid
        else:
            p = m.get("lastTradePrice")
            if p is None:
                try:
                    p = float(json.loads(m.get("outcomePrices") or "[0]")[0])
                except Exception:
                    p = None
        if p is None:
            continue
        out[name] = float(p)
        _PM_QUOTES[name] = {
            "bid": quote.bid if quote else None,
            "ask": quote.ask if quote else None,
            "spread_bps": quote.spread_bps if quote else None,
        }
    return out


# ---------- Smarkets ----------
def fetch_smarkets() -> Dict[str, Optional[float]]:
    contracts = json.loads(http_get(f"https://api.smarkets.com/v3/markets/{SM_MARKET}/contracts/"))["contracts"]
    quotes = json.loads(http_get(f"https://api.smarkets.com/v3/markets/{SM_MARKET}/quotes/"))
    out: Dict[str, Optional[float]] = {}
    for c in contracts:
        cid = c["id"]; name = c["name"].strip()
        q = quotes.get(cid, {})
        bids, offers = q.get("bids") or [], q.get("offers") or []
        # Best ask = lowest offer (in basis points, 10000 = 100%). Conservative implied prob.
        # Filter out junk 5000+ (50%+) tail offers if a tighter one exists.
        if offers:
            best_ask = min(o["price"] for o in offers) / 10000.0
        elif bids:
            best_ask = max(b["price"] for b in bids) / 10000.0
        else:
            best_ask = None
        out[name] = best_ask
    return out


# ---------- Wikipedia polling (best-effort) ----------
def fetch_polling() -> Dict[str, float]:
    """Best-effort: pull the most recent (top) row of the rolling-average / latest poll table."""
    try:
        html = http_get(WIKI_URL).decode("utf-8", "ignore")
    except Exception as e:
        print(f"  [polling] fetch failed: {e}", file=sys.stderr)
        return {}
    # Heuristic: find blocks like "Bardella" followed by a number within ~60 chars.
    candidates = ["Bardella", "Philippe", "Mélenchon", "Le Pen", "Attal", "Zemmour",
                  "Wauquiez", "Glucksmann", "Darmanin", "Retailleau"]
    out: Dict[str, float] = {}
    text = re.sub(r"<[^>]+>", " ", html)  # strip tags
    text = re.sub(r"\s+", " ", text)
    for cand in candidates:
        # First numeric % within 80 chars after the surname mention
        m = re.search(re.escape(cand) + r".{0,80}?(\d{1,2}(?:\.\d)?)\s*%", text)
        if m:
            out[cand] = float(m.group(1))
    return out


# ---------- Compare ----------
def devig(prices: Dict[str, float]) -> Dict[str, float]:
    total = sum(prices.values())
    if total <= 0:
        return prices
    return {k: v / total for k, v in prices.items()}


def match_name(target: str, pool: Dict[str, float]) -> Optional[str]:
    tn = normalize_name(target)
    for k in pool:
        kn = normalize_name(k)
        if tn == kn or tn.split()[-1] == kn.split()[-1]:
            return k
    return None


def scan_french_pres(verbose: bool = True) -> List[dict]:
    pm_raw = fetch_polymarket()
    sm_raw = fetch_smarkets()
    poll = fetch_polling()
    pm_fair = devig(pm_raw)

    # Smarkets devig only over priced contracts
    sm_priced = {k: v for k, v in sm_raw.items() if v is not None}
    sm_fair = devig(sm_priced) if sm_priced else {}

    rows: List[dict] = []
    seen_pm = set()
    for name, pm_p in sorted(pm_fair.items(), key=lambda x: -x[1]):
        if pm_p < 0.005:
            continue
        seen_pm.add(name)
        sm_match = match_name(name, sm_fair)
        sm_p = sm_fair.get(sm_match) if sm_match else None
        sm_raw_p = sm_raw.get(sm_match) if sm_match else None
        poll_match = next((k for k in poll if normalize_name(k) in normalize_name(name)), None)
        poll_p = poll.get(poll_match) if poll_match else None

        edge_pp = None
        action = ""
        spread_bps = (_PM_QUOTES.get(name, {}) or {}).get("spread_bps")
        if sm_p is not None:
            edge_pp = (pm_p - sm_p) * 100
            if abs(edge_pp) >= 3:
                base = (f"PM cheap vs Smarkets ({edge_pp:+.1f}pp)" if edge_pp < 0
                        else f"PM rich vs Smarkets ({edge_pp:+.1f}pp)")
                if spread_bps is not None and spread_bps > abs(edge_pp) * 100 * 0.5:
                    action = base + f"  -- SPREAD EATS EDGE ({spread_bps:.0f}bp)"
                elif spread_bps is not None and spread_bps > 500:
                    action = base + f"  -- WIDE SPREAD ({spread_bps:.0f}bp)"
                else:
                    action = "** " + base
        rows.append({
            "candidate": name,
            "pm": pm_p, "pm_raw": pm_raw[name],
            "sm": sm_p, "sm_raw": sm_raw_p,
            "poll": poll_p, "edge_pp": edge_pp, "action": action,
            "spread_bps": spread_bps,
        })

    if verbose:
        print(f"\nFrench 2027 Presidential Election — winner-market cross-venue scan")
        print(f"Polymarket event 79987 | Smarkets event {SM_EVENT} market {SM_MARKET}")
        print(f"PM raw sum: {sum(pm_raw.values()):.3f} (devigged) | "
              f"SM priced sum: {sum(sm_priced.values()):.3f} ({len(sm_priced)} contracts) | "
              f"Polling rows: {len(poll)}")
        print(f"\n{'Candidate':<24}{'PM':>8}{'SM(ask)':>10}{'SM_fair':>10}{'Poll(1R)':>10}{'Edge':>9}{'Spr':>8}  Action")
        print("-" * 110)
        for r in rows:
            pm = f"{r['pm']*100:.1f}%"
            sm_raw_s = f"{r['sm_raw']*100:.1f}%" if r['sm_raw'] is not None else "  -  "
            sm_s = f"{r['sm']*100:.1f}%" if r['sm'] is not None else "  -  "
            pl = f"{r['poll']:.1f}%" if r['poll'] is not None else "  -  "
            ed = f"{r['edge_pp']:+.1f}pp" if r['edge_pp'] is not None else "  -  "
            sp = f"{r['spread_bps']:.0f}bp" if r['spread_bps'] is not None else "  -  "
            print(f"{r['candidate']:<24}{pm:>8}{sm_raw_s:>10}{sm_s:>10}{pl:>10}{ed:>9}{sp:>8}  {r['action']}")
        flagged = [r for r in rows if r["action"]]
        print(f"\nFlagged (|edge| >= 3pp on cross-venue, devigged): {len(flagged)}")
        if not sm_priced:
            print("WARNING: Smarkets returned no priced contracts — market illiquid for this event.")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0,
                    help="Re-run every N seconds (0 = once)")
    args = ap.parse_args()
    while True:
        try:
            scan_french_pres(verbose=True)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
        if args.watch <= 0:
            break
        print(f"\n[sleeping {args.watch}s]\n")
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
