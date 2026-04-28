"""
deriv_trade.py
==============
Đặt lệnh Binary Options (CALL / PUT) qua Deriv WebSocket API và theo dõi kết quả.

Luồng hoạt động:
  1. Xác thực token, lấy số dư tài khoản
  2. Đặt hợp đồng CALL hoặc PUT với kích thước lệnh được truyền vào
  3. Chờ hợp đồng kết thúc, lấy kết quả (thắng/thua, P&L)
  4. Trả về dict kết quả cho robot.py

API tham khảo: https://api.deriv.com/
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Optional

import websockets

import config


# ------------------------------------------------------------------
# Xác thực và lấy số dư  (với cache TTL để giảm round-trip)
# ------------------------------------------------------------------

_balance_cache: dict = {"value": None, "ts": 0.0}
_BALANCE_CACHE_TTL   = 5.0   # giây
_TOKEN_PLACEHOLDERS  = {
    "",
    "your_real_token",
    "your_deriv_api_token_here",
    "changeme",
    "changeme_in_env",
}


def _token_present(token: str) -> bool:
    cleaned = (token or "").strip()
    return cleaned.lower() not in _TOKEN_PLACEHOLDERS


async def _get_balance_async() -> Optional[float]:
    """Xác thực và lấy số dư tài khoản hiện tại."""
    async with websockets.connect(config.DERIV_WS_URL) as ws:
        await ws.send(json.dumps({"authorize": config.DERIV_API_TOKEN}))
        res = json.loads(await ws.recv())
        if "error" in res:
            raise PermissionError(f"Xác thực Deriv thất bại: {res['error']['message']}")
        return float(res["authorize"].get("balance", 0))


def get_balance(force_refresh: bool = False) -> float:
    """
    Lấy số dư tài khoản (đồng bộ).

    Kết quả được cache trong _BALANCE_CACHE_TTL giây.
    Truyền force_refresh=True để bỏ qua cache (dùng sau khi đặt lệnh thật).
    """
    now = time.monotonic()
    if (
        not force_refresh
        and _balance_cache["value"] is not None
        and (now - _balance_cache["ts"]) < _BALANCE_CACHE_TTL
    ):
        return _balance_cache["value"]

    value = asyncio.run(_get_balance_async())
    _balance_cache["value"] = value
    _balance_cache["ts"]    = now
    return value


def invalidate_balance_cache() -> None:
    """Xoá cache số dư — gọi sau khi đặt lệnh thật."""
    _balance_cache["value"] = None
    _balance_cache["ts"]    = 0.0


# ------------------------------------------------------------------
# Deriv live-path probe (token → broker reachable → order-capable)
# ------------------------------------------------------------------

async def probe_live_path(timeout_seconds: float = 6.0) -> dict:
    """
    Probe đường đi giao dịch thật theo 3 tầng:
      1) token_present
      2) broker_reachable (WebSocket + authorize)
      3) order_capable (proposal test, KHÔNG đặt lệnh thật)
    """
    token = (config.DERIV_API_TOKEN or "").strip()
    token_present = _token_present(token)
    deriv_env = getattr(config, "DERIV_ENV", "demo")
    token_source = getattr(config, "DERIV_TOKEN_SOURCE", "unknown")
    symbol = (
        (config.SCAN_SYMBOLS[0] if getattr(config, "SCAN_SYMBOLS", None) else None)
        or getattr(config, "SYMBOL", "R_100")
        or "R_100"
    )

    result = {
        "token_present"    : token_present,
        "deriv_env"        : deriv_env,
        "token_source"     : token_source,
        "broker_reachable" : False,
        "order_capable"    : False,
        "stage"            : "token",
        "symbol"           : symbol,
        "timeout_seconds"  : timeout_seconds,
        "latency_ms"       : {
            "connect"  : None,
            "authorize": None,
            "proposal" : None,
            "total"    : None,
        },
        "detail"           : "",
    }

    t0 = time.perf_counter()

    if not token_present:
        expected_var = "DERIV_API_TOKEN_LIVE" if deriv_env == "live" else "DERIV_API_TOKEN_DEMO"
        result["detail"] = (
            f"Token Deriv thiếu cho mode '{deriv_env}'. "
            f"Hãy cấu hình {expected_var} (hoặc DERIV_API_TOKEN cho tương thích cũ)."
        )
        return result

    try:
        t_conn = time.perf_counter()
        async with websockets.connect(
            config.DERIV_WS_URL,
            open_timeout=timeout_seconds,
            close_timeout=2,
            ping_timeout=timeout_seconds,
        ) as ws:
            result["latency_ms"]["connect"] = round((time.perf_counter() - t_conn) * 1000, 2)

            # Stage 1: authorize
            t_auth = time.perf_counter()
            await ws.send(json.dumps({"authorize": token}))
            auth_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_seconds))
            result["latency_ms"]["authorize"] = round((time.perf_counter() - t_auth) * 1000, 2)
            if "error" in auth_msg:
                result["stage"] = "authorize"
                result["latency_ms"]["total"] = round((time.perf_counter() - t0) * 1000, 2)
                result["detail"] = f"Authorize thất bại: {auth_msg['error'].get('message', 'unknown')}"
                return result

            result["broker_reachable"] = True
            result["stage"] = "broker"

            # Stage 2: proposal test (không tạo order thật)
            proposal_req = {
                "proposal"      : 1,
                "amount"        : 1,
                "basis"         : "stake",
                "contract_type" : "CALL",
                "currency"      : config.TRADE_CURRENCY,
                "duration"      : max(1, int(config.CONTRACT_DURATION)),
                "duration_unit" : config.CONTRACT_DURATION_UNIT,
                "symbol"        : symbol,
            }
            t_prop = time.perf_counter()
            await ws.send(json.dumps(proposal_req))
            prop_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_seconds))
            result["latency_ms"]["proposal"] = round((time.perf_counter() - t_prop) * 1000, 2)
            if "error" in prop_msg:
                result["stage"] = "proposal"
                result["latency_ms"]["total"] = round((time.perf_counter() - t0) * 1000, 2)
                result["detail"] = f"Proposal thất bại: {prop_msg['error'].get('message', 'unknown')}"
                return result

            result["order_capable"] = True
            result["stage"] = "order"
            result["latency_ms"]["total"] = round((time.perf_counter() - t0) * 1000, 2)
            result["detail"] = "Token hợp lệ, broker reachable, proposal pass (sẵn sàng đặt lệnh)"
            return result

    except Exception as exc:
        result["stage"] = "connect"
        result["latency_ms"]["total"] = round((time.perf_counter() - t0) * 1000, 2)
        result["detail"] = f"Không thể kết nối Deriv WS: {exc}"
        return result


# ------------------------------------------------------------------
# Đặt lệnh và chờ kết quả
# ------------------------------------------------------------------

async def _place_and_wait_async(contract_type: str,
                                 symbol: str,
                                 stake: float) -> dict:
    """
    Đặt hợp đồng Binary Options và chờ kết thúc để lấy kết quả.

    Returns
    -------
    dict với các key:
      contract_id, won (bool), buy_price, sell_price, payout, pnl, profit, status
    """
    async with websockets.connect(config.DERIV_WS_URL) as ws:
        # Bước 1: Xác thực
        await ws.send(json.dumps({"authorize": config.DERIV_API_TOKEN}))
        auth_res = json.loads(await ws.recv())
        if "error" in auth_res:
            raise PermissionError(f"Xác thực thất bại: {auth_res['error']['message']}")

        balance = float(auth_res["authorize"].get("balance", 0))
        print(
            f"[{datetime.now()}] Đăng nhập thành công: "
            f"balance={balance} {auth_res['authorize'].get('currency')}"
        )

        # Bước 2: Đặt lệnh
        buy_req = {
            "buy": "1",
            "price": stake,
            "parameters": {
                "amount"        : stake,
                "basis"         : "stake",
                "contract_type" : contract_type,
                "currency"      : config.TRADE_CURRENCY,
                "duration"      : config.CONTRACT_DURATION,
                "duration_unit" : config.CONTRACT_DURATION_UNIT,
                "symbol"        : symbol,
            },
        }
        await ws.send(json.dumps(buy_req))
        buy_res = json.loads(await ws.recv())

        if "error" in buy_res:
            raise RuntimeError(f"Đặt lệnh thất bại: {buy_res['error']['message']}")

        buy_info    = buy_res.get("buy", {})
        contract_id = buy_info.get("contract_id", "")
        buy_price   = float(buy_info.get("buy_price", stake))
        payout      = float(buy_info.get("payout", 0))

        print(
            f"[{datetime.now()}] ✅ Đặt lệnh {contract_type} thành công! "
            f"contract_id={contract_id} stake={buy_price} payout={payout}"
        )

        # Bước 3: Theo dõi hợp đồng đến khi kết thúc
        await ws.send(json.dumps({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        }))

        sell_price = 0.0
        status     = "open"
        while True:
            msg = json.loads(await ws.recv())
            if "error" in msg:
                print(f"[WARN] Lỗi khi theo dõi: {msg['error']['message']}")
                break
            poc = msg.get("proposal_open_contract", {})
            status = poc.get("status", "open")
            if status in ("sold", "won", "lost"):
                sell_price = float(poc.get("sell_price", 0))
                break
            # Chờ cập nhật tiếp theo
            await asyncio.sleep(1)

    won = sell_price > 0 and sell_price >= buy_price
    pnl = sell_price - buy_price

    return {
        "contract_id": str(contract_id),
        "won"        : won,
        "buy_price"  : buy_price,
        "sell_price" : sell_price,
        "payout"     : payout,
        "pnl"        : round(pnl, 2),
        "status"     : status,
    }


def place_and_wait(contract_type: str,
                   symbol: str,
                   stake: float) -> dict:
    """
    Đặt hợp đồng và chờ kết quả (đồng bộ).

    Parameters
    ----------
    contract_type : 'CALL' hoặc 'PUT'
    symbol        : mã thị trường, vd. 'R_100'
    stake         : số tiền đặt cược (USD)

    Returns
    -------
    dict kết quả (xem _place_and_wait_async)
    """
    result = asyncio.run(_place_and_wait_async(contract_type, symbol, stake))
    # Balance đã thay đổi sau khi đặt lệnh — xoá cache để cycle tiếp lấy lại
    invalidate_balance_cache()
    return result


# ------------------------------------------------------------------
# Chạy trực tiếp để kiểm tra
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("Đang lấy số dư...")
    bal = get_balance()
    print(f"Số dư: {bal} {config.TRADE_CURRENCY}")
