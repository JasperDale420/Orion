import argparse
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv


@dataclass(frozen=True)
class DarkpoolPrint:
    ticker: str
    executed_at_utc: datetime
    price: float
    size: float


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def _load_env() -> None:
    load_dotenv(".env")


def _headers() -> dict[str, str]:
    token = os.getenv("UW_API_KEY")
    if not token:
        raise RuntimeError("Missing UW_API_KEY in environment")
    return {"Authorization": f"Bearer {token}"}


def _base() -> str:
    return (os.getenv("UW_BASE_URL") or "https://api.unusualwhales.com/api").rstrip("/")


def fetch_darkpool_for_ticker_day(ticker: str, day: str) -> list[DarkpoolPrint]:
    """
    Fetch darkpool prints for a ticker/day using UW's per-ticker endpoint.
    Falls back to [] on 404/422 which can indicate no data for that ticker/day.
    """
    # NOTE: UW_BASE_URL in this repo includes the `/api` prefix, so the correct per-ticker path is `/darkpool/{ticker}`.
    r = httpx.get(f"{_base()}/darkpool/{ticker}", params={"date": day}, headers=_headers(), timeout=30)
    if r.status_code in (404, 422):
        return []
    r.raise_for_status()
    payload = r.json()
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []

    out: list[DarkpoolPrint] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ts = it.get("executed_at") or it.get("timestamp") or it.get("date")
        price = _as_float(it.get("price"))
        size = _as_float(it.get("size"))
        if not ts or price is None or size is None:
            continue
        try:
            out.append(
                DarkpoolPrint(
                    ticker=ticker,
                    executed_at_utc=_parse_ts(str(ts)),
                    price=float(price),
                    size=float(size),
                )
            )
        except Exception:
            continue
    return out


def _parse_csv_to_prints(df: "pd.DataFrame") -> tuple[dict[str, list[DarkpoolPrint]], int]:
    """Parse CSV rows into DarkpoolPrint objects grouped by ticker."""
    sample_by_ticker: dict[str, list[DarkpoolPrint]] = defaultdict(list)
    bad_rows = 0
    for row in df.to_dict(orient="records"):
        ticker = row.get("ticker")
        ts = row.get("executed_at")
        price = _as_float(row.get("price"))
        size = _as_float(row.get("size"))
        if not isinstance(ticker, str) or not ticker or not ts or price is None or size is None:
            bad_rows += 1
            continue
        try:
            sample_by_ticker[ticker].append(
                DarkpoolPrint(ticker=ticker, executed_at_utc=_parse_ts(str(ts)), price=float(price), size=float(size))
            )
        except Exception:
            bad_rows += 1
            continue
    return dict(sample_by_ticker), bad_rows


def _match_ticker_prints(csv_prints: list[DarkpoolPrint], api_prints: list[DarkpoolPrint]) -> tuple[int, int, int, int]:
    """Match CSV prints against API prints for a single ticker, returning (matched, total, covered_matched, covered_total)."""
    api_keys: set[tuple[int, float, float]] = set()
    api_min_ts: int | None = None
    for p in api_prints:
        ts_sec = int(p.executed_at_utc.timestamp())
        api_keys.add((ts_sec, round(p.price, 4), round(p.size, 2)))
        api_min_ts = ts_sec if api_min_ts is None else min(api_min_ts, ts_sec)

    t_matched = 0
    t_total = 0
    t_covered_matched = 0
    t_covered_total = 0

    for p in csv_prints:
        t_total += 1
        key = (int(p.executed_at_utc.timestamp()), round(p.price, 4), round(p.size, 2))

        is_covered = api_min_ts is not None and key[0] >= (api_min_ts - 1)
        if is_covered:
            t_covered_total += 1

        is_match = key in api_keys
        if not is_match:
            # Tolerate +/- 1 second on timestamp
            key_m1 = (key[0] - 1, key[1], key[2])
            key_p1 = (key[0] + 1, key[1], key[2])
            is_match = key_m1 in api_keys or key_p1 in api_keys

        if is_match:
            t_matched += 1
            if is_covered:
                t_covered_matched += 1

    return t_matched, t_total, t_covered_matched, t_covered_total


def _print_darkpool_report(
    day: str,
    df_len: int,
    bad_rows: int,
    tickers: list[str],
    max_tickers: int,
    per_ticker: list[tuple[str, int, int, int, int, int]],
    matched: int,
    total: int,
    covered_matched: int,
    covered_total: int,
) -> None:
    """Print darkpool validation comparison report."""
    print(f"Day: {day}")
    print(f"Darkpool CSV sampled rows: {df_len} (bad_rows_skipped={bad_rows})")
    print(f"Tickers compared: {len(tickers)} (max_tickers={max_tickers})")
    print(f"Row match rate (overall): {matched}/{max(1, total)} ({matched / max(1, total):.1%})")
    print(
        f"Row match rate (within API-covered window): {covered_matched}/{max(1, covered_total)} ({covered_matched / max(1, covered_total):.1%})"
    )
    print("Per-ticker (matched/total, covered_matched/covered_total, api_rows):")
    for t, m, tot, cm, ctot, api_n in per_ticker:
        print(
            f"  {t}: {m}/{tot} ({m / max(1, tot):.1%}), covered={cm}/{max(1, ctot)} ({cm / max(1, ctot):.1%}), api={api_n}"
        )


def validate(day: str, uw_raw_root: Path, sample_n: int, max_tickers: int) -> None:
    dp_csv = uw_raw_root / "dark_pool" / f"dp-eod-report-{day}.csv"
    if not dp_csv.exists():
        raise FileNotFoundError(str(dp_csv))

    df = pd.read_csv(dp_csv, low_memory=False)
    required = {"ticker", "executed_at", "price", "size"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Unexpected darkpool CSV schema; missing columns: {sorted(missing)}")

    if sample_n > 0 and len(df) > sample_n:
        df = df.sample(sample_n, random_state=7)

    sample_by_ticker, bad_rows = _parse_csv_to_prints(df)

    tickers = sorted(sample_by_ticker.keys())
    if max_tickers > 0:
        tickers = tickers[:max_tickers]

    api_by_ticker: dict[str, list[DarkpoolPrint]] = {}
    for t in tickers:
        api_by_ticker[t] = fetch_darkpool_for_ticker_day(t, day)

    matched = 0
    total = 0
    covered_matched = 0
    covered_total = 0
    per_ticker = []

    for t in tickers:
        api = api_by_ticker.get(t, [])
        t_matched, t_total, t_covered_matched, t_covered_total = _match_ticker_prints(sample_by_ticker.get(t, []), api)
        matched += t_matched
        total += t_total
        covered_matched += t_covered_matched
        covered_total += t_covered_total
        per_ticker.append((t, t_matched, t_total, t_covered_matched, t_covered_total, len(api)))

    _print_darkpool_report(
        day,
        len(df),
        bad_rows,
        tickers,
        max_tickers,
        per_ticker,
        matched,
        total,
        covered_matched,
        covered_total,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    parser.add_argument("--uw-raw-root", required=True, help="Path to whalehunter/data/uw_raw")
    parser.add_argument("--sample", type=int, default=500, help="Rows sampled from dp CSV (0 = all)")
    parser.add_argument("--max-tickers", type=int, default=50, help="Limit unique tickers queried from API (0 = all)")
    args = parser.parse_args()

    _load_env()
    validate(args.day, Path(args.uw_raw_root), args.sample, args.max_tickers)


if __name__ == "__main__":
    main()
