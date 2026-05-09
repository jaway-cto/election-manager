"""
pollers/daemon.py — single async process running every poller on its own
schedule. Survives any individual poller crashing.

Schedules (per Stage 2b validation):
  * SCOTUS  — 30s during 09:55-11:00 ET on opinion days, 600s otherwise
  * NHC     — 600s (advisories every 3-6 hrs, intermediate updates between)
  * FDA     — 120s (RSS feed updates within 1-10 min of issuer filing)

Add new pollers in POLLERS below.

Usage:
    python -m pollers.daemon
    python -m pollers.daemon --once   # one cycle, exit
"""
from __future__ import annotations
import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notify import fyi
import killswitch
from pollers import scotus, nhc, fda


def scotus_cadence() -> int:
    """30s during SCOTUS opinion windows; 600s otherwise."""
    now = dt.datetime.now(dt.timezone.utc)
    et = now - dt.timedelta(hours=4)  # rough EDT; close enough for cadence
    # Mon/Thu in May-Jul tend to be opinion days
    if et.weekday() in (0, 3) and 5 <= et.month <= 7:
        if 13 <= et.hour < 16:  # 9 ET window
            return 30
    return 600


POLLERS = [
    ("scotus", lambda: scotus.scan(term=25), scotus_cadence),
    ("nhc",    lambda: nhc.scan(),           lambda: 600),
    ("fda",    lambda: fda.scan(),           lambda: 120),
]


async def run_poller(name: str, fn, cadence_fn):
    fyi(f"daemon: started {name}")
    while True:
        if killswitch.tripped():
            fyi(f"daemon: {name} halted ({killswitch.reason()})")
            return
        try:
            # Run synchronous scan in a worker thread so we don't block loop
            await asyncio.to_thread(fn)
        except Exception as e:
            sys.stderr.write(f"[{name}] crashed: {e}\n")
            import traceback; traceback.print_exc()
        await asyncio.sleep(max(10, cadence_fn()))


async def main_async(once: bool) -> None:
    if once:
        for name, fn, _ in POLLERS:
            try:
                await asyncio.to_thread(fn)
            except Exception as e:
                sys.stderr.write(f"[{name}] {e}\n")
        return
    fyi("pollers/daemon: starting all pollers")
    await asyncio.gather(
        *(run_poller(name, fn, cadence) for name, fn, cadence in POLLERS),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="Run one cycle of each poller and exit")
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args.once))
    except KeyboardInterrupt:
        fyi("pollers/daemon: stopped by user")


if __name__ == "__main__":
    main()
