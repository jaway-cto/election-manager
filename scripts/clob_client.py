"""
clob_client.py — Polymarket CLOB client wrapper.

Read-side: works without auth, uses validator_core helpers.
Trade-side: requires L1 (private key) + L2 (HMAC API key set) credentials.
            We do NOT implement order signing here directly — that's complex
            and best done via the official py-clob-client. We import it
            lazily so the rest of the project works without it installed.

To trade:
    pip install py-clob-client web3
    setx PM_PRIVATE_KEY      "0x..."           # your funded wallet
    setx PM_API_KEY          "..."             # from POST /auth/api-key
    setx PM_API_SECRET       "..."
    setx PM_API_PASSPHRASE   "..."
    setx PM_PROXY_ADDRESS    "0x..."           # your Polymarket proxy
    setx PM_TRADING_ENABLED  "1"               # belt+suspenders gate

Usage (read-only):
    from clob_client import client
    book = client.book(token_id)
    mid  = client.midpoint(token_id)

Usage (trading):
    client.require_trading()                       # raises if not enabled
    order = client.create_order(token_id, side="BUY", size=10, price=0.40)
    resp  = client.post_order(order)               # or client.post_market_order(...)
"""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass
from typing import Optional

from validator_core import (
    fetch_clob_book, fetch_clob_midpoint, fetch_clob_spread, fetch_oi,
    get_quote, gamma_event, gamma_search,
)

import killswitch


@dataclass
class Credentials:
    private_key: str = ""
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    proxy_address: str = ""
    chain_id: int = 137  # Polygon mainnet
    trading_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Credentials":
        return cls(
            private_key=os.environ.get("PM_PRIVATE_KEY", ""),
            api_key=os.environ.get("PM_API_KEY", ""),
            api_secret=os.environ.get("PM_API_SECRET", ""),
            api_passphrase=os.environ.get("PM_API_PASSPHRASE", ""),
            proxy_address=os.environ.get("PM_PROXY_ADDRESS", ""),
            trading_enabled=os.environ.get("PM_TRADING_ENABLED") == "1",
        )

    def ready(self) -> bool:
        return bool(self.private_key and self.api_key and
                    self.api_secret and self.api_passphrase and
                    self.proxy_address and self.trading_enabled)


class CLOBClient:
    def __init__(self):
        self.creds = Credentials.from_env()
        self._signed = None  # lazy

    # ----- read side (always available) -----

    def book(self, token_id: str) -> Optional[dict]:
        return fetch_clob_book(token_id)

    def midpoint(self, token_id: str) -> Optional[float]:
        return fetch_clob_midpoint(token_id)

    def spread(self, token_id: str) -> Optional[float]:
        return fetch_clob_spread(token_id)

    def quote(self, token_id: str):
        return get_quote(token_id)

    def oi(self, market_id: str) -> Optional[dict]:
        return fetch_oi(market_id)

    def event(self, slug: str):
        return gamma_event(slug)

    def search(self, query: str, limit: int = 10):
        return gamma_search(query, limit)

    # ----- trade side (requires creds + opt-in) -----

    def require_trading(self) -> None:
        killswitch.assert_armed()
        if not self.creds.ready():
            raise RuntimeError(
                "Trading not configured. Set PM_PRIVATE_KEY, PM_API_KEY, "
                "PM_API_SECRET, PM_API_PASSPHRASE, PM_PROXY_ADDRESS and "
                "PM_TRADING_ENABLED=1, then reinstantiate CLOBClient."
            )

    def _signed_client(self):
        if self._signed is not None:
            return self._signed
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except ImportError as e:
            raise RuntimeError(
                "py-clob-client not installed. Run: pip install py-clob-client"
            ) from e
        creds = ApiCreds(
            api_key=self.creds.api_key,
            api_secret=self.creds.api_secret,
            api_passphrase=self.creds.api_passphrase,
        )
        self._signed = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=self.creds.chain_id,
            key=self.creds.private_key,
            creds=creds,
            funder=self.creds.proxy_address,
            signature_type=2,  # gnosis-safe proxy (Polymarket default)
        )
        return self._signed

    def create_order(self, token_id: str, side: str, size: float,
                     price: float, *, order_type: str = "GTC") -> dict:
        """Build (but do not post) a signed order.

        side: 'BUY' or 'SELL'
        size: in shares
        price: 0..1
        order_type: 'GTC' | 'GTD' | 'FOK' | 'FAK'
        """
        self.require_trading()
        from py_clob_client.clob_types import OrderArgs
        client = self._signed_client()
        args = OrderArgs(price=price, size=size,
                         side=side, token_id=token_id)
        return client.create_order(args)

    def post_order(self, order, order_type: str = "GTC") -> dict:
        """Post a previously created order. Final gate before going live."""
        self.require_trading()
        client = self._signed_client()
        return client.post_order(order, order_type)

    def cancel_order(self, order_id: str) -> dict:
        self.require_trading()
        return self._signed_client().cancel(order_id=order_id)

    def cancel_all(self) -> dict:
        """Panic button — cancels every resting order on the account."""
        self.require_trading()
        return self._signed_client().cancel_all()


# Module-level singleton — most callers want this.
client = CLOBClient()


if __name__ == "__main__":
    print(f"trading_enabled: {client.creds.trading_enabled}")
    print(f"creds ready:     {client.creds.ready()}")
    print(f"killswitch:      {'TRIPPED' if killswitch.tripped() else 'armed'}")
    if len(sys.argv) > 1 and sys.argv[1] == "test-read":
        ev = client.event("what-price-will-bitcoin-hit-in-may-2026")
        if ev:
            print(f"event: {ev.get('title')}  ({len(ev.get('markets', []))} markets)")
