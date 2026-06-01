"""Tests for VolumeMill and YieldMaker signal generation."""

import time
from decimal import Decimal

import pytest

from dreamdex_bot.config import MarketSymbol
from dreamdex_bot.interfaces.strategy import (
    FundingSource, MarketState, OrderType, OwnInventory, Side, SignalAction,
)
from dreamdex_bot.strategies.volume_mill import VolumeMill
from dreamdex_bot.strategies.yield_maker import YieldMaker


def make_market_state(market: MarketSymbol, bid: str, ask: str) -> MarketState:
    bid_d = Decimal(bid)
    ask_d = Decimal(ask)
    return MarketState(
        market=market,
        best_bid=bid_d, best_ask=ask_d, mid=(bid_d + ask_d) / 2,
        bid_depth_usd=Decimal("1000"), ask_depth_usd=Decimal("1000"),
        last_trade_price=(bid_d + ask_d) / 2, volatility_5m=None,
        ts=time.time(),
    )


def make_market_state_with_depth(
    market: MarketSymbol,
    bid: str,
    ask: str,
    bid_depth: str,
    ask_depth: str,
) -> MarketState:
    ms = make_market_state(market, bid, ask)
    ms.bid_depth_usd = Decimal(bid_depth)
    ms.ask_depth_usd = Decimal(ask_depth)
    return ms


def make_inventory(market: MarketSymbol, quote: str = "0", base: str = "0") -> OwnInventory:
    return OwnInventory(
        market=market,
        base_balance=Decimal(base), quote_balance=Decimal(quote),
        base_locked_in_orders=Decimal("0"), quote_locked_in_orders=Decimal("0"),
        realized_pnl_usd=Decimal("0"), unrealized_pnl_usd=Decimal("0"),
    )


# ════════════════════════════════════════════════════════════════════
# VolumeMill
# ════════════════════════════════════════════════════════════════════

class TestVolumeMill:
    @pytest.mark.asyncio
    async def test_no_signal_when_no_balance(self):
        """Gap #2 regression: with zero balance the strategy idles (with warning)
        rather than emitting a zero-quantity order."""
        strat = VolumeMill({"market": "SOMI:USDso", "size_per_cycle_usd": "20.00"})
        assert strat.name == "volume_mill:SOMI:USDso"
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.500", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="0", base="0")
        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_emits_buy_when_funded_with_quote(self):
        strat = VolumeMill({"market": "SOMI:USDso", "size_per_cycle_usd": "20.00"})
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.500", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="50", base="0")
        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert len(signals) == 1
        assert signals[0].action == SignalAction.PLACE
        order = signals[0].order
        assert order is not None
        assert order.side == Side.BUY
        assert order.order_type == OrderType.IOC
        assert order.funding == FundingSource.WALLET
        # Quantity = target_usd / ask, rounded to lot 0.01
        assert order.quantity > Decimal("0")
        assert order.price == Decimal("0.5013")

    @pytest.mark.asyncio
    async def test_emits_sell_when_holding_base(self):
        strat = VolumeMill({"market": "SOMI:USDso", "size_per_cycle_usd": "20.00",
                              "max_inventory_imbalance": "1"})
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.500", "0.501")
        # Hold base > imbalance threshold
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="0", base="40")
        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert len(signals) == 1
        assert signals[0].order.side == Side.SELL
        assert signals[0].order.price == Decimal("0.4997")  # crosses below best bid

    @pytest.mark.asyncio
    async def test_does_not_sell_reserved_native_base(self):
        strat = VolumeMill({
            "market": "SOMI:USDso",
            "size_per_cycle_usd": "20.00",
            "native_base_reserve_by_market": {"SOMI:USDso": "10"},
        })
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.500", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="0", base="10")

        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )

        assert signals == []

    @pytest.mark.asyncio
    async def test_sells_only_native_base_above_reserve(self):
        strat = VolumeMill({
            "market": "SOMI:USDso",
            "size_per_cycle_usd": "20.00",
            "max_inventory_imbalance": "1",
            "native_base_reserve_by_market": {"SOMI:USDso": "10"},
        })
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.500", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="0", base="40")

        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )

        assert len(signals) == 1
        assert signals[0].order.side == Side.SELL
        assert signals[0].order.quantity == Decimal("30.00")

    @pytest.mark.asyncio
    async def test_throttles_within_cycle_interval(self):
        strat = VolumeMill({"market": "SOMI:USDso", "size_per_cycle_usd": "20.00",
                              "cycle_interval_sec": 60})
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.500", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="50", base="0")
        first = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert len(first) == 1
        # Second call within throttle window — should idle
        second = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert second == []

    @pytest.mark.asyncio
    async def test_no_signal_when_no_book(self):
        strat = VolumeMill({"market": "SOMI:USDso"})
        ms = MarketState(market=MarketSymbol.SOMI_USDSO,
                          best_bid=None, best_ask=None, mid=None,
                          bid_depth_usd=Decimal("0"), ask_depth_usd=Decimal("0"),
                          last_trade_price=None, volatility_5m=None, ts=time.time())
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="50")
        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert signals == []
        assert strat.last_skip_reason == "one_sided_or_empty_book"

    @pytest.mark.asyncio
    async def test_skips_when_spread_too_wide(self):
        strat = VolumeMill({
            "market": "SOMI:USDso",
            "max_spread_bps": "10",
            "min_side_depth_usd": "1",
        })
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.50", "0.51")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="50", base="0")

        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )

        assert signals == []
        assert strat.last_skip_reason == "spread_too_wide"

    @pytest.mark.asyncio
    async def test_skips_when_bid_depth_too_thin(self):
        strat = VolumeMill({
            "market": "SOMI:USDso",
            "max_spread_bps": "100",
            "min_side_depth_usd": "2",
        })
        ms = make_market_state_with_depth(MarketSymbol.SOMI_USDSO, "0.50", "0.501", "1", "10")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="50", base="0")

        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )

        assert signals == []
        assert strat.last_skip_reason == "bid_depth_too_thin"

    @pytest.mark.asyncio
    async def test_buy_size_is_capped_by_ask_depth(self):
        strat = VolumeMill({
            "market": "SOMI:USDso",
            "size_per_cycle_usd": "20",
            "max_spread_bps": "100",
            "min_side_depth_usd": "1",
            "depth_usage_fraction": "0.50",
        })
        ms = make_market_state_with_depth(MarketSymbol.SOMI_USDSO, "0.50", "0.501", "100", "2")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="50", base="0")

        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )

        assert len(signals) == 1
        assert signals[0].order.quantity == Decimal("1.99")

    @pytest.mark.asyncio
    async def test_ioc_cross_bps_prices_inside_taker_direction(self):
        strat = VolumeMill({
            "market": "SOMI:USDso",
            "size_per_cycle_usd": "2",
            "max_spread_bps": "100",
            "min_side_depth_usd": "1",
            "ioc_cross_bps": "10",
        })
        ms = make_market_state_with_depth(MarketSymbol.SOMI_USDSO, "0.5000", "0.5010", "100", "100")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="50", base="0")

        buy = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert buy[0].order.price == Decimal("0.5016")

        strat._last_cycle_ts = 0
        strat._last_action = Side.BUY
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="0", base="10")
        sell = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert sell[0].order.price == Decimal("0.4995")

    @pytest.mark.asyncio
    async def test_profit_aware_exit_waits_until_target_bid(self):
        strat = VolumeMill({
            "market": "WETH:USDso",
            "size_per_cycle_usd": "4",
            "max_spread_bps": "100",
            "min_side_depth_usd": "1",
            "cycle_interval_sec": "0",
            "profit_aware_exit_enabled": True,
            "take_profit_bps": "10",
        })
        buy_book = make_market_state_with_depth(
            MarketSymbol.WETH_USDSO, "2045.93", "2046.35", "1000", "1000",
        )
        buy = await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: buy_book},
            {MarketSymbol.WETH_USDSO: make_inventory(MarketSymbol.WETH_USDSO, quote="50")},
        )
        assert buy[0].order.side == Side.BUY

        weak_bid = make_market_state_with_depth(
            MarketSymbol.WETH_USDSO, "2046.00", "2046.42", "1000", "1000",
        )
        wait = await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: weak_bid},
            {MarketSymbol.WETH_USDSO: make_inventory(MarketSymbol.WETH_USDSO, quote="46", base="0.0019")},
        )
        assert wait == []
        assert strat.last_skip_reason == "profit_target_not_reached"

        strong_bid = make_market_state_with_depth(
            MarketSymbol.WETH_USDSO, "2050.00", "2050.42", "1000", "1000",
        )
        sell = await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: strong_bid},
            {MarketSymbol.WETH_USDSO: make_inventory(MarketSymbol.WETH_USDSO, quote="46", base="0.0019")},
        )
        assert sell[0].order.side == Side.SELL
        assert sell[0].order.price >= Decimal("2048.00")

    @pytest.mark.asyncio
    async def test_profit_aware_exit_flattens_after_timeout(self):
        strat = VolumeMill({
            "market": "WETH:USDso",
            "size_per_cycle_usd": "4",
            "max_spread_bps": "100",
            "min_side_depth_usd": "1",
            "cycle_interval_sec": "0",
            "profit_aware_exit_enabled": True,
            "take_profit_bps": "10",
            "max_hold_sec": "1",
        })
        buy_book = make_market_state_with_depth(
            MarketSymbol.WETH_USDSO, "2045.93", "2046.35", "1000", "1000",
        )
        await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: buy_book},
            {MarketSymbol.WETH_USDSO: make_inventory(MarketSymbol.WETH_USDSO, quote="50")},
        )
        strat._last_cycle_ts = 0
        strat._entry_ts = time.time() - 2

        weak_bid = make_market_state_with_depth(
            MarketSymbol.WETH_USDSO, "2046.00", "2046.42", "1000", "1000",
        )
        sell = await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: weak_bid},
            {MarketSymbol.WETH_USDSO: make_inventory(MarketSymbol.WETH_USDSO, quote="46", base="0.0019")},
        )
        assert sell[0].order.side == Side.SELL
        assert sell[0].order.price < Decimal("2046.00")

    @pytest.mark.asyncio
    async def test_uses_per_market_cycle_size(self):
        strat = VolumeMill({
            "market": "WETH:USDso",
            "size_per_cycle_usd": "2",
            "size_per_cycle_usd_by_market": {"WETH:USDso": "4"},
            "max_spread_bps": "100",
            "min_side_depth_usd": "1",
        })
        ms = make_market_state_with_depth(MarketSymbol.WETH_USDSO, "2045.93", "2046.35", "100", "100")
        inv = make_inventory(MarketSymbol.WETH_USDSO, quote="50", base="0")

        signals = await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: ms}, {MarketSymbol.WETH_USDSO: inv},
        )

        assert len(signals) == 1
        assert signals[0].order.quantity == Decimal("0.0019")

    @pytest.mark.asyncio
    async def test_retries_sell_when_previous_sell_left_base_and_quote_too_small(self):
        strat = VolumeMill({
            "market": "WETH:USDso",
            "size_per_cycle_usd": "4",
            "max_spread_bps": "100",
            "min_side_depth_usd": "1",
            "cycle_interval_sec": "0",
        })
        strat._last_action = Side.SELL
        ms = make_market_state_with_depth(MarketSymbol.WETH_USDSO, "2045.93", "2046.35", "1000", "1000")
        inv = make_inventory(MarketSymbol.WETH_USDSO, quote="0.14", base="0.001")

        signals = await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: ms}, {MarketSymbol.WETH_USDSO: inv},
        )

        assert len(signals) == 1
        assert signals[0].order.side == Side.SELL
        assert signals[0].order.quantity == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_sells_existing_base_after_restart_when_quote_too_small_to_buy(self):
        strat = VolumeMill({
            "market": "WETH:USDso",
            "size_per_cycle_usd": "4",
            "max_spread_bps": "100",
            "min_side_depth_usd": "1",
        })
        ms = make_market_state_with_depth(MarketSymbol.WETH_USDSO, "2045.93", "2046.35", "1000", "1000")
        inv = make_inventory(MarketSymbol.WETH_USDSO, quote="0.14", base="0.001")

        signals = await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: ms}, {MarketSymbol.WETH_USDSO: inv},
        )

        assert len(signals) == 1
        assert signals[0].order.side == Side.SELL
        assert signals[0].order.quantity == Decimal("0.001")


# ════════════════════════════════════════════════════════════════════
# YieldMaker
# ════════════════════════════════════════════════════════════════════

class TestYieldMaker:
    @pytest.mark.asyncio
    async def test_weth_paper_mode_tracks_top_of_book_quotes_without_emitting_signals(self):
        strat = YieldMaker({
            "paper_mode": True,
            "market": "WETH:USDso",
            "quote_mode": "top_of_book",
            "quote_size_usd": "8.00",
            "improve_ticks": 1,
            "requote_min_interval_sec": 0,
        })
        ms = make_market_state(MarketSymbol.WETH_USDSO, "1979.14", "1979.55")
        inv = make_inventory(MarketSymbol.WETH_USDSO, quote="35", base="0")

        signals = await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: ms}, {MarketSymbol.WETH_USDSO: inv},
        )

        assert signals == []
        assert strat._our_bid is not None
        assert strat._our_bid["price"] == Decimal("1979.15")
        assert strat._our_ask is not None
        assert strat._our_ask["price"] == Decimal("1979.54")

    @pytest.mark.asyncio
    async def test_weth_paper_mode_records_crossed_bid_fill(self):
        strat = YieldMaker({
            "paper_mode": True,
            "market": "WETH:USDso",
            "quote_mode": "top_of_book",
            "quote_size_usd": "8.00",
            "improve_ticks": 1,
            "requote_min_interval_sec": 0,
        })
        inv = make_inventory(MarketSymbol.WETH_USDSO, quote="35", base="0")
        first = make_market_state(MarketSymbol.WETH_USDSO, "1979.14", "1979.55")
        await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: first}, {MarketSymbol.WETH_USDSO: inv},
        )
        crossed = make_market_state(MarketSymbol.WETH_USDSO, "1978.90", "1979.10")
        await strat.generate_signals(
            {MarketSymbol.WETH_USDSO: crossed}, {MarketSymbol.WETH_USDSO: inv},
        )

        assert strat._paper_base_delta == Decimal("0.0040")
        assert strat._paper_quote_delta == Decimal("-7.916400")

    def test_native_base_reserve_is_excluded_from_inventory_skew(self):
        strat = YieldMaker({
            "target_base_value_usd": "12.50",
            "native_base_reserve_by_market": {"SOMI:USDso": "10"},
        })
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="0", base="12")

        assert strat._inventory_base_balance(inv) == Decimal("2")

    @pytest.mark.asyncio
    async def test_emits_both_sides_when_no_quotes_yet(self):
        """First tick should place both a bid and an ask."""
        strat = YieldMaker({
            "target_base_value_usd": "12.50", "quote_size_usd": "5.00",
            "min_half_spread_bps": 25, "requote_min_interval_sec": 0,
        })
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.499", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="100", base="25")
        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        # Should emit a PLACE buy and a PLACE sell
        place_signals = [s for s in signals if s.action == SignalAction.PLACE]
        sides = [s.order.side for s in place_signals]
        assert Side.BUY in sides
        assert Side.SELL in sides
        # All quotes are PostOnly + Vault-funded
        for s in place_signals:
            assert s.order.order_type == OrderType.POST_ONLY
            assert s.order.funding == FundingSource.VAULT

    @pytest.mark.asyncio
    async def test_tracks_placed_quotes(self):
        """Gap #3 regression: after placing, _our_bid and _our_ask must be set."""
        strat = YieldMaker({
            "target_base_value_usd": "12.50", "quote_size_usd": "5.00",
            "min_half_spread_bps": 25, "requote_min_interval_sec": 0,
        })
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.499", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="100", base="25")
        await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert strat._our_bid is not None
        assert strat._our_ask is not None
        assert "coid" in strat._our_bid
        assert "coid" in strat._our_ask

    @pytest.mark.asyncio
    async def test_does_not_requote_when_within_threshold(self):
        """After placing, a tick with similar prices should not re-quote."""
        strat = YieldMaker({
            "target_base_value_usd": "12.50", "quote_size_usd": "5.00",
            "min_half_spread_bps": 25, "requote_min_interval_sec": 0,
            "requote_threshold_bps": 100,  # very loose
        })
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.499", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="100", base="25")
        await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        # Force time forward past interval debounce so the interval check passes
        strat._last_requote_ts = 0
        # Second tick with identical book → quotes already placed, no drift → no signals
        second = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        assert second == [], "Expected no requote when quotes haven't drifted"

    @pytest.mark.asyncio
    async def test_on_fill_clears_quote_tracking(self):
        strat = YieldMaker({
            "target_base_value_usd": "12.50", "quote_size_usd": "5.00",
            "requote_min_interval_sec": 0,
        })
        ms = make_market_state(MarketSymbol.SOMI_USDSO, "0.499", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="100", base="25")
        await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms}, {MarketSymbol.SOMI_USDSO: inv},
        )
        # Bid was placed
        bid_coid = strat._our_bid["coid"]
        await strat.on_fill({"clientOrderId": bid_coid, "side": "buy",
                              "quantity": "10", "price": "0.499"})
        assert strat._our_bid is None
        assert strat._our_ask is not None  # ask still tracked

    @pytest.mark.asyncio
    async def test_requote_on_significant_drift(self):
        strat = YieldMaker({
            "target_base_value_usd": "12.50", "quote_size_usd": "5.00",
            "min_half_spread_bps": 25, "requote_min_interval_sec": 0,
            "requote_threshold_bps": 5,
        })
        ms1 = make_market_state(MarketSymbol.SOMI_USDSO, "0.499", "0.501")
        inv = make_inventory(MarketSymbol.SOMI_USDSO, quote="100", base="25")
        await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms1}, {MarketSymbol.SOMI_USDSO: inv},
        )
        strat._last_requote_ts = 0
        # Major price move — should trigger requote
        ms2 = make_market_state(MarketSymbol.SOMI_USDSO, "0.599", "0.601")
        signals = await strat.generate_signals(
            {MarketSymbol.SOMI_USDSO: ms2}, {MarketSymbol.SOMI_USDSO: inv},
        )
        cancels = [s for s in signals if s.action == SignalAction.CANCEL]
        places = [s for s in signals if s.action == SignalAction.PLACE]
        assert len(cancels) > 0, "Expected cancel signals on price drift"
        assert len(places) > 0, "Expected new place signals after cancel"
