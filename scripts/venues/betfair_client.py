"""
Betfair Exchange client.

Two modes:
  * Authenticated (BETFAIR_APP_KEY + BETFAIR_USERNAME + BETFAIR_PASSWORD)
    — full Exchange Betting API access (listMarketBook, listMarketCatalogue).
    Free to register: https://developer.betfair.com/. Approval ~3 business days.
  * Public navigation (no auth) — limited but free. Uses the cached navigation
    JSON the website itself uses for sport/event/market discovery, then
    scrapes the per-market page for live prices.

For our cross-venue scanner we generally want the authenticated path. Without
keys this module surfaces the structure + an explicit "needs auth" error so
the rest of the pipeline can degrade gracefully.

Usage:
    from venues.betfair_client import BetfairClient
    bf = BetfairClient.from_env()
    if bf.authed:
        markets = bf.list_market_catalogue(filter={"eventTypeIds": ["1"]})
        book = bf.list_market_book(market_ids=["1.234"])
"""
from __future__ import annotations
import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

import requests


BF_LOGIN = "https://identitysso-cert.betfair.com/api/certlogin"
BF_LOGIN_NONCERT = "https://identitysso.betfair.com/api/login"
BF_KEEPALIVE = "https://identitysso.betfair.com/api/keepAlive"
BF_BETTING = "https://api.betfair.com/exchange/betting/json-rpc/v1"
BF_NAV_PUBLIC = "https://www.betfair.com/exchange/plus/api/cache/v1/navigation"

UA = {"User-Agent": "Mozilla/5.0 (odds-betfair-client; r.ingham@live.co.uk)"}


@dataclass
class BetfairCreds:
    app_key: str = ""
    username: str = ""
    password: str = ""

    @classmethod
    def from_env(cls) -> "BetfairCreds":
        return cls(
            app_key=os.environ.get("BETFAIR_APP_KEY", ""),
            username=os.environ.get("BETFAIR_USERNAME", ""),
            password=os.environ.get("BETFAIR_PASSWORD", ""),
        )

    def ready(self) -> bool:
        return bool(self.app_key and self.username and self.password)


class BetfairClient:
    def __init__(self, creds: Optional[BetfairCreds] = None):
        self.creds = creds or BetfairCreds.from_env()
        self.session_token: Optional[str] = None

    @classmethod
    def from_env(cls) -> "BetfairClient":
        return cls(BetfairCreds.from_env())

    @property
    def authed(self) -> bool:
        return bool(self.session_token)

    # --------------- auth ---------------

    def login(self) -> bool:
        """Login via identitysso (no client cert). Stores session token."""
        if not self.creds.ready():
            return False
        try:
            r = requests.post(
                BF_LOGIN_NONCERT,
                data={"username": self.creds.username,
                      "password": self.creds.password},
                headers={**UA, "X-Application": self.creds.app_key,
                         "Accept": "application/json"},
                timeout=20,
            )
            js = r.json()
            if js.get("status") == "SUCCESS":
                self.session_token = js["token"]
                return True
            sys.stderr.write(f"[betfair] login failed: {js}\n")
            return False
        except Exception as e:
            sys.stderr.write(f"[betfair] login error: {e}\n")
            return False

    def keepalive(self) -> bool:
        if not self.session_token:
            return False
        try:
            r = requests.post(
                BF_KEEPALIVE,
                headers={**UA, "X-Application": self.creds.app_key,
                         "X-Authentication": self.session_token,
                         "Accept": "application/json"},
                timeout=15,
            )
            return r.json().get("status") == "SUCCESS"
        except Exception:
            return False

    # --------------- betting RPC (authed) ---------------

    def _rpc(self, method: str, params: dict) -> dict:
        if not self.authed:
            raise RuntimeError("Betfair not authenticated; call login() first")
        body = [{"jsonrpc": "2.0", "method": f"SportsAPING/v1.0/{method}",
                 "params": params, "id": 1}]
        r = requests.post(
            BF_BETTING,
            data=json.dumps(body),
            headers={**UA,
                     "X-Application": self.creds.app_key,
                     "X-Authentication": self.session_token,
                     "Content-Type": "application/json",
                     "Accept": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        out = r.json()
        if isinstance(out, list):
            out = out[0]
        if "error" in out:
            raise RuntimeError(f"betfair rpc error: {out['error']}")
        return out.get("result", {})

    def list_event_types(self) -> list[dict]:
        return self._rpc("listEventTypes", {"filter": {}})

    def list_events(self, event_type_ids: list[str]) -> list[dict]:
        return self._rpc("listEvents",
                         {"filter": {"eventTypeIds": event_type_ids}})

    def list_market_catalogue(self, filter_: dict, max_results: int = 50,
                              market_projection: Optional[list[str]] = None) -> list[dict]:
        return self._rpc("listMarketCatalogue", {
            "filter": filter_, "maxResults": max_results,
            "marketProjection": market_projection or
                ["EVENT", "MARKET_DESCRIPTION", "RUNNER_DESCRIPTION"],
        })

    def list_market_book(self, market_ids: list[str]) -> list[dict]:
        """Best-prices snapshot for multiple market IDs."""
        return self._rpc("listMarketBook", {
            "marketIds": market_ids,
            "priceProjection": {
                "priceData": ["EX_BEST_OFFERS"],
                "exBestOffersOverrides": {"bestPricesDepth": 1},
            },
        })

    # --------------- public navigation (no auth) ---------------

    def public_navigation(self) -> dict:
        """The website's own JSON for sport/event tree. Public, free, no auth.
        Returns the top-level navigation tree; consumers must traverse to
        markets of interest. This does NOT include live prices.
        """
        try:
            r = requests.get(BF_NAV_PUBLIC, headers=UA, timeout=20)
            r.raise_for_status()
            return r.json() or {}
        except Exception as e:
            sys.stderr.write(f"[betfair] nav fetch failed: {e}\n")
            return {}

    # --------------- helpers ---------------

    def best_lay_back(self, market: dict, runner_name: str) -> Optional[tuple[float, float]]:
        """From a listMarketBook response item, find runner by id and return
        (best_back_price, best_lay_price). best_back is the price you can
        BACK at (highest), best_lay is the price you can LAY at (lowest).
        """
        for r in market.get("runners", []):
            if r.get("metadata", {}).get("runnerName") != runner_name:
                continue
            ex = r.get("ex", {})
            backs = ex.get("availableToBack") or []
            lays = ex.get("availableToLay") or []
            back_price = max((p["price"] for p in backs), default=None)
            lay_price = min((p["price"] for p in lays), default=None)
            return back_price, lay_price
        return None


if __name__ == "__main__":
    bf = BetfairClient.from_env()
    print(f"app_key set: {bool(bf.creds.app_key)}")
    print(f"creds ready: {bf.creds.ready()}")
    if bf.creds.ready():
        if bf.login():
            print(f"authed; session token: {bf.session_token[:20]}...")
            ets = bf.list_event_types()
            for et in ets[:6]:
                print(f"  {et['eventType']['id']:>4}  {et['eventType']['name']}  "
                      f"({et.get('marketCount')} markets)")
        else:
            print("login failed")
    else:
        print("\nTo enable Betfair access:")
        print("  1. Register a free app key at https://developer.betfair.com/")
        print("  2. setx BETFAIR_APP_KEY \"...\"")
        print("  3. setx BETFAIR_USERNAME \"...\"")
        print("  4. setx BETFAIR_PASSWORD \"...\"")
        print("\nProbing public navigation (no auth needed):")
        nav = bf.public_navigation()
        if nav:
            children = nav.get("children", [])
            print(f"  top-level nav: {len(children)} sport categories")
            for c in children[:8]:
                print(f"    - {c.get('name')}  ({c.get('type')})")
