"""
IG Index client — FCA-regulated spread-betting.

Why IG matters for the UK arbitrage stack:
  * FCA-regulated, full UK consumer protection
  * CGT-EXEMPT on every spread-bet (HMRC BIM22015)
  * Retail-accessible without FCA-pro classification on:
    - Equity events (single stocks, indices)
    - FX (any pair)
    - UK political seat / turnout spreads
    - Oil + natural gas + commodities (non-crypto)
    - Bond yields, gilt spreads
  * NOT accessible retail (pro-only): crypto-derivative spread-bets

This is the missing execution layer for "PM signal → IG hedge" trades:
  * EIA petroleum status surprise → IG WTI spread-bet
  * Fed-rate-decision PM divergence → IG short-sterling spread-bet
  * "MSFT > $X" PM market → IG MSFT spread-bet
  * "USDJPY > 160" PM market → IG USDJPY spread-bet

Auth model (3-step):
  1. POST /session with username + password + api_key in body
  2. Response includes CST + X-SECURITY-TOKEN as headers
  3. Subsequent calls require CST + X-SECURITY-TOKEN + x-ig-api-key
  4. Tokens valid 6 hours; refresh via POST /session/refresh-token

Two environments:
  * Demo: https://demo-api.ig.com/gateway/deal — totally free, no funding
  * Production: https://api.ig.com/gateway/deal — needs funded live account

Env vars:
  IG_USERNAME, IG_PASSWORD, IG_API_KEY, IG_ENV (default 'demo')
  IG_ACCOUNT_TYPE (default 'SPREADBET' — alternative 'CFD' for CGT-applies-mode)
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

import requests

ENVIRONMENTS = {
    "demo": "https://demo-api.ig.com/gateway/deal",
    "live": "https://api.ig.com/gateway/deal",
}

UA = {"User-Agent": "odds-ig-client/1.0 (r.ingham@live.co.uk)"}


@dataclass
class IGCreds:
    username: str = ""
    password: str = ""
    api_key: str = ""
    env: str = "demo"               # 'demo' or 'live'
    account_type: str = "SPREADBET"  # 'SPREADBET' (CGT-exempt) or 'CFD'

    @classmethod
    def from_env(cls) -> "IGCreds":
        return cls(
            username=os.environ.get("IG_USERNAME", ""),
            password=os.environ.get("IG_PASSWORD", ""),
            api_key=os.environ.get("IG_API_KEY", ""),
            env=os.environ.get("IG_ENV", "demo"),
            account_type=os.environ.get("IG_ACCOUNT_TYPE", "SPREADBET"),
        )

    def ready(self) -> bool:
        return bool(self.username and self.password and self.api_key)


class IGClient:
    def __init__(self, creds: Optional[IGCreds] = None):
        self.creds = creds or IGCreds.from_env()
        self.cst: Optional[str] = None
        self.security_token: Optional[str] = None
        self.account_id: Optional[str] = None
        self._login_at: Optional[float] = None
        self._session = requests.Session()
        self._session.headers.update(UA)

    @classmethod
    def from_env(cls) -> "IGClient":
        return cls(IGCreds.from_env())

    @property
    def base_url(self) -> str:
        return ENVIRONMENTS[self.creds.env]

    @property
    def authed(self) -> bool:
        if not (self.cst and self.security_token):
            return False
        # IG tokens valid 6 hours — refresh proactively at 5 hours
        if self._login_at and time.time() - self._login_at > 5 * 3600:
            return False
        return True

    def _auth_headers(self, version: int = 2) -> dict:
        if not self.authed:
            raise RuntimeError("not authed; call login() first")
        return {
            "X-IG-API-KEY": self.creds.api_key,
            "CST": self.cst or "",
            "X-SECURITY-TOKEN": self.security_token or "",
            "Version": str(version),
            "Content-Type": "application/json",
            "Accept": "application/json; charset=UTF-8",
        }

    # --------- auth ---------

    def login(self) -> bool:
        if not self.creds.ready():
            return False
        body = {
            "identifier": self.creds.username,
            "password": self.creds.password,
        }
        try:
            r = self._session.post(
                f"{self.base_url}/session", data=json.dumps(body),
                headers={
                    "X-IG-API-KEY": self.creds.api_key,
                    "Version": "2",
                    "Content-Type": "application/json",
                    "Accept": "application/json; charset=UTF-8",
                },
                timeout=20,
            )
            if r.status_code != 200:
                sys.stderr.write(f"[ig] login HTTP {r.status_code}: "
                                 f"{r.text[:300]}\n")
                return False
            self.cst = r.headers.get("CST")
            self.security_token = r.headers.get("X-SECURITY-TOKEN")
            js = r.json()
            self.account_id = js.get("currentAccountId")
            self._login_at = time.time()
            return True
        except Exception as e:
            sys.stderr.write(f"[ig] login error: {e}\n")
            return False

    def logout(self) -> bool:
        if not self.authed:
            return True
        try:
            r = self._session.delete(f"{self.base_url}/session",
                                     headers=self._auth_headers(version=1),
                                     timeout=10)
            self.cst = None; self.security_token = None
            self._login_at = None
            return r.status_code in (200, 204)
        except Exception:
            return False

    def refresh_session(self) -> bool:
        """Bump the session window before tokens expire."""
        if not self.creds.ready():
            return False
        return self.login()

    # --------- account info ---------

    def get_accounts(self) -> Optional[list[dict]]:
        try:
            r = self._session.get(f"{self.base_url}/accounts",
                                  headers=self._auth_headers(version=1),
                                  timeout=15)
            r.raise_for_status()
            return (r.json() or {}).get("accounts", [])
        except Exception as e:
            sys.stderr.write(f"[ig] accounts: {e}\n")
            return None

    def get_balance(self) -> Optional[dict]:
        accs = self.get_accounts()
        if not accs:
            return None
        for a in accs:
            # Match either active account or the one matching our IG_ACCOUNT_TYPE
            if a.get("accountType") == self.creds.account_type:
                return a
        return accs[0]

    # --------- market discovery ---------

    def search_markets(self, query: str) -> list[dict]:
        try:
            r = self._session.get(f"{self.base_url}/markets",
                                  params={"searchTerm": query},
                                  headers=self._auth_headers(version=1),
                                  timeout=15)
            r.raise_for_status()
            return (r.json() or {}).get("markets", [])
        except Exception as e:
            sys.stderr.write(f"[ig] search_markets: {e}\n")
            return []

    def get_market(self, epic: str) -> Optional[dict]:
        try:
            r = self._session.get(f"{self.base_url}/markets/{epic}",
                                  headers=self._auth_headers(version=3),
                                  timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            sys.stderr.write(f"[ig] get_market {epic}: {e}\n")
            return None

    def market_navigation(self, node_id: Optional[str] = None) -> Optional[dict]:
        path = "/marketnavigation" + (f"/{node_id}" if node_id else "")
        try:
            r = self._session.get(f"{self.base_url}{path}",
                                  headers=self._auth_headers(version=1),
                                  timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            sys.stderr.write(f"[ig] market_navigation: {e}\n")
            return None

    # --------- positions + orders ---------

    def list_positions(self) -> list[dict]:
        try:
            r = self._session.get(f"{self.base_url}/positions",
                                  headers=self._auth_headers(version=2),
                                  timeout=15)
            r.raise_for_status()
            return (r.json() or {}).get("positions", [])
        except Exception as e:
            sys.stderr.write(f"[ig] list_positions: {e}\n")
            return []

    def place_order(self, epic: str, direction: str, size: float,
                    *, order_type: str = "MARKET",
                    level: Optional[float] = None,
                    currency_code: str = "GBP",
                    force_open: bool = True,
                    stop_level: Optional[float] = None,
                    limit_level: Optional[float] = None,
                    guaranteed_stop: bool = False,
                    expiry: str = "DFB") -> Optional[str]:
        """Place a deal. direction: 'BUY' (back / long) or 'SELL' (short / lay).

        Returns deal_reference (string) on success — caller polls /confirms/{ref}
        for fill confirmation.

        size: in IG units (£ per point). For e.g. UK shares, 1 = £1/point;
        for FX cable, 1 = £1/pip (£100 notional per pip), etc. Convert from
        £-stake using market metadata.

        expiry: 'DFB' (Daily Funded Bet, rolling for spread-bet on indices/FX),
        or specific contract month for futures-style bets.
        """
        body = {
            "epic": epic, "expiry": expiry,
            "direction": direction.upper(),
            "size": str(size),
            "orderType": order_type,
            "currencyCode": currency_code,
            "forceOpen": force_open,
            "guaranteedStop": guaranteed_stop,
        }
        if order_type == "LIMIT" and level is not None:
            body["level"] = str(level)
        if stop_level is not None:
            body["stopLevel"] = str(stop_level)
        if limit_level is not None:
            body["limitLevel"] = str(limit_level)
        try:
            r = self._session.post(f"{self.base_url}/positions/otc",
                                   data=json.dumps(body),
                                   headers=self._auth_headers(version=2),
                                   timeout=20)
            if r.status_code != 200:
                sys.stderr.write(f"[ig] place_order HTTP {r.status_code}: "
                                 f"{r.text[:300]}\n")
                return None
            return (r.json() or {}).get("dealReference")
        except Exception as e:
            sys.stderr.write(f"[ig] place_order error: {e}\n")
            return None

    def confirm_deal(self, deal_reference: str) -> Optional[dict]:
        """After place_order, poll for actual fill state."""
        try:
            r = self._session.get(f"{self.base_url}/confirms/{deal_reference}",
                                  headers=self._auth_headers(version=1),
                                  timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            sys.stderr.write(f"[ig] confirm: {e}\n")
            return None

    def close_position(self, deal_id: str, direction: str, size: float,
                       order_type: str = "MARKET",
                       level: Optional[float] = None) -> Optional[str]:
        """Close an open position. direction is opposite of original."""
        body = {
            "dealId": deal_id,
            "direction": direction.upper(),
            "size": str(size), "orderType": order_type,
        }
        if level is not None:
            body["level"] = str(level)
        try:
            r = self._session.delete(f"{self.base_url}/positions/otc",
                                     data=json.dumps(body),
                                     headers={**self._auth_headers(version=1),
                                              "_method": "DELETE"},
                                     timeout=20)
            if r.status_code != 200:
                sys.stderr.write(f"[ig] close HTTP {r.status_code}: "
                                 f"{r.text[:300]}\n")
                return None
            return (r.json() or {}).get("dealReference")
        except Exception as e:
            sys.stderr.write(f"[ig] close error: {e}\n")
            return None


# ============================================================================
# Helper: convert £-stake to IG size
# ============================================================================

def stake_gbp_to_ig_size(stake_gbp: float, market_meta: dict,
                         current_price: float) -> float:
    """Approximate £-stake → IG size (size is £/point for spread-bets).

    For a £100 notional position at price 5000:
      pip = 1 (most equities) → size = 100/5000 = 0.02 £/point
    For FX where pip = 0.0001:
      size = 100 / (5000 * 0.0001) = 200 £/pip equivalent

    market_meta should come from get_market(epic). Falls back to size = 1
    when metadata is incomplete.
    """
    instrument = (market_meta or {}).get("instrument", {})
    value_per_point = float(instrument.get("valueOfOnePip") or 0)
    if value_per_point > 0:
        return round(stake_gbp / value_per_point, 2)
    # Fallback: assume 1 unit of stake = £stake/price
    if current_price > 0:
        return round(stake_gbp / current_price, 2)
    return 1.0


# ============================================================================
# Self-test
# ============================================================================

if __name__ == "__main__":
    ig = IGClient.from_env()
    print(f"creds ready: {ig.creds.ready()}, env: {ig.creds.env}")
    if not ig.creds.ready():
        print("\nTo enable: register at https://labs.ig.com/, get API key, then:")
        print("  setx IG_USERNAME \"...\"")
        print("  setx IG_PASSWORD \"...\"")
        print("  setx IG_API_KEY  \"...\"")
        print("  setx IG_ENV \"demo\"  # or \"live\" once tested")
        sys.exit(0)
    if not ig.login():
        print("login failed"); sys.exit(1)
    print(f"authed; account: {ig.account_id}")
    bal = ig.get_balance()
    if bal:
        print(f"  balance: £{bal.get('balance')}, "
              f"available: £{bal.get('available')}, "
              f"type: {bal.get('accountType')}")
    # Sample search
    results = ig.search_markets("USD/JPY")
    print(f"\nUSDJPY search: {len(results)} markets")
    for m in results[:3]:
        print(f"  {m.get('epic'):<30}  {m.get('instrumentName')}  "
              f"bid {m.get('bid')}  ask {m.get('offer')}")
    ig.logout()
