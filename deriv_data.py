"""
deriv_data.py
=============
Lấy dữ liệu nến (OHLCV) từ Deriv WebSocket API và lưu vào Redis.
"""

import asyncio
import json
import redis
import pandas as pd
import websockets
from datetime import datetime
from typing import Dict

import config


def fetch_candles(symbol: str = config.SYMBOL,
                  count: int = config.CANDLE_COUNT,
                  granularity: int = config.GRANULARITY) -> pd.DataFrame:
    """
    Gọi đồng bộ để lấy dữ liệu nến từ Deriv WebSocket API.

    Returns
    -------
    pd.DataFrame với các cột: open, high, low, close, epoch
    """
    return asyncio.run(_async_fetch_candles(symbol, count, granularity))


def fetch_candles_batch(
    symbols: list,
    count: int = config.CANDLE_COUNT,
    granularity: int = config.GRANULARITY,
) -> Dict[str, pd.DataFrame]:
    """
    Lấy nến cho nhiều symbol trên một WebSocket duy nhất, xử lý song song.

    Dùng req_id để phân biệt response — tránh phải mở N handshake TCP/TLS.
    Fallback tuần tự nếu server không trả đủ response trong thời gian quy định.

    Returns
    -------
    dict {symbol: pd.DataFrame}  — DataFrame rỗng nếu symbol bị lỗi.
    """
    return asyncio.run(_async_fetch_candles_batch(symbols, count, granularity))


async def _async_fetch_candles_batch(
    symbols: list,
    count: int,
    granularity: int,
) -> Dict[str, pd.DataFrame]:
    """
    Fetch nhiều symbol trên một WebSocket duy nhất.

    Gửi tất cả request ngay sau khi kết nối, gắn req_id = index để
    map response về đúng symbol, thu về bất đồng bộ.
    """
    if not symbols:
        return {}

    results: Dict[str, pd.DataFrame] = {sym: pd.DataFrame() for sym in symbols}
    req_id_to_sym: Dict[int, str] = {}

    try:
        async with websockets.connect(config.DERIV_WS_URL) as ws:
            # Gửi tất cả request cùng lúc
            for idx, sym in enumerate(symbols, start=1):
                req = {
                    "ticks_history"  : sym,
                    "adjust_start_time": 1,
                    "count"          : count,
                    "end"            : "latest",
                    "granularity"    : granularity,
                    "start"          : 1,
                    "style"          : "candles",
                    "req_id"         : idx,
                }
                await ws.send(json.dumps(req))
                req_id_to_sym[idx] = sym

            # Thu response cho đến khi đủ hoặc hết timeout
            remaining = set(symbols)
            while remaining:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                except asyncio.TimeoutError:
                    print(f"  ⚠️  [batch] Timeout — bỏ qua: {remaining}")
                    break

                response = json.loads(raw)
                r_id = response.get("req_id")
                sym  = req_id_to_sym.get(r_id)
                if sym is None:
                    continue

                if "error" in response:
                    print(f"  [{sym}] ⚠️  fetch failed: {response['error']['message']}")
                else:
                    candles = response.get("candles", [])
                    if candles:
                        df = pd.DataFrame(candles)
                        df["epoch"] = pd.to_datetime(df["epoch"], unit="s")
                        df = df.rename(columns={"epoch": "datetime"})
                        df = df[["datetime", "open", "high", "low", "close"]].astype(
                            {"open": float, "high": float, "low": float, "close": float}
                        )
                        results[sym] = df

                remaining.discard(sym)

    except Exception as exc:
        print(f"  ⚠️  [batch] WebSocket error: {exc} — fallback to per-symbol fetch")
        # Fallback: fetch song song từng symbol riêng lẻ
        async def _fetch_one(sym: str):
            try:
                df = await _async_fetch_candles(sym, count, granularity)
                return sym, df
            except Exception as e:
                print(f"  [{sym}] ⚠️  fetch failed: {e}")
                return sym, pd.DataFrame()
        pairs = await asyncio.gather(*[_fetch_one(s) for s in symbols])
        results = dict(pairs)

    return results



async def _async_fetch_candles(symbol: str, count: int, granularity: int) -> pd.DataFrame:
    request = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": count,
        "end": "latest",
        "granularity": granularity,
        "start": 1,
        "style": "candles",
    }

    async with websockets.connect(config.DERIV_WS_URL) as ws:
        await ws.send(json.dumps(request))
        response = json.loads(await ws.recv())

    if "error" in response:
        raise RuntimeError(f"Deriv API lỗi: {response['error']['message']}")

    candles = response.get("candles", [])
    if not candles:
        raise ValueError("Không có dữ liệu nến trả về từ Deriv.")

    df = pd.DataFrame(candles)
    df["epoch"] = pd.to_datetime(df["epoch"], unit="s")
    df = df.rename(columns={"epoch": "datetime"})
    df = df[["datetime", "open", "high", "low", "close"]].astype(
        {"open": float, "high": float, "low": float, "close": float}
    )
    return df


def save_candles_to_redis(df: pd.DataFrame,
                          r: redis.Redis,
                          key: str = "Deriv_Candles") -> None:
    """Lưu DataFrame nến vào Redis dưới dạng JSON string."""
    r.set(key, df.to_json(orient="records", date_format="iso"))
    print(f"[{datetime.now()}] Đã lưu {len(df)} nến vào Redis key='{key}'")


def load_candles_from_redis(r: redis.Redis,
                             key: str = "Deriv_Candles") -> pd.DataFrame:
    """Đọc DataFrame nến từ Redis."""
    raw = r.get(key)
    if raw is None:
        raise KeyError(f"Không tìm thấy dữ liệu tại Redis key='{key}'")
    df = pd.read_json(raw, orient="records")
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


# -------------------------------------------------------
# Chạy trực tiếp để kiểm tra
# -------------------------------------------------------
if __name__ == "__main__":
    print(f"Đang lấy dữ liệu {config.SYMBOL} từ Deriv...")
    df = fetch_candles()
    print(df.tail(5).to_string())

    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB)
    save_candles_to_redis(df, r)
    print("Hoàn tất.")
