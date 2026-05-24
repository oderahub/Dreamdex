"""Generate a Markdown competition report from runtime JSONL events.

Usage:
    python -m dreamdex_bot.reports.generate
    python -m dreamdex_bot.reports.generate --input logs/reports/session.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dreamdex_bot.config import load_settings


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"event": "malformed_jsonl", "raw": line[:500]})
    return rows


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal(0)


def _fmt_decimal(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"))).rstrip("0").rstrip(".")


def _ts(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value) if value else "n/a"


def build_summary(events: list[dict[str, Any]]) -> str:
    counts = Counter(str(e.get("event", "unknown")) for e in events)
    categories = Counter(str(e.get("category", "uncategorized")) for e in events)

    orders = [e for e in events if e.get("event") == "order_submitted"]
    confirmed = [e for e in events if e.get("event") == "order_confirmed"]
    confirmed_with_logs = [
        e for e in confirmed
        if int(e.get("status", 0) or 0) == 1 and int(e.get("logs_count", 0) or 0) > 0
    ]
    fills = [e for e in events if e.get("event") == "fill"]
    risk = [e for e in events if str(e.get("category")) == "risk"]
    api_issues = [
        e for e in events
        if e.get("event") in {"rest_4xx", "rest_5xx", "rest_401_reauth", "rest_rate_limit", "auth_login_failed"}
    ]
    bootstrap = [e for e in events if str(e.get("category")) == "bootstrap"]
    liquidity = [e for e in events if e.get("event") == "liquidity_snapshot"]

    volume_by_market: dict[str, Decimal] = defaultdict(Decimal)
    tx_hashes: set[str] = set()
    for e in orders:
        market = str(e.get("market", "unknown"))
        volume_by_market[market] += _decimal(e.get("notional"))
        if e.get("tx_hash"):
            tx_hashes.add(str(e["tx_hash"]))
    for e in fills:
        market = str(e.get("market", "unknown"))
        volume_by_market[market] += _decimal(e.get("notional"))

    started = next((e for e in events if e.get("event") == "bot_starting"), {})
    stopped = next((e for e in reversed(events) if e.get("event") == "bot_stopped"), {})
    strategies = next((e for e in events if e.get("event") == "strategies_configured"), {})

    lines = [
        "# DreamDEX Bot Session Report",
        "",
        f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"- Network: {started.get('network', 'n/a')}",
        f"- Wallet: {started.get('wallet', 'n/a')}",
        f"- Config: {started.get('config_path', 'n/a')}",
        f"- Started: {started.get('started_at', 'n/a')}",
        f"- Stopped: {stopped.get('stopped_at', 'n/a')}",
        "",
        "## Strategy Setup",
        "",
        f"- Markets: {', '.join(str(m) for m in strategies.get('markets', [])) or 'n/a'}",
        f"- Strategies: {', '.join(str(s) for s in strategies.get('strategies', [])) or 'n/a'}",
        "",
        "## Activity",
        "",
        f"- Events recorded: {len(events)}",
        f"- Orders submitted: {len(orders)}",
        f"- Receipt-confirmed order txs (waited paths only): {len(confirmed)}",
        f"- Receipt-confirmed with logs / OrderPlaced evidence: {len(confirmed_with_logs)}",
        f"- Unique submitted tx hashes: {len(tx_hashes)}",
        f"- Fill events observed: {len(fills)}",
        "",
        "## Estimated Volume",
        "",
    ]

    if volume_by_market:
        for market, volume in sorted(volume_by_market.items()):
            lines.append(f"- {market}: {_fmt_decimal(volume)} quote notional")
    else:
        lines.append("- No order/fill notional recorded yet.")

    lines.extend(["", "## Bootstrap", ""])
    if bootstrap:
        for e in bootstrap[-10:]:
            lines.append(
                f"- {_ts(e.get('ts'))}: {e.get('event')} "
                f"{e.get('market', '')} {e.get('spend_quote', '')}".rstrip()
            )
    else:
        lines.append("- No bootstrap events recorded.")

    lines.extend(["", "## Liquidity Snapshots", ""])
    if liquidity:
        for e in liquidity[-12:]:
            lines.append(
                f"- {e.get('market')}: bid {e.get('best_bid')} / ask {e.get('best_ask')}, "
                f"bid depth {e.get('bid_depth_usd')}, ask depth {e.get('ask_depth_usd')}, "
                f"spread {e.get('spread_bps')} bps"
            )
    else:
        lines.append("- No liquidity snapshots recorded.")

    lines.extend(["", "## API / Docs Findings", ""])
    if api_issues:
        for e in api_issues[-20:]:
            lines.append(
                f"- {_ts(e.get('ts'))}: {e.get('event')} {e.get('method', '')} "
                f"{e.get('path', '')} status={e.get('status', 'n/a')} body={str(e.get('body', ''))[:180]}"
            )
    else:
        lines.append("- No API/auth issue events recorded.")

    lines.extend(["", "## Risk Events", ""])
    if risk:
        for e in risk[-20:]:
            lines.append(
                f"- {_ts(e.get('ts'))}: {e.get('event')} rule={e.get('rule')} "
                f"action={e.get('action')} reason={e.get('reason')}"
            )
    else:
        lines.append("- No risk events recorded.")

    lines.extend(["", "## Event Counts", ""])
    for name, count in counts.most_common():
        lines.append(f"- {name}: {count}")

    lines.extend(["", "## Category Counts", ""])
    for name, count in categories.most_common():
        lines.append(f"- {name}: {count}")

    return "\n".join(lines) + "\n"


def main() -> None:
    settings = load_settings()
    default_input = Path(settings.log_dir) / "reports" / "session.jsonl"
    default_output = Path(settings.log_dir) / "reports" / "summary.md"

    parser = argparse.ArgumentParser(description="Generate DreamDEX competition report")
    parser.add_argument("--input", default=str(default_input), help="Runtime JSONL event file")
    parser.add_argument("--output", default=str(default_output), help="Markdown output path")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    events = _read_jsonl(input_path)
    summary = build_summary(events)
    output_path.write_text(summary)
    print(str(output_path))


if __name__ == "__main__":
    main()
