import argparse
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv


@dataclass(frozen=True)
class FlowAlert:
    alert_id: str
    option_chain: str
    ticker: str | None
    put_call: str | None
    strike: float | None
    expiry: str | None
    start_ms: int
    end_ms: int
    total_size: float | None
    total_premium: float | None


def _parse_iso_to_ms(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except Exception:
        return None


def _as_int(v: Any) -> int | None:
    f = _as_float(v)
    if f is None:
        return None
    return int(f)


def _load_env() -> None:
    load_dotenv(".env")


def _headers() -> dict[str, str]:
    token = os.getenv("UW_API_KEY")
    if not token:
        raise RuntimeError("Missing UW_API_KEY in environment")
    return {"Authorization": f"Bearer {token}"}


def _base() -> str:
    return (os.getenv("UW_BASE_URL") or "https://api.unusualwhales.com/api").rstrip("/")


def fetch_flow_alerts_for_day(day: str) -> list[FlowAlert]:
    """
    Fetch UW `/option-trades/flow-alerts` for a single UTC day by paging backwards using `older_than`.

    The endpoint is cursor-based and appears capped at `limit<=500`. We page by `older_than` and stop
    when the oldest record crosses into the previous day.
    """
    target = datetime.fromisoformat(day).date()
    cursor = datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=UTC).isoformat()
    limit = 500

    alerts: list[FlowAlert] = []
    seen_ids: set[str] = set()

    while True:
        r = httpx.get(
            f"{_base()}/option-trades/flow-alerts",
            params={"limit": limit, "older_than": cursor},
            headers=_headers(),
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list) or not items:
            break

        def _created(it: dict) -> str:
            return str(it.get("created_at") or "")

        created_vals = [c for c in (_created(it) for it in items) if c]
        if not created_vals:
            break
        oldest_created = min(created_vals)

        day_count = 0
        for it in items:
            created_at = _created(it)
            if not created_at or created_at[:10] != day:
                continue
            alert_id = str(it.get("id"))
            if not alert_id or alert_id in seen_ids:
                continue
            seen_ids.add(alert_id)

            option_chain = str(it.get("option_chain") or "")
            if not option_chain:
                continue

            t = str(it.get("type") or "").lower()
            put_call = "C" if t == "call" else ("P" if t == "put" else None)

            start_ms = _as_int(it.get("start_time")) or 0
            end_ms = _as_int(it.get("end_time")) or start_ms

            alerts.append(
                FlowAlert(
                    alert_id=alert_id,
                    option_chain=option_chain,
                    ticker=it.get("ticker"),
                    put_call=put_call,
                    strike=_as_float(it.get("strike")),
                    expiry=it.get("expiry"),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    total_size=_as_float(it.get("total_size")),
                    total_premium=_as_float(
                        it.get("total_premium") or it.get("total_ask_side_prem") or it.get("total_bid_side_prem")
                    ),
                )
            )
            day_count += 1

        if oldest_created[:10] < day:
            break
        if oldest_created == cursor:
            break
        cursor = oldest_created

        # If we didn't capture any in-day alerts in this page, we still continue until we cross the day boundary,
        # because pages can include multiple dates.
        if day_count == 0 and oldest_created[:10] > day:
            continue

    return alerts


def _match_flow_rows_to_alerts(
    df: "pd.DataFrame", by_chain: dict[str, list[FlowAlert]]
) -> tuple[int, int, int, int, dict[str, float], dict[str, float]]:
    """Match CSV bot rows to API alert windows by option_chain and timestamp."""
    total_rows = 0
    matched_rows = 0
    unmatched_rows = 0
    missing_chain = 0
    per_alert_sum_size: dict[str, float] = {}
    per_alert_sum_premium: dict[str, float] = {}

    for row in df.to_dict(orient="records"):
        total_rows += 1
        chain = row.get("option_chain_id")
        if not isinstance(chain, str) or not chain:
            missing_chain += 1
            continue

        executed_at = row.get("executed_at")
        if not executed_at:
            unmatched_rows += 1
            continue

        try:
            exec_ms = _parse_iso_to_ms(str(executed_at))
        except Exception:
            unmatched_rows += 1
            continue

        candidates = by_chain.get(chain)
        if not candidates:
            unmatched_rows += 1
            continue

        match = _find_alert_match(candidates, exec_ms)
        if not match:
            unmatched_rows += 1
            continue

        matched_rows += 1
        size = _as_float(row.get("size")) or 0.0
        premium = _as_float(row.get("premium")) or 0.0
        per_alert_sum_size[match.alert_id] = per_alert_sum_size.get(match.alert_id, 0.0) + size
        per_alert_sum_premium[match.alert_id] = per_alert_sum_premium.get(match.alert_id, 0.0) + premium

    return total_rows, matched_rows, unmatched_rows, missing_chain, per_alert_sum_size, per_alert_sum_premium


def _find_alert_match(candidates: list[FlowAlert], exec_ms: int) -> FlowAlert | None:
    """Find the matching alert for a given execution timestamp."""
    for a in candidates:
        if a.start_ms <= exec_ms <= a.end_ms:
            return a
        # Tolerate minor clock differences: +/- 1000ms
        if (a.start_ms - 1000) <= exec_ms <= (a.end_ms + 1000):
            return a
    return None


def _print_flow_report(
    day: str,
    total_rows: int,
    api_count: int,
    matched_rows: int,
    unmatched_rows: int,
    missing_chain: int,
    per_alert_sum_size: dict[str, float],
    per_alert_sum_premium: dict[str, float],
    api_alerts: list[FlowAlert],
) -> None:
    """Print validation comparison report."""
    alert_by_id = {a.alert_id: a for a in api_alerts}
    compared = 0
    size_match = 0
    premium_match = 0

    for alert_id, size_sum in per_alert_sum_size.items():
        a = alert_by_id.get(alert_id)
        if not a:
            continue
        compared += 1
        if a.total_size is not None and abs(a.total_size - size_sum) < 1e-6:
            size_match += 1
        prem_sum = per_alert_sum_premium.get(alert_id, 0.0)
        if a.total_premium is not None and abs(a.total_premium - prem_sum) <= 0.01:
            premium_match += 1

    print(f"Day: {day}")
    print(f"Bot CSV rows checked: {total_rows}")
    print(f"API flow-alerts fetched: {api_count}")
    print(
        f"Row match rate (bot row -> some API alert window): {matched_rows}/{total_rows} ({matched_rows / max(1, total_rows):.1%})"
    )
    print(f"Unmatched rows: {unmatched_rows} (missing_chain={missing_chain})")
    print(f"Per-alert aggregate comparisons: {compared}")
    print(f"Exact total_size matches: {size_match}/{compared} ({size_match / max(1, compared):.1%})")
    print(f"Total_premium matches (<= $0.01): {premium_match}/{compared} ({premium_match / max(1, compared):.1%})")


def validate(day: str, uw_raw_root: Path, sample_n: int) -> None:
    flow_csv = uw_raw_root / "options_flow" / f"bot-eod-report-{day}.csv"
    if not flow_csv.exists():
        raise FileNotFoundError(str(flow_csv))

    df = pd.read_csv(flow_csv, low_memory=False)
    required = {
        "executed_at",
        "underlying_symbol",
        "option_chain_id",
        "option_type",
        "expiry",
        "strike",
        "price",
        "size",
        "premium",
    }
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Unexpected bot CSV schema; missing columns: {sorted(missing)}")

    if sample_n > 0 and len(df) > sample_n:
        df = df.sample(sample_n, random_state=7)

    api_alerts = fetch_flow_alerts_for_day(day)
    by_chain: dict[str, list[FlowAlert]] = {}
    for a in api_alerts:
        by_chain.setdefault(a.option_chain, []).append(a)
    for _chain, items in by_chain.items():
        items.sort(key=lambda x: x.start_ms)

    total_rows, matched_rows, unmatched_rows, missing_chain, per_alert_sum_size, per_alert_sum_premium = (
        _match_flow_rows_to_alerts(df, by_chain)
    )

    _print_flow_report(
        day,
        total_rows,
        len(api_alerts),
        matched_rows,
        unmatched_rows,
        missing_chain,
        per_alert_sum_size,
        per_alert_sum_premium,
        api_alerts,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    parser.add_argument("--uw-raw-root", required=True, help="Path to whalehunter/data/uw_raw")
    parser.add_argument("--sample", type=int, default=500, help="Rows sampled from bot CSV (0 = all)")
    args = parser.parse_args()

    _load_env()
    validate(args.day, Path(args.uw_raw_root), args.sample)


if __name__ == "__main__":
    main()
