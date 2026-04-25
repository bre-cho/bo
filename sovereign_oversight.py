"""
sovereign_oversight.py
======================
STRATEGIC SOVEREIGN OVERSIGHT LAYER (SSOL)

Bước chuyển từ federated governance intelligence platform
sang sovereign media intelligence empire OS.

Vấn đề với kiến trúc hiện tại (single-engine / symbol-pool):
  • DecisionEngine tự vận hành tốt ở cấp cluster đơn, nhưng thiếu
    tầng điều hành chiến lược ở cấp network.
  • Không có cơ chế phân bổ nguồn lực liên-cluster theo hiệu suất.
  • Không có policy kill/scale/quarantine dựa trên sức khỏe từng cluster.
  • Không có bộ nhớ chiến lược liên-cluster để tránh lặp sai lầm hệ thống.
  • Không có ngân sách attention toàn cục.

SSOL trả lời năm câu hỏi cấp network:
  1. Mục tiêu hệ thống hiện tại là gì?   → NetworkObjectiveEngine
  2. Cluster nào đang khỏe / yếu / nguy? → ClusterScorer
  3. Tài nguyên phân bổ thế nào?         → ResourceAllocator
  4. Cluster nào nên tồn tại / bị kill?  → ClusterGovernor
  5. Hệ thống đủ an toàn toàn mạng?      → SovereigntyGuardrails

Năm tầng kiến trúc:
  NetworkObjectiveEngine:
    - Phase SURVIVAL  : toàn hệ drawdown nguy hiểm → ưu tiên sống còn
    - Phase GROWTH    : win rate tốt → tăng tốc vốn
    - Phase EXPANSION : PF cao → mở rộng cluster mới
    - Phase REBALANCE : mặc định → cân bằng lại tài nguyên

  ClusterScorer:
    - Score = f(win_rate, profit_factor, drawdown, n_trades, regime_fit)
    - Health: HEALTHY / DEGRADED / CRITICAL / QUARANTINED / DEAD

  ResourceAllocator:
    - Phân bổ capital_fraction + attention_weight theo score
    - Cluster DEAD/QUARANTINED nhận budget tối thiểu
    - Cluster HEALTHY nhận budget tương ứng score tương đối
    - Ràng buộc: max_capital_fraction per cluster

  ClusterGovernor:
    - Phát lệnh: KEEP / SCALE_UP / SCALE_DOWN / PAUSE / QUARANTINE / KILL / REVIVE
    - Quản lý quarantine timer: sau N chu kỳ thử revive
    - Shadow mode: chỉ log, không enforce

  SovereigntyGuardrails:
    - Network-wide drawdown limit: toàn mạng lỗ quá → emergency pause
    - Min active clusters: không để hệ rỗng
    - Regime diversity: không concentrate vào 1 regime
    - Max capital concentration per cluster

  StrategicMemory:
    - Lưu bài học: cluster_id + điều kiện thất bại + lesson
    - Redis-persistent, FIFO, max 200 entries
    - Tránh lặp sai lầm hệ thống qua các cluster

Thành phần:
  NetworkPhase          — enum: SURVIVAL, GROWTH, EXPANSION, REBALANCE
  ClusterHealth         — enum: HEALTHY, DEGRADED, CRITICAL, QUARANTINED, DEAD
  ClusterAction         — enum: KEEP, SCALE_UP, SCALE_DOWN, PAUSE, QUARANTINE, KILL, REVIVE
  ClusterTelemetry      — per-cluster metrics (win_rate, pnl, drawdown, ...)
  ResourceBudget        — per-cluster resource allocation
  ClusterVerdict        — governor decision for a cluster
  SovereignReport       — full SSOL report
  ClusterTelemetryCollector — reads Redis trade log → per-symbol metrics
  NetworkObjectiveEngine — system-level phase + objective
  ClusterScorer         — health + score per cluster
  ResourceAllocator     — dynamic budget assignment
  ClusterGovernor       — lifecycle policy enforcement
  SovereigntyGuardrails — network safety checks
  StrategicMemory       — cross-cluster lessons
  SovereignOversightLayer — orchestrator
  get_sovereign_report()  — API helper
  run_sovereign_cycle()   — top-level entry point

Cách dùng:
  >>> from sovereign_oversight import run_sovereign_cycle
  >>> report = run_sovereign_cycle(active_symbols=["R_10","R_100"])
  >>> print(report.network_objective)   # current system phase
  >>> print(report.cluster_verdicts)    # per-cluster decisions
  >>> print(report.insights)            # strategic intelligence
"""

from __future__ import annotations

import json
import math
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config


# ──────────────────────────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────────────────────────

_REDIS_SSOL_REPORT_KEY    = getattr(config, "REDIS_SSOL_REPORT_KEY",    "Deriv_SSOL_Report")
_REDIS_SSOL_VERDICTS_KEY  = getattr(config, "REDIS_SSOL_VERDICTS_KEY",  "Deriv_SSOL_Verdicts")
_REDIS_SSOL_MEMORY_KEY    = getattr(config, "REDIS_SSOL_MEMORY_KEY",    "Deriv_SSOL_Memory")
_REDIS_SSOL_QUARANTINE_KEY= getattr(config, "REDIS_SSOL_QUARANTINE_KEY","Deriv_SSOL_Quarantine")


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


# ──────────────────────────────────────────────────────────────────
# 1. Enumerations
# ──────────────────────────────────────────────────────────────────

class NetworkPhase(str, Enum):
    """
    Giai đoạn chiến lược của toàn network.

    SURVIVAL  : toàn mạng đang drawdown nguy hiểm → ưu tiên sống còn,
                thu hẹp cluster, giảm stake, không mở rộng.
    GROWTH    : win rate tổng tốt → tăng tốc vốn, giữ cluster mạnh.
    EXPANSION : profit factor cao → mở rộng sang cluster mới.
    REBALANCE : trạng thái mặc định → cân bằng tài nguyên, giám sát.
    """
    SURVIVAL  = "SURVIVAL"
    GROWTH    = "GROWTH"
    EXPANSION = "EXPANSION"
    REBALANCE = "REBALANCE"


class ClusterHealth(str, Enum):
    """
    Sức khỏe của một cluster (symbol hoặc symbol group).

    HEALTHY    : hoạt động tốt, không có vấn đề.
    DEGRADED   : hiệu suất giảm nhưng chưa nguy hiểm.
    CRITICAL   : hiệu suất xấu, cần can thiệp ngay.
    QUARANTINED: tạm ngưng, đang trong thời gian kiểm sát.
    DEAD       : bị loại khỏi pool hoàn toàn.
    """
    HEALTHY    = "HEALTHY"
    DEGRADED   = "DEGRADED"
    CRITICAL   = "CRITICAL"
    QUARANTINED= "QUARANTINED"
    DEAD       = "DEAD"


class ClusterAction(str, Enum):
    """
    Lệnh governor phát ra cho cluster.

    KEEP       : giữ nguyên trạng thái.
    SCALE_UP   : tăng attention/capital budget.
    SCALE_DOWN : giảm attention/capital budget.
    PAUSE      : tạm dừng giao dịch, giữ trong pool.
    QUARANTINE : cách ly, ngưng nhận lệnh, theo dõi.
    KILL       : xóa khỏi active pool vĩnh viễn (trong run này).
    REVIVE     : khôi phục cluster từ QUARANTINED sau kiểm sát.
    """
    KEEP       = "KEEP"
    SCALE_UP   = "SCALE_UP"
    SCALE_DOWN = "SCALE_DOWN"
    PAUSE      = "PAUSE"
    QUARANTINE = "QUARANTINE"
    KILL       = "KILL"
    REVIVE     = "REVIVE"


# ──────────────────────────────────────────────────────────────────
# 2. Dataclasses
# ──────────────────────────────────────────────────────────────────

@dataclass
class ClusterTelemetry:
    """
    Số liệu hiệu suất của một cluster trong một cửa sổ thời gian gần đây.

    Được tính từ Redis trade log, lọc theo cluster_id (symbol).
    """
    cluster_id            : str
    n_trades              : int   = 0
    win_rate              : float = 0.0   # 0.0 – 1.0
    profit_factor         : float = 0.0   # tổng lãi / tổng lỗ
    total_pnl             : float = 0.0
    avg_pnl_per_trade     : float = 0.0
    max_consecutive_loss  : int   = 0
    drawdown_pct          : float = 0.0   # max drawdown as fraction of peak equity
    last_trade_ts         : str   = ""
    regime                : str   = "unknown"   # current market regime (if available)
    is_active             : bool  = True


@dataclass
class ResourceBudget:
    """
    Ngân sách tài nguyên được phân bổ cho một cluster.

    capital_fraction : phần vốn tối đa cluster này được sử dụng (0.0-1.0).
    attention_weight : trọng số quét (proportion of scan cycles devoted).
    compute_priority : 1=low, 2=medium, 3=high (ảnh hưởng đến scan order).
    max_concurrent   : số lệnh tối đa đang chờ cùng lúc.
    """
    cluster_id       : str
    capital_fraction : float = 0.20
    attention_weight : float = 0.20
    compute_priority : int   = 2
    max_concurrent   : int   = 1


@dataclass
class ClusterVerdict:
    """
    Phán quyết của Governor cho một cluster trong một chu kỳ SSOL.
    """
    cluster_id   : str
    health       : str   = ClusterHealth.HEALTHY.value
    action       : str   = ClusterAction.KEEP.value
    score        : float = 0.0
    confidence   : float = 1.0
    rationale    : str   = ""
    enforced     : bool  = False   # False nếu shadow mode


@dataclass
class SovereignReport:
    """
    Báo cáo đầy đủ của một chu kỳ SSOL.
    """
    timestamp              : str
    network_phase          : str
    network_health_score   : float
    n_clusters_total       : int
    n_clusters_active      : int
    n_clusters_quarantined : int
    n_clusters_dead        : int
    cluster_verdicts       : List[dict]
    resource_budgets       : List[dict]
    guardrail_alerts       : List[str]
    strategic_lessons      : List[str]
    insights               : List[str]
    shadow_mode            : bool


# ──────────────────────────────────────────────────────────────────
# 3. ClusterTelemetryCollector
# ──────────────────────────────────────────────────────────────────

class ClusterTelemetryCollector:
    """
    Đọc Redis trade log và tính toán telemetry per-cluster (per-symbol).

    Trade log là Redis List tại config.REDIS_LOG_KEY, mỗi entry là JSON:
      {"symbol": "R_100", "won": true, "pnl": 0.85, "timestamp": "...", ...}

    Chỉ lấy N giao dịch gần nhất (window) để tính metrics.
    """

    def __init__(self, window: int = 200) -> None:
        """
        Parameters
        ----------
        window : int
            Số lệnh gần nhất để tính metrics (per cluster).
        """
        self._window = window

    def collect(self, cluster_ids: List[str]) -> Dict[str, ClusterTelemetry]:
        """
        Tính ClusterTelemetry cho từng cluster_id từ Redis trade log.

        Returns dict {cluster_id: ClusterTelemetry}.
        Missing clusters trả về telemetry với n_trades=0.
        """
        # Giới hạn số bản ghi đọc: window * số cluster, tối đa TRADE_LOG_WINDOW
        read_limit = min(
            self._window * max(len(cluster_ids), 1),
            getattr(config, "TRADE_LOG_WINDOW", 200),
        )
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
            )
            raw_list = r.lrange(config.REDIS_LOG_KEY, 0, read_limit - 1)
        except Exception:
            raw_list = []

        # Parse and group by symbol
        by_cluster: Dict[str, List[dict]] = defaultdict(list)
        for raw in raw_list:
            try:
                rec = json.loads(raw)
                sym = rec.get("symbol", "")
                if sym:
                    by_cluster[sym].append(rec)
            except Exception:
                pass

        result: Dict[str, ClusterTelemetry] = {}
        for cid in cluster_ids:
            records = by_cluster.get(cid, [])
            # Most recent window records
            records = records[: self._window]
            result[cid] = self._compute(cid, records)
        return result

    def _compute(self, cid: str, records: List[dict]) -> ClusterTelemetry:
        """Compute metrics from a list of trade records for one cluster."""
        if not records:
            return ClusterTelemetry(cluster_id=cid, n_trades=0)

        n      = len(records)
        wins   = sum(1 for r in records if r.get("won", False))
        pnls   = [float(r.get("pnl", 0.0)) for r in records]

        win_rate = wins / n
        total_pnl = sum(pnls)
        avg_pnl   = total_pnl / n

        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss   = abs(sum(p for p in pnls if p < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        # Max consecutive loss
        max_cl = cur_cl = 0
        for r in records:
            if not r.get("won", False):
                cur_cl += 1
                max_cl  = max(max_cl, cur_cl)
            else:
                cur_cl = 0

        # Drawdown from equity curve
        equity = np.cumsum(pnls)
        peak   = np.maximum.accumulate(equity)
        dd     = peak - equity
        drawdown_pct = float(dd.max() / abs(peak.max())) if peak.max() != 0 else 0.0
        drawdown_pct = min(drawdown_pct, 1.0)

        last_ts = records[0].get("timestamp", "") if records else ""

        return ClusterTelemetry(
            cluster_id           = cid,
            n_trades             = n,
            win_rate             = win_rate,
            profit_factor        = pf,
            total_pnl            = total_pnl,
            avg_pnl_per_trade    = avg_pnl,
            max_consecutive_loss = max_cl,
            drawdown_pct         = drawdown_pct,
            last_trade_ts        = last_ts,
            is_active            = True,
        )


# ──────────────────────────────────────────────────────────────────
# 4. NetworkObjectiveEngine
# ──────────────────────────────────────────────────────────────────

class NetworkObjectiveEngine:
    """
    Xác định giai đoạn chiến lược (NetworkPhase) của toàn network
    dựa trên số liệu tổng hợp của tất cả cluster.

    Thứ tự ưu tiên:
      1. SURVIVAL  — khi tổng drawdown >= SSOL_OBJECTIVE_SURVIVAL_DRAWDOWN
      2. EXPANSION — khi profit_factor >= SSOL_OBJECTIVE_EXPANSION_PF
      3. GROWTH    — khi win_rate >= SSOL_OBJECTIVE_GROWTH_WIN_RATE
      4. REBALANCE — mặc định
    """

    def determine_phase(
        self,
        telemetries: Dict[str, ClusterTelemetry],
    ) -> Tuple[NetworkPhase, str]:
        """
        Tính network-level aggregate và trả về (phase, reason).
        """
        active = [t for t in telemetries.values() if t.n_trades > 0]

        if not active:
            return NetworkPhase.REBALANCE, "Chưa đủ dữ liệu để xác định phase"

        # Aggregate metrics weighted by n_trades
        total_trades = sum(t.n_trades for t in active)
        if total_trades == 0:
            return NetworkPhase.REBALANCE, "Tổng lệnh = 0"

        net_win_rate = sum(t.win_rate * t.n_trades for t in active) / total_trades
        net_pf_parts = [t.profit_factor * t.n_trades for t in active]
        net_pf       = sum(net_pf_parts) / total_trades

        # Use max drawdown across active clusters as network signal
        max_dd = max(t.drawdown_pct for t in active)

        survival_dd = float(getattr(config, "SSOL_OBJECTIVE_SURVIVAL_DRAWDOWN", 0.15))
        growth_wr   = float(getattr(config, "SSOL_OBJECTIVE_GROWTH_WIN_RATE",   0.60))
        expansion_pf= float(getattr(config, "SSOL_OBJECTIVE_EXPANSION_PF",      1.50))

        if max_dd >= survival_dd:
            return (
                NetworkPhase.SURVIVAL,
                f"Drawdown tối đa {max_dd:.1%} vượt ngưỡng {survival_dd:.1%} → SURVIVAL"
            )
        if net_pf >= expansion_pf and net_win_rate >= growth_wr:
            return (
                NetworkPhase.EXPANSION,
                f"PF={net_pf:.2f} ≥ {expansion_pf} và WR={net_win_rate:.1%} → EXPANSION"
            )
        if net_win_rate >= growth_wr:
            return (
                NetworkPhase.GROWTH,
                f"Win rate tổng {net_win_rate:.1%} ≥ {growth_wr:.1%} → GROWTH"
            )
        return (
            NetworkPhase.REBALANCE,
            f"WR={net_win_rate:.1%}  PF={net_pf:.2f}  maxDD={max_dd:.1%} → REBALANCE"
        )


# ──────────────────────────────────────────────────────────────────
# 5. ClusterScorer
# ──────────────────────────────────────────────────────────────────

class ClusterScorer:
    """
    Chấm điểm sức khỏe của từng cluster (0.0 – 1.0).

    Score = tổng hợp có trọng số của:
      win_rate_component      (40%) — thước đo lợi thế chiến lược
      profit_factor_component (30%) — thước đo hiệu quả vốn
      drawdown_component      (20%) — thước đo an toàn vốn
      volume_component        (10%) — thước đo đủ mẫu để tin cậy

    Health mapping:
      score >= 0.65 → HEALTHY
      score >= 0.45 → DEGRADED
      score >= 0.25 → CRITICAL
      else          → CRITICAL (chờ quarantine từ governor)
    """

    _W_WIN_RATE = 0.40
    _W_PF       = 0.30
    _W_DRAWDOWN = 0.20
    _W_VOLUME   = 0.10

    def score_all(
        self,
        telemetries: Dict[str, ClusterTelemetry],
    ) -> Dict[str, Tuple[float, ClusterHealth]]:
        """
        Returns dict {cluster_id: (score, health)}.
        """
        min_trades = int(getattr(config, "SSOL_MIN_TRADES_PER_CLUSTER", 10))
        results    = {}
        for cid, t in telemetries.items():
            score, health = self._score_one(t, min_trades)
            results[cid]  = (score, health)
        return results

    def _score_one(
        self,
        t         : ClusterTelemetry,
        min_trades: int,
    ) -> Tuple[float, ClusterHealth]:
        if t.n_trades < min_trades:
            # Not enough data — neutral score, DEGRADED (insufficient info)
            return 0.50, ClusterHealth.DEGRADED

        # ── Win rate component ─────────────────────────────────────
        # 0.50 → 0.0;  0.65 → 1.0;  linear interpolation, capped [0,1]
        wr_score = max(0.0, min(1.0, (t.win_rate - 0.50) / 0.15))

        # ── Profit factor component ────────────────────────────────
        # PF=1.0 → 0.0; PF=2.0 → 1.0; log scale
        pf_score = max(0.0, min(1.0, math.log(max(t.profit_factor, 1e-9)) / math.log(2.0)))

        # ── Drawdown component ─────────────────────────────────────
        # dd=0 → 1.0; dd=0.25 → 0.0; linear, higher dd → lower score
        dd_score = max(0.0, 1.0 - t.drawdown_pct / 0.25)

        # ── Volume component ───────────────────────────────────────
        # n=min_trades → 0.2; n=100 → 1.0; logarithmic
        vol_score = max(0.0, min(1.0,
            math.log(max(t.n_trades, 1) / min_trades + 1) / math.log(100.0 / min_trades + 1)
        ))

        score = (
            self._W_WIN_RATE * wr_score
            + self._W_PF     * pf_score
            + self._W_DRAWDOWN * dd_score
            + self._W_VOLUME * vol_score
        )
        score = max(0.0, min(1.0, score))

        if score >= 0.65:
            health = ClusterHealth.HEALTHY
        elif score >= 0.45:
            health = ClusterHealth.DEGRADED
        else:
            health = ClusterHealth.CRITICAL

        return score, health


# ──────────────────────────────────────────────────────────────────
# 6. ResourceAllocator
# ──────────────────────────────────────────────────────────────────

class ResourceAllocator:
    """
    Phân bổ ngân sách tài nguyên cho từng cluster dựa trên score.

    Nguyên tắc:
      • Cluster DEAD/QUARANTINED → nhận budget tối thiểu (attention chỉ để giám sát)
      • Cluster CRITICAL         → budget giảm (capital_fraction / 2)
      • Budget tỉ lệ thuận với score tương đối (softmax-like normalization)
      • Ràng buộc cứng: max_capital_fraction per cluster
      • Tổng attention_weight = 1.0 sau chuẩn hóa
    """

    _MIN_ATTENTION   = float(getattr(config, "SSOL_ATTENTION_MIN_FRACTION", 0.10))
    _MIN_CAPITAL     = float(getattr(config, "SSOL_CAPITAL_MIN_FRACTION",   0.10))
    _MAX_CAPITAL     = float(getattr(config, "SSOL_MAX_CLUSTER_CAPITAL_PCT", 0.50))

    def allocate(
        self,
        scores       : Dict[str, Tuple[float, ClusterHealth]],
        quarantined  : set,
        dead         : set,
    ) -> Dict[str, ResourceBudget]:
        """
        Parameters
        ----------
        scores      : {cluster_id: (score, health)} from ClusterScorer
        quarantined : set of quarantined cluster ids
        dead        : set of dead cluster ids

        Returns dict {cluster_id: ResourceBudget}.
        """
        budgets: Dict[str, ResourceBudget] = {}

        # Separate tradeable clusters
        tradeable = {
            cid: sc for cid, (sc, _) in scores.items()
            if cid not in quarantined and cid not in dead
        }

        if not tradeable:
            # All clusters non-tradeable — give uniform minimal to all
            for cid in scores:
                budgets[cid] = ResourceBudget(
                    cluster_id       = cid,
                    capital_fraction = self._MIN_CAPITAL,
                    attention_weight = 1.0 / max(len(scores), 1),
                    compute_priority = 1,
                    max_concurrent   = 1,
                )
            return budgets

        # ── Compute score-proportional weights ────────────────────
        total_score = sum(max(sc, 0.01) for sc in tradeable.values())
        raw_weights = {
            cid: max(sc, 0.01) / total_score
            for cid, sc in tradeable.items()
        }

        # ── Attention weights — uniform floor then proportional ───
        n_tradeable = len(tradeable)
        for cid, w in raw_weights.items():
            # Blend: 50% proportional + 50% uniform
            attention = 0.5 * w + 0.5 * (1.0 / n_tradeable)
            attention = max(self._MIN_ATTENTION, attention)

            # Capital fraction: proportional but capped
            score_val = tradeable[cid]
            capital = max(self._MIN_CAPITAL, min(self._MAX_CAPITAL, w))

            health = scores[cid][1]
            if health == ClusterHealth.CRITICAL:
                capital   /= 2.0
                attention /= 1.5
                priority   = 1
                max_conc   = 1
            elif health == ClusterHealth.HEALTHY:
                priority = 3
                max_conc = 2
            else:
                priority = 2
                max_conc = 1

            budgets[cid] = ResourceBudget(
                cluster_id       = cid,
                capital_fraction = round(capital, 4),
                attention_weight = round(attention, 4),
                compute_priority = priority,
                max_concurrent   = max_conc,
            )

        # ── Quarantined / dead get minimal monitoring budget ──────
        for cid in list(quarantined) + list(dead):
            budgets[cid] = ResourceBudget(
                cluster_id       = cid,
                capital_fraction = 0.0,
                attention_weight = 0.0,
                compute_priority = 1,
                max_concurrent   = 0,
            )

        # ── Normalize attention to sum = 1.0 ─────────────────────
        total_attn = sum(b.attention_weight for b in budgets.values())
        if total_attn > 0:
            for b in budgets.values():
                b.attention_weight = round(b.attention_weight / total_attn, 4)

        return budgets


# ──────────────────────────────────────────────────────────────────
# 7. ClusterGovernor
# ──────────────────────────────────────────────────────────────────

class ClusterGovernor:
    """
    Ra quyết định vòng đời (lifecycle) cho từng cluster.

    Policy (theo thứ tự ưu tiên):
      1. Đã DEAD → không xét lại trong run này (KILL confirmed)
      2. Đang QUARANTINED → đếm timer, nếu hết hạn → REVIVE, nếu chưa → PAUSE
      3. CRITICAL + win_rate < SSOL_KILL_WIN_RATE → KILL
      4. CRITICAL + win_rate < SSOL_QUARANTINE_WIN_RATE → QUARANTINE
      5. HEALTHY + win_rate >= SSOL_SCALE_UP_WIN_RATE + PF >= SSOL_SCALE_UP_PF → SCALE_UP
      6. DEGRADED → SCALE_DOWN
      7. Còn lại → KEEP

    Shadow mode: verdict được tạo nhưng enforced=False (không ghi vào Redis active pool).
    """

    _KILL_WR        = float(getattr(config, "SSOL_KILL_WIN_RATE",         0.35))
    _QUARANTINE_WR  = float(getattr(config, "SSOL_QUARANTINE_WIN_RATE",   0.42))
    _SCALE_UP_WR    = float(getattr(config, "SSOL_SCALE_UP_WIN_RATE",     0.62))
    _SCALE_UP_PF    = float(getattr(config, "SSOL_SCALE_UP_PF",           1.40))
    _REVIVE_CYCLES  = int(  getattr(config, "SSOL_REVIVE_QUARANTINE_CYCLES", 20))

    def __init__(self) -> None:
        self._quarantine_timers: Dict[str, int] = {}   # cluster_id → cycles remaining
        self._dead_set         : set             = set()
        self._load_state()

    def _load_state(self) -> None:
        """Load quarantine state from Redis."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.get(_REDIS_SSOL_QUARANTINE_KEY)
            if raw:
                state = json.loads(raw)
                self._quarantine_timers = state.get("timers", {})
                self._dead_set          = set(state.get("dead", []))
        except Exception:
            pass

    def _save_state(self) -> None:
        """Persist quarantine state to Redis."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            state = {
                "timers": self._quarantine_timers,
                "dead"  : list(self._dead_set),
            }
            r.set(_REDIS_SSOL_QUARANTINE_KEY, json.dumps(state))
        except Exception:
            pass

    def govern(
        self,
        scores       : Dict[str, Tuple[float, ClusterHealth]],
        telemetries  : Dict[str, ClusterTelemetry],
        phase        : NetworkPhase,
        shadow_mode  : bool,
        min_trades   : int,
    ) -> List[ClusterVerdict]:
        """
        Issue verdicts for all clusters.

        Parameters
        ----------
        scores      : from ClusterScorer
        telemetries : raw telemetry
        phase       : current NetworkPhase
        shadow_mode : if True, enforced=False on all verdicts
        min_trades  : minimum trades required for kill/quarantine decisions

        Returns list of ClusterVerdict.
        """
        verdicts: List[ClusterVerdict] = []

        for cid, (score, health) in scores.items():
            t = telemetries.get(cid, ClusterTelemetry(cluster_id=cid))

            # ── Phase SURVIVAL: be conservative ───────────────────
            if phase == NetworkPhase.SURVIVAL:
                if health in (ClusterHealth.CRITICAL, ClusterHealth.DEGRADED):
                    action    = ClusterAction.PAUSE
                    rationale = f"SURVIVAL mode: tạm dừng cluster {cid} (health={health.value})"
                    verdicts.append(self._make_verdict(cid, health, action, score, rationale, shadow_mode))
                    continue

            # ── Already dead ──────────────────────────────────────
            if cid in self._dead_set:
                verdicts.append(self._make_verdict(
                    cid, ClusterHealth.DEAD,
                    ClusterAction.KILL, score,
                    f"Cluster đã DEAD từ trước", shadow_mode
                ))
                continue

            # ── Currently quarantined ─────────────────────────────
            if cid in self._quarantine_timers:
                remaining = self._quarantine_timers[cid] - 1
                if remaining <= 0:
                    del self._quarantine_timers[cid]
                    action    = ClusterAction.REVIVE
                    rationale = f"Hết thời gian kiểm sát — thử phục hồi cluster {cid}"
                else:
                    self._quarantine_timers[cid] = remaining
                    action    = ClusterAction.PAUSE
                    rationale = (
                        f"Đang kiểm sát cluster {cid} — còn {remaining} chu kỳ"
                    )
                verdicts.append(self._make_verdict(cid, ClusterHealth.QUARANTINED, action, score, rationale, shadow_mode))
                continue

            # ── Not enough data — defer judgment ──────────────────
            if t.n_trades < min_trades:
                verdicts.append(self._make_verdict(
                    cid, health, ClusterAction.KEEP, score,
                    f"Chưa đủ {min_trades} lệnh ({t.n_trades}) — giữ nguyên", shadow_mode
                ))
                continue

            # ── Kill policy ───────────────────────────────────────
            if health == ClusterHealth.CRITICAL and t.win_rate < self._KILL_WR:
                if not shadow_mode:
                    self._dead_set.add(cid)
                action    = ClusterAction.KILL
                rationale = (
                    f"Win rate {t.win_rate:.1%} < {self._KILL_WR:.1%} "
                    f"và CRITICAL → KILL cluster {cid}"
                )
                verdicts.append(self._make_verdict(cid, ClusterHealth.DEAD, action, score, rationale, shadow_mode))
                continue

            # ── Quarantine policy ─────────────────────────────────
            if health == ClusterHealth.CRITICAL and t.win_rate < self._QUARANTINE_WR:
                if not shadow_mode:
                    self._quarantine_timers[cid] = self._REVIVE_CYCLES
                action    = ClusterAction.QUARANTINE
                rationale = (
                    f"Win rate {t.win_rate:.1%} < {self._QUARANTINE_WR:.1%} → QUARANTINE {cid} "
                    f"({self._REVIVE_CYCLES} chu kỳ)"
                )
                verdicts.append(self._make_verdict(cid, ClusterHealth.QUARANTINED, action, score, rationale, shadow_mode))
                continue

            # ── Scale up policy ───────────────────────────────────
            if (
                health == ClusterHealth.HEALTHY
                and t.win_rate >= self._SCALE_UP_WR
                and t.profit_factor >= self._SCALE_UP_PF
                and phase in (NetworkPhase.GROWTH, NetworkPhase.EXPANSION)
            ):
                action    = ClusterAction.SCALE_UP
                rationale = (
                    f"WR={t.win_rate:.1%} PF={t.profit_factor:.2f} "
                    f"trong phase {phase.value} → SCALE UP {cid}"
                )
                verdicts.append(self._make_verdict(cid, health, action, score, rationale, shadow_mode))
                continue

            # ── Scale down (degraded) ──────────────────────────────
            if health == ClusterHealth.DEGRADED:
                action    = ClusterAction.SCALE_DOWN
                rationale = (
                    f"Score={score:.2f} DEGRADED "
                    f"WR={t.win_rate:.1%} → SCALE DOWN {cid}"
                )
                verdicts.append(self._make_verdict(cid, health, action, score, rationale, shadow_mode))
                continue

            # ── Default: keep ─────────────────────────────────────
            verdicts.append(self._make_verdict(
                cid, health, ClusterAction.KEEP, score,
                f"Score={score:.2f} {health.value} — giữ nguyên {cid}", shadow_mode
            ))

        self._save_state()
        return verdicts

    def get_quarantined(self) -> set:
        return set(self._quarantine_timers.keys())

    def get_dead(self) -> set:
        return set(self._dead_set)

    @staticmethod
    def _make_verdict(
        cid       : str,
        health    : ClusterHealth,
        action    : ClusterAction,
        score     : float,
        rationale : str,
        shadow    : bool,
    ) -> ClusterVerdict:
        return ClusterVerdict(
            cluster_id = cid,
            health     = health.value if isinstance(health, ClusterHealth) else str(health),
            action     = action.value if isinstance(action, ClusterAction) else str(action),
            score      = round(score, 4),
            confidence = 1.0,
            rationale  = rationale,
            enforced   = not shadow,
        )


# ──────────────────────────────────────────────────────────────────
# 8. SovereigntyGuardrails
# ──────────────────────────────────────────────────────────────────

class SovereigntyGuardrails:
    """
    Kiểm tra an toàn toàn mạng — network-wide fail-safe.

    Bốn kiểm tra:
      1. Network drawdown limit: nếu tổng PnL âm quá ngưỡng → emergency pause
      2. Min active clusters: không để pool hoàn toàn rỗng
      3. Max capital concentration: không cluster nào chiếm quá 50% vốn
      4. Regime diversity: cảnh báo nếu nhiều cluster đang cùng regime kém

    Trả về (alerts: List[str], emergency_pause: bool).
    """

    _MAX_NETWORK_DD   = float(getattr(config, "SSOL_MAX_NETWORK_DRAWDOWN",   0.25))
    _MIN_ACTIVE       = int(  getattr(config, "SSOL_MIN_ACTIVE_CLUSTERS",    1))
    _MAX_CAPITAL_CONC = float(getattr(config, "SSOL_MAX_CLUSTER_CAPITAL_PCT", 0.50))
    _REGIME_DIV_THRESH= float(getattr(config, "SSOL_ANTIFRAGILE_REGIME_DIV", 0.30))

    def check(
        self,
        telemetries  : Dict[str, ClusterTelemetry],
        budgets      : Dict[str, ResourceBudget],
        verdicts     : List[ClusterVerdict],
    ) -> Tuple[List[str], bool]:
        """
        Returns (alerts, emergency_pause).

        alerts          : list of warning/alert strings
        emergency_pause : True if full network should halt immediately
        """
        alerts: List[str] = []
        emergency = False

        # ── 1. Network drawdown ───────────────────────────────────
        total_pnl = sum(t.total_pnl for t in telemetries.values())
        # Proxy drawdown: worst cluster drawdown as network signal
        max_dd = max((t.drawdown_pct for t in telemetries.values() if t.n_trades > 0), default=0.0)
        if max_dd >= self._MAX_NETWORK_DD:
            alerts.append(
                f"🚨 GUARDRAIL: Network drawdown {max_dd:.1%} vượt ngưỡng "
                f"{self._MAX_NETWORK_DD:.1%} → Emergency pause"
            )
            emergency = True

        # ── 2. Min active clusters ────────────────────────────────
        active_verdicts = [
            v for v in verdicts
            if v.action not in (ClusterAction.KILL.value, ClusterAction.PAUSE.value,
                                ClusterAction.QUARANTINE.value)
        ]
        if len(active_verdicts) < self._MIN_ACTIVE:
            alerts.append(
                f"⚠️  GUARDRAIL: Chỉ còn {len(active_verdicts)} cluster active "
                f"(tối thiểu {self._MIN_ACTIVE}) — giữ ít nhất 1 cluster"
            )

        # ── 3. Capital concentration ──────────────────────────────
        for bid, b in budgets.items():
            if b.capital_fraction > self._MAX_CAPITAL_CONC:
                alerts.append(
                    f"⚠️  GUARDRAIL: Cluster {bid} nhận {b.capital_fraction:.1%} vốn "
                    f"vượt ngưỡng {self._MAX_CAPITAL_CONC:.1%}"
                )

        # ── 4. Regime diversity ────────────────────────────────────
        regimes = [t.regime for t in telemetries.values() if t.regime and t.regime != "unknown"]
        if regimes:
            regime_counts = defaultdict(int)
            for reg in regimes:
                regime_counts[reg] += 1
            dominant_fraction = max(regime_counts.values()) / len(regimes)
            if dominant_fraction > (1.0 - self._REGIME_DIV_THRESH) and len(regimes) > 1:
                dominant_regime = max(regime_counts, key=regime_counts.__getitem__)
                alerts.append(
                    f"⚠️  GUARDRAIL: Regime '{dominant_regime}' chiếm "
                    f"{dominant_fraction:.1%} clusters — đa dạng thấp"
                )

        return alerts, emergency


# ──────────────────────────────────────────────────────────────────
# 9. StrategicMemory
# ──────────────────────────────────────────────────────────────────

class StrategicMemory:
    """
    Lưu và truy vấn bài học chiến lược liên-cluster.

    Bài học được tạo khi:
      - Cluster bị KILL → bài học về điều kiện thất bại
      - Cluster được REVIVE thành công → bài học về phục hồi
      - Phase chuyển từ GROWTH → SURVIVAL → bài học về cảnh báo sớm

    Lưu tại Redis key REDIS_SSOL_MEMORY_KEY (JSON list, FIFO, max 200 entries).
    """

    _MAX_LESSONS = int(getattr(config, "SSOL_MEMORY_MAX_LESSONS", 200))

    def record(self, lesson: str, context: dict) -> None:
        """
        Ghi một bài học mới vào bộ nhớ chiến lược.

        Parameters
        ----------
        lesson  : mô tả bài học ngắn gọn
        context : thông tin context (cluster_id, phase, metrics, ...)
        """
        entry = {
            "id"       : str(uuid.uuid4())[:8],
            "timestamp": _now_iso(),
            "lesson"   : lesson,
            "context"  : context,
        }
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.lpush(_REDIS_SSOL_MEMORY_KEY, json.dumps(entry))
            r.ltrim(_REDIS_SSOL_MEMORY_KEY, 0, self._MAX_LESSONS - 1)
        except Exception:
            pass

    def get_recent(self, n: int = 10) -> List[dict]:
        """Lấy N bài học gần nhất từ Redis."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw_list = r.lrange(_REDIS_SSOL_MEMORY_KEY, 0, n - 1)
            return [json.loads(x) for x in raw_list]
        except Exception:
            return []

    def extract_lessons_from_verdicts(
        self,
        verdicts    : List[ClusterVerdict],
        telemetries : Dict[str, ClusterTelemetry],
        phase       : NetworkPhase,
    ) -> List[str]:
        """
        Tạo danh sách bài học từ kết quả chu kỳ này.

        Returns list of lesson strings for the report.
        """
        lessons: List[str] = []

        for v in verdicts:
            t = telemetries.get(v.cluster_id, ClusterTelemetry(cluster_id=v.cluster_id))

            if v.action == ClusterAction.KILL.value:
                lesson = (
                    f"Cluster {v.cluster_id} bị KILL: "
                    f"WR={t.win_rate:.1%} PF={t.profit_factor:.2f} "
                    f"DD={t.drawdown_pct:.1%} ({t.n_trades} lệnh)"
                )
                self.record(lesson, {
                    "cluster_id"    : v.cluster_id,
                    "action"        : v.action,
                    "win_rate"      : t.win_rate,
                    "profit_factor" : t.profit_factor,
                    "drawdown_pct"  : t.drawdown_pct,
                    "n_trades"      : t.n_trades,
                    "phase"         : phase.value,
                })
                lessons.append(lesson)

            elif v.action == ClusterAction.QUARANTINE.value:
                lesson = (
                    f"Cluster {v.cluster_id} vào QUARANTINE: "
                    f"WR={t.win_rate:.1%} — theo dõi"
                )
                self.record(lesson, {
                    "cluster_id": v.cluster_id,
                    "win_rate"  : t.win_rate,
                    "phase"     : phase.value,
                })
                lessons.append(lesson)

            elif v.action == ClusterAction.REVIVE.value:
                lesson = f"Cluster {v.cluster_id} được REVIVE — giám sát kỹ"
                self.record(lesson, {"cluster_id": v.cluster_id, "phase": phase.value})
                lessons.append(lesson)

        return lessons


# ──────────────────────────────────────────────────────────────────
# 10. SovereignOversightLayer — Orchestrator
# ──────────────────────────────────────────────────────────────────

class SovereignOversightLayer:
    """
    Orchestrator cho toàn bộ SSOL pipeline.

    Workflow mỗi chu kỳ:
      1. Thu thập telemetry từ Redis trade log
      2. Chấm điểm cluster (ClusterScorer)
      3. Xác định phase mạng (NetworkObjectiveEngine)
      4. Phân bổ tài nguyên (ResourceAllocator)
      5. Kiểm tra guardrails (SovereigntyGuardrails)
      6. Phát lệnh governor (ClusterGovernor)
      7. Cập nhật strategic memory (StrategicMemory)
      8. Tạo insights + lưu báo cáo vào Redis

    Shadow mode (SSOL_SHADOW_MODE=True):
      - Tất cả tính toán chạy đầy đủ
      - Verdicts được ghi log nhưng không enforce (enforced=False)
      - Active symbols không thay đổi
      - Phase 2 của lộ trình triển khai

    Enforce mode (SSOL_SHADOW_MODE=False):
      - Verdicts KILL/QUARANTINE được ghi vào Redis active pool
      - DecisionEngine đọc và lọc active symbols theo verdicts
      - Phase 3-4 của lộ trình triển khai
    """

    def __init__(self) -> None:
        self._collector   = ClusterTelemetryCollector()
        self._scorer      = ClusterScorer()
        self._obj_engine  = NetworkObjectiveEngine()
        self._allocator   = ResourceAllocator()
        self._guardrails  = SovereigntyGuardrails()
        self._governor    = ClusterGovernor()
        self._memory      = StrategicMemory()

        self._shadow_mode = bool(getattr(config, "SSOL_SHADOW_MODE", True))
        self._min_trades  = int(getattr(config, "SSOL_MIN_TRADES_PER_CLUSTER", 10))

    def run(
        self,
        active_symbols: List[str],
        verbose       : bool = True,
    ) -> SovereignReport:
        """
        Chạy một chu kỳ SSOL đầy đủ.

        Parameters
        ----------
        active_symbols : danh sách cluster IDs hiện đang active
        verbose        : in log ra console

        Returns SovereignReport.
        """
        if verbose:
            print(f"\n  👑 [SSOL] Sovereign Oversight Cycle — "
                  f"{'SHADOW' if self._shadow_mode else 'ENFORCE'} mode")

        # ── Bước 1: Thu thập telemetry ────────────────────────────
        telemetries = self._collector.collect(active_symbols)

        # ── Bước 2: Chấm điểm ────────────────────────────────────
        scores = self._scorer.score_all(telemetries)

        # ── Bước 3: Phase mạng ───────────────────────────────────
        phase, phase_reason = self._obj_engine.determine_phase(telemetries)

        if verbose:
            print(f"  👑 [SSOL] Phase: {phase.value} — {phase_reason}")

        # ── Bước 4: Phân bổ tài nguyên ───────────────────────────
        quarantined = self._governor.get_quarantined()
        dead        = self._governor.get_dead()
        budgets     = self._allocator.allocate(scores, quarantined, dead)

        # ── Bước 5: Kiểm tra guardrails ──────────────────────────
        verdicts_prelim: List[ClusterVerdict] = []
        alerts, emergency_pause = self._guardrails.check(
            telemetries, budgets, verdicts_prelim
        )

        if emergency_pause and not self._shadow_mode:
            if verbose:
                print("  👑 [SSOL] 🚨 EMERGENCY PAUSE triggered by guardrails")

        # ── Bước 6: Governor phát lệnh ────────────────────────────
        # If emergency pause in enforce mode, mark all clusters as PAUSE
        if emergency_pause and not self._shadow_mode:
            verdicts = [
                ClusterGovernor._make_verdict(
                    cid, health, ClusterAction.PAUSE,
                    sc, "Emergency pause — guardrail kích hoạt", self._shadow_mode
                )
                for cid, (sc, health) in scores.items()
            ]
        else:
            verdicts = self._governor.govern(
                scores, telemetries, phase, self._shadow_mode, self._min_trades
            )

        # ── Bước 7: Strategic Memory ──────────────────────────────
        lessons = self._memory.extract_lessons_from_verdicts(
            verdicts, telemetries, phase
        )

        # ── Bước 8: Insights ─────────────────────────────────────
        insights = self._build_insights(
            phase, scores, budgets, verdicts, alerts, telemetries
        )

        if verbose:
            for v in verdicts:
                icon = {"KEEP":"✅","SCALE_UP":"📈","SCALE_DOWN":"📉",
                        "PAUSE":"⏸️","QUARANTINE":"🔒","KILL":"☠️","REVIVE":"♻️"}.get(v.action, "⚪")
                print(f"  👑 [SSOL]  {icon} {v.cluster_id}: {v.action} "
                      f"score={v.score:.2f} — {v.rationale}")
            for a in alerts:
                print(f"  👑 [SSOL] {a}")

        # ── Persist verdicts (enforce mode) ───────────────────────
        if not self._shadow_mode:
            self._persist_verdicts(verdicts)

        # ── Build + save report ───────────────────────────────────
        n_quarantined = len([v for v in verdicts if v.health == ClusterHealth.QUARANTINED.value])
        n_dead        = len([v for v in verdicts if v.health == ClusterHealth.DEAD.value])
        n_active      = len(verdicts) - n_quarantined - n_dead

        network_health = self._compute_network_health(scores, telemetries)

        report = SovereignReport(
            timestamp              = _now_iso(),
            network_phase          = phase.value,
            network_health_score   = round(network_health, 4),
            n_clusters_total       = len(active_symbols),
            n_clusters_active      = n_active,
            n_clusters_quarantined = n_quarantined,
            n_clusters_dead        = n_dead,
            cluster_verdicts       = [asdict(v) for v in verdicts],
            resource_budgets       = [asdict(b) for b in budgets.values()],
            guardrail_alerts       = alerts,
            strategic_lessons      = lessons,
            insights               = insights,
            shadow_mode            = self._shadow_mode,
        )

        self._save_report(report)
        return report

    def get_allowed_symbols(self, active_symbols: List[str]) -> List[str]:
        """
        Trả về danh sách symbol được phép giao dịch theo verdicts SSOL.

        Nếu shadow mode → trả về toàn bộ active_symbols (không thay đổi).
        Nếu enforce mode → loại bỏ cluster bị KILL/QUARANTINE/PAUSE.
        """
        if self._shadow_mode:
            return list(active_symbols)

        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.get(_REDIS_SSOL_VERDICTS_KEY)
            if not raw:
                return list(active_symbols)
            verdicts_raw = json.loads(raw)
        except Exception:
            return list(active_symbols)

        blocked_actions = {
            ClusterAction.KILL.value,
            ClusterAction.QUARANTINE.value,
            ClusterAction.PAUSE.value,
        }
        blocked = {
            v["cluster_id"]
            for v in verdicts_raw
            if v.get("action") in blocked_actions
        }

        allowed = [s for s in active_symbols if s not in blocked]
        # Guardrail: always keep at least 1 symbol
        if not allowed:
            return list(active_symbols)[:1]
        return allowed

    # ── Private helpers ───────────────────────────────────────────

    def _compute_network_health(
        self,
        scores      : Dict[str, Tuple[float, ClusterHealth]],
        telemetries : Dict[str, ClusterTelemetry],
    ) -> float:
        """Weighted average cluster score → network health score."""
        active = {
            cid: sc for cid, (sc, _) in scores.items()
            if telemetries.get(cid, ClusterTelemetry(cluster_id=cid)).n_trades > 0
        }
        if not active:
            return 0.50
        return float(np.mean(list(active.values())))

    def _build_insights(
        self,
        phase       : NetworkPhase,
        scores      : Dict[str, Tuple[float, ClusterHealth]],
        budgets     : Dict[str, ResourceBudget],
        verdicts    : List[ClusterVerdict],
        alerts      : List[str],
        telemetries : Dict[str, ClusterTelemetry],
    ) -> List[str]:
        insights: List[str] = []

        insights.append(
            f"Network phase: {phase.value} — "
            + {
                NetworkPhase.SURVIVAL  : "Ưu tiên bảo toàn vốn, thu hẹp cluster",
                NetworkPhase.GROWTH    : "Win rate tốt, tăng tốc vốn",
                NetworkPhase.EXPANSION : "Hiệu suất cao, sẵn sàng mở rộng",
                NetworkPhase.REBALANCE : "Trạng thái cân bằng, theo dõi liên tục",
            }[phase]
        )

        healthy = sum(1 for _, (_, h) in scores.items() if h == ClusterHealth.HEALTHY)
        total   = len(scores)
        insights.append(
            f"Sức khỏe mạng: {healthy}/{total} cluster HEALTHY "
            f"(network score={self._compute_network_health(scores, telemetries):.2f})"
        )

        scale_ups   = [v.cluster_id for v in verdicts if v.action == ClusterAction.SCALE_UP.value]
        scale_downs = [v.cluster_id for v in verdicts if v.action == ClusterAction.SCALE_DOWN.value]
        kills       = [v.cluster_id for v in verdicts if v.action == ClusterAction.KILL.value]
        quar        = [v.cluster_id for v in verdicts if v.action == ClusterAction.QUARANTINE.value]

        if scale_ups:
            insights.append(f"📈 Scale UP: {', '.join(scale_ups)}")
        if scale_downs:
            insights.append(f"📉 Scale DOWN: {', '.join(scale_downs)}")
        if kills:
            insights.append(f"☠️  KILL: {', '.join(kills)} — loại khỏi pool")
        if quar:
            insights.append(f"🔒 QUARANTINE: {', '.join(quar)} — kiểm sát {getattr(config, 'SSOL_REVIVE_QUARANTINE_CYCLES', 20)} chu kỳ")

        # Budget summary
        top_budget = max(budgets.items(), key=lambda x: x[1].attention_weight, default=None)
        if top_budget:
            insights.append(
                f"💰 Cluster nhận budget cao nhất: {top_budget[0]} "
                f"(attention={top_budget[1].attention_weight:.1%} "
                f"capital={top_budget[1].capital_fraction:.1%})"
            )

        if alerts:
            insights.append(f"⚠️  {len(alerts)} guardrail alert(s) kích hoạt")

        if self._shadow_mode:
            insights.append(
                "🌑 Shadow mode — khuyến nghị đã ghi log, chưa cưỡng chế. "
                "Đặt SSOL_SHADOW_MODE=False để bật enforce."
            )
        else:
            insights.append(
                "👑 Enforce mode — verdicts được ghi vào Redis và áp dụng ngay."
            )

        return insights

    def _persist_verdicts(self, verdicts: List[ClusterVerdict]) -> None:
        """Lưu verdicts vào Redis để DecisionEngine đọc."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_SSOL_VERDICTS_KEY, json.dumps([asdict(v) for v in verdicts]))
        except Exception:
            pass

    def _save_report(self, report: SovereignReport) -> None:
        """Lưu báo cáo SSOL vào Redis + file."""
        payload = asdict(report)
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_SSOL_REPORT_KEY, json.dumps(payload))
        except Exception:
            pass
        try:
            os.makedirs("models", exist_ok=True)
            with open("models/sovereign_report.json", "w") as f:
                import json as _json
                _json.dump(payload, f, indent=2)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────
# 11. API helpers
# ──────────────────────────────────────────────────────────────────

def get_sovereign_report() -> dict:
    """
    Đọc báo cáo SSOL gần nhất từ Redis hoặc file.
    Dùng cho API endpoint GET /sovereign/report.
    """
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw = r.get(_REDIS_SSOL_REPORT_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass

    try:
        with open("models/sovereign_report.json") as f:
            return json.load(f)
    except Exception:
        pass

    return {"status": "no_report", "message": "Chưa có báo cáo SSOL — chạy run_sovereign_cycle() trước"}


def run_sovereign_cycle(
    active_symbols: List[str] = None,
    verbose       : bool = True,
) -> SovereignReport:
    """
    Top-level entry point: chạy một chu kỳ SSOL đầy đủ.

    Parameters
    ----------
    active_symbols : danh sách cluster IDs. Mặc định: config.SCAN_SYMBOLS.
    verbose        : in log ra console

    Returns SovereignReport.

    Cách dùng:
      >>> from sovereign_oversight import run_sovereign_cycle
      >>> report = run_sovereign_cycle()
      >>> for insight in report.insights:
      ...     print(insight)
    """
    symbols = active_symbols or list(config.SCAN_SYMBOLS)
    layer   = SovereignOversightLayer()
    return layer.run(active_symbols=symbols, verbose=verbose)
