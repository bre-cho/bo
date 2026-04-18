"""
logger.py
=========
Nhật ký giao dịch tự động.

Chức năng:
  - Ghi mỗi lệnh vào file CSV (trade_log.csv)
  - Đồng thời đẩy vào Redis List để truy vấn nhanh
  - Duy trì thống kê tích lũy trong Redis Hash (O(1) đọc)
  - Tính win-rate, tổng P&L, và các chỉ số hiệu suất
"""

import csv
import json
import os
import redis
from datetime import datetime
from dataclasses import dataclass, asdict

import config


# ------------------------------------------------------------------
# Dataclass ghi nhận một lệnh
# ------------------------------------------------------------------

@dataclass
class TradeRecord:
    timestamp:    str     # ISO datetime
    symbol:       str
    direction:    str     # 'CALL' / 'PUT'
    signal_score: float
    stake:        float   # Số tiền đặt
    payout:       float   # Khoản nhận về (0 nếu thua)
    pnl:          float   # Lãi/lỗ thực tế
    won:          bool
    contract_id:  str     = ""
    rsi:          float   = 0.0
    momentum:     float   = 0.0
    macd_hist:    float   = 0.0
    bb_position:  float   = 0.0


# ------------------------------------------------------------------
# Logger
# ------------------------------------------------------------------

_CSV_HEADER = [
    "timestamp", "symbol", "direction", "signal_score",
    "stake", "payout", "pnl", "won",
    "contract_id", "rsi", "momentum", "macd_hist", "bb_position",
]


class TradeLogger:
    """
    Ghi nhật ký giao dịch vào CSV và Redis.

    Thống kê được duy trì tích lũy trong Redis Hash để get_stats()
    trả về ngay lập tức thay vì parse toàn bộ log list.

    Sử dụng:
        logger = TradeLogger()
        logger.log(record)
        stats = logger.get_stats()
    """

    def __init__(self,
                 csv_path: str = config.TRADE_LOG_FILE,
                 redis_key: str = config.REDIS_LOG_KEY) -> None:
        self._csv_path   = csv_path
        self._redis_key  = redis_key
        self._stats_key  = config.REDIS_STATS_SUMMARY_KEY
        self._r = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
        )
        self._ensure_csv()

    def _ensure_csv(self) -> None:
        """Tạo file CSV với header nếu chưa tồn tại."""
        if not os.path.exists(self._csv_path):
            with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_HEADER)
                writer.writeheader()

    def log(self, record: TradeRecord) -> None:
        """Ghi một lệnh vào CSV, Redis list, và cập nhật stats hash."""
        row = asdict(record)

        # Ghi vào CSV
        with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_HEADER)
            writer.writerow(row)

        # Đẩy vào Redis List (giữ tối đa 500 bản ghi) + cập nhật stats hash
        pipe = self._r.pipeline()
        pipe.lpush(self._redis_key, json.dumps(row))
        pipe.ltrim(self._redis_key, 0, 499)

        # Cập nhật thống kê tích lũy — O(1) mỗi lệnh
        pipe.hincrby(self._stats_key, "total_trades", 1)
        if record.won:
            pipe.hincrby(self._stats_key, "wins", 1)
        pnl_rounded = round(record.pnl, 4)
        pipe.hincrbyfloat(self._stats_key, "total_pnl", pnl_rounded)
        if record.pnl > 0:
            pipe.hincrbyfloat(self._stats_key, "gross_win", pnl_rounded)
        elif record.pnl < 0:
            pipe.hincrbyfloat(self._stats_key, "gross_loss", abs(pnl_rounded))
        pipe.execute()

        status = "✅ THẮNG" if record.won else "❌ THUA"
        print(
            f"[Logger] {status} | {record.symbol} {record.direction} | "
            f"stake={record.stake:.2f} payout={record.payout:.2f} "
            f"P&L={record.pnl:+.2f} USD | score={record.signal_score}"
        )

    def get_stats(self) -> dict:
        """
        Trả về chỉ số hiệu suất.

        Đọc từ stats hash Redis (O(1)). Nếu hash chưa tồn tại (lần đầu
        chạy sau migration), fallback sang parse toàn bộ log và khởi
        tạo hash để các lần sau nhanh hơn.
        """
        data = self._r.hgetall(self._stats_key)
        if data:
            total     = int(data.get(b"total_trades", 0))
            if total == 0:
                return {"message": "Chưa có dữ liệu giao dịch."}
            wins      = int(data.get(b"wins", 0))
            total_pnl = float(data.get(b"total_pnl", 0))
            gross_win = float(data.get(b"gross_win", 0))
            gross_loss= float(data.get(b"gross_loss", 0))
        else:
            # Fallback: build stats từ log list (chạy một lần khi migrate)
            return self._rebuild_stats_from_log()

        win_rate      = wins / total * 100
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
        return {
            "total_trades" : total,
            "wins"         : wins,
            "losses"       : total - wins,
            "win_rate_pct" : round(win_rate, 2),
            "total_pnl"    : round(total_pnl, 2),
            "gross_win"    : round(gross_win, 2),
            "gross_loss"   : round(gross_loss, 2),
            "profit_factor": round(profit_factor, 4),
        }

    def _rebuild_stats_from_log(self) -> dict:
        """
        Tính stats từ log list và ghi vào stats hash.

        Chỉ gọi khi hash chưa tồn tại (migration / khởi tạo lần đầu).
        """
        raw_list = self._r.lrange(self._redis_key, 0, -1)
        if not raw_list:
            return {"message": "Chưa có dữ liệu giao dịch."}

        records = [json.loads(r) for r in raw_list]
        total   = len(records)
        wins    = sum(1 for r in records if r.get("won"))
        total_pnl = sum(r.get("pnl", 0) for r in records)
        win_rate  = wins / total * 100 if total else 0

        gross_win  = sum(r["pnl"] for r in records if r.get("pnl", 0) > 0)
        gross_loss = abs(sum(r["pnl"] for r in records if r.get("pnl", 0) < 0))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        # Ghi vào hash để các lần sau dùng nhanh
        pipe = self._r.pipeline()
        pipe.hset(self._stats_key, mapping={
            "total_trades": total,
            "wins"        : wins,
            "total_pnl"   : round(total_pnl, 4),
            "gross_win"   : round(gross_win, 4),
            "gross_loss"  : round(gross_loss, 4),
        })
        pipe.execute()

        return {
            "total_trades" : total,
            "wins"         : wins,
            "losses"       : total - wins,
            "win_rate_pct" : round(win_rate, 2),
            "total_pnl"    : round(total_pnl, 2),
            "gross_win"    : round(gross_win, 2),
            "gross_loss"   : round(gross_loss, 2),
            "profit_factor": round(profit_factor, 4),
        }

    def print_stats(self) -> None:
        stats = self.get_stats()
        if "message" in stats:
            print(f"[Logger] {stats['message']}")
            return
        print(
            f"\n{'='*50}\n"
            f"📊 HIỆU SUẤT GIAO DỊCH\n"
            f"{'='*50}\n"
            f"  Tổng lệnh      : {stats['total_trades']}\n"
            f"  Thắng / Thua   : {stats['wins']} / {stats['losses']}\n"
            f"  Tỉ lệ thắng    : {stats['win_rate_pct']}%\n"
            f"  Tổng P&L       : {stats['total_pnl']:+.2f} USD\n"
            f"  Gross Win      : +{stats['gross_win']:.2f} USD\n"
            f"  Gross Loss     : -{stats['gross_loss']:.2f} USD\n"
            f"  Profit Factor  : {stats['profit_factor']}\n"
            f"{'='*50}"
        )


# ------------------------------------------------------------------
# Chạy trực tiếp để xem thống kê
# ------------------------------------------------------------------
if __name__ == "__main__":
    logger = TradeLogger()
    logger.print_stats()
