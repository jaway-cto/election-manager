"""
Smarkets Exchange client — UK-legal, UKGC-licensed, sharp-friendly.

Why Smarkets is the recommended primary venue for a UK systematic operator:
  * Flat 2% commission, 1% Pro tier
  * NO premium/expert charge regardless of profit
  * Welcomes winners explicitly (vs Betfair's Expert Fee at £25k+ profit)
  * Modern OAuth2 API, official Python SDK at smarkets/smk_python_sdk
  * REST + WebSocket streaming
  * Lower min stake than Betfair (£0.05 vs £2)
  * Gambling-exempt for UK individual tax (HMRC SAIM2080)

Auth modes:
  * App key (read-only public data) — no creds needed
  * OAuth2 password grant — requires SMARKETS_USERNAME, SMARKETS_PASSWORD,
    optional SMARKETS_API_KEY for higher rate limits

This client uses the documented v3 REST endpoints. Order placement requires
a logged-in session.

Usage:
    from venues.smarkets_client import SmarketsClient
    sm = SmarketsClient.from_env()
    if sm.login():
        contracts = sm.list_contracts(market_id="23982189")
        sm.place_order(contract_id=..., side="buy", price_pct=42.5,
                       quantity_pence=1000)  # £10 stake
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

import requests

API_BASE = "https://api.smarkets.com"
UA = {"User-Agent": "odds-smarkets-client/1.0 (r.ingham@live.co.uk)"}


@dataclass
class SmarketsCreds:
    username: str = ""
    password: str = ""
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "SmarketsCreds":
        return cls(
            username=os.environ.get("SMARKETS_USERNAME", ""),
            password=os.environ.get("SMARKETS_PASSWORD", ""),
            api_key=os.environ.get("SMARKETS_API_KEY", ""),
        )

    def ready(self) -> bool:
        return bool(self.username and self.password)


class SmarketsClient:
    def __init__(self, creds: Optional[SmarketsCreds] = None):
        self.creds = creds or SmarketsCreds.from_env()
        self.session_token: Optional[str] = None
        self.session_id: Optional[str] = None
        self._session = requests.Session()
        self._session.headers.update(UA)

    @classmethod
    def from_env(cls) -> "SmarketsClient":
        return cls(SmarketsCreds.from_env())

    @property
    def authed(self) -> bool:
        return bool(self.session_token)

    # --------- read-only: works without auth ---------

    def list_events(self, type_domain: str = "politics",
                    limit: int = 100) -> list[dict]:
        """E.g. type_domain='politics' or 'sport/football'."""
        try:
            r = self._session.get(
                f"{API_BASE}/v3/events/",
                params={"type_domain": type_domain, "limit": limit},
                timeout=15,
            )
            r.raise_for_status()
            return (r.json() or {}).get("events", [])
        except Exception as e:
            sys.stderr.write(f"[smarkets] events fetch: {e}\n")
            return []

    def list_markets(self, event_id: str) -> list[dict]:
        try:
            r = self._session.get(f"{API_BASE}/v3/events/{event_id}/markets/",
                                  timeout=15)
            r.raise_for_status()
            return (r.json() or {}).get("markets", [])
        except Exception as e:
            sys.stderr.write(f"[smarkets] markets {event_id}: {e}\n")
            return []

    def list_contracts(self, market_id: str) -> list[dict]:
        try:
            r = self._session.get(
                f"{API_BASE}/v3/markets/{market_id}/contracts/", timeout=15)
            r.raise_for_status()
            return (r.json() or {}).get("contracts", [])
        except Exception as e:
            sys.stderr.write(f"[smarkets] contracts {market_id}: {e}\n")
            return []

    def get_quotes(self, market_id: str) -> dict:
        """Returns {contract_id: {bids: [...], offers: [...]}} with prices in
        basis points (10000 = 100%).
        """
        try:
            r = self._session.get(
                f"{API_BASE}/v3/markets/{market_id}/quotes/", timeout=15)
            r.raise_for_status()
            return r.json() or {}
        except Exception as e:
            sys.stderr.write(f"[smarkets] quotes {market_id}: {e}\n")
            return {}

    # --------- auth + trading ---------

    def login(self) -> bool:
        """OAuth2 password grant. Returns True if session_token obtained."""
        if not self.creds.ready():
            return False
        try:
            body = {
                "username": self.creds.username,
                "password": self.creds.password,
            }
            headers = {"Content-Type": "application/json"}
            if self.creds.api_key:
                headers["x-api-key"] = self.creds.api_key
            r = self._session.post(
                f"{API_BASE}/v3/sessions/", data=json.dumps(body),
                headers=headers, timeout=20,
            )
            if r.status_code == 200 or r.status_code == 201:
                js = r.json()
                self.session_token = js.get("token")
                self.session_id = js.get("id")
                if self.session_token:
                    self._session.headers["Authorization"] = (
                        f"Session-Token {self.session_token}")
                    return True
            sys.stderr.write(f"[smarkets] login HTTP {r.status_code}: "
                             f"{r.text[:200]}\n")
            return False
        except Exception as e:
            sys.stderr.write(f"[smarkets] login error: {e}\n")
            return False

    def logout(self) -> bool:
        if not self.session_id:
            return False
        try:
            r = self._session.delete(
                f"{API_BASE}/v3/sessions/{self.session_id}/", timeout=10)
            self.session_token = None
            self.session_id = None
            self._session.headers.pop("Authorization", None)
            return r.status_code in (200, 204)
        except Exception:
            return False

    def get_balance(self) -> Optional[dict]:
        if not self.authed:
            raise RuntimeError("login() first")
        try:
            r = self._session.get(f"{API_BASE}/v3/accounts/", timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            sys.stderr.write(f"[smarkets] balance: {e}\n")
            return None

    def list_my_orders(self, state: str = "live") -> list[dict]:
        if not self.authed:
            raise RuntimeError("login() first")
        try:
            r = self._session.get(
                f"{API_BASE}/v3/orders/", params={"state": state}, timeout=15)
            r.raise_for_status()
            return (r.json() or {}).get("orders", [])
        except Exception as e:
            sys.stderr.write(f"[smarkets] orders: {e}\n")
            return []

    def place_order(self, contract_id: str, side: str,
                    price_pct: float, quantity_pence: int,
                    *, type_: str = "limit") -> Optional[dict]:
        """Place a limit order.

        Args:
            contract_id: Smarkets contract id
            side: 'buy' (back) or 'sell' (lay)
            price_pct: 0..100, how Smarkets web shows odds
                       (e.g. 42.5 = 42.5% implied probability)
            quantity_pence: stake in pence (£10 = 1000)
            type_: 'limit' (default) or 'immediate-or-cancel'
        Returns: order dict or None on failure
        """
        if not self.authed:
            raise RuntimeError("login() first")
        # Smarkets internal price uses basis points (10000 = 100%).
        price_bp = int(round(price_pct * 100))
        body = {
            "contract_id": contract_id,
            "type": type_, "side": side,
            "price": price_bp, "quantity": quantity_pence,
        }
        try:
            r = self._session.post(
                f"{API_BASE}/v3/orders/", data=json.dumps(body),
                headers={"Content-Type": "application/json"}, timeout=20,
            )
            if r.status_code in (200, 201):
                return r.json()
            sys.stderr.write(f"[smarkets] place_order HTTP {r.status_code}: "
                             f"{r.text[:300]}\n")
            return None
        except Exception as e:
            sys.stderr.write(f"[smarkets] place_order error: {e}\n")
            return None

    def cancel_order(self, order_id: str) -> bool:
        if not self.authed:
            raise RuntimeError("login() first")
        try:
            r = self._session.delete(
                f"{API_BASE}/v3/orders/{order_id}/", timeout=10)
            return r.status_code in (200, 204)
        except Exception as e:
            sys.stderr.write(f"[smarkets] cancel: {e}\n")
            return False

    def cancel_all(self) -> int:
        """Panic button — cancel every live order. Returns count cancelled."""
        if not self.authed:
            raise RuntimeError("login() first")
        orders = self.list_my_orders("live")
        n = 0
        for o in orders:
            if self.cancel_order(o.get("id", "")):
                n += 1
        return n


if __name__ == "__main__":
    sm = SmarketsClient.from_env()
    print(f"creds ready: {sm.creds.ready()}")
    if not sm.creds.ready():
        print("set SMARKETS_USERNAME + SMARKETS_PASSWORD env vars to enable trading")
        # Public read works without creds
        evs = sm.list_events("politics", limit=5)
        print(f"\npolitics events ({len(evs)} returned):")
        for e in evs[:5]:
            print(f"  {e.get('id'):>12}  {e.get('name')}")
    else:
        if sm.login():
            print(f"authed; session {sm.session_id}")
            bal = sm.get_balance()
            print(f"balance: {bal}")
            sm.logout()
        else:
            print("login failed")
