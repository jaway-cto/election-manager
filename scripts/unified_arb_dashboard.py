"""
unified_arb_dashboard.py — Run every validator in parallel, consolidate edges
across crypto/macro/wti/sports/eurovision/french-pres, rank by attempt score.

Output:
  * Console: ranked actionable edges
  * Markdown file (default DASHBOARD.md): for human review

Usage:
    python unified_arb_dashboard.py
    python unified_arb_dashboard.py --watch 300
    python unified_arb_dashboard.py --out C:/Dev/odds/docs/DASHBOARD.md
"""
from __future__ import annotations
import argparse
import concurrent.futures as futures
import datetime as dt
import io
import sys
import time
import traceback
from contextlib import redirect_stdout
from typing import Callable

from validator_core import EdgeRow, format_table, rank_edges
from backtest_validator import install_capture_hook

# Auto-capture every event() call to data/signals.jsonl for backtesting
install_capture_hook()


# Each entry: (label, callable returning list[EdgeRow])
def _safe_call(fn) -> list[EdgeRow]:
    """Run a scan; capture/discard stdout; return rows or [] on error."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            res = fn() or []
        return [r for r in res if isinstance(r, EdgeRow)]
    except Exception as e:
        sys.stderr.write(f"[{fn.__module__}] {e}\n")
        traceback.print_exc(file=sys.stderr)
        return []


def _load_validators() -> list[tuple[str, Callable]]:
    out: list[tuple[str, Callable]] = []
    try:
        from crypto_validator import scan_crypto_markets
        out.append(("crypto", scan_crypto_markets))
    except Exception as e:
        sys.stderr.write(f"crypto_validator import failed: {e}\n")
    try:
        from wti_validator import scan_wti_markets
        out.append(("wti", scan_wti_markets))
    except Exception as e:
        sys.stderr.write(f"wti_validator import failed: {e}\n")
    try:
        from macro_validator import scan_macro
        out.append(("macro", scan_macro))
    except Exception as e:
        sys.stderr.write(f"macro_validator import failed: {e}\n")
    try:
        from sports_validator import scan_sport
        import os
        if os.environ.get("THE_ODDS_API_KEY"):
            out.append(("sports-nba", lambda: scan_sport("basketball_nba")))
    except Exception as e:
        sys.stderr.write(f"sports_validator import failed: {e}\n")
    try:
        from tail_decay_scanner import scan as scan_tail_decay
        from validator_core import EdgeRow
        def _tail_as_edges() -> list:
            rows = scan_tail_decay(max_days=7, min_ask=0.92, max_ask=0.995)
            out_rows = []
            for r in rows:
                out_rows.append(EdgeRow(
                    validator="tail-decay",
                    market=("[PAST] " if r["past_deadline"] else "") +
                           (r["question"] or "")[:50],
                    market_id=str(r["market_id"]) if r["market_id"] else None,
                    yes_token=r["token"],
                    pm_yes=r["ask"], fair=1.0,
                    edge_bps=(1.0 - r["ask"]) * 10000,
                    action=f"BUY {r['side_label']} @ {r['ask']:.3f}",
                    spread_bps=r["spread_bps"],
                    oi_usd=r["volume_24h"],
                    note="past-deadline" if r["past_deadline"] else "",
                ))
            return out_rows
        out.append(("tail-decay", _tail_as_edges))
    except Exception as e:
        sys.stderr.write(f"tail_decay_scanner import failed: {e}\n")
    return out


def run_all() -> dict[str, list[EdgeRow]]:
    """Run all validators in parallel via threads (I/O-bound)."""
    validators = _load_validators()
    out: dict[str, list[EdgeRow]] = {}
    with futures.ThreadPoolExecutor(max_workers=len(validators) or 1) as pool:
        future_to_name = {pool.submit(_safe_call, fn): name
                          for name, fn in validators}
        for fut in futures.as_completed(future_to_name):
            name = future_to_name[fut]
            try:
                out[name] = fut.result()
            except Exception as e:
                sys.stderr.write(f"{name} crashed: {e}\n")
                out[name] = []
    return out


def _emit_signals(results: dict[str, list[EdgeRow]],
                  min_bps: float = 200) -> int:
    """Emit notify.event() for every actionable EdgeRow so the backtest
    capture hook writes them to signals.jsonl. Returns count emitted."""
    from notify import event as log_event
    n = 0
    for validator_name, rows in results.items():
        for r in rows:
            if r.edge_bps is None or abs(r.edge_bps) < min_bps:
                continue
            if r.skipped:
                continue
            log_event(f"{validator_name.replace('-', '_')}.signal", {
                "validator": validator_name,
                "market_id": r.market_id,
                "yes_token": r.yes_token,
                "side": "BUY" if (r.edge_bps or 0) > 0 else "SELL",
                "ask": r.pm_yes,
                "fair": r.fair,
                "edge_pp": (r.edge_bps or 0) / 100,
                "edge_bps_net": r.edge_bps_net,
                "spread_bps": r.spread_bps,
                "oi_usd": r.oi_usd,
                "market": r.market[:80],
                "action": r.action,
            })
            n += 1
    return n


def render_dashboard(results: dict[str, list[EdgeRow]],
                     min_bps: float = 200) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# Polymarket Arbitrage Dashboard\n")
    lines.append(f"_Generated {now}_\n")

    # Flatten
    all_rows: list[EdgeRow] = []
    for rows in results.values():
        all_rows.extend(rows)

    # Top actionable (post-filter)
    actionable = [r for r in all_rows if r.edge_bps is not None
                  and abs(r.edge_bps) >= min_bps and not r.skipped]
    actionable.sort(key=lambda r: -r.attempt)

    lines.append(f"## Top actionable edges  ({len(actionable)} found)\n")
    if actionable:
        lines.append("```")
        lines.append(format_table(actionable[:30], title=""))
        lines.append("```\n")
    else:
        lines.append("_No edges survived spread + OI filtering._\n")

    # Per-validator detail
    for name in sorted(results.keys()):
        rows = results[name]
        ranked = rank_edges(rows, drop_skipped=False, min_threshold_bps=min_bps)
        lines.append(f"## {name}  ({len(rows)} markets, {len(ranked)} flagged)\n")
        if ranked:
            lines.append("```")
            lines.append(format_table(ranked[:20]))
            lines.append("```\n")
        skipped = [r for r in rows if r.skipped]
        if skipped:
            lines.append(f"<details><summary>{len(skipped)} skipped (would-be edges, "
                         "unexecutable)</summary>\n\n```")
            for r in skipped[:10]:
                lines.append(f"  {r.market[:55]:<55}  edge={(r.edge_bps or 0)/100:+5.1f}pp  "
                             f"reason: {r.skip_reason}")
            lines.append("```\n</details>\n")

    # Summary stats
    total_markets = sum(len(rs) for rs in results.values())
    total_flagged = len(actionable)
    lines.append(f"## Summary\n")
    lines.append(f"- Validators run: {len(results)}")
    lines.append(f"- Markets scanned: {total_markets}")
    lines.append(f"- Actionable edges (>={min_bps/100:.0f}pp, post-filter): {total_flagged}\n")

    return "\n".join(lines)


def print_console_summary(results: dict[str, list[EdgeRow]],
                          min_bps: float = 200) -> None:
    actionable: list[EdgeRow] = []
    for rows in results.values():
        actionable.extend(r for r in rows if r.edge_bps is not None
                          and abs(r.edge_bps) >= min_bps and not r.skipped)
    actionable.sort(key=lambda r: -r.attempt)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'=' * 90}")
    print(f"POLYMARKET ARBITRAGE DASHBOARD  {now}")
    print(f"{'=' * 90}")
    print(f"Validators run: {len(results)}  | "
          f"Total markets: {sum(len(rs) for rs in results.values())}  | "
          f"Actionable edges: {len(actionable)}\n")
    if actionable:
        print(format_table(actionable[:25], title="Top edges (ranked by attempt score):"))
    else:
        print("No edges survived spread + OI filtering this run.\n")
    print("Per-validator counts:")
    for name in sorted(results.keys()):
        rows = results[name]
        flag = sum(1 for r in rows if r.edge_bps is not None
                   and abs(r.edge_bps) >= min_bps and not r.skipped)
        skip = sum(1 for r in rows if r.skipped)
        print(f"  {name:<14}  {len(rows):>4} markets   "
              f"{flag:>3} actionable   {skip:>3} skipped")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="C:/Dev/odds/docs/DASHBOARD.md",
                    help="Markdown output path")
    ap.add_argument("--watch", type=int, default=0,
                    help="Re-run every N seconds (0 = once)")
    ap.add_argument("--min-bps", type=float, default=200,
                    help="Edge threshold in bps (default 200 = 2pp)")
    args = ap.parse_args()
    while True:
        results = run_all()
        n_emitted = _emit_signals(results, args.min_bps)
        if n_emitted:
            sys.stderr.write(f"captured {n_emitted} actionable signals "
                             f"to signals.jsonl\n")
        print_console_summary(results, args.min_bps)
        try:
            md = render_dashboard(results, args.min_bps)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"\nDashboard written to {args.out}")
        except Exception as e:
            sys.stderr.write(f"failed to write dashboard: {e}\n")
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
