"""
market_classifier.py — Understand what a Polymarket market actually IS
before treating it as a signal.

Bug this fixes:
  Tail-decay scanner saw "endDate < now AND bestAsk = 0.96" and flagged
  "tail-decay opportunity". Reality on inspection: the market was
  - one of 9 brackets in a calendar ladder ("Trump visits China by ..."),
  - with successor brackets (May 15, May 31, June 30) STILL LIVE,
  - and the 0.96 ask was the dying YES *minutes before UMA resolved NO*.
  Buying at 0.96 would have been -100%, not +4pp.

Three classification dimensions:
  1. SHAPE — what kind of contract is this?
       STANDALONE_BINARY, CALENDAR_LADDER_LEG, THRESHOLD_LADDER_LEG,
       NEGRISK_OUTCOME, AUGMENTED_PLACEHOLDER
  2. STATUS — what state is it in right now?
       ACTIVE, AWAITING_PROPOSAL, RESOLVED_YES, RESOLVED_NO,
       IN_DISPUTE, NO_LIQUIDITY, ZOMBIE (stale endDate, archived in
       all but name)
  3. STRUCTURE — relationships to siblings
       successor (live future leg), predecessor (resolved past leg)

Public API:
    classify(market, event) -> MarketClassification
    safe_for_tail_decay(market, event) -> (bool, reason)
    safe_for_negrisk_basket(market, event) -> (bool, reason)
    effective_deadline(market) -> datetime  # parsed from question text
"""
from __future__ import annotations
import datetime as dt
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Shape(str, Enum):
    STANDALONE_BINARY = "standalone_binary"
    CALENDAR_LADDER_LEG = "calendar_ladder_leg"
    THRESHOLD_LADDER_LEG = "threshold_ladder_leg"
    NEGRISK_OUTCOME = "negrisk_outcome"
    AUGMENTED_PLACEHOLDER = "augmented_placeholder"
    UNKNOWN = "unknown"


class Status(str, Enum):
    ACTIVE = "active"                    # tradeable, deadline in the future
    AWAITING_PROPOSAL = "awaiting_proposal"  # past deadline, no UMA proposal yet
    IN_DISPUTE = "in_dispute"            # UMA proposal disputed
    RESOLVED_YES = "resolved_yes"        # umaStatus=resolved, paid out YES
    RESOLVED_NO = "resolved_no"          # umaStatus=resolved, paid out NO
    NO_LIQUIDITY = "no_liquidity"        # no real book (bestBid=0, bestAsk=1)
    ZOMBIE = "zombie"                    # archived/closed but stuck in metadata
    UNKNOWN = "unknown"


@dataclass
class MarketClassification:
    shape: Shape
    status: Status
    effective_deadline: Optional[dt.datetime]  # parsed from question, NOT endDate
    siblings: list[dict]          # other markets in same event
    live_successors: list[dict]   # later-deadline siblings still active
    resolved_predecessors: list[dict]  # earlier-deadline siblings already resolved
    rationale: str


# ============================================================================
# Deadline extraction — parse question text, ignore endDate (often stale)
# ============================================================================

MONTH_RE = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
            r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|"
            r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")

# "by Dec 31, 2025" / "by May 15" / "before March 31, 2026" / "by EOY 2026"
DEADLINE_RE = re.compile(
    r"\b(?:by|before|until|on)\s+"
    r"(?:" + MONTH_RE + r")\s+(\d{1,2})"
    r"(?:,?\s+(\d{4}))?",
    re.I,
)
# "by May 15, 2026, 11:59 PM ET" — same pattern, year captured
# Also: "by 2027" / "by end of 2026"
YEAR_ONLY_RE = re.compile(r"\b(?:by|before|until)\s+(?:end of\s+)?(\d{4})\b", re.I)
# groupItemTitle like "May 15" or "March 31, 2026"
GROUPITEM_RE = re.compile(
    r"^\s*(?:" + MONTH_RE + r")\s+(\d{1,2})(?:,?\s+(\d{4}))?\s*$", re.I)

MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_month(s: str) -> Optional[int]:
    s = s.lower()
    for k, v in MONTH_NUM.items():
        if s.startswith(k):
            return v
    return None


def effective_deadline(market: dict) -> Optional[dt.datetime]:
    """Parse deadline from groupItemTitle / question. Falls back to endDate.

    Polymarket's `endDate` field on calendar-ladder legs is often stale —
    set to the original event start, not the leg's actual deadline.
    Always prefer the human-readable date in question/groupItemTitle.
    """
    today = dt.datetime.now(dt.timezone.utc)
    candidates = []
    # 1. groupItemTitle is most reliable for ladders
    git = market.get("groupItemTitle") or ""
    m = GROUPITEM_RE.match(git)
    if m:
        day, year = m.groups()
        # Find which month was matched
        month = None
        for k, v in MONTH_NUM.items():
            if k in git.lower():
                month = v; break
        if month:
            yr = int(year) if year else today.year
            try:
                candidates.append(dt.datetime(yr, month, int(day),
                                              23, 59, tzinfo=dt.timezone.utc))
            except ValueError:
                pass
    # 2. Question text "by Month Day, Year"
    q = market.get("question") or ""
    for mm in DEADLINE_RE.finditer(q):
        day, year = mm.groups()
        # Find month substring
        month_match = re.search(MONTH_RE, mm.group(0), re.I)
        if not month_match:
            continue
        month = _parse_month(month_match.group(0))
        if not month:
            continue
        yr = int(year) if year else today.year
        try:
            candidates.append(dt.datetime(yr, month, int(day),
                                          23, 59, tzinfo=dt.timezone.utc))
        except ValueError:
            pass
    # 3. Year-only "by 2027"
    if not candidates:
        m = YEAR_ONLY_RE.search(q)
        if m:
            try:
                candidates.append(dt.datetime(int(m.group(1)), 12, 31,
                                              23, 59, tzinfo=dt.timezone.utc))
            except ValueError:
                pass
    # 4. Fallback to endDate
    if not candidates:
        end = market.get("endDate") or market.get("endDateIso")
        if end:
            try:
                candidates.append(dt.datetime.fromisoformat(
                    end.replace("Z", "+00:00")))
            except Exception:
                pass
    # Prefer the deadline closest to (but not in the distant past of) today.
    # If multiple, pick the latest non-past one; if all past, pick the most
    # recent (closest to today).
    if not candidates:
        return None
    future = [c for c in candidates if c >= today]
    if future:
        return min(future)  # nearest future
    return max(candidates)


# ============================================================================
# Shape classification
# ============================================================================

def _siblings_in_event(market: dict, event: Optional[dict]) -> list[dict]:
    if not event:
        return []
    out = []
    mid = str(market.get("id", ""))
    for m in event.get("markets") or []:
        if str(m.get("id", "")) != mid:
            out.append(m)
    return out


def _is_calendar_ladder(market: dict, siblings: list[dict]) -> bool:
    """Calendar ladder = multiple sibling markets whose groupItemTitles
    are dates (i.e. the market is keyed by deadline within a shared question).
    """
    if len(siblings) < 1:
        return False
    git = (market.get("groupItemTitle") or "").strip()
    if not GROUPITEM_RE.match(git):
        return False
    date_siblings = sum(
        1 for s in siblings
        if GROUPITEM_RE.match((s.get("groupItemTitle") or "").strip())
    )
    return date_siblings >= 1


def _is_threshold_ladder(market: dict, siblings: list[dict]) -> bool:
    """Threshold ladder = sibling markets whose groupItemTitles are
    monetary/numeric thresholds within a shared underlying question.
    Pattern: "$75,000" / "$80,000" / "70k" / "above 100" etc.
    """
    if len(siblings) < 2:
        return False
    git = (market.get("groupItemTitle") or "").strip()
    threshold_re = re.compile(r"^\s*\$?[\d,]+(?:\.\d+)?\s*[kKmM]?\s*$")
    if not threshold_re.match(git):
        return False
    threshold_siblings = sum(
        1 for s in siblings
        if threshold_re.match((s.get("groupItemTitle") or "").strip())
    )
    return threshold_siblings >= 2


def _is_augmented_placeholder(market: dict) -> bool:
    git = (market.get("groupItemTitle") or "").lower()
    q = (market.get("question") or "").lower()
    placeholder_terms = ("another candidate", "any other", "other ",
                         "person ", "candidate ")
    return any(t in git or t in q for t in placeholder_terms)


def classify_shape(market: dict, event: Optional[dict] = None) -> Shape:
    if _is_augmented_placeholder(market):
        return Shape.AUGMENTED_PLACEHOLDER
    siblings = _siblings_in_event(market, event)
    if event and event.get("negRisk") and len(siblings) >= 1:
        # In a negRisk event, every market is one of the mutually-exclusive
        # outcomes — unless it's a calendar leg (some negRisk events ARE
        # calendar ladders e.g. "Will X happen by..."). Calendar wins.
        if _is_calendar_ladder(market, siblings):
            return Shape.CALENDAR_LADDER_LEG
        return Shape.NEGRISK_OUTCOME
    if _is_calendar_ladder(market, siblings):
        return Shape.CALENDAR_LADDER_LEG
    if _is_threshold_ladder(market, siblings):
        return Shape.THRESHOLD_LADDER_LEG
    if not siblings:
        return Shape.STANDALONE_BINARY
    return Shape.UNKNOWN


# ============================================================================
# Status classification
# ============================================================================

def classify_status(market: dict) -> Status:
    uma = (market.get("umaResolutionStatus") or "").lower()
    if uma == "resolved":
        # Determine YES vs NO from outcomePrices [yes, no]
        op = market.get("outcomePrices")
        if isinstance(op, str):
            try:
                import json
                op = json.loads(op)
            except Exception:
                op = []
        if op and len(op) >= 2:
            try:
                if float(op[0]) >= 0.99:
                    return Status.RESOLVED_YES
                if float(op[1]) >= 0.99:
                    return Status.RESOLVED_NO
            except (TypeError, ValueError):
                pass
        return Status.RESOLVED_YES if "yes" in uma else Status.RESOLVED_NO
    if "dispute" in uma or "challeng" in uma:
        return Status.IN_DISPUTE
    if uma in ("proposed", "awaiting_settlement"):
        return Status.AWAITING_PROPOSAL
    # No UMA status — check book
    bb = market.get("bestBid")
    ba = market.get("bestAsk")
    try:
        bb_f = float(bb) if bb is not None else 0.0
        ba_f = float(ba) if ba is not None else 1.0
    except (TypeError, ValueError):
        bb_f, ba_f = 0.0, 1.0
    # Pure 0/1 book = no liquidity (placeholder slot)
    if bb_f <= 0.001 and ba_f >= 0.999:
        return Status.NO_LIQUIDITY
    # Closed but no UMA resolution? It's stuck/zombie.
    if market.get("closed") and not market.get("archived"):
        return Status.ZOMBIE
    if market.get("archived"):
        return Status.ZOMBIE
    return Status.ACTIVE


# ============================================================================
# Full classification
# ============================================================================

def classify(market: dict, event: Optional[dict] = None) -> MarketClassification:
    """Top-level: classify a market with full event context."""
    siblings = _siblings_in_event(market, event)
    today = dt.datetime.now(dt.timezone.utc)
    my_deadline = effective_deadline(market)

    # Live successors = siblings whose deadline is later AND status is ACTIVE
    live_successors: list[dict] = []
    resolved_predecessors: list[dict] = []
    for s in siblings:
        s_dl = effective_deadline(s)
        s_status = classify_status(s)
        if not s_dl or not my_deadline:
            continue
        if s_dl > my_deadline and s_status == Status.ACTIVE:
            live_successors.append(s)
        if s_dl < my_deadline and s_status in (Status.RESOLVED_YES, Status.RESOLVED_NO):
            resolved_predecessors.append(s)

    shape = classify_shape(market, event)
    status = classify_status(market)
    rationale = (f"shape={shape.value} status={status.value} "
                 f"deadline={my_deadline.isoformat()[:10] if my_deadline else 'unknown'} "
                 f"siblings={len(siblings)} "
                 f"live_successors={len(live_successors)} "
                 f"resolved_predecessors={len(resolved_predecessors)}")
    return MarketClassification(
        shape=shape, status=status, effective_deadline=my_deadline,
        siblings=siblings, live_successors=live_successors,
        resolved_predecessors=resolved_predecessors, rationale=rationale,
    )


# ============================================================================
# Decision helpers — every scanner uses these
# ============================================================================

def safe_for_tail_decay(market: dict, event: Optional[dict] = None
                        ) -> tuple[bool, str]:
    """A market is safe to flag as tail-decay (buy near $1, redeem at $1) iff:
      * Not a placeholder
      * Not already resolved (would be redemption sweep, different play)
      * Not in dispute (capital lock risk)
      * Not part of a calendar ladder with live successor brackets (the
        "Trump visits China by Mar 31" trap — you'd be buying a leg about
        to die while traders pile into the May/June legs)
      * Has actual liquidity
      * Effective deadline (parsed from question, not endDate field) is
        genuinely past, not just the field reporting an old date
    """
    c = classify(market, event)
    if c.shape == Shape.AUGMENTED_PLACEHOLDER:
        return False, "augmented placeholder, no real outcome"
    if c.shape == Shape.NEGRISK_OUTCOME:
        return False, ("negRisk multi-outcome event — only one candidate "
                       "wins; tail-decay assumption invalid. Edge here is "
                       "fundamental analysis, not tail-decay.")
    if c.shape == Shape.THRESHOLD_LADDER_LEG:
        return False, ("threshold ladder leg — price reflects barrier-touch "
                       "probability, not tail-decay.")
    if c.status in (Status.RESOLVED_YES, Status.RESOLVED_NO):
        return False, f"already {c.status.value}"
    if c.status == Status.IN_DISPUTE:
        return False, "in UMA dispute — capital lock risk"
    if c.status == Status.NO_LIQUIDITY:
        return False, "no real book (placeholder slot)"
    if c.status == Status.ZOMBIE:
        return False, "zombie market (closed but stuck)"
    if c.live_successors:
        successor_titles = ", ".join(
            (s.get("groupItemTitle") or "?")[:20] for s in c.live_successors[:3])
        return False, (f"calendar ladder leg with live successor "
                       f"brackets [{successor_titles}] — true edge is in "
                       f"the spread, not this leg")
    if c.effective_deadline is None:
        return False, "no parseable deadline"
    today = dt.datetime.now(dt.timezone.utc)
    if c.effective_deadline > today:
        return False, ("deadline is in the future — not tail-decay, "
                       f"deadline={c.effective_deadline.isoformat()[:10]}")
    return True, "ok"


def safe_for_negrisk_basket(market: dict, event: Optional[dict] = None
                            ) -> tuple[bool, str]:
    """For negRisk basket arbs, the basket sum must include EVERY leg of the
    event including augmenter/Other slots. We also need every leg to be
    ACTIVE — if any leg is resolved or in dispute, the basket math breaks.
    """
    if not event or not event.get("negRisk"):
        return False, "not a negRisk event"
    statuses = [classify_status(m) for m in (event.get("markets") or [])]
    if any(s in (Status.RESOLVED_YES, Status.RESOLVED_NO) for s in statuses):
        return False, "event has at least one resolved leg"
    if any(s == Status.IN_DISPUTE for s in statuses):
        return False, "event has a leg in dispute"
    return True, "ok"


# ============================================================================
# Self-test
# ============================================================================

def _selftest():
    from validator_core import gamma_event
    print("Trump-China event diagnostic:")
    ev = gamma_event("will-trump-visit-china-by")
    if not ev:
        print("  could not fetch event")
        return
    for m in ev.get("markets", []):
        c = classify(m, ev)
        ok, why = safe_for_tail_decay(m, ev)
        flag = "FLAG" if ok else "skip"
        print(f"  [{flag:>4}] {m.get('groupItemTitle','?'):>22}  "
              f"{c.shape.value:>22}  {c.status.value:>17}  "
              f"successors={len(c.live_successors)}  ({why})")


if __name__ == "__main__":
    _selftest()
