"""
validator_core.py — Shared utilities for all Polymarket arbitrage validators.

Provides:
  * CLOB /book fetching (real bids/asks instead of stale outcomePrices)
  * Spread + OI computation
  * Edge classification with execution-aware filtering
  * Markdown-table formatting
  * Cross-validator merge + ranking

Used by: crypto_validator, wti_validator, nba_validator, eurovision_validator,
french_pres_validator, sports_validator, macro_validator, unified_arb_dashboard.

All endpoints are free + unauthenticated.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

UA = {"User-Agent": "Mozilla/5.0 (odds-validator)"}

# ---------- in-process cache to avoid hammering CLOB on tight loops -----------
_BOOK_CACHE: dict[str, tuple[float, dict]] = {}
_OI_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 5.0  # seconds


def _cache_get(cache: dict, key: str) -> Optional[dict]:
    hit = cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


def _cache_set(cache: dict, key: str, val: dict) -> None:
    cache[key] = (time.time(), val)


# ============================================================================
# Polymarket CLOB / Data API fetchers
# ============================================================================

def fetch_clob_book(token_id: str, timeout: float = 15.0) -> Optional[dict]:
    """Fetch CLOB order book for a single outcome token.

    Returns: {"asset_id", "bids":[{"price","size"}], "asks":[...], "timestamp"}
             or None on failure.
    """
    if not token_id:
        return None
    cached = _cache_get(_BOOK_CACHE, token_id)
    if cached is not None:
        return cached
    try:
        r = requests.get(f"{CLOB}/book", params={"token_id": token_id},
                         headers=UA, timeout=timeout)
        r.raise_for_status()
        js = r.json()
        _cache_set(_BOOK_CACHE, token_id, js)
        return js
    except Exception:
        return None


def fetch_clob_midpoint(token_id: str, timeout: float = 10.0) -> Optional[float]:
    """Lighter-weight than full book — single midpoint price."""
    if not token_id:
        return None
    try:
        r = requests.get(f"{CLOB}/midpoint", params={"token_id": token_id},
                         headers=UA, timeout=timeout)
        r.raise_for_status()
        return float(r.json().get("mid", 0))
    except Exception:
        return None


def fetch_clob_spread(token_id: str, timeout: float = 10.0) -> Optional[float]:
    """Spread in dollars (e.g. 0.02 = 2¢)."""
    if not token_id:
        return None
    try:
        r = requests.get(f"{CLOB}/spread", params={"token_id": token_id},
                         headers=UA, timeout=timeout)
        r.raise_for_status()
        return float(r.json().get("spread", 0))
    except Exception:
        return None


def fetch_oi(market_id: str, timeout: float = 10.0) -> Optional[dict]:
    """Open interest + 24h volume from Data API.

    Returns: {"open_interest": float (USD), "volume_24h": float (USD)} or None.
    """
    if not market_id:
        return None
    cached = _cache_get(_OI_CACHE, market_id)
    if cached is not None:
        return cached
    out: dict = {}
    # Try multiple endpoint shapes — Polymarket Data API has evolved
    for path, key in (("/oi", "openInterest"), ("/open-interest", "openInterest")):
        try:
            r = requests.get(f"{DATA}{path}", params={"market": market_id},
                             headers=UA, timeout=timeout)
            if r.status_code == 200:
                js = r.json()
                if isinstance(js, dict):
                    out["open_interest"] = float(js.get(key) or js.get("oi") or 0)
                    break
        except Exception:
            continue
    # Volume: try /volume or fall back to gamma market field
    try:
        r = requests.get(f"{DATA}/volume", params={"market": market_id},
                         headers=UA, timeout=timeout)
        if r.status_code == 200:
            js = r.json()
            if isinstance(js, dict):
                out["volume_24h"] = float(js.get("volume24hr") or js.get("volume") or 0)
    except Exception:
        pass
    _cache_set(_OI_CACHE, market_id, out)
    return out or None


def gamma_event(slug: str, timeout: float = 15.0) -> Optional[dict]:
    """Fetch event by slug from Gamma. Includes markets list with clobTokenIds."""
    try:
        r = requests.get(f"{GAMMA}/events", params={"slug": slug},
                         headers=UA, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return data[0] if isinstance(data, list) else data
    except Exception:
        return None


def gamma_search(query: str, limit: int = 10, timeout: float = 15.0) -> dict:
    """Public search — events, markets, profiles."""
    try:
        r = requests.get(f"{GAMMA}/public-search",
                         params={"q": query, "limit_per_type": limit},
                         headers=UA, timeout=timeout)
        r.raise_for_status()
        return r.json() or {}
    except Exception:
        return {}


# ============================================================================
# Price + spread extraction
# ============================================================================

def best_levels(book: dict) -> tuple[Optional[float], Optional[float],
                                     Optional[float], Optional[float]]:
    """Return (best_bid, best_ask, bid_size, ask_size).

    CLOB book shape: {"bids":[{"price":"0.475","size":"1234"},...],
                     "asks":[{"price":"0.485","size":"567"},...]}
    Bids are sorted descending, asks ascending — but we sort defensively.
    """
    if not book:
        return None, None, None, None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bb = ba = bs = as_ = None
    if bids:
        try:
            top_bid = max(bids, key=lambda x: float(x["price"]))
            bb = float(top_bid["price"]); bs = float(top_bid.get("size", 0))
        except Exception:
            pass
    if asks:
        try:
            top_ask = min(asks, key=lambda x: float(x["price"]))
            ba = float(top_ask["price"]); as_ = float(top_ask.get("size", 0))
        except Exception:
            pass
    return bb, ba, bs, as_


@dataclass
class Quote:
    """Executable quote summary for a single outcome token."""
    token_id: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_size: Optional[float] = None  # in shares
    ask_size: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None    # in $ (e.g. 0.02 = 2¢)
    spread_bps: Optional[float] = None  # spread in basis points of mid
    timestamp: Optional[int] = None
    source: str = "clob"

    @property
    def has_book(self) -> bool:
        return self.bid is not None and self.ask is not None

    def executable(self, side: str) -> Optional[float]:
        """Price at which we'd actually transact.
        side='buy' -> ask (best price to buy YES)
        side='sell' -> bid (best price to sell YES)
        """
        return self.ask if side == "buy" else self.bid


def quote_from_book(token_id: str, book: dict | None) -> Quote:
    if not book:
        return Quote(token_id=token_id)
    bb, ba, bs, as_ = best_levels(book)
    mid = None
    spread = None
    spread_bps = None
    if bb is not None and ba is not None:
        mid = (bb + ba) / 2.0
        spread = ba - bb
        if mid > 0:
            spread_bps = (spread / mid) * 10000.0
    return Quote(
        token_id=token_id, bid=bb, ask=ba, bid_size=bs, ask_size=as_,
        mid=mid, spread=spread, spread_bps=spread_bps,
        timestamp=book.get("timestamp"),
    )


def get_quote(token_id: str) -> Quote:
    """One-call helper: fetch book and parse to Quote."""
    return quote_from_book(token_id, fetch_clob_book(token_id))


def parse_clob_token_ids(market: dict) -> tuple[Optional[str], Optional[str]]:
    """Extract (yes_token, no_token) from a Polymarket Gamma market dict.

    Gamma encodes clobTokenIds as a JSON-string list of 2 IDs.
    """
    raw = market.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None, None
    if not raw or not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None, None
    return str(raw[0]), str(raw[1])


# ============================================================================
# Edge math
# ============================================================================

def edge_bps(fair: float, market: float) -> float:
    """Positive bps => market is under-pricing (BUY); negative => over-pricing (SELL)."""
    return (fair - market) * 10000.0


def edge_action(fair: float, market_yes: float,
                threshold_bps: float = 200) -> tuple[str, float]:
    """Classify edge.
    Returns (action, edge_bps).
    action: BUY YES | SELL YES | -
    """
    e = edge_bps(fair, market_yes)
    if abs(e) < threshold_bps:
        return "-", e
    return ("BUY YES" if e > 0 else "SELL YES"), e


# ============================================================================
# Filtering — execution-viability gates
# ============================================================================

@dataclass
class FilterParams:
    min_oi_usd: float = 50_000.0
    max_spread_bps: float = 500.0    # 5pp
    max_spread_vs_edge: float = 0.5  # spread must be <50% of edge magnitude
    min_book_size_shares: float = 100.0  # at least 100 shares on best ask
    max_book_age_seconds: float = 300.0  # 5 min stale = skip
    # Polymarket fee model (per market.feeSchedule on Gamma — varies by category)
    # taker_fee_bps: bps charged on taker fills (gets eaten from edge)
    # rebate_bps: bps rebated to makers (offsets taker fee for market makers)
    # On the safe side we discount edges by full taker fee unless we'll be a maker.
    taker_fee_bps: float = 200.0    # 2pp default — actual is 0-700bps per market
    maker_rebate_bps: float = 0.0   # set if posting maker orders on rewards markets


def fees_for_market(market: dict | None) -> tuple[float, float]:
    """Read taker fee + maker rebate (bps) from a Polymarket market dict.
    Returns (taker_fee_bps, maker_rebate_bps).

    Polymarket V2 fee schedule (Mar 2026): fee = rate * p * (1-p) * notional.
    Peak fee at p=0.50. Returns the PEAK fee in bps; callers can scale down
    by 4*p*(1-p) at the actual trade price for more accuracy.
    """
    if not market:
        return (200.0, 0.0)
    fs = market.get("feeSchedule") or {}
    rate = float(fs.get("rate") or 0)
    rebate = float(fs.get("rebateRate") or 0)
    # rate=0.072 → peak 180 bps fee at p=0.5 (= rate * 0.25 * 10000).
    return (rate * 2500.0, rebate * 2500.0)


# Per-category minimum gross edge (in bps) required to clear the full UK
# cost stack: PM taker fee + Polygon gas + spread cross + GBP/USD FX + CGT.
# Source: docs/RED_TEAM_FINDINGS.md cost-forensics analysis (2026-05-09).
MIN_EDGE_BPS_BY_CATEGORY = {
    "geopolitical":  300,   # fee-free, one-sided held to redemption
    "sports":        450,   # 0.030 fee tier
    "finance":       550,   # 0.040 fee tier (WTI, rates)
    "politics":      550,   # 0.040
    "tech":          550,
    "culture":       650,   # 0.050 (Eurovision, awards)
    "economics":     650,
    "weather":       650,
    "crypto":        750,   # 0.072
}
NEGRISK_BASKET_MIN_GROSS_BPS = 800     # 8pp gross
NEGRISK_PER_LEG_MIN_BPS = 200          # 2pp per leg
NEGRISK_SURCHARGE_BPS = 204            # PM-documented multi-leg conversion
FX_GBP_USD_DRAG_BPS = 70               # ~1σ on 1-week hold
UK_CGT_DRAG_BPS = 200                  # 24% CGT on USDC disposals applied to net


# Markets to exclude entirely until UMA dispute history is reviewed.
# Adversarial agent flagged these as systematic loss vectors.
BLACKLIST_KEYWORDS = [
    "war ", "invades", "military strike", "drone strike",
    "election fraud", "stolen election",
    "hostage", "coup ", "regime change",
    "nuclear weapon", "biological weapon",
]


def edge_after_fees(edge_bps_gross: float, taker_fee_bps: float = 200.0,
                    maker_rebate_bps: float = 0.0,
                    *, is_two_legged: bool = False,
                    is_negrisk_basket: bool = False,
                    apply_fx: bool = True,
                    apply_cgt: bool = False) -> float:
    """Net edge after the full UK cost stack.

    Args:
        is_two_legged: round-trip (entry + exit) doubles taker fee
        is_negrisk_basket: subtract 2.04% conversion surcharge
        apply_fx: subtract 70bps FX drag on 1-week hold (default True for
                  any USD-denominated edge realised by a GBP-base operator)
        apply_cgt: subtract 200bps CGT drag (only if conservatively assuming
                  HMRC classifies as CGT — operator should leave default OFF
                  for paper-mode edge assessment, set ON for after-tax target)
    """
    fee_legs = 2 if is_two_legged else 1
    net = edge_bps_gross - taker_fee_bps * fee_legs + maker_rebate_bps
    if is_negrisk_basket:
        net -= NEGRISK_SURCHARGE_BPS
    if apply_fx:
        net -= FX_GBP_USD_DRAG_BPS
    if apply_cgt and net > 0:
        net -= UK_CGT_DRAG_BPS
    return net


def is_blacklisted(market: dict | None) -> bool:
    """Geopolitical/war/fraud markets banned per red-team findings."""
    if not market:
        return False
    text = ((market.get("question") or "") + " " +
            (market.get("description") or "") + " " +
            (market.get("groupItemTitle") or "")).lower()
    return any(kw in text for kw in BLACKLIST_KEYWORDS)


def market_category(market: dict | None) -> str:
    """Best-effort category extraction for fee-tier mapping."""
    if not market:
        return "politics"
    cat = (market.get("category") or "").lower()
    if cat:
        for known in MIN_EDGE_BPS_BY_CATEGORY:
            if known in cat:
                return known
    # Fall back to keyword heuristics on tags / question
    txt = ((market.get("question") or "") + " " +
           " ".join(market.get("tags") or [])).lower()
    if any(k in txt for k in ("bitcoin", "btc", "eth", "crypto", "ether")):
        return "crypto"
    if any(k in txt for k in ("nfl", "nba", "mlb", "nhl", "soccer", "tennis", "ufc", "mma")):
        return "sports"
    if any(k in txt for k in ("wti", "oil", "gold", "copper", "natural gas", "gas")):
        return "finance"
    if any(k in txt for k in ("fed", "rate", "cpi", "nfp", "gdp", "ecb", "boe")):
        return "finance"
    if any(k in txt for k in ("temperature", "snowfall", "hurricane", "weather")):
        return "weather"
    if any(k in txt for k in ("eurovision", "oscar", "emmy", "grammy", "awards",
                              "movie", "film", "song", "album")):
        return "culture"
    if any(k in txt for k in ("ukraine", "russia", "iran", "israel", "china",
                              "war", "ceasefire", "treaty")):
        return "geopolitical"
    return "politics"


def min_edge_for_category(market: dict) -> float:
    return MIN_EDGE_BPS_BY_CATEGORY.get(market_category(market), 550.0)


def should_skip(quote: Quote, oi: dict | None, edge_bps_val: float,
                params: FilterParams = FilterParams()) -> tuple[bool, str]:
    """Return (skip, reason). Reason is human-readable for logging."""
    if not quote.has_book:
        return True, "no book"
    if quote.spread_bps is None:
        return True, "no spread"
    if quote.spread_bps > params.max_spread_bps:
        return True, f"spread {quote.spread_bps:.0f}bps > {params.max_spread_bps:.0f}"
    if abs(edge_bps_val) > 0 and quote.spread_bps > params.max_spread_vs_edge * abs(edge_bps_val):
        return True, f"spread {quote.spread_bps:.0f}bps eats edge {abs(edge_bps_val):.0f}bps"
    # OI is optional — many markets don't expose it. Only enforce if available.
    if oi and oi.get("open_interest") is not None:
        if oi["open_interest"] < params.min_oi_usd:
            return True, f"OI ${oi['open_interest']:,.0f} < ${params.min_oi_usd:,.0f}"
    # Stale book
    if quote.timestamp:
        try:
            ts_ms = float(quote.timestamp)
            age = time.time() - (ts_ms / 1000.0)
            if age > params.max_book_age_seconds:
                return True, f"book stale {age:.0f}s"
        except (TypeError, ValueError):
            pass
    # Book depth
    if quote.ask_size is not None and quote.ask_size < params.min_book_size_shares:
        return True, f"thin book ({quote.ask_size:.0f} shares on ask)"
    return False, ""


# ============================================================================
# Confidence + attempt scoring
# ============================================================================

def score_confidence(spread_bps: Optional[float], oi_usd: Optional[float],
                     model_verified: bool = False) -> str:
    """HIGH / MED / LOW based on liquidity + model status."""
    sp = spread_bps if spread_bps is not None else 9999
    oi = oi_usd if oi_usd is not None else 0
    if sp < 100 and oi > 1_000_000 and model_verified:
        return "HIGH"
    if sp < 300 and oi > 100_000:
        return "MED"
    return "LOW"


def attempt_score(edge_bps_val: float, spread_bps: Optional[float],
                  oi_usd: Optional[float]) -> float:
    """Single number ranking edges by execution viability.
    Higher = better.

      score = (|edge| / max(spread, 1)) * sqrt(oi / 500k)
    """
    sp = max(spread_bps or 100.0, 1.0)
    oi = max(oi_usd or 50_000.0, 1.0)
    return (abs(edge_bps_val) / sp) * math.sqrt(oi / 500_000.0)


def attempt_label(score: float) -> str:
    if score > 5: return "STRONG"
    if score > 2: return "GOOD"
    if score > 1: return "MARGINAL"
    return "SKIP"


# ============================================================================
# Standardised row + formatting
# ============================================================================

@dataclass
class EdgeRow:
    """Common shape every validator produces."""
    validator: str
    market: str           # human-readable description
    market_id: Optional[str] = None
    yes_token: Optional[str] = None
    pm_yes: Optional[float] = None      # market mid or executable price (0-1)
    fair: Optional[float] = None        # model probability (0-1)
    edge_bps: Optional[float] = None    # gross edge
    edge_bps_net: Optional[float] = None  # after taker fee
    taker_fee_bps: Optional[float] = None
    action: str = "-"
    spread_bps: Optional[float] = None
    oi_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    confidence: str = "LOW"
    attempt: float = 0.0
    attempt_label: str = "SKIP"
    note: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def format_table(rows: list[EdgeRow], title: str = "") -> str:
    """Render rows as fixed-width markdown-ish table."""
    if not rows:
        return f"{title}\n  (no rows)\n"
    out: list[str] = []
    if title:
        out.append(title)
    hdr = (f"{'Market':<40} {'PM%':>6} {'Fair%':>6} {'Edge':>7} "
           f"{'Spr':>5} {'OI$':>9} {'Conf':>4} {'Att':>5}  Action")
    out.append(hdr)
    out.append("-" * len(hdr))
    for r in rows:
        m = (r.market or "")[:40]
        pm = f"{r.pm_yes*100:>5.1f}%" if r.pm_yes is not None else "  n/a"
        fa = f"{r.fair*100:>5.1f}%" if r.fair is not None else "  n/a"
        ed = f"{(r.edge_bps or 0)/100:>+5.1f}pp" if r.edge_bps is not None else "  n/a"
        sp = f"{r.spread_bps:>4.0f}bp" if r.spread_bps is not None else " n/a"
        oi = f"${r.oi_usd:>8,.0f}" if r.oi_usd else "    n/a "
        att = f"{r.attempt:>4.1f}"
        out.append(f"{m:<40} {pm:>6} {fa:>6} {ed:>7} {sp:>5} {oi:>9} "
                   f"{r.confidence:>4} {att:>5}  {r.action}")
    return "\n".join(out) + "\n"


# ============================================================================
# Standard end-to-end pipeline for a single market
# ============================================================================

def evaluate_market(market: dict, fair: float,
                    side: str = "buy",
                    threshold_bps: float = 200,
                    validator_name: str = "?",
                    market_label: Optional[str] = None,
                    params: FilterParams = FilterParams()) -> EdgeRow:
    """Full pipeline: parse market, fetch book + OI, compute edge + filter.

    Args:
        market: Polymarket Gamma market dict (must have clobTokenIds, id)
        fair: Model fair-value probability for YES outcome (0-1)
        side: 'buy' if you'd buy YES on edge, 'sell' if you'd sell YES.
              Used to pick executable price (ask vs bid).
        threshold_bps: Edge threshold for action classification.
        validator_name: Name tag for the resulting row.
        market_label: Override question text shown in output.
        params: FilterParams for execution-viability gating.

    Returns: EdgeRow (potentially with skipped=True).
    """
    yes_tok, _no_tok = parse_clob_token_ids(market)
    label = market_label or market.get("question", "?")
    mid = market.get("id")
    quote = get_quote(yes_tok) if yes_tok else Quote(token_id="")
    oi = fetch_oi(mid) if mid else None

    # Use executable side; fall back to mid then to outcomePrices snapshot
    pm = quote.executable(side)
    if pm is None:
        pm = quote.mid
    if pm is None:
        # Last-resort fallback to stale snapshot
        raw = market.get("outcomePrices")
        if isinstance(raw, str):
            try:
                pm = float(json.loads(raw)[0])
            except Exception:
                pm = None
        elif isinstance(raw, (list, tuple)) and raw:
            try:
                pm = float(raw[0])
            except Exception:
                pm = None

    row = EdgeRow(
        validator=validator_name,
        market=label,
        market_id=str(mid) if mid else None,
        yes_token=yes_tok,
        pm_yes=pm,
        fair=fair,
        spread_bps=quote.spread_bps,
        oi_usd=(oi or {}).get("open_interest"),
        volume_24h_usd=(oi or {}).get("volume_24h"),
    )

    if pm is None or fair is None:
        row.skipped = True
        row.skip_reason = "no price"
        return row

    # Geopolitical / war / fraud blacklist (red-team flag #4)
    if is_blacklisted(market):
        row.skipped = True
        row.skip_reason = "blacklisted (geopolitical/war/fraud) — UMA risk"
        row.edge_bps = 0
        return row

    action, eb = edge_action(fair, pm, threshold_bps)
    row.edge_bps = eb
    row.action = action

    # Net edge after the full UK cost stack (fee + FX), excluding CGT
    # (CGT applied to realised P&L, not signal evaluation).
    taker, rebate = fees_for_market(market)
    row.taker_fee_bps = taker
    eb_abs = abs(eb)
    net_abs = edge_after_fees(eb_abs, taker, rebate, is_two_legged=True,
                              apply_fx=True, apply_cgt=False)
    row.edge_bps_net = net_abs if eb >= 0 else -net_abs

    skip, reason = should_skip(quote, oi, eb, params)
    if not skip:
        # Per-category minimum-edge gate (red-team findings #1).
        min_bps = min_edge_for_category(market)
        if eb_abs < min_bps:
            skip = True
            reason = (f"gross edge {eb_abs:.0f}bps < category min "
                      f"{min_bps:.0f}bps ({market_category(market)})")
        elif net_abs < threshold_bps / 2.0:
            skip = True
            reason = (f"net edge {net_abs:.0f}bps < threshold/2 after "
                      f"taker {taker:.0f}bps + FX {FX_GBP_USD_DRAG_BPS}bps")
    if skip:
        row.skipped = True
        row.skip_reason = reason

    row.confidence = score_confidence(quote.spread_bps, row.oi_usd)
    row.attempt = attempt_score(eb, quote.spread_bps, row.oi_usd)
    row.attempt_label = attempt_label(row.attempt)
    return row


# ============================================================================
# Multi-validator merge + ranking
# ============================================================================

def rank_edges(rows: list[EdgeRow], drop_skipped: bool = False,
               min_threshold_bps: float = 200) -> list[EdgeRow]:
    """Filter + sort by attempt score (descending)."""
    out = [r for r in rows if r.edge_bps is not None
           and abs(r.edge_bps) >= min_threshold_bps]
    if drop_skipped:
        out = [r for r in out if not r.skipped]
    out.sort(key=lambda r: -r.attempt)
    return out


# ============================================================================
# Self-test
# ============================================================================

def _selftest() -> None:
    print("validator_core selftest")
    # Probe a known liquid market via Gamma -> CLOB
    ev = gamma_event("what-price-will-bitcoin-hit-in-may-2026")
    if not ev:
        print("  ! could not fetch event"); return
    mks = ev.get("markets", [])[:2]
    for m in mks:
        yes, no = parse_clob_token_ids(m)
        q = get_quote(yes) if yes else Quote(token_id="")
        print(f"  {m.get('question','?')[:60]:<60}  bid={q.bid} ask={q.ask} "
              f"spread_bps={q.spread_bps}")


if __name__ == "__main__":
    _selftest()
