"""
Yield Maker — SOMI:USDso vault-funded PostOnly with Avellaneda-Stoikov-style
reservation price.

Fix for gap #3: this version actually tracks placed quotes. The strategy
assigns `_our_bid`/`_our_ask` immediately when it emits a PLACE signal, so
subsequent ticks know we already have resting quotes and only requote when
they drift beyond threshold. `on_fill` and `on_reject` clear the tracking so
the next tick re-quotes that side.

Reservation price (Avellaneda & Stoikov 2008):
    r = s − q · γ · σ² · (T−t)
where s=mid, q=inventory delta from target (normalized), γ=risk aversion,
σ²=variance, (T−t)=remaining time. We use a simplified version that drops the
(T−t) term (constant = 1 in tight loops) and adds an empirical floor on
half-spread.

Pattern adapted from Polymarket/poly-market-maker (MIT) Bands strategy.
"""

from __future__ import annotations

import statistics
import time
import uuid
from collections import deque
from decimal import Decimal
from typing import Any

from dreamdex_bot.config import MARKETS, MarketSymbol
from dreamdex_bot.interfaces.strategy import (
    CancelIntent, FundingSource, MarketState, OrderIntent, OrderType, OwnInventory,
    Side, SignalAction, TradingSignal, TradingStrategy,
)
from dreamdex_bot.utils.logger import get_logger
from dreamdex_bot.utils.markets import ensure_min_quantity, round_to_lot, round_to_tick


log = get_logger(__name__)


class YieldMaker(TradingStrategy):
    """PostOnly quoting on SOMI:USDso with inventory-skewed reservation price."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(name="yield_maker", config=config)
        self.market = MarketSymbol.SOMI_USDSO

        self.target_base_value_usd = Decimal(str(config.get("target_base_value_usd", "12.50")))
        self.quote_size_usd = Decimal(str(config.get("quote_size_usd", "2.00")))
        self.min_half_spread_bps = int(config.get("min_half_spread_bps", 25))
        self.gamma = float(config.get("gamma", 0.5))
        self.k_vol = float(config.get("k_vol", 2.0))
        self.requote_threshold_bps = int(config.get("requote_threshold_bps", 5))
        self.requote_min_interval_sec = float(config.get("requote_min_interval_sec", 3.0))
        reserve_by_market = config.get("native_base_reserve_by_market", {})
        self.native_base_reserve = Decimal(str(
            reserve_by_market.get(self.market.value, config.get("native_base_reserve", "0"))
        ))

        # Quote tracking: set when we emit PLACE, cleared on fill/reject/cancel.
        # Each is None when no resting order is known, otherwise:
        # {"coid": str, "price": Decimal, "qty": Decimal, "placed_at": float}
        self._our_bid: dict[str, Any] | None = None
        self._our_ask: dict[str, Any] | None = None
        self._last_requote_ts: float = 0.0

        self._mid_window: deque[Decimal] = deque(maxlen=int(config.get("vol_window", 60)))

    async def generate_signals(
        self,
        market_state: dict[MarketSymbol, MarketState],
        inventory: dict[MarketSymbol, OwnInventory],
    ) -> list[TradingSignal]:
        ms = market_state.get(self.market)
        inv = inventory.get(self.market)
        if ms is None or inv is None or ms.mid is None:
            return []

        self._mid_window.append(ms.mid)

        # Reservation price
        sigma = self._realized_vol()
        q_delta_usd = (self._inventory_base_balance(inv) * ms.mid) - self.target_base_value_usd
        q_normalized = float(q_delta_usd / self.target_base_value_usd) if self.target_base_value_usd > 0 else 0.0
        reservation_shift = q_normalized * self.gamma * (sigma ** 2) * float(ms.mid)
        reservation_price = float(ms.mid) - reservation_shift

        # Half-spread
        min_half = float(ms.mid) * self.min_half_spread_bps / 10_000
        vol_half = self.k_vol * sigma * float(ms.mid)
        half_spread = max(min_half, vol_half)

        bid_price = round_to_tick(
            Decimal(str(reservation_price - half_spread)), self.market, direction="down",
        )
        ask_price = round_to_tick(
            Decimal(str(reservation_price + half_spread)), self.market, direction="up",
        )

        # Don't requote if too recent
        if time.time() - self._last_requote_ts < self.requote_min_interval_sec:
            return []

        signals: list[TradingSignal] = []

        # Sizes — guard against zero/negative price
        if bid_price <= 0 or ask_price <= 0:
            return []
        bid_qty = round_to_lot(self.quote_size_usd / bid_price, self.market, direction="down")
        ask_qty = round_to_lot(self.quote_size_usd / ask_price, self.market, direction="down")

        # Bid
        signals.extend(self._manage_quote(
            current=self._our_bid, target_price=bid_price, target_qty=bid_qty,
            side=Side.BUY, mid=ms.mid,
        ))
        # Ask
        signals.extend(self._manage_quote(
            current=self._our_ask, target_price=ask_price, target_qty=ask_qty,
            side=Side.SELL, mid=ms.mid,
        ))

        if signals:
            self._last_requote_ts = time.time()
            log.debug(
                "yield_maker.requote",
                mid=str(ms.mid), reservation=reservation_price,
                bid=str(bid_price), ask=str(ask_price),
                inventory_skew_usd=str(q_delta_usd), sigma=sigma,
            )
        return signals

    def _manage_quote(
        self,
        current: dict[str, Any] | None,
        target_price: Decimal,
        target_qty: Decimal,
        side: Side,
        mid: Decimal,
    ) -> list[TradingSignal]:
        qty_checked = ensure_min_quantity(target_qty, self.market)
        if qty_checked is None or qty_checked <= 0:
            return []

        if current is None:
            place = self._place(side, qty_checked, target_price)
            self._record_placement(side, place.order)
            return [place]

        # Existing quote — check drift
        existing_price = current["price"]
        drift_bps = abs(target_price - existing_price) / mid * 10_000
        if drift_bps <= self.requote_threshold_bps:
            return []

        # Cancel existing + place new
        cancel = TradingSignal(
            action=SignalAction.CANCEL,
            cancel=CancelIntent(market=self.market, order_id=current["coid"],
                                reason=f"yield_maker requote drift={drift_bps:.1f}bps"),
        )
        # Optimistically clear; the cancel WS event will arrive shortly
        if side == Side.BUY:
            self._our_bid = None
        else:
            self._our_ask = None

        place = self._place(side, qty_checked, target_price)
        self._record_placement(side, place.order)
        return [cancel, place]

    def _place(self, side: Side, qty: Decimal, price: Decimal) -> TradingSignal:
        coid = f"ym_{side.value}_{uuid.uuid4().hex[:8]}"
        return TradingSignal(
            action=SignalAction.PLACE,
            order=OrderIntent(
                market=self.market,
                side=side,
                order_type=OrderType.POST_ONLY,
                quantity=qty,
                price=price,
                funding=FundingSource.VAULT,
                client_order_id=coid,
                reason="yield_maker quote",
            ),
        )

    def _inventory_base_balance(self, inv: OwnInventory) -> Decimal:
        if not MARKETS[self.market].is_base_native:
            return inv.base_balance
        reserved = min(inv.base_balance, self.native_base_reserve)
        return max(Decimal("0"), inv.base_balance - reserved)

    def _record_placement(self, side: Side, order: OrderIntent) -> None:
        """Fix for gap #3: track that we have a resting quote on this side
        so subsequent ticks don't re-place duplicate quotes."""
        record = {
            "coid": order.client_order_id,
            "price": order.price,
            "qty": order.quantity,
            "placed_at": time.time(),
        }
        if side == Side.BUY:
            self._our_bid = record
        else:
            self._our_ask = record

    def _realized_vol(self) -> float:
        if len(self._mid_window) < 5:
            return 0.001
        returns = []
        prev = self._mid_window[0]
        for m in list(self._mid_window)[1:]:
            if prev > 0:
                returns.append(float((m - prev) / prev))
            prev = m
        if len(returns) < 2:
            return 0.001
        return max(0.0001, statistics.stdev(returns))

    async def on_fill(self, fill_event: dict[str, Any]) -> None:
        coid = fill_event.get("clientOrderId", "")
        if self._our_bid and self._our_bid.get("coid") == coid:
            self._our_bid = None
        if self._our_ask and self._our_ask.get("coid") == coid:
            self._our_ask = None
        log.info("yield_maker.fill", coid=coid,
                 side=fill_event.get("side"),
                 qty=fill_event.get("quantity"),
                 price=fill_event.get("price"))

    async def on_reject(self, order_id: str, reason: str) -> None:
        # Clear tracking for whichever quote was rejected — match by coid OR order_id
        if self._our_bid and self._our_bid.get("coid") in (order_id, reason):
            self._our_bid = None
        if self._our_ask and self._our_ask.get("coid") in (order_id, reason):
            self._our_ask = None
        log.warning("yield_maker.rejected", order_id=order_id, reason=reason)
