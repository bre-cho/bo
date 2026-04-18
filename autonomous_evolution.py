"""
autonomous_evolution.py
=======================
AUTONOMOUS EVOLUTION ENGINE (AEE)

Bước chuyển từ operator-triggered evolution (EvolutionEngine)
sang system-level self-improvement intelligence.

Vấn đề với evolution_engine.py hiện tại:
  • Người dùng phải tự kích hoạt evolution cycle (POST /evolution/run)
  • Engine chỉ tối ưu StrategyGenome (13 gene) — không tối ưu hệ thống
  • Không phát hiện điểm yếu: ai biết khi nào cần evolve?
  • Không kiểm tra regression: mutation xấu vẫn được apply nếu fitness tăng đôi chút
  • Không có memory về những mutation đã thất bại trước đó

Autonomous Evolution Engine (AEE) trả lời:
  1. Hệ đang yếu ở đâu?              → WeaknessDetector
  2. Có thể cải thiện bằng cách nào? → HypothesisGenerator
  3. Mutation cụ thể là gì?          → MutationFactory
  4. Mutation này tốt hơn baseline?  → MutationEvaluator
  5. An toàn để áp dụng không?       → SafeEvolutionGate
  6. Kết quả + bài học?              → EvolutionMemory

Bốn loại mutation:
  CONFIG_PARAM   : thay đổi config.py parameter (threshold, multiplier)
  GENOME_GENE    : thay đổi một gene trong champion genome
  SIGNAL_WEIGHT  : thay đổi trọng số signal component
  SCAN_POOL      : thêm/bớt symbol khỏi scan pool

Đánh giá mutation:
  - Mỗi mutation được run qua synthetic simulation (không live)
  - So sánh win_rate/profit_factor với baseline (champion genome hiện tại)
  - Mutation thắng nếu: improvement ≥ AEE_MIN_IMPROVEMENT_PCT
    VÀ regression trong bất kỳ metric nào < AEE_MAX_REGRESSION_PCT
  - Safe gate: không apply nếu drawdown increase > AEE_MAX_DD_INCREASE

AEE là hệ thống self-improving, không phải GA:
  - Không cần population (1 champion + 1 mutation)
  - Nhanh hơn (đánh giá đơn lẻ thay vì 30×8 matrix)
  - Nhắm mục tiêu cụ thể (hypothesis-driven, không random search)
  - Có memory về lý do mutation thất bại

Thành phần:
  WeaknessType         — enum: LOW_WIN_RATE, HIGH_DRAWDOWN, SIGNAL_DEGRADATION, ...
  MutationType         — enum: CONFIG_PARAM, GENOME_GENE, SIGNAL_WEIGHT, SCAN_POOL
  Weakness             — detected weakness dataclass
  MutationProposal     — proposed change + expected improvement
  MutationResult       — outcome of evaluating a proposal
  EvolutionCycleReport — full AEE report dataclass
  WeaknessDetector     — scan system telemetry for weaknesses
  HypothesisGenerator  — turn weaknesses into improvement ideas
  MutationFactory      — turn ideas into concrete mutations
  MutationEvaluator    — test mutation vs baseline using synthetic sim
  SafeEvolutionGate    — regression guard before apply
  EvolutionMemory      — Redis-persistent mutation history + lessons
  AutonomousEvolutionEngine — orchestrator
  get_aee_report()     — API helper
  run_autonomous_evolution() — top-level entry point

Cách dùng:
  >>> from autonomous_evolution import run_autonomous_evolution
  >>> report = run_autonomous_evolution()
  >>> print(report.weaknesses)     # detected weaknesses
  >>> print(report.applied_mutations)  # mutations that passed all gates
  >>> print(report.insights)       # evolution intelligence
"""

from __future__ import annotations

import copy
import json
import math
import os
import random
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config


# ──────────────────────────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────────────────────────

_REDIS_AEE_REPORT_KEY   = getattr(config, "REDIS_AEE_REPORT_KEY",   "Deriv_AEE_Report")
_REDIS_AEE_MEMORY_KEY   = getattr(config, "REDIS_AEE_MEMORY_KEY",   "Deriv_AEE_Memory")
_REDIS_AEE_APPLIED_KEY  = getattr(config, "REDIS_AEE_APPLIED_KEY",  "Deriv_AEE_Applied")


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


# ──────────────────────────────────────────────────────────────────
# 1. Enumerations
# ──────────────────────────────────────────────────────────────────

class WeaknessType(str, Enum):
    """
    Loại điểm yếu hệ thống được phát hiện.
    """
    LOW_WIN_RATE        = "LOW_WIN_RATE"        # win rate dưới ngưỡng
    HIGH_DRAWDOWN       = "HIGH_DRAWDOWN"        # drawdown quá cao
    SIGNAL_DEGRADATION  = "SIGNAL_DEGRADATION"  # signal score không convert
    STALE_GENOME        = "STALE_GENOME"        # champion không được cập nhật lâu
    HIGH_REJECTION_RATE = "HIGH_REJECTION_RATE"  # pipeline reject nhiều lệnh
    LOW_TRADE_VOLUME    = "LOW_TRADE_VOLUME"    # không đủ lệnh để học
    CONSECUTIVE_LOSS    = "CONSECUTIVE_LOSS"    # chuỗi thua liên tiếp


class MutationType(str, Enum):
    """
    Loại mutation được đề xuất.
    """
    CONFIG_PARAM   = "CONFIG_PARAM"    # thay đổi config.py parameter
    GENOME_GENE    = "GENOME_GENE"     # thay đổi gene trong champion genome
    SIGNAL_WEIGHT  = "SIGNAL_WEIGHT"   # thay đổi signal component weight
    SCAN_POOL      = "SCAN_POOL"       # thêm/bớt symbol khỏi scan pool


class MutationStatus(str, Enum):
    """
    Trạng thái của một mutation proposal sau evaluation.
    """
    PENDING   = "PENDING"   # chưa đánh giá
    PASSED    = "PASSED"    # qua gate, được apply
    REJECTED  = "REJECTED"  # không đủ improvement
    BLOCKED   = "BLOCKED"   # an toàn gate từ chối (regression)
    SKIPPED   = "SKIPPED"   # loại đã thử thất bại trước đó


# ──────────────────────────────────────────────────────────────────
# 2. Dataclasses
# ──────────────────────────────────────────────────────────────────

@dataclass
class Weakness:
    """
    Điểm yếu được phát hiện trong hệ thống.
    """
    weakness_id   : str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    weakness_type : str = WeaknessType.LOW_WIN_RATE.value
    severity      : float = 0.0    # 0.0 (nhẹ) → 1.0 (nghiêm trọng)
    description   : str = ""
    metric_actual : float = 0.0
    metric_target : float = 0.0
    context       : dict  = field(default_factory=dict)


@dataclass
class MutationProposal:
    """
    Đề xuất mutation cụ thể để giải quyết một điểm yếu.
    """
    proposal_id    : str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    mutation_type  : str = MutationType.CONFIG_PARAM.value
    weakness_id    : str = ""
    target_param   : str = ""        # tên parameter/gene cần thay đổi
    old_value      : Any = None
    new_value      : Any = None
    expected_delta : float = 0.0    # expected improvement in win_rate (abs)
    description    : str  = ""
    status         : str  = MutationStatus.PENDING.value


@dataclass
class MutationResult:
    """
    Kết quả đánh giá một mutation proposal.
    """
    proposal_id       : str
    baseline_win_rate : float = 0.0
    baseline_pf       : float = 0.0
    mutant_win_rate   : float = 0.0
    mutant_pf         : float = 0.0
    win_rate_delta    : float = 0.0
    pf_delta          : float = 0.0
    dd_delta          : float = 0.0    # drawdown change (positive = worse)
    passed_gate       : bool  = False
    reject_reason     : str   = ""
    confidence        : float = 0.0    # 0.0–1.0 based on n_trades evaluated


@dataclass
class EvolutionCycleReport:
    """
    Báo cáo đầy đủ của một chu kỳ AEE.
    """
    timestamp          : str
    n_weaknesses       : int
    n_proposals        : int
    n_passed           : int
    n_rejected         : int
    n_blocked          : int
    weaknesses         : List[dict]
    proposals          : List[dict]
    results            : List[dict]
    applied_mutations  : List[dict]
    evolution_safety   : float      # 1.0 = tất cả passed gate; 0.0 = nhiều blocked
    insights           : List[str]


# ──────────────────────────────────────────────────────────────────
# 3. WeaknessDetector
# ──────────────────────────────────────────────────────────────────

class WeaknessDetector:
    """
    Quét hệ thống để phát hiện điểm yếu từ:
      1. Redis trade log (win rate, drawdown, consecutive loss)
      2. Pipeline metrics (rejection rate)
      3. Champion genome age (SSOL cycle count since last evolution)
      4. Signal score distribution (if available)

    Mỗi điểm yếu có severity 0.0–1.0 để ưu tiên fix.
    """

    _WR_THRESHOLD    = float(getattr(config, "AEE_WR_WEAKNESS_THRESHOLD",    0.52))
    _DD_THRESHOLD    = float(getattr(config, "AEE_DD_WEAKNESS_THRESHOLD",    0.15))
    _REJECT_THRESHOLD= float(getattr(config, "AEE_REJECT_WEAKNESS_THRESHOLD",0.60))
    _STALE_CYCLES    = int(  getattr(config, "AEE_STALE_GENOME_CYCLES",      100))
    _MIN_TRADES      = int(  getattr(config, "AEE_MIN_TRADES_FOR_ANALYSIS",  20))
    _CONSEC_LOSS_THR = int(  getattr(config, "AEE_CONSEC_LOSS_THRESHOLD",    5))

    def detect(self, cycle_count: int = 0) -> List[Weakness]:
        """
        Phát hiện tất cả điểm yếu hiện tại.

        Parameters
        ----------
        cycle_count : hiện tại engine đang ở cycle nào

        Returns list of Weakness sorted by severity desc.
        """
        weaknesses: List[Weakness] = []
        stats = self._read_trade_stats()

        if stats:
            weaknesses.extend(self._check_win_rate(stats))
            weaknesses.extend(self._check_drawdown(stats))
            weaknesses.extend(self._check_consecutive_loss(stats))
            weaknesses.extend(self._check_trade_volume(stats))

        weaknesses.extend(self._check_stale_genome(cycle_count))
        weaknesses.extend(self._check_pipeline_rejection())

        weaknesses.sort(key=lambda w: w.severity, reverse=True)
        return weaknesses

    def _read_trade_stats(self) -> Optional[dict]:
        """Đọc thống kê giao dịch từ Redis."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.lrange(config.REDIS_LOG_KEY, 0, -1)
            if not raw:
                return None
            records = [json.loads(x) for x in raw]
            if len(records) < self._MIN_TRADES:
                return None

            wins   = sum(1 for rec in records if rec.get("won", False))
            n      = len(records)
            pnls   = [float(rec.get("pnl", 0.0)) for rec in records]
            wr     = wins / n

            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss   = abs(sum(p for p in pnls if p < 0))
            pf = gross_profit / gross_loss if gross_loss > 0 else (1.0 if gross_profit > 0 else 0.0)

            # Max consecutive loss
            max_cl = cur_cl = 0
            for rec in records:
                if not rec.get("won", False):
                    cur_cl += 1
                    max_cl  = max(max_cl, cur_cl)
                else:
                    cur_cl = 0

            equity = np.cumsum(pnls)
            peak   = np.maximum.accumulate(equity)
            dd     = peak - equity
            max_dd = float(dd.max() / abs(peak.max())) if peak.max() != 0 else 0.0

            return {
                "n_trades"         : n,
                "win_rate"         : wr,
                "profit_factor"    : pf,
                "max_drawdown"     : min(max_dd, 1.0),
                "consecutive_loss" : max_cl,
                "total_pnl"        : sum(pnls),
            }
        except Exception:
            return None

    def _check_win_rate(self, stats: dict) -> List[Weakness]:
        wr  = stats["win_rate"]
        tgt = self._WR_THRESHOLD
        if wr >= tgt:
            return []
        severity = min(1.0, (tgt - wr) / tgt)
        return [Weakness(
            weakness_type = WeaknessType.LOW_WIN_RATE.value,
            severity      = round(severity, 3),
            description   = f"Win rate {wr:.1%} dưới ngưỡng {tgt:.1%}",
            metric_actual = wr,
            metric_target = tgt,
            context       = {"n_trades": stats["n_trades"]},
        )]

    def _check_drawdown(self, stats: dict) -> List[Weakness]:
        dd  = stats["max_drawdown"]
        tgt = self._DD_THRESHOLD
        if dd <= tgt:
            return []
        severity = min(1.0, (dd - tgt) / tgt)
        return [Weakness(
            weakness_type = WeaknessType.HIGH_DRAWDOWN.value,
            severity      = round(severity, 3),
            description   = f"Max drawdown {dd:.1%} vượt ngưỡng {tgt:.1%}",
            metric_actual = dd,
            metric_target = tgt,
            context       = {"profit_factor": stats["profit_factor"]},
        )]

    def _check_consecutive_loss(self, stats: dict) -> List[Weakness]:
        cl  = stats["consecutive_loss"]
        tgt = self._CONSEC_LOSS_THR
        if cl < tgt:
            return []
        severity = min(1.0, cl / (tgt * 2))
        return [Weakness(
            weakness_type = WeaknessType.CONSECUTIVE_LOSS.value,
            severity      = round(severity, 3),
            description   = f"Chuỗi thua liên tiếp {cl} lần (ngưỡng={tgt})",
            metric_actual = float(cl),
            metric_target = float(tgt),
        )]

    def _check_trade_volume(self, stats: dict) -> List[Weakness]:
        n   = stats["n_trades"]
        tgt = self._MIN_TRADES * 5   # ideally 5× minimum
        if n >= tgt:
            return []
        severity = max(0.0, 1.0 - n / tgt) * 0.5   # max severity 0.5 (not critical)
        return [Weakness(
            weakness_type = WeaknessType.LOW_TRADE_VOLUME.value,
            severity      = round(severity, 3),
            description   = f"Chỉ có {n} lệnh — chưa đủ để học hiệu quả (tgt={tgt})",
            metric_actual = float(n),
            metric_target = float(tgt),
        )]

    def _check_stale_genome(self, cycle_count: int) -> List[Weakness]:
        """Genome stale nếu không được cập nhật trong STALE_CYCLES chu kỳ."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.get("Deriv_Evolution_Status")
            if not raw:
                return []
            status = json.loads(raw)
            last_gen_cycle = int(status.get("cycle_count", 0))
            age = cycle_count - last_gen_cycle
            tgt = self._STALE_CYCLES
            if age < tgt:
                return []
            severity = min(1.0, age / (tgt * 2)) * 0.6   # max 0.6
            return [Weakness(
                weakness_type = WeaknessType.STALE_GENOME.value,
                severity      = round(severity, 3),
                description   = f"Champion genome chưa được cập nhật {age} chu kỳ",
                metric_actual = float(age),
                metric_target = float(tgt),
            )]
        except Exception:
            return []

    def _check_pipeline_rejection(self) -> List[Weakness]:
        """High rejection rate trong pipeline metrics."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            # Pipeline metrics stored in Redis hash by DecisionEngine
            raw = r.get("Deriv_Pipeline_Metrics")
            if not raw:
                return []
            metrics = json.loads(raw)
            rr = float(metrics.get("rejection_rate", 0.0))
            tgt = self._REJECT_THRESHOLD
            if rr < tgt:
                return []
            severity = min(1.0, (rr - tgt) / (1.0 - tgt))
            return [Weakness(
                weakness_type = WeaknessType.HIGH_REJECTION_RATE.value,
                severity      = round(severity, 3),
                description   = f"Pipeline rejection rate {rr:.1%} cao (ngưỡng {tgt:.1%})",
                metric_actual = rr,
                metric_target = tgt,
            )]
        except Exception:
            return []


# ──────────────────────────────────────────────────────────────────
# 4. HypothesisGenerator
# ──────────────────────────────────────────────────────────────────

class HypothesisGenerator:
    """
    Chuyển điểm yếu thành hypothesis cải tiến cụ thể.

    Mỗi WeaknessType có một tập hypothesis patterns:
      LOW_WIN_RATE      → tăng min_signal_score, giảm lookahead, tăng RSI threshold
      HIGH_DRAWDOWN     → giảm stake multiplier, tăng min_signal_score
      STALE_GENOME      → trigger evolution cycle, thử meta-bred seeds
      CONSECUTIVE_LOSS  → giảm trade frequency, tăng cooldown
      HIGH_REJECTION    → giảm min_signal_score (đang quá strict)
      LOW_TRADE_VOLUME  → giảm min_signal_score, mở rộng scan pool

    Hypothesis = (mutation_type, target_param, direction, magnitude, description)
    """

    _HYPOTHESES: Dict[str, List[dict]] = {
        WeaknessType.LOW_WIN_RATE.value: [
            {"type": MutationType.CONFIG_PARAM.value, "param": "MIN_SIGNAL_SCORE",
             "direction": +1, "magnitude": 5.0,
             "desc": "Tăng ngưỡng tín hiệu tối thiểu để chỉ lấy lệnh có chất lượng cao hơn"},
            {"type": MutationType.GENOME_GENE.value, "param": "min_signal_score",
             "direction": +1, "magnitude": 5.0,
             "desc": "Tăng min_signal_score trong champion genome"},
            {"type": MutationType.GENOME_GENE.value, "param": "lookahead_candles",
             "direction": -1, "magnitude": 1.0,
             "desc": "Giảm lookahead để đo kết quả sớm hơn (tránh noise)"},
        ],
        WeaknessType.HIGH_DRAWDOWN.value: [
            {"type": MutationType.CONFIG_PARAM.value, "param": "RISK_MAX_DAILY_LOSS_PCT",
             "direction": -1, "magnitude": 0.03,
             "desc": "Giảm ngưỡng daily loss để dừng sớm hơn khi thua"},
            {"type": MutationType.GENOME_GENE.value, "param": "min_signal_score",
             "direction": +1, "magnitude": 3.0,
             "desc": "Tăng min_signal_score để giảm số lệnh, giảm exposure"},
        ],
        WeaknessType.CONSECUTIVE_LOSS.value: [
            {"type": MutationType.CONFIG_PARAM.value, "param": "RISK_MAX_CONSECUTIVE_LOSS",
             "direction": -1, "magnitude": 1.0,
             "desc": "Giảm ngưỡng consecutive loss để cooldown sớm hơn"},
            {"type": MutationType.GENOME_GENE.value, "param": "min_signal_score",
             "direction": +1, "magnitude": 7.0,
             "desc": "Tăng ngưỡng tín hiệu mạnh để chỉ lấy cơ hội chắc chắn nhất"},
        ],
        WeaknessType.STALE_GENOME.value: [
            {"type": MutationType.GENOME_GENE.value, "param": "wave_weight",
             "direction": +1, "magnitude": 0.2,
             "desc": "Tăng trọng số wave analysis — thường undervalued"},
            {"type": MutationType.GENOME_GENE.value, "param": "macd_weight",
             "direction": +1, "magnitude": 0.1,
             "desc": "Tăng trọng số MACD để cải thiện trend detection"},
            {"type": MutationType.SIGNAL_WEIGHT.value, "param": "rsi_weight",
             "direction": -1, "magnitude": 0.1,
             "desc": "Giảm nhẹ RSI weight để giảm false signals trong choppy market"},
        ],
        WeaknessType.HIGH_REJECTION_RATE.value: [
            {"type": MutationType.CONFIG_PARAM.value, "param": "MIN_SIGNAL_SCORE",
             "direction": -1, "magnitude": 3.0,
             "desc": "Giảm ngưỡng tín hiệu — đang reject quá nhiều cơ hội tốt"},
            {"type": MutationType.CONFIG_PARAM.value, "param": "PIPELINE_MIN_AUTHORITY_GATES",
             "direction": -1, "magnitude": 1.0,
             "desc": "Giảm yêu cầu cổng authority (cần 1/3 thay vì 2/3)"},
        ],
        WeaknessType.LOW_TRADE_VOLUME.value: [
            {"type": MutationType.CONFIG_PARAM.value, "param": "MIN_SIGNAL_SCORE",
             "direction": -1, "magnitude": 5.0,
             "desc": "Giảm ngưỡng tín hiệu để tăng số lệnh, thu thập dữ liệu nhanh hơn"},
            {"type": MutationType.SCAN_POOL.value, "param": "SCAN_SYMBOLS",
             "direction": +1, "magnitude": 1.0,
             "desc": "Mở rộng scan pool thêm 1 symbol để tăng cơ hội"},
        ],
    }

    def generate(self, weaknesses: List[Weakness]) -> List[MutationProposal]:
        """
        Sinh hypothesis cho mỗi điểm yếu.

        Mỗi weakness → chọn 1-2 hypothesis phù hợp nhất.
        Loại bỏ duplicate (cùng param + direction).

        Returns list of MutationProposal.
        """
        proposals: List[MutationProposal] = []
        seen_params: set = set()   # tránh duplicate

        for weakness in weaknesses[:5]:   # xử lý top-5 weaknesses
            hypotheses = self._HYPOTHESES.get(weakness.weakness_type, [])
            for h in hypotheses[:2]:   # top-2 per weakness
                param_key = (h["param"], h["direction"])
                if param_key in seen_params:
                    continue
                seen_params.add(param_key)

                proposals.append(MutationProposal(
                    mutation_type  = h["type"],
                    weakness_id    = weakness.weakness_id,
                    target_param   = h["param"],
                    old_value      = self._get_current_value(h["param"], h["type"]),
                    new_value      = None,   # filled by MutationFactory
                    expected_delta = weakness.severity * 0.05,
                    description    = h["desc"] + f" (solving: {weakness.description})",
                ))

        return proposals

    def _get_current_value(self, param: str, mutation_type: str) -> Any:
        """Read current value of target parameter."""
        if mutation_type in (MutationType.CONFIG_PARAM.value, MutationType.SCAN_POOL.value):
            return getattr(config, param, None)
        if mutation_type in (MutationType.GENOME_GENE.value, MutationType.SIGNAL_WEIGHT.value):
            # Try to read from Redis champion genome
            try:
                import redis as _redis
                r = _redis.Redis(
                    host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
                )
                raw = r.get("Deriv_Champion_Genome")
                if raw:
                    genome_dict = json.loads(raw)
                    return genome_dict.get(param, None)
            except Exception:
                pass
        return None


# ──────────────────────────────────────────────────────────────────
# 5. MutationFactory
# ──────────────────────────────────────────────────────────────────

class MutationFactory:
    """
    Tạo mutation cụ thể từ MutationProposal.

    Tính new_value dựa trên:
      - old_value + direction × magnitude (cho CONFIG_PARAM, GENOME_GENE)
      - Ràng buộc: không vượt quá gene bounds (từ evolution_engine)
      - Ràng buộc: không vượt quá hợp lý range cho config params
    """

    # Bounds cho config params
    _CONFIG_BOUNDS: Dict[str, Tuple[Any, Any]] = {
        "MIN_SIGNAL_SCORE"          : (40.0,  85.0),
        "RISK_MAX_DAILY_LOSS_PCT"   : (0.05,  0.40),
        "RISK_MAX_CONSECUTIVE_LOSS" : (2,     10),
        "PIPELINE_MIN_AUTHORITY_GATES": (1,   3),
    }

    def apply(self, proposals: List[MutationProposal]) -> List[MutationProposal]:
        """
        Fill new_value for each proposal.

        Returns proposals with new_value populated.
        Proposals where new_value cannot be computed are removed.
        """
        from sovereign_oversight import _now_iso as _ni
        result: List[MutationProposal] = []

        for p in proposals:
            direction = self._infer_direction(p)
            if direction is None:
                continue

            new_val = self._compute_new_value(p, direction)
            if new_val is None:
                continue

            p.new_value = new_val
            result.append(p)

        return result

    def _infer_direction(self, p: MutationProposal) -> Optional[int]:
        """Infer direction (+1/-1) from hypothesis metadata."""
        # Direction stored in description hint: look for hypothesis config
        from autonomous_evolution import HypothesisGenerator
        for wt, hypotheses in HypothesisGenerator._HYPOTHESES.items():
            for h in hypotheses:
                if h["param"] == p.target_param and h["type"] == p.mutation_type:
                    return h["direction"]
        # Fallback: default +1 if expected_delta > 0 else -1
        return +1

    def _compute_new_value(self, p: MutationProposal, direction: int) -> Any:
        """Compute new value given direction and bounds."""
        if p.mutation_type == MutationType.SCAN_POOL.value:
            # Add one symbol from SCAN_SYMBOLS not currently active
            full_pool = list(getattr(config, "SCAN_SYMBOLS", []))
            try:
                import redis as _redis
                r = _redis.Redis(
                    host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
                )
                raw = r.get("Deriv_Active_Symbols")
                active = json.loads(raw) if raw else full_pool[:1]
            except Exception:
                active = full_pool[:1]

            if direction > 0:
                candidates = [s for s in full_pool if s not in active]
                return (active + [candidates[0]]) if candidates else None
            else:
                if len(active) <= 1:
                    return None
                return active[:-1]

        old = p.old_value
        if old is None:
            return None

        if p.mutation_type == MutationType.CONFIG_PARAM.value:
            bounds = self._CONFIG_BOUNDS.get(p.target_param, (None, None))
            if isinstance(old, int):
                step  = max(1, int(abs(self._find_magnitude(p.target_param))))
                new_v = old + direction * step
                lo, hi = bounds
                if lo is not None:
                    new_v = max(lo, min(hi, new_v))
                return int(new_v)
            elif isinstance(old, float):
                mag   = self._find_magnitude(p.target_param)
                new_v = old + direction * mag
                lo, hi = bounds
                if lo is not None:
                    new_v = max(lo, min(hi, new_v))
                return round(float(new_v), 4)

        elif p.mutation_type in (MutationType.GENOME_GENE.value, MutationType.SIGNAL_WEIGHT.value):
            try:
                from evolution_engine import GENE_BOUNDS
            except Exception:
                return None
            if p.target_param not in GENE_BOUNDS:
                return None
            lo, hi = GENE_BOUNDS[p.target_param]
            mag    = self._find_magnitude(p.target_param)
            if isinstance(old, (int, float)):
                new_v = float(old) + direction * mag
                new_v = max(lo, min(hi, new_v))
                if p.target_param == "lookahead_candles":
                    return max(2, int(round(new_v)))
                return round(new_v, 4)

        return None

    def _find_magnitude(self, param: str) -> float:
        """Look up magnitude for a param from hypothesis table."""
        from autonomous_evolution import HypothesisGenerator
        for wt, hypotheses in HypothesisGenerator._HYPOTHESES.items():
            for h in hypotheses:
                if h["param"] == param:
                    return float(h["magnitude"])
        return 1.0


# ──────────────────────────────────────────────────────────────────
# 6. MutationEvaluator
# ──────────────────────────────────────────────────────────────────

class MutationEvaluator:
    """
    Đánh giá mutation bằng cách so sánh baseline vs mutant
    trên synthetic simulation data.

    Quy trình:
      1. Load champion genome từ Redis
      2. Apply mutation to create mutant
      3. Evaluate both on N synthetic environments
      4. Compare win_rate, profit_factor, max_drawdown
      5. Return MutationResult

    Không cần live data — dùng SyntheticScenarioLibrary để tạo test set.
    N_EVAL_ENVS=4 (nhanh) → đủ để phát hiện regressions nghiêm trọng.
    """

    _N_EVAL_ENVS = int(getattr(config, "AEE_N_EVAL_ENVS", 4))

    def evaluate(self, proposal: MutationProposal) -> MutationResult:
        """
        Evaluate a mutation proposal.

        Returns MutationResult with comparison metrics.
        """
        result = MutationResult(proposal_id=proposal.proposal_id)

        try:
            baseline_score, mutant_score = self._run_comparison(proposal)

            result.baseline_win_rate = baseline_score["win_rate"]
            result.baseline_pf       = baseline_score["profit_factor"]
            result.mutant_win_rate   = mutant_score["win_rate"]
            result.mutant_pf         = mutant_score["profit_factor"]
            result.win_rate_delta    = round(result.mutant_win_rate - result.baseline_win_rate, 4)
            result.pf_delta          = round(result.mutant_pf - result.baseline_pf, 4)
            result.dd_delta          = round(mutant_score.get("drawdown", 0) - baseline_score.get("drawdown", 0), 4)
            result.confidence        = min(1.0, baseline_score.get("n_trades", 0) / 50.0)

        except Exception as exc:
            result.reject_reason = f"Evaluation error: {exc}"

        return result

    def _run_comparison(self, proposal: MutationProposal) -> Tuple[dict, dict]:
        """
        Run baseline and mutant through N synthetic environments.

        Returns (baseline_metrics, mutant_metrics).
        """
        try:
            from evolution_engine import (
                StrategyGenome, SimEnvironment, GenomeScorer,
                GeneticOperators, _clip_genome, load_champion
            )
        except ImportError:
            raise RuntimeError("evolution_engine not available for mutation evaluation")

        # Load champion as baseline
        baseline_genome = None
        try:
            baseline_genome = load_champion()
        except Exception:
            pass
        if baseline_genome is None:
            baseline_genome = StrategyGenome.from_config()

        # Create mutant by applying the genome-level mutation
        mutant_genome = copy.deepcopy(baseline_genome)
        if proposal.mutation_type in (MutationType.GENOME_GENE.value, MutationType.SIGNAL_WEIGHT.value):
            if proposal.new_value is not None:
                setattr(mutant_genome, proposal.target_param, proposal.new_value)
                mutant_genome = _clip_genome(mutant_genome)
        # CONFIG_PARAM and SCAN_POOL mutations: apply to genome's effective params
        elif proposal.mutation_type == MutationType.CONFIG_PARAM.value:
            if proposal.target_param == "MIN_SIGNAL_SCORE" and proposal.new_value is not None:
                mutant_genome.min_signal_score = float(proposal.new_value)
                mutant_genome = _clip_genome(mutant_genome)

        # Evaluate on synthetic environments
        envs = [SimEnvironment(seed=i * 7 + 42) for i in range(self._N_EVAL_ENVS)]
        scorer = GenomeScorer()

        baseline_results, mutant_results = [], []
        for env in envs:
            try:
                candles = env.get_candles()
                b_r = scorer.score(baseline_genome, candles)
                m_r = scorer.score(mutant_genome, candles)
                baseline_results.append(b_r)
                mutant_results.append(m_r)
            except Exception:
                continue

        if not baseline_results:
            raise RuntimeError("No valid evaluation environments")

        def agg(results) -> dict:
            total_t   = sum(r.n_trades for r in results)
            wins      = sum(r.wins for r in results)
            losses    = sum(r.losses for r in results)
            total_pnl = sum(r.total_pnl for r in results)
            wr        = wins / max(total_t, 1)
            gp        = sum(r.total_pnl for r in results if r.total_pnl > 0)
            gl        = abs(sum(r.total_pnl for r in results if r.total_pnl < 0))
            pf        = gp / gl if gl > 0 else (1.0 if gp > 0 else 0.0)
            # Proxy drawdown from losses
            dd = losses / max(total_t, 1)
            return {
                "win_rate"     : round(wr, 4),
                "profit_factor": round(pf, 4),
                "n_trades"     : total_t,
                "drawdown"     : round(dd, 4),
            }

        return agg(baseline_results), agg(mutant_results)


# ──────────────────────────────────────────────────────────────────
# 7. SafeEvolutionGate
# ──────────────────────────────────────────────────────────────────

class SafeEvolutionGate:
    """
    Cổng an toàn trước khi apply mutation.

    Tiêu chí pass:
      1. win_rate_delta ≥ AEE_MIN_WIN_RATE_IMPROVEMENT (default 0.005 = 0.5%)
         HOẶC pf_delta ≥ AEE_MIN_PF_IMPROVEMENT (default 0.05)
      2. dd_delta ≤ AEE_MAX_DD_INCREASE (default 0.05)
      3. Không có previous failure cho cùng (param, new_value) pair trong EvolutionMemory

    Nếu confidence < 0.3 → reject (không đủ data để kết luận).
    """

    _MIN_WR_IMPROVEMENT = float(getattr(config, "AEE_MIN_WIN_RATE_IMPROVEMENT", 0.005))
    _MIN_PF_IMPROVEMENT = float(getattr(config, "AEE_MIN_PF_IMPROVEMENT",       0.05))
    _MAX_DD_INCREASE    = float(getattr(config, "AEE_MAX_DD_INCREASE",           0.05))
    _MIN_CONFIDENCE     = float(getattr(config, "AEE_MIN_CONFIDENCE",            0.20))

    def check(
        self,
        proposal : MutationProposal,
        result   : MutationResult,
        memory   : "EvolutionMemory",
    ) -> Tuple[bool, str]:
        """
        Returns (passed, reason).

        passed=True → mutation is safe to apply.
        """
        if result.reject_reason:
            return False, f"Evaluation failed: {result.reject_reason}"

        if result.confidence < self._MIN_CONFIDENCE:
            return False, (
                f"Confidence {result.confidence:.2f} < {self._MIN_CONFIDENCE} "
                f"(không đủ data để kết luận)"
            )

        # Check for previous failure in memory
        if memory.has_failed_before(proposal.target_param, proposal.new_value):
            return False, (
                f"Mutation ({proposal.target_param}={proposal.new_value}) "
                f"đã thất bại trước đó — skip"
            )

        # Improvement check (OR logic)
        wr_ok = result.win_rate_delta >= self._MIN_WR_IMPROVEMENT
        pf_ok = result.pf_delta       >= self._MIN_PF_IMPROVEMENT
        if not (wr_ok or pf_ok):
            return False, (
                f"Không đủ cải thiện: WR_delta={result.win_rate_delta:+.4f} "
                f"(min={self._MIN_WR_IMPROVEMENT}) "
                f"PF_delta={result.pf_delta:+.4f} "
                f"(min={self._MIN_PF_IMPROVEMENT})"
            )

        # Regression check
        if result.dd_delta > self._MAX_DD_INCREASE:
            return False, (
                f"Drawdown tăng {result.dd_delta:+.3f} > max {self._MAX_DD_INCREASE} — blocked"
            )

        return True, (
            f"PASSED: WR_delta={result.win_rate_delta:+.4f}  "
            f"PF_delta={result.pf_delta:+.4f}  "
            f"DD_delta={result.dd_delta:+.4f}  "
            f"confidence={result.confidence:.2f}"
        )


# ──────────────────────────────────────────────────────────────────
# 8. EvolutionMemory
# ──────────────────────────────────────────────────────────────────

class EvolutionMemory:
    """
    Bộ nhớ về lịch sử mutation — tránh lặp lại thất bại.

    Lưu Redis List tại REDIS_AEE_MEMORY_KEY, FIFO, max AEE_MEMORY_MAX_ENTRIES.
    Mỗi entry: {param, new_value, status, win_rate_delta, pf_delta, timestamp, reason}

    has_failed_before(param, value) → True nếu cùng mutation đã BLOCKED/REJECTED.
    """

    _MAX_ENTRIES = int(getattr(config, "AEE_MEMORY_MAX_ENTRIES", 500))

    def record(
        self,
        proposal : MutationProposal,
        result   : MutationResult,
        status   : str,
        reason   : str,
    ) -> None:
        entry = {
            "id"            : proposal.proposal_id,
            "param"         : proposal.target_param,
            "old_value"     : str(proposal.old_value),
            "new_value"     : str(proposal.new_value),
            "mutation_type" : proposal.mutation_type,
            "status"        : status,
            "win_rate_delta": result.win_rate_delta,
            "pf_delta"      : result.pf_delta,
            "dd_delta"      : result.dd_delta,
            "confidence"    : result.confidence,
            "reason"        : reason,
            "timestamp"     : _now_iso(),
        }
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.lpush(_REDIS_AEE_MEMORY_KEY, json.dumps(entry))
            r.ltrim(_REDIS_AEE_MEMORY_KEY, 0, self._MAX_ENTRIES - 1)
        except Exception:
            pass

    def get_recent(self, n: int = 20) -> List[dict]:
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw_list = r.lrange(_REDIS_AEE_MEMORY_KEY, 0, n - 1)
            return [json.loads(x) for x in raw_list]
        except Exception:
            return []

    def has_failed_before(self, param: str, new_value: Any) -> bool:
        """True nếu cùng (param, new_value) đã BLOCKED hoặc REJECTED gần đây."""
        recent = self.get_recent(n=50)
        new_val_str = str(new_value)
        for entry in recent:
            if (entry.get("param") == param
                    and entry.get("new_value") == new_val_str
                    and entry.get("status") in (MutationStatus.BLOCKED.value,
                                                MutationStatus.REJECTED.value)):
                return True
        return False


# ──────────────────────────────────────────────────────────────────
# 9. MutationApplicator
# ──────────────────────────────────────────────────────────────────

class MutationApplicator:
    """
    Apply passed mutations to live system.

    CONFIG_PARAM → setattr(config, param, new_value) (runtime only, no file write)
    GENOME_GENE / SIGNAL_WEIGHT → update champion genome in Redis
    SCAN_POOL → update Redis Deriv_Active_Symbols

    Tất cả changes là runtime-only. Để persist, cần restart hoặc export.
    Lưu applied mutations vào Redis list REDIS_AEE_APPLIED_KEY.
    """

    def apply(self, proposal: MutationProposal) -> bool:
        """Apply mutation. Returns True on success."""
        try:
            if proposal.mutation_type == MutationType.CONFIG_PARAM.value:
                return self._apply_config(proposal)
            elif proposal.mutation_type in (MutationType.GENOME_GENE.value,
                                            MutationType.SIGNAL_WEIGHT.value):
                return self._apply_genome(proposal)
            elif proposal.mutation_type == MutationType.SCAN_POOL.value:
                return self._apply_scan_pool(proposal)
        except Exception:
            return False
        return False

    def _apply_config(self, p: MutationProposal) -> bool:
        if not hasattr(config, p.target_param):
            return False
        setattr(config, p.target_param, p.new_value)
        self._log_applied(p)
        return True

    def _apply_genome(self, p: MutationProposal) -> bool:
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.get("Deriv_Champion_Genome")
            if not raw:
                return False
            genome_dict = json.loads(raw)
            genome_dict[p.target_param] = p.new_value
            r.set("Deriv_Champion_Genome", json.dumps(genome_dict))
            self._log_applied(p)
            return True
        except Exception:
            return False

    def _apply_scan_pool(self, p: MutationProposal) -> bool:
        if not isinstance(p.new_value, list):
            return False
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set("Deriv_Active_Symbols", json.dumps(p.new_value))
            self._log_applied(p)
            return True
        except Exception:
            return False

    def _log_applied(self, p: MutationProposal) -> None:
        entry = {
            "proposal_id"  : p.proposal_id,
            "mutation_type": p.mutation_type,
            "param"        : p.target_param,
            "old_value"    : str(p.old_value),
            "new_value"    : str(p.new_value),
            "timestamp"    : _now_iso(),
        }
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.lpush(_REDIS_AEE_APPLIED_KEY, json.dumps(entry))
            r.ltrim(_REDIS_AEE_APPLIED_KEY, 0, 199)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────
# 10. AutonomousEvolutionEngine — Orchestrator
# ──────────────────────────────────────────────────────────────────

class AutonomousEvolutionEngine:
    """
    Orchestrator của Autonomous Evolution Engine.

    Workflow một chu kỳ AEE:
      1. Phát hiện điểm yếu (WeaknessDetector)
      2. Sinh hypothesis cải tiến (HypothesisGenerator)
      3. Tạo mutations cụ thể (MutationFactory)
      4. Đánh giá từng mutation vs baseline (MutationEvaluator)
      5. Kiểm tra an toàn (SafeEvolutionGate)
      6. Apply mutations đã pass (MutationApplicator)
      7. Ghi bài học vào memory (EvolutionMemory)
      8. Tạo insights + lưu report

    Safe-by-default:
      - AEE_ENABLED=True cho phép chạy
      - AEE_DRY_RUN=True (default) → detect + evaluate nhưng KHÔNG apply
      - AEE_DRY_RUN=False → apply mutations đã pass gate
    """

    def __init__(self) -> None:
        self._detector  = WeaknessDetector()
        self._hypo_gen  = HypothesisGenerator()
        self._factory   = MutationFactory()
        self._evaluator = MutationEvaluator()
        self._gate      = SafeEvolutionGate()
        self._memory    = EvolutionMemory()
        self._applicator= MutationApplicator()

        self._dry_run   = bool(getattr(config, "AEE_DRY_RUN",  True))
        self._enabled   = bool(getattr(config, "AEE_ENABLED",  True))

    def run(
        self,
        cycle_count : int  = 0,
        verbose     : bool = True,
    ) -> EvolutionCycleReport:
        """
        Chạy một chu kỳ AEE đầy đủ.

        Parameters
        ----------
        cycle_count : engine cycle count (for stale genome detection)
        verbose     : in log ra console

        Returns EvolutionCycleReport.
        """
        if verbose:
            dry_tag = "DRY-RUN" if self._dry_run else "LIVE"
            print(f"\n  🧬 [AEE] Autonomous Evolution Cycle [{dry_tag}]")

        if not self._enabled:
            return self._empty_report("AEE disabled")

        # ── Bước 1: Phát hiện điểm yếu ───────────────────────────
        weaknesses = self._detector.detect(cycle_count=cycle_count)
        if verbose:
            print(f"  🧬 [AEE] {len(weaknesses)} weakness(es) detected")
            for w in weaknesses[:3]:
                print(f"  🧬 [AEE]  ⚠️  [{w.weakness_type}] {w.description} (severity={w.severity:.2f})")

        if not weaknesses:
            report = self._empty_report("Không phát hiện điểm yếu — hệ thống ổn định")
            self._save_report(report)
            return report

        # ── Bước 2-3: Sinh hypothesis + tạo mutations ─────────────
        proposals = self._hypo_gen.generate(weaknesses)
        proposals = self._factory.apply(proposals)
        proposals = [p for p in proposals if p.new_value is not None]

        if verbose:
            print(f"  🧬 [AEE] {len(proposals)} mutation proposal(s) generated")

        # ── Bước 4-6: Đánh giá + gate + apply ────────────────────
        results: List[MutationResult]  = []
        applied: List[MutationProposal] = []
        passed_count = rejected_count = blocked_count = 0

        for proposal in proposals:
            if verbose:
                print(f"  🧬 [AEE]  → Evaluating: {proposal.target_param} "
                      f"{proposal.old_value!r} → {proposal.new_value!r}")

            # Skip if already failed in memory
            if self._memory.has_failed_before(proposal.target_param, proposal.new_value):
                proposal.status = MutationStatus.SKIPPED.value
                if verbose:
                    print(f"  🧬 [AEE]    ⏭️  Skipped (known failure)")
                continue

            result = self._evaluator.evaluate(proposal)
            results.append(result)

            passed, reason = self._gate.check(proposal, result, self._memory)
            if passed:
                passed_count += 1
                proposal.status = MutationStatus.PASSED.value
                if verbose:
                    print(f"  🧬 [AEE]    ✅ Passed: {reason}")
                if not self._dry_run:
                    ok = self._applicator.apply(proposal)
                    if ok:
                        applied.append(proposal)
                        if verbose:
                            print(f"  🧬 [AEE]    🚀 Applied: {proposal.target_param}={proposal.new_value}")
                self._memory.record(proposal, result, MutationStatus.PASSED.value, reason)
            else:
                if result.dd_delta > getattr(config, "AEE_MAX_DD_INCREASE", 0.05):
                    blocked_count += 1
                    proposal.status = MutationStatus.BLOCKED.value
                else:
                    rejected_count += 1
                    proposal.status = MutationStatus.REJECTED.value
                if verbose:
                    print(f"  🧬 [AEE]    ❌ {proposal.status}: {reason}")
                self._memory.record(proposal, result, proposal.status, reason)

        # ── Insights ─────────────────────────────────────────────
        safety = (passed_count / max(len(proposals), 1))
        insights = self._build_insights(
            weaknesses, proposals, results, applied, passed_count,
            rejected_count, blocked_count, safety
        )

        if verbose:
            print(f"  🧬 [AEE] Results: passed={passed_count}  rejected={rejected_count}  "
                  f"blocked={blocked_count}  applied={len(applied)}")

        report = EvolutionCycleReport(
            timestamp         = _now_iso(),
            n_weaknesses      = len(weaknesses),
            n_proposals       = len(proposals),
            n_passed          = passed_count,
            n_rejected        = rejected_count,
            n_blocked         = blocked_count,
            weaknesses        = [asdict(w) for w in weaknesses],
            proposals         = [asdict(p) for p in proposals],
            results           = [asdict(r) for r in results],
            applied_mutations = [asdict(a) for a in applied],
            evolution_safety  = round(safety, 4),
            insights          = insights,
        )

        self._save_report(report)
        return report

    # ── Private helpers ───────────────────────────────────────────

    def _build_insights(
        self,
        weaknesses    : List[Weakness],
        proposals     : List[MutationProposal],
        results       : List[MutationResult],
        applied       : List[MutationProposal],
        passed        : int,
        rejected      : int,
        blocked       : int,
        safety        : float,
    ) -> List[str]:
        insights: List[str] = []

        if weaknesses:
            top_w = weaknesses[0]
            insights.append(
                f"🔍 Top weakness: [{top_w.weakness_type}] {top_w.description} "
                f"(severity={top_w.severity:.2f})"
            )

        n_total = max(len(proposals), 1)
        insights.append(
            f"🧬 Evolution: {len(proposals)} proposals → "
            f"{passed} passed / {rejected} rejected / {blocked} blocked"
        )

        if applied:
            changes = [f"{a.target_param}: {a.old_value!r}→{a.new_value!r}" for a in applied]
            insights.append(f"🚀 Applied mutations: {'; '.join(changes)}")
        elif self._dry_run:
            if passed > 0:
                insights.append(
                    f"🌑 Dry-run mode: {passed} mutation(s) would be applied. "
                    f"Set AEE_DRY_RUN=False to enable."
                )
            else:
                insights.append("🌑 Dry-run: no mutations cleared the gate this cycle")

        if blocked > 0:
            insights.append(
                f"🛡️  Safety gate blocked {blocked} mutation(s) (drawdown regression risk)"
            )

        insights.append(
            f"🔒 Evolution safety score: {safety:.1%} — "
            + ("safe zone" if safety >= 0.5 else "caution — many mutations not passing")
        )

        return insights

    def _empty_report(self, reason: str) -> EvolutionCycleReport:
        return EvolutionCycleReport(
            timestamp        = _now_iso(),
            n_weaknesses     = 0,
            n_proposals      = 0,
            n_passed         = 0,
            n_rejected       = 0,
            n_blocked        = 0,
            weaknesses       = [],
            proposals        = [],
            results          = [],
            applied_mutations= [],
            evolution_safety = 1.0,
            insights         = [reason],
        )

    def _save_report(self, report: EvolutionCycleReport) -> None:
        payload = asdict(report)
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_AEE_REPORT_KEY, json.dumps(payload))
        except Exception:
            pass
        try:
            os.makedirs("models", exist_ok=True)
            with open("models/aee_report.json", "w") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────
# 11. API helpers
# ──────────────────────────────────────────────────────────────────

def get_aee_report() -> dict:
    """
    Đọc báo cáo AEE gần nhất từ Redis hoặc file.
    Dùng cho API endpoint GET /evolution/aee/report.
    """
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw = r.get(_REDIS_AEE_REPORT_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open("models/aee_report.json") as f:
            return json.load(f)
    except Exception:
        pass
    return {"status": "no_report", "message": "Chưa có báo cáo AEE — chạy run_autonomous_evolution() trước"}


def run_autonomous_evolution(
    cycle_count : int  = 0,
    verbose     : bool = True,
) -> EvolutionCycleReport:
    """
    Top-level entry point: chạy một chu kỳ AEE.

    Parameters
    ----------
    cycle_count : current engine cycle (for stale genome detection)
    verbose     : in log

    Cách dùng:
      >>> from autonomous_evolution import run_autonomous_evolution
      >>> report = run_autonomous_evolution()
      >>> for w in report.weaknesses:
      ...     print(w['weakness_type'], w['description'])
      >>> for a in report.applied_mutations:
      ...     print(a['target_param'], '→', a['new_value'])
    """
    engine = AutonomousEvolutionEngine()
    return engine.run(cycle_count=cycle_count, verbose=verbose)
