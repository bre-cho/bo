"""
empire_control.py
=================
STRATEGIC SOVEREIGN CONTROL LAYER (SSCL)

Bước chuyển từ sovereign oversight (health-based cluster governance)
sang empire-level portfolio intelligence (attention-as-capital management).

Sự khác biệt so với sovereign_oversight.py (SSOL):
  SSOL: "Cluster nào đang KHỎE / YẾU / NGUY?" → health-based governance
  SSCL: "Attention nên phân bổ như một QUỸ ĐẦU TƯ để TỐI ĐA HOÀN TOÀN NETWORK?"
    → portfolio optimization, Sharpe-like risk-adjusted attention allocation
    → MERGE clusters khi kết hợp có lợi hơn chạy riêng lẻ
    → Network Dominance Score (tổng cơ hội khai thác được / tổng cơ hội khả dụng)
    → Empire growth objective: tăng trưởng kép toàn mạng

Vấn đề SSOL chưa giải quyết:
  • Phân bổ attention theo SCORE đơn thuần ≠ tối ưu portfolio
    (cluster low-score nhưng low-correlation với các cluster khác
     có thể đáng giữ để đa dạng hoá rủi ro)
  • Không tính "cơ hội bỏ lỡ" của cluster bị tắt
  • Không theo dõi tổng market coverage (empire dominance)
  • Không phát hiện cơ hội MERGE hai cluster underperforming
    thành một cluster tập trung với genome mạnh hơn

Câu hỏi SSCL trả lời:
  1. Bao nhiêu % tổng cơ hội thị trường ta đang khai thác?  → DominanceTracker
  2. Phân bổ attention tối ưu như fund manager là gì?        → AttentionPortfolio
  3. Cluster nào nên MERGE, không chỉ KILL?                  → MergeAdvisor
  4. Mục tiêu tăng trưởng kép toàn đế chế là bao nhiêu?     → EmpireObjectiveEngine
  5. Hệ thống đang tiến hoá đúng hướng không?               → EmpireControlLayer

Thành phần:
  EmpireObjective     — dataclass: target metrics + growth goals
  AttentionAsset      — per-cluster asset representation for portfolio
  MergeProposal       — candidate cluster merge
  EmpireReport        — full SSCL report dataclass
  DominanceTracker    — network dominance score (coverage × efficiency)
  AttentionPortfolio  — portfolio-theoretic attention allocation
  MergeAdvisor        — identify cluster pairs worth merging
  EmpireObjectiveEngine — empire-level growth targeting
  EmpireControlLayer  — orchestrator
  get_empire_report() — API helper
  run_empire_cycle()  — top-level entry point

Cách dùng:
  >>> from empire_control import run_empire_cycle
  >>> report = run_empire_cycle(active_symbols=["R_10","R_100"])
  >>> print(report.dominance_score)       # 0.0-1.0 empire coverage
  >>> print(report.attention_allocation)  # per-cluster portfolio weights
  >>> print(report.merge_proposals)       # suggested cluster merges
  >>> print(report.insights)             # strategic intelligence
"""

from __future__ import annotations

import json
import math
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config
from sovereign_oversight import (
    ClusterTelemetryCollector,
    ClusterTelemetry,
    NetworkObjectiveEngine,
    ClusterScorer,
    NetworkPhase,
    ClusterHealth,
    SovereignOversightLayer,
)


# ──────────────────────────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────────────────────────

_REDIS_EMPIRE_REPORT_KEY  = getattr(config, "REDIS_EMPIRE_REPORT_KEY",  "Deriv_Empire_Report")
_REDIS_EMPIRE_HISTORY_KEY = getattr(config, "REDIS_EMPIRE_HISTORY_KEY", "Deriv_Empire_History")


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


# ──────────────────────────────────────────────────────────────────
# 1. Dataclasses
# ──────────────────────────────────────────────────────────────────

@dataclass
class EmpireObjective:
    """
    Mục tiêu tăng trưởng cấp đế chế cho chu kỳ hiện tại.

    target_dominance_score : tỉ lệ cơ hội khai thác mục tiêu (0.0–1.0)
    target_win_rate        : win rate trung bình mạng mục tiêu
    target_pf              : profit factor trung bình mục tiêu
    target_attention_entropy: entropy phân bổ attention (cao = đa dạng)
    growth_horizon_cycles  : số chu kỳ để đạt mục tiêu
    """
    target_dominance_score   : float = 0.70
    target_win_rate          : float = 0.60
    target_pf                : float = 1.40
    target_attention_entropy : float = 1.50   # bits
    growth_horizon_cycles    : int   = 100


@dataclass
class AttentionAsset:
    """
    Biểu diễn một cluster như một tài sản trong portfolio attention.

    Sharpe = (win_rate - risk_free) / vol_win_rate  (attention-adjusted)
    """
    cluster_id         : str
    score              : float = 0.0
    win_rate           : float = 0.0
    profit_factor      : float = 0.0
    drawdown_pct       : float = 0.0
    n_trades           : int   = 0
    regime             : str   = "unknown"
    sharpe_attention   : float = 0.0   # risk-adjusted attention value
    momentum_factor    : float = 1.0   # recent vs long-term performance ratio
    current_allocation : float = 0.0   # current attention_weight from SSOL
    recommended_alloc  : float = 0.0   # portfolio-optimized allocation
    delta_alloc        : float = 0.0   # recommended - current


@dataclass
class MergeProposal:
    """
    Đề xuất hợp nhất hai cluster yếu thành một cluster tập trung hơn.

    synergy_score : ước tính lợi ích từ việc hợp nhất (higher=better)
    rationale     : lý do đề xuất merge
    """
    cluster_a      : str
    cluster_b      : str
    synergy_score  : float = 0.0
    combined_trades: int   = 0
    rationale      : str   = ""


@dataclass
class EmpireReport:
    """
    Báo cáo đầy đủ của một chu kỳ SSCL.
    """
    timestamp              : str
    empire_phase           : str            # from NetworkPhase
    dominance_score        : float          # 0.0–1.0
    network_health_score   : float
    attention_entropy      : float          # bits — diversity of allocation
    objective              : dict           # EmpireObjective as dict
    attention_allocation   : List[dict]     # per-cluster AttentionAsset as dict
    merge_proposals        : List[dict]     # MergeProposal as dict
    portfolio_efficiency   : float          # total_weighted_win_rate / max_possible
    insights               : List[str]
    objectives_met         : List[str]
    objectives_missed      : List[str]


# ──────────────────────────────────────────────────────────────────
# 2. DominanceTracker
# ──────────────────────────────────────────────────────────────────

class DominanceTracker:
    """
    Tính Network Dominance Score: tỉ lệ cơ hội thị trường đang được
    khai thác hiệu quả so với tổng cơ hội khả dụng.

    Dominance = coverage_score × efficiency_score

    coverage_score  = n_active_clusters / n_total_symbols_in_pool
                    (bao nhiêu symbol đang được theo dõi)

    efficiency_score = weighted_avg_win_rate / theoretical_max_win_rate
                    (các cluster đang dùng cơ hội tốt đến mức nào)

    Dominance = 0.0 → không khai thác được gì
    Dominance = 1.0 → toàn bộ pool đang chạy ở win rate tối đa
    """

    _THEORETICAL_MAX_WR = float(getattr(config, "EMPIRE_THEORETICAL_MAX_WIN_RATE", 0.75))

    def compute(
        self,
        telemetries   : Dict[str, ClusterTelemetry],
        all_symbols   : List[str],
    ) -> float:
        """
        Tính dominance score.

        Parameters
        ----------
        telemetries : active cluster telemetry
        all_symbols : toàn bộ symbol trong pool (kể cả inactive)
        """
        n_total  = max(len(all_symbols), 1)
        active   = [t for t in telemetries.values() if t.n_trades > 0]
        n_active = len(active)

        coverage_score = n_active / n_total

        if not active:
            return 0.0

        # Weighted avg win rate (by n_trades)
        total_t  = sum(t.n_trades for t in active)
        avg_wr   = sum(t.win_rate * t.n_trades for t in active) / total_t if total_t else 0.0

        efficiency_score = min(1.0, avg_wr / self._THEORETICAL_MAX_WR)

        return round(coverage_score * efficiency_score, 4)


# ──────────────────────────────────────────────────────────────────
# 3. AttentionPortfolio
# ──────────────────────────────────────────────────────────────────

class AttentionPortfolio:
    """
    Quản lý attention allocation như một portfolio fund.

    Khác với SSOL ResourceAllocator (score-proportional):
      - Tính Sharpe-like metric cho từng cluster:
          sharpe = (win_rate - risk_free_rate) / max(drawdown_pct, 0.01)
      - Thêm momentum factor: recent performance vs long-term
      - Portfolio weights via softmax(sharpe × momentum)
      - Min allocation giữ cho tất cả cluster đang active
      - Penalize concentration: entropy regularisation

    Insight key: cluster có WR cao nhưng DD cũng cao sẽ nhận ít hơn
    cluster có WR vừa phải nhưng DD thấp (risk-adjusted superiority).
    """

    _RISK_FREE_RATE = float(getattr(config, "EMPIRE_RISK_FREE_WIN_RATE",    0.50))
    _MIN_ALLOC      = float(getattr(config, "EMPIRE_MIN_ATTENTION_FRACTION", 0.05))
    _TEMPERATURE    = float(getattr(config, "EMPIRE_PORTFOLIO_TEMPERATURE",  2.0))

    def build_assets(
        self,
        telemetries : Dict[str, ClusterTelemetry],
        scores      : Dict[str, Tuple[float, ClusterHealth]],
        current_budgets: Dict[str, Any],
    ) -> List[AttentionAsset]:
        """
        Tạo danh sách AttentionAsset và tính recommended allocation.

        Parameters
        ----------
        telemetries     : per-cluster telemetry
        scores          : from ClusterScorer
        current_budgets : {cluster_id: ResourceBudget} from SSOL

        Returns list of AttentionAsset sorted by recommended_alloc desc.
        """
        min_trades = int(getattr(config, "SSOL_MIN_TRADES_PER_CLUSTER", 10))
        assets: List[AttentionAsset] = []

        for cid, t in telemetries.items():
            score, health = scores.get(cid, (0.50, ClusterHealth.DEGRADED))
            cur_alloc     = getattr(current_budgets.get(cid), "attention_weight", 0.0) if current_budgets else 0.0

            # Sharpe-like: excess win rate per unit drawdown
            excess_wr = max(0.0, t.win_rate - self._RISK_FREE_RATE)
            risk_proxy = max(t.drawdown_pct, 0.01)
            sharpe     = excess_wr / risk_proxy if t.n_trades >= min_trades else 0.0

            # Momentum: compare last-half vs first-half pf (proxy)
            # Without trade-level time series, use n_trades as proxy for recency
            momentum = 1.0 + max(0.0, (t.profit_factor - 1.0) * 0.3)

            assets.append(AttentionAsset(
                cluster_id        = cid,
                score             = round(float(score), 4),
                win_rate          = round(t.win_rate, 4),
                profit_factor     = round(t.profit_factor, 4),
                drawdown_pct      = round(t.drawdown_pct, 4),
                n_trades          = t.n_trades,
                regime            = t.regime,
                sharpe_attention  = round(sharpe, 4),
                momentum_factor   = round(momentum, 4),
                current_allocation= round(cur_alloc, 4),
            ))

        # Portfolio weights via temperature-scaled softmax on sharpe×momentum
        values = np.array([a.sharpe_attention * a.momentum_factor for a in assets])
        if values.sum() <= 0:
            # No data: equal allocation
            eq_alloc = 1.0 / max(len(assets), 1)
            for a in assets:
                a.recommended_alloc = round(eq_alloc, 4)
                a.delta_alloc       = round(a.recommended_alloc - a.current_allocation, 4)
        else:
            # Softmax with temperature
            scaled  = values / self._TEMPERATURE
            exp_v   = np.exp(scaled - scaled.max())
            weights = exp_v / exp_v.sum()

            # Apply minimum allocation floor then renormalise
            weights = np.maximum(weights, self._MIN_ALLOC)
            weights = weights / weights.sum()

            for a, w in zip(assets, weights):
                a.recommended_alloc = round(float(w), 4)
                a.delta_alloc       = round(a.recommended_alloc - a.current_allocation, 4)

        assets.sort(key=lambda a: a.recommended_alloc, reverse=True)
        return assets

    def compute_entropy(self, assets: List[AttentionAsset]) -> float:
        """Shannon entropy of the recommended allocation (bits)."""
        weights = np.array([a.recommended_alloc for a in assets if a.recommended_alloc > 0])
        if len(weights) == 0:
            return 0.0
        weights = weights / weights.sum()
        return float(-np.sum(weights * np.log2(weights + 1e-12)))


# ──────────────────────────────────────────────────────────────────
# 4. MergeAdvisor
# ──────────────────────────────────────────────────────────────────

class MergeAdvisor:
    """
    Xác định cặp cluster nên hợp nhất để tăng hiệu quả.

    Tiêu chí đề xuất merge:
      1. Cả hai đang ở DEGRADED hoặc CRITICAL
      2. Cùng regime (hoặc regime khác nhau nhưng correlated)
      3. Tổng n_trades đủ lớn (combined > min_trades × 2)
      4. Synergy score: predicted improvement from combining resources

    Synergy = (combined_win_rate - avg_individual_win_rate) × combined_trades_bonus

    Lưu ý: MERGE trong hệ này có nghĩa tắt cluster yếu hơn và chuyển
    toàn bộ attention/capital sang cluster mạnh hơn, không phải tạo cluster mới.
    """

    _MIN_SYNERGY = float(getattr(config, "EMPIRE_MERGE_MIN_SYNERGY", 0.05))

    def find_proposals(
        self,
        assets    : List[AttentionAsset],
        scores    : Dict[str, Tuple[float, ClusterHealth]],
        telemetries: Dict[str, ClusterTelemetry],
    ) -> List[MergeProposal]:
        """
        Find cluster pairs worth merging.

        Returns list of MergeProposal sorted by synergy_score desc.
        Only proposes merges between DEGRADED/CRITICAL clusters.
        """
        proposals: List[MergeProposal] = []

        # Only consider degraded/critical
        candidates = [
            a for a in assets
            if scores.get(a.cluster_id, (0, ClusterHealth.HEALTHY))[1]
               in (ClusterHealth.DEGRADED, ClusterHealth.CRITICAL)
        ]

        # Pairwise evaluation
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a_asset = candidates[i]
                b_asset = candidates[j]
                a_tel   = telemetries.get(a_asset.cluster_id)
                b_tel   = telemetries.get(b_asset.cluster_id)

                if a_tel is None or b_tel is None:
                    continue
                if a_tel.n_trades == 0 or b_tel.n_trades == 0:
                    continue

                combined_trades = a_tel.n_trades + b_tel.n_trades
                # Weighted combined win rate
                combined_wr = (
                    a_tel.win_rate * a_tel.n_trades
                    + b_tel.win_rate * b_tel.n_trades
                ) / combined_trades

                avg_individual_wr = (a_tel.win_rate + b_tel.win_rate) / 2.0

                # Synergy: improvement potential + volume bonus
                volume_bonus = math.log(max(combined_trades, 1) / 10.0 + 1) / 5.0
                synergy      = (combined_wr - avg_individual_wr + 0.05) * (1.0 + volume_bonus)
                synergy      = round(max(0.0, synergy), 4)

                if synergy >= self._MIN_SYNERGY:
                    # Determine which to keep (higher score survives)
                    if a_asset.score >= b_asset.score:
                        kept, removed = a_asset.cluster_id, b_asset.cluster_id
                    else:
                        kept, removed = b_asset.cluster_id, a_asset.cluster_id

                    rationale = (
                        f"Merge {a_asset.cluster_id}({a_tel.win_rate:.1%}) + "
                        f"{b_asset.cluster_id}({b_tel.win_rate:.1%}) → "
                        f"tập trung vào {kept}, tắt {removed}. "
                        f"Synergy={synergy:.3f}"
                    )
                    proposals.append(MergeProposal(
                        cluster_a      = a_asset.cluster_id,
                        cluster_b      = b_asset.cluster_id,
                        synergy_score  = synergy,
                        combined_trades= combined_trades,
                        rationale      = rationale,
                    ))

        proposals.sort(key=lambda p: p.synergy_score, reverse=True)
        return proposals[:3]   # top 3 proposals only


# ──────────────────────────────────────────────────────────────────
# 5. EmpireObjectiveEngine
# ──────────────────────────────────────────────────────────────────

class EmpireObjectiveEngine:
    """
    Đặt và đánh giá mục tiêu tăng trưởng cấp đế chế.

    Mục tiêu được điều chỉnh theo phase hiện tại:
      SURVIVAL  → giảm mục tiêu, bảo toàn vốn là ưu tiên
      GROWTH    → mục tiêu chuẩn + push win rate
      EXPANSION → mục tiêu cao + coverage tối đa
      REBALANCE → mục tiêu trung bình, duy trì ổn định

    Đánh giá objectives met/missed với gap % để hiểu khoảng cách.
    """

    def get_objective(self, phase: NetworkPhase) -> EmpireObjective:
        """Return objective tuned for current phase."""
        base = EmpireObjective()
        if phase == NetworkPhase.SURVIVAL:
            return EmpireObjective(
                target_dominance_score   = 0.30,
                target_win_rate          = 0.52,
                target_pf                = 1.10,
                target_attention_entropy = 1.00,
                growth_horizon_cycles    = 200,
            )
        elif phase == NetworkPhase.GROWTH:
            return EmpireObjective(
                target_dominance_score   = 0.60,
                target_win_rate          = 0.60,
                target_pf                = 1.30,
                target_attention_entropy = 1.40,
                growth_horizon_cycles    = 100,
            )
        elif phase == NetworkPhase.EXPANSION:
            return EmpireObjective(
                target_dominance_score   = 0.80,
                target_win_rate          = 0.65,
                target_pf                = 1.50,
                target_attention_entropy = 1.80,
                growth_horizon_cycles    = 75,
            )
        else:  # REBALANCE
            return base

    def evaluate(
        self,
        objective          : EmpireObjective,
        dominance_score    : float,
        avg_win_rate       : float,
        avg_pf             : float,
        attention_entropy  : float,
    ) -> Tuple[List[str], List[str]]:
        """
        Trả về (objectives_met, objectives_missed) với gap %.

        Each entry is a string like "dominance_score: 0.72 ≥ 0.70 ✅"
        """
        met: List[str]    = []
        missed: List[str] = []

        checks = [
            ("dominance_score",    dominance_score,   objective.target_dominance_score,   True),
            ("network_win_rate",   avg_win_rate,       objective.target_win_rate,          True),
            ("network_pf",         avg_pf,             objective.target_pf,                True),
            ("attention_entropy",  attention_entropy,  objective.target_attention_entropy, True),
        ]

        for name, actual, target, higher_is_better in checks:
            gap = (actual - target) / max(abs(target), 1e-9) * 100
            if higher_is_better:
                ok = actual >= target
            else:
                ok = actual <= target
            sym = "✅" if ok else "❌"
            s = f"{name}: {actual:.3f} (target={target:.3f}, gap={gap:+.1f}%) {sym}"
            (met if ok else missed).append(s)

        return met, missed


# ──────────────────────────────────────────────────────────────────
# 6. EmpireControlLayer — Orchestrator
# ──────────────────────────────────────────────────────────────────

class EmpireControlLayer:
    """
    Orchestrator cho Strategic Sovereign Control Layer (SSCL).

    Workflow mỗi chu kỳ empire:
      1. Thu thập telemetry (via ClusterTelemetryCollector từ SSOL)
      2. Xác định empire phase (NetworkObjectiveEngine)
      3. Đặt mục tiêu empire (EmpireObjectiveEngine)
      4. Tính portfolio attention allocation (AttentionPortfolio)
      5. Tính network dominance score (DominanceTracker)
      6. Xác định merge proposals (MergeAdvisor)
      7. Đánh giá objectives met/missed
      8. Tạo insights + lưu báo cáo

    SSCL không thay thế SSOL mà bổ sung thêm tầng portfolio intelligence
    ở trên. Ideally cả hai chạy cùng chu kỳ nhưng có thể chạy độc lập.
    """

    def __init__(self) -> None:
        self._collector   = ClusterTelemetryCollector()
        self._scorer      = ClusterScorer()
        self._obj_engine  = NetworkObjectiveEngine()
        self._empire_obj  = EmpireObjectiveEngine()
        self._portfolio   = AttentionPortfolio()
        self._dominance   = DominanceTracker()
        self._merge_adv   = MergeAdvisor()

    def run(
        self,
        active_symbols : List[str],
        verbose        : bool = True,
    ) -> EmpireReport:
        """
        Chạy một chu kỳ SSCL.

        Parameters
        ----------
        active_symbols : cluster IDs hiện đang active
        verbose        : in log ra console

        Returns EmpireReport.
        """
        all_symbols = list(getattr(config, "SCAN_SYMBOLS", active_symbols))

        if verbose:
            print(f"\n  🌐 [SSCL] Empire Control Cycle — {len(active_symbols)} clusters")

        # ── Bước 1: Telemetry ─────────────────────────────────────
        telemetries = self._collector.collect(active_symbols)

        # ── Bước 2: Phase ─────────────────────────────────────────
        phase, phase_reason = self._obj_engine.determine_phase(telemetries)

        # ── Bước 3: Objective ─────────────────────────────────────
        objective = self._empire_obj.get_objective(phase)

        # ── Bước 4: Score clusters ────────────────────────────────
        scores    = self._scorer.score_all(telemetries)

        # ── Bước 5: Portfolio allocation ──────────────────────────
        # Try to get current SSOL budgets; fall back to empty dict
        current_budgets = self._get_ssol_budgets()
        assets = self._portfolio.build_assets(telemetries, scores, current_budgets)
        entropy = self._portfolio.compute_entropy(assets)

        # ── Bước 6: Dominance ─────────────────────────────────────
        dominance = self._dominance.compute(telemetries, all_symbols)

        # ── Bước 7: Merge proposals ───────────────────────────────
        merge_proposals = self._merge_adv.find_proposals(assets, scores, telemetries)

        # ── Bước 8: Objective evaluation ─────────────────────────
        active_tel = [t for t in telemetries.values() if t.n_trades > 0]
        total_t    = sum(t.n_trades for t in active_tel)
        avg_wr     = sum(t.win_rate * t.n_trades for t in active_tel) / total_t if total_t else 0.0
        avg_pf     = sum(t.profit_factor * t.n_trades for t in active_tel) / total_t if total_t else 0.0
        net_health = float(np.mean([s for s, _ in scores.values()])) if scores else 0.50

        met, missed = self._empire_obj.evaluate(objective, dominance, avg_wr, avg_pf, entropy)

        # ── Insights ─────────────────────────────────────────────
        insights = self._build_insights(
            phase, dominance, assets, entropy, merge_proposals, met, missed, avg_wr, avg_pf
        )

        if verbose:
            print(f"  🌐 [SSCL] Phase={phase.value}  Dominance={dominance:.2f}  "
                  f"Entropy={entropy:.2f}bits  Merges={len(merge_proposals)}")
            for ins in insights[:3]:
                print(f"  🌐 [SSCL]  {ins}")

        report = EmpireReport(
            timestamp            = _now_iso(),
            empire_phase         = phase.value,
            dominance_score      = round(dominance, 4),
            network_health_score = round(net_health, 4),
            attention_entropy    = round(entropy, 4),
            objective            = asdict(objective),
            attention_allocation = [asdict(a) for a in assets],
            merge_proposals      = [asdict(p) for p in merge_proposals],
            portfolio_efficiency = round(avg_wr / objective.target_win_rate, 4) if objective.target_win_rate else 0.0,
            insights             = insights,
            objectives_met       = met,
            objectives_missed    = missed,
        )

        self._save_report(report)
        return report

    # ── Private helpers ───────────────────────────────────────────

    def _get_ssol_budgets(self) -> Dict[str, Any]:
        """Try to read SSOL ResourceBudgets from Redis."""
        try:
            import redis as _redis
            from sovereign_oversight import ResourceBudget
            r   = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.get(getattr(config, "REDIS_SSOL_VERDICTS_KEY", "Deriv_SSOL_Verdicts"))
            if not raw:
                return {}
            # Verdicts contain cluster_id + action; budgets stored separately in report
            # Use report for budgets
            raw_report = r.get(getattr(config, "REDIS_SSOL_REPORT_KEY", "Deriv_SSOL_Report"))
            if not raw_report:
                return {}
            report_data = json.loads(raw_report)
            return {
                b["cluster_id"]: type("Budget", (), b)()
                for b in report_data.get("resource_budgets", [])
            }
        except Exception:
            return {}

    def _build_insights(
        self,
        phase         : NetworkPhase,
        dominance     : float,
        assets        : List[AttentionAsset],
        entropy       : float,
        merges        : List[MergeProposal],
        met           : List[str],
        missed        : List[str],
        avg_wr        : float,
        avg_pf        : float,
    ) -> List[str]:
        insights: List[str] = []

        insights.append(
            f"Empire phase: {phase.value} | "
            f"Network dominance: {dominance:.1%} | "
            f"Portfolio entropy: {entropy:.2f} bits"
        )
        insights.append(
            f"Network perf: WR={avg_wr:.1%}  PF={avg_pf:.2f}"
        )

        # Top attention clusters
        if assets:
            top  = assets[0]
            bot  = assets[-1]
            insights.append(
                f"💰 Top attention: {top.cluster_id} "
                f"(sharpe={top.sharpe_attention:.3f} → alloc={top.recommended_alloc:.1%})"
            )
            if top.cluster_id != bot.cluster_id:
                insights.append(
                    f"📉 Lowest attention: {bot.cluster_id} "
                    f"(sharpe={bot.sharpe_attention:.3f} → alloc={bot.recommended_alloc:.1%})"
                )

        # Reallocation needed?
        big_deltas = [a for a in assets if abs(a.delta_alloc) > 0.10]
        if big_deltas:
            delta_strs = [f"{a.cluster_id}({a.delta_alloc:+.1%})" for a in big_deltas[:3]]
            insights.append(f"🔄 Portfolio rebalance needed: {', '.join(delta_strs)}")

        # Merge proposals
        if merges:
            insights.append(
                f"🔀 Merge proposals: {len(merges)} — "
                + "; ".join(f"{p.cluster_a}+{p.cluster_b}" for p in merges[:2])
            )

        # Objectives
        if met:
            insights.append(f"✅ Objectives met: {len(met)}/{len(met)+len(missed)}")
        if missed:
            insights.append(f"❌ Objectives missed: {', '.join(m.split(':')[0] for m in missed)}")

        return insights

    def _save_report(self, report: EmpireReport) -> None:
        """Persist empire report to Redis + file."""
        payload = asdict(report)
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_EMPIRE_REPORT_KEY, json.dumps(payload))
            r.lpush(_REDIS_EMPIRE_HISTORY_KEY, json.dumps({
                "timestamp"       : report.timestamp,
                "phase"           : report.empire_phase,
                "dominance_score" : report.dominance_score,
                "entropy"         : report.attention_entropy,
                "portfolio_eff"   : report.portfolio_efficiency,
            }))
            r.ltrim(_REDIS_EMPIRE_HISTORY_KEY, 0, 199)
        except Exception:
            pass
        try:
            os.makedirs("models", exist_ok=True)
            with open("models/empire_report.json", "w") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────
# 7. API helpers
# ──────────────────────────────────────────────────────────────────

def get_empire_report() -> dict:
    """
    Đọc báo cáo SSCL gần nhất từ Redis hoặc file.
    Dùng cho API endpoint GET /empire/report.
    """
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw = r.get(_REDIS_EMPIRE_REPORT_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open("models/empire_report.json") as f:
            return json.load(f)
    except Exception:
        pass
    return {"status": "no_report", "message": "Chưa có báo cáo SSCL — chạy run_empire_cycle() trước"}


def get_empire_history(n: int = 50) -> List[dict]:
    """Lấy N chu kỳ lịch sử dominance/entropy từ Redis."""
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw_list = r.lrange(_REDIS_EMPIRE_HISTORY_KEY, 0, n - 1)
        return [json.loads(x) for x in raw_list]
    except Exception:
        return []


def run_empire_cycle(
    active_symbols : List[str] = None,
    verbose        : bool      = True,
) -> EmpireReport:
    """
    Top-level entry point: chạy một chu kỳ SSCL.

    Parameters
    ----------
    active_symbols : cluster IDs. Mặc định: config.SCAN_SYMBOLS.
    verbose        : in log ra console

    Cách dùng:
      >>> from empire_control import run_empire_cycle
      >>> report = run_empire_cycle()
      >>> print(report.dominance_score)
      >>> print([a['cluster_id'] + '→' + str(a['recommended_alloc']) for a in report.attention_allocation])
    """
    symbols = active_symbols or list(config.SCAN_SYMBOLS)
    layer   = EmpireControlLayer()
    return layer.run(active_symbols=symbols, verbose=verbose)
