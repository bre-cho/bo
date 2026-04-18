"""
candle_library.py
=================
Thư viện nến tập trung: 10.000 nến lịch sử + cập nhật realtime.

Chức năng:
  - Tải và cache 10.000 nến lịch sử mỗi symbol (Parquet + Redis)
  - Cập nhật realtime: append nến mới + loại nến cũ (sliding window)
  - Kiểm soát chất lượng: missing, duplicate, drift, timestamp gaps
  - Versioning snapshot: train và inference dùng cùng nguồn dữ liệu
"""

from __future__ import annotations

import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import redis
import websockets

import config


# ──────────────────────────────────────────────────────────────────
# Quality control helpers
# ──────────────────────────────────────────────────────────────────

def _qc_check(df: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, list[str]]:
    """
    Kiểm soát chất lượng dữ liệu nến.

    Returns (cleaned_df, list of warning strings)
    """
    warnings: list[str] = []
    original_len = len(df)

    # 1. Remove duplicates by datetime
    df = df.drop_duplicates(subset=["datetime"]).copy()
    if len(df) < original_len:
        warnings.append(f"[QC] Removed {original_len - len(df)} duplicate rows")

    # 2. Sort by datetime
    df = df.sort_values("datetime").reset_index(drop=True)

    # 3. Drop rows with NaN in OHLC
    before = len(df)
    df = df.dropna(subset=["open", "high", "low", "close"])
    if len(df) < before:
        warnings.append(f"[QC] Dropped {before - len(df)} NaN OHLC rows")

    # 4. Check for price outliers (zscore > 5 on close)
    if len(df) > 30:
        z = np.abs((df["close"] - df["close"].mean()) / (df["close"].std() + 1e-9))
        outliers = (z > 5).sum()
        if outliers > 0:
            warnings.append(f"[QC] {outliers} price outliers (z>5) detected in {symbol}")

    # 5. Check timestamp gaps (gaps > 5× granularity)
    if len(df) > 10 and "datetime" in df.columns:
        time_diffs = df["datetime"].diff().dt.total_seconds().dropna()
        expected_gap = config.GRANULARITY
        large_gaps = (time_diffs > expected_gap * 5).sum()
        if large_gaps > 0:
            warnings.append(f"[QC] {large_gaps} large timestamp gaps in {symbol}")

    # 6. Check OHLC consistency: high >= low, high >= close, low <= close
    invalid = ((df["high"] < df["low"]) | (df["high"] < df["close"]) | (df["low"] > df["close"])).sum()
    if invalid > 0:
        warnings.append(f"[QC] {invalid} OHLC consistency errors in {symbol}")
        df = df[~((df["high"] < df["low"]) | (df["high"] < df["close"]) | (df["low"] > df["close"]))]

    return df, warnings


# ──────────────────────────────────────────────────────────────────
# Async fetch 10k candles (chunked if API limit)
# ──────────────────────────────────────────────────────────────────

_MAX_SINGLE_REQUEST = 5000  # Deriv API returns max ~5000 candles per request


async def _fetch_chunk(symbol: str, count: int, end_epoch: Optional[int] = None) -> pd.DataFrame:
    """Fetch a single chunk of candles from Deriv API."""
    request: dict = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": min(count, _MAX_SINGLE_REQUEST),
        "end": end_epoch if end_epoch else "latest",
        "granularity": config.GRANULARITY,
        "start": 1,
        "style": "candles",
    }
    async with websockets.connect(config.DERIV_WS_URL) as ws:
        await ws.send(json.dumps(request))
        response = json.loads(await ws.recv())

    if "error" in response:
        raise RuntimeError(f"Deriv API error: {response['error']['message']}")

    candles = response.get("candles", [])
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)
    df["epoch"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
    df = df.rename(columns={"epoch": "datetime"})
    df = df[["datetime", "open", "high", "low", "close"]].astype(
        {"open": float, "high": float, "low": float, "close": float}
    )
    return df


async def _fetch_10k_async(symbol: str, count: int = None) -> pd.DataFrame:
    """Fetch up to 10k candles by chunking requests."""
    count = count or config.CANDLE_LIBRARY_COUNT
    if count <= _MAX_SINGLE_REQUEST:
        return await _fetch_chunk(symbol, count)

    chunks: list[pd.DataFrame] = []
    remaining = count
    end_epoch = None

    while remaining > 0:
        chunk_size = min(remaining, _MAX_SINGLE_REQUEST)
        chunk = await _fetch_chunk(symbol, chunk_size, end_epoch)
        if chunk.empty:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
        if len(chunk) < chunk_size:
            break  # No more data available
        # Next chunk ends just before the oldest candle in this chunk
        oldest_dt = chunk["datetime"].min()
        end_epoch = int(oldest_dt.timestamp()) - 1
        await asyncio.sleep(0.3)  # Avoid rate limiting

    if not chunks:
        return pd.DataFrame()

    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    return df.tail(count).reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────
# CandleLibrary — main class
# ──────────────────────────────────────────────────────────────────

class CandleLibrary:
    """
    Thư viện nến tập trung cho một symbol.

    Usage:
        lib = CandleLibrary("R_100")
        df = lib.load()              # Load from file/Redis (fast)
        lib.refresh()                # Fetch new data from Deriv (slow, call rarely)
        lib.append_realtime(df_new)  # Append new candles + drop oldest
        df = lib.get_dataframe()     # Get latest full DataFrame
        df_train = lib.snapshot()    # Versioned snapshot for training
    """

    def __init__(self, symbol: str) -> None:
        self.symbol    = symbol
        self._r        = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
        )
        os.makedirs(config.CANDLE_LIBRARY_DIR, exist_ok=True)
        self._parquet_path = os.path.join(
            config.CANDLE_LIBRARY_DIR,
            f"candles_{symbol}_{config.GRANULARITY}.parquet",
        )
        self._df: Optional[pd.DataFrame] = None

    # ── Load / Save ───────────────────────────────────────────────

    def load(self) -> pd.DataFrame:
        """Load candles from Parquet file (fastest) or Redis cache."""
        if os.path.exists(self._parquet_path):
            df = pd.read_parquet(self._parquet_path)
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            self._df = df
            print(f"[CandleLib] {self.symbol}: loaded {len(df)} rows from {self._parquet_path}")
            return df

        # Fallback: Redis
        raw = self._r.get(config.CANDLE_LIBRARY_REDIS_KEY.format(symbol=self.symbol))
        if raw:
            df = pd.read_json(raw, orient="records")
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            self._df = df
            print(f"[CandleLib] {self.symbol}: loaded {len(df)} rows from Redis")
            return df

        print(f"[CandleLib] {self.symbol}: no cached data found, call refresh()")
        return pd.DataFrame()

    def _save(self, df: pd.DataFrame) -> None:
        """Persist to Parquet + Redis."""
        df.to_parquet(self._parquet_path, index=False)
        # Redis cache (just last 1000 rows for speed)
        self._r.set(
            config.CANDLE_LIBRARY_REDIS_KEY.format(symbol=self.symbol),
            df.tail(1000).to_json(orient="records", date_format="iso"),
            ex=3600 * 24,  # 24h expiry
        )

    # ── Refresh from API ──────────────────────────────────────────

    def refresh(self, count: int = None) -> pd.DataFrame:
        """
        Fetch fresh data from Deriv API (10k candles).
        Slow operation — run once or periodically.
        """
        count = count or config.CANDLE_LIBRARY_COUNT
        print(f"[CandleLib] {self.symbol}: fetching {count} candles from Deriv...")
        df = asyncio.run(_fetch_10k_async(self.symbol, count))
        if df.empty:
            print(f"[CandleLib] {self.symbol}: empty response from Deriv")
            return pd.DataFrame()

        df, warnings = _qc_check(df, self.symbol)
        for w in warnings:
            print(w)

        self._df = df
        self._save(df)
        print(f"[CandleLib] {self.symbol}: saved {len(df)} clean candles")
        return df

    # ── Realtime append ───────────────────────────────────────────

    def append_realtime(self, new_df: pd.DataFrame) -> None:
        """
        Append new candles from realtime feed.
        Maintains sliding window of CANDLE_LIBRARY_COUNT candles.
        Saves updated library to disk.
        """
        if new_df.empty:
            return

        current = self._df if self._df is not None else self.load()
        if current.empty:
            self._df = new_df
        else:
            combined = pd.concat([current, new_df], ignore_index=True)
            combined, warnings = _qc_check(combined, self.symbol)
            for w in warnings:
                print(w)
            # Keep only the newest CANDLE_LIBRARY_COUNT rows
            self._df = combined.tail(config.CANDLE_LIBRARY_COUNT).reset_index(drop=True)

        self._save(self._df)
        # Also store latest N in Redis for realtime access
        latest = self._df.tail(config.CANDLE_REALTIME_MAX_CACHE)
        self._r.set(
            config.CANDLE_LIBRARY_REALTIME_KEY.format(symbol=self.symbol),
            latest.to_json(orient="records", date_format="iso"),
            ex=600,  # 10-min expiry for realtime cache
        )

    # ── Get data ──────────────────────────────────────────────────

    def get_dataframe(self, n: Optional[int] = None) -> pd.DataFrame:
        """Return the latest N candles (or all if n=None)."""
        if self._df is None:
            self.load()
        if self._df is None or self._df.empty:
            return pd.DataFrame()
        return self._df.tail(n) if n else self._df

    def snapshot(self, n: int = None) -> pd.DataFrame:
        """
        Versioned snapshot for model training.
        Returns copy of current library (no drift from live updates during training).
        """
        df = self.get_dataframe(n)
        return df.copy()

    # ── Refresh latest N candles (light update) ───────────────────

    def update_recent(self, count: int = 200) -> pd.DataFrame:
        """
        Fetch only the most recent `count` candles and merge into library.
        Lighter operation than full refresh — call each cycle.
        """
        import deriv_data
        try:
            new_df = deriv_data.fetch_candles(symbol=self.symbol, count=count)
            new_df["datetime"] = pd.to_datetime(new_df["datetime"], utc=True)
            self.append_realtime(new_df)
            return new_df
        except Exception as exc:
            print(f"[CandleLib] {self.symbol}: update_recent failed: {exc}")
            return pd.DataFrame()

    # ── Stats ─────────────────────────────────────────────────────

    def stats(self) -> dict:
        df = self.get_dataframe()
        if df.empty:
            return {"symbol": self.symbol, "count": 0}
        return {
            "symbol"    : self.symbol,
            "count"     : len(df),
            "from"      : str(df["datetime"].min()),
            "to"        : str(df["datetime"].max()),
            "parquet"   : self._parquet_path,
        }


# ──────────────────────────────────────────────────────────────────
# Multi-symbol library manager
# ──────────────────────────────────────────────────────────────────

class CandleLibraryManager:
    """Manages CandleLibrary instances for all symbols."""

    def __init__(self, symbols: list[str] = None) -> None:
        self._symbols = symbols or list(config.SCAN_SYMBOLS)
        self._libs: dict[str, CandleLibrary] = {
            sym: CandleLibrary(sym) for sym in self._symbols
        }

    def get(self, symbol: str) -> CandleLibrary:
        if symbol not in self._libs:
            self._libs[symbol] = CandleLibrary(symbol)
        return self._libs[symbol]

    def refresh_all(self) -> None:
        for sym, lib in self._libs.items():
            print(f"[LibMgr] Refreshing {sym}...")
            lib.refresh()

    def load_all(self) -> None:
        for lib in self._libs.values():
            lib.load()

    def update_all_recent(self) -> None:
        for lib in self._libs.values():
            lib.update_recent()

    def stats_all(self) -> list[dict]:
        return [lib.stats() for lib in self._libs.values()]


if __name__ == "__main__":
    lib = CandleLibrary(config.SYMBOL)
    df = lib.refresh(count=config.CANDLE_LIBRARY_COUNT)
    print(f"Library stats: {lib.stats()}")
    print(df.tail(3).to_string())
