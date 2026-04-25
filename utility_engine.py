"""
utility_engine.py
=================
DECISION THEORY + UTILITY OPTIMIZATION ENGINE

Bước chuyển từ causal intelligence → rational strategic agent.

Vấn đề với fitness-only approach (evolution_engine, causal_engine):
  • "Fitness" = một con số duy nhất → bỏ qua nhiều chiều giá trị thực tế
  • Win/loss không phân biệt "thắng an toàn" vs "thắng may mắn"
  • Không tối ưu trade-off: growth vs drawdown, speed vs stability
  • Không tính risk aversion — cùng EV nhưng mức độ rủi ro khác nhau

Bốn chiều Utility:
  1. GROWTH     — Tốc độ tăng vốn; Kelly-adjusted expected log return
  2. TRUST      — Độ tin cậy, tính nhất quán; drawdown ổn định, survivability
  3. SPEED      — Tần suất tín hiệu × win rate; đạt mục tiêu nhanh
  4. STABILITY  — Độ biến động thấp; ít nhạy cảm với regime changes

Ba công cụ lý thuyết:
  KellyOptimizer:
    f* = (p·b − q) / b      [Kelly fraction, b = odds = payout]
    Fractional Kelly = f* × kelly_fraction (config, default 0.25)
    Kelly-adjusted growth rate = p·log(1 + f*·b) + q·log(1 − f*)
    Mục tiêu: tối đa hóa log wealth (long-run optimal)

  ParetoOptimizer:
    Multi-objective: không hy sinh hoàn toàn 1 chiều cho chiều khác
    Dominated genome: có genome khác tốt hơn TRÊN MỌI CHIỀU → loại bỏ
    Pareto front: tập genomes không bị dominated → "efficient frontier"
    Scalarization: w_g·growth + w_t·trust + w_s·speed + w_b·stability

  TemporalDiscounter:
    Short-term horizon: H=5 trades  → weight = exp(-λ·0)  = 1.0
    Medium horizon:    H=20 trades  → weight = exp(-λ·1)  = e^{-λ}
    Long-term horizon: H=50 trades  → weight = exp(-λ·2)  = e^{-2λ}
    λ = discount rate (config UTILITY_DISCOUNT_RATE, default 0.2)
    Temporal utility = Σ discount(h) × utility(h)

  RationalAgent:
    Kết hợp:
      CausalReport → biết gene nào thực sự drive từng utility axis
      RegimeFitnessMap → biết genome nào survive trong current regime
      ParetoFront → trong tập efficient, chọn genome maximize weighted utility
      KellyFraction → tính stake size tối ưu cho genome được chọn
    Output: optimal_genome_id, recommended_kelly_stake, utility_breakdown, rationale

Thành phần:
  UtilityWeights      — trade-off sliders (sum = 1.0)
  UtilityScore        — per-genome utility on all axes
  UtilityEvaluator    — compute all utility scores from pool + regime data
  KellyOptimizer      — Kelly fraction + growth rate computation
  TemporalDiscounter  — discount-weighted utility across horizons
  ParetoOptimizer     — non-dominated sorting + efficient frontier
  RationalAgent       — orchestrator → picks optimal genome + stake
  UtilityReport       — full report dataclass
  get_utility_report()— top-level API helper
  run_utility_optimization() — top-level entry point

Cách dùng:
  >>> from utility_engine import run_utility_optimization
  >>> report = run_utility_optimization()
  >>> print(report.optimal_genome_id)   # best genome under current preferences
  >>> print(report.kelly_stake)          # recommended stake size
  >>> print(report.insights)             # human-readable rationale
"""

from __future__ import annotations

import copy
import json
import math
import os
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config
from evolution_engine import (
    GENE_BOUNDS,
    StrategyGenome,
    _clip_genome,
)
from strategy_genetics import GenePool


# ──────────────────────────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────────────────────────

_REDIS_UTILITY_REPORT_KEY = "Deriv_Utility_Report"


# ──────────────────────────────────────────────────────────────────
# 1. Data structures
# ──────────────────────────────────────────────────────────────────

@dataclass
class UtilityWeights:
    """
    User-configurable trade-off weights across four utility axes.

    Constraints: all weights >= 0, sum = 1.0 (normalized automatically).

    Presets:
      "aggressive"  : growth=0.6, trust=0.1, speed=0.2, stability=0.1
      "conservative": growth=0.2, trust=0.5, speed=0.1, stability=0.2
      "balanced"    : growth=0.25 each
      "speed"       : growth=0.2, trust=0.1, speed=0.6, stability=0.1
      "stable"      : growth=0.2, trust=0.3, speed=0.1, stability=0.4
    """
    growth   : float = 0.35   # Weight for expected log wealth (Kelly growth)
    trust    : float = 0.30   # Weight for consistency / survivability
    speed    : float = 0.20   # Weight for trade frequency × win rate
    stability: float = 0.15   # Weight for low regime-sensitivity

    def normalize(self) -> "UtilityWeights":
        """Ensure weights sum to 1.0."""
        total = self.growth + self.trust + self.speed + self.stability
        if total < 1e-9:
            total = 1.0
        return UtilityWeights(
            growth   = self.growth    / total,
            trust    = self.trust     / total,
            speed    = self.speed     / total,
            stability= self.stability / total,
        )

    def scalarize(self, score: "UtilityScore") -> float:
        """Compute weighted scalar utility from a UtilityScore."""
        w = self.normalize()
        return (w.growth    * score.growth_utility
              + w.trust     * score.trust_utility
              + w.speed     * score.speed_utility
              + w.stability * score.stability_utility)

    @classmethod
    def preset(cls, name: str) -> "UtilityWeights":
        presets = {
            "aggressive"  : cls(0.60, 0.10, 0.20, 0.10),
            "conservative": cls(0.20, 0.50, 0.10, 0.20),
            "balanced"    : cls(0.25, 0.25, 0.25, 0.25),
            "speed"       : cls(0.20, 0.10, 0.60, 0.10),
            "stable"      : cls(0.20, 0.30, 0.10, 0.40),
        }
        return presets.get(name.lower(), cls()).normalize()

    def to_dict(self) -> dict:
        w = self.normalize()
        return {"growth": round(w.growth, 4), "trust": round(w.trust, 4),
                "speed": round(w.speed, 4), "stability": round(w.stability, 4)}


@dataclass
class UtilityScore:
    """
    Per-genome utility decomposition across four axes.

    All utility values are normalized to [0, 1] for comparability.
    """
    genome_id        : str
    # Raw fitness metrics
    fitness          : float = 0.0
    win_rate_pct     : float = 0.0
    profit_factor    : float = 0.0
    n_trades         : int   = 0
    # Four utility axes [0, 1]
    growth_utility   : float = 0.0   # Kelly-adjusted growth potential
    trust_utility    : float = 0.0   # Consistency / survivability
    speed_utility    : float = 0.0   # Signal frequency × win rate
    stability_utility: float = 0.0   # Low regime sensitivity
    # Temporal utilities (discounted across horizons)
    short_term_utility: float = 0.0  # H=5 trades
    medium_term_utility: float = 0.0  # H=20 trades
    long_term_utility : float = 0.0  # H=50 trades
    temporal_utility  : float = 0.0  # Discount-weighted composite
    # Kelly
    kelly_fraction    : float = 0.0  # Optimal Kelly fraction f*
    kelly_growth_rate : float = 0.0  # E[log wealth] per trade
    # Pareto
    is_pareto_optimal : bool  = False
    pareto_rank       : int   = 999  # 1 = Pareto front, 2 = 2nd front, …
    # Weighted scalar utility (depends on UtilityWeights)
    weighted_utility  : float = 0.0
    # Source info
    regime_survivability: float = 0.0
    regime_robust_score : float = 0.0


@dataclass
class UtilityReport:
    """Complete utility optimization report."""
    pool_size           : int
    n_evaluated         : int
    weights             : dict             # UtilityWeights as dict
    optimal_genome_id   : str              # Best genome under current weights
    kelly_stake         : float            # Recommended stake (fraction of bankroll)
    pareto_front_size   : int
    scores              : List[dict]       # UtilityScore as dicts (top-N)
    pareto_front        : List[str]        # genome_ids on Pareto front
    utility_breakdown   : dict             # Optimal genome's axis breakdown
    causal_alignment    : dict             # Which utility axis each causal gene drives
    temporal_analysis   : dict            # Short vs medium vs long-term comparison
    insights            : List[str]
    timestamp           : str = ""


# ──────────────────────────────────────────────────────────────────
# 2. KellyOptimizer — rational position sizing
# ──────────────────────────────────────────────────────────────────

class KellyOptimizer:
    """
    Kelly Criterion: optimal fraction of bankroll to bet per trade.

    Kelly formula: f* = (p·b − q) / b
      p = win probability
      q = 1 - p (loss probability)
      b = net odds (payout ratio, e.g. 0.85 for 85% payout)

    Kelly-adjusted growth rate (per trade):
      g(f) = p·log(1 + f·b) + q·log(1 − f)

    This is the expected log wealth growth per trade.
    Maximized at f = f* (Kelly optimal).

    In practice, use Fractional Kelly (f* × fraction) to reduce
    variance while retaining most of the growth benefit.

    Half-Kelly: ~75% of max growth, ~50% of variance
    Quarter-Kelly: ~55% of max growth, ~25% of variance
    """

    def __init__(self) -> None:
        self._payout  = getattr(config, "SIM_PAYOUT_RATIO", 0.85)
        self._fraction = getattr(config, "UTILITY_KELLY_FRACTION", 0.25)
        self._max_kelly = getattr(config, "UTILITY_MAX_KELLY", 0.20)  # cap at 20%

    def compute(
        self,
        win_rate_pct : float,
        payout       : float = None,
    ) -> Tuple[float, float, float]:
        """
        Compute Kelly fraction, fractional Kelly, and growth rate.

        Returns (kelly_full, kelly_fractional, growth_rate_per_trade).
        """
        p = float(np.clip(win_rate_pct / 100.0, 0.01, 0.99))
        q = 1.0 - p
        b = payout or self._payout

        if b <= 0:
            return 0.0, 0.0, 0.0

        kelly_full = (p * b - q) / b
        kelly_full = max(0.0, kelly_full)    # Never bet negative (never bet if EV < 0)

        kelly_frac = min(kelly_full * self._fraction, self._max_kelly)

        # Expected log wealth growth per trade at fractional Kelly
        growth_rate = self._growth_rate(p, b, kelly_frac)

        return round(kelly_full, 4), round(kelly_frac, 4), round(growth_rate, 6)

    def _growth_rate(self, p: float, b: float, f: float) -> float:
        """E[log(1 + f·X)] where X = +b with prob p, -1 with prob q."""
        if f <= 0.0:
            return 0.0
        q = 1.0 - p
        win_term  = p * math.log(max(1e-12, 1.0 + f * b))
        loss_term = q * math.log(max(1e-12, 1.0 - f))
        return win_term + loss_term

    def growth_curve(
        self,
        win_rate_pct: float,
        n_fractions : int = 20,
        payout      : float = None,
    ) -> List[dict]:
        """
        Compute growth rate at various Kelly fractions.

        Useful for visualizing the risk-return trade-off curve.
        Returns [{fraction, growth_rate, relative_to_max}].
        """
        p = float(np.clip(win_rate_pct / 100.0, 0.01, 0.99))
        b = payout or self._payout
        kelly_full, _, _ = self.compute(win_rate_pct, payout)

        fracs = np.linspace(0.0, min(kelly_full * 2, 0.50), n_fractions)
        curve = []
        max_g = self._growth_rate(p, b, kelly_full) if kelly_full > 0 else 0.0

        for f in fracs:
            g = self._growth_rate(p, b, float(f))
            curve.append({
                "fraction"      : round(float(f), 4),
                "growth_rate"   : round(g, 6),
                "relative_to_max": round(g / (max_g + 1e-12), 4),
            })
        return curve

    def breakeven_winrate(self, payout: float = None) -> float:
        """Minimum win rate for positive Kelly fraction (profitable strategy)."""
        b = payout or self._payout
        return round(1.0 / (1.0 + b) * 100.0, 2)


# ──────────────────────────────────────────────────────────────────
# 3. TemporalDiscounter — short vs long-term utility
# ──────────────────────────────────────────────────────────────────

class TemporalDiscounter:
    """
    Evaluates utility across multiple time horizons with discounting.

    Three horizons:
      Short:  H=5  trades  — immediate performance, reactive strategies
      Medium: H=20 trades  — established performance signal
      Long:   H=50 trades  — stable, generalizable strategy

    Discount weights (exponential):
      w(h) = exp(-λ · h_index)   where λ = UTILITY_DISCOUNT_RATE

      Default λ=0.2: short=1.0, medium=0.82, long=0.67
        → Slight preference for near-term but respects long-term

      If λ=0 (no discounting): equal weight (purely rational)
      If λ→∞ (myopic): only short-term matters

    Formula for horizon utility:
      Short:  simulate last 5 trades from genome's evaluated results
      Medium: extrapolate from fitness assuming stationary returns
      Long:   use Kelly growth rate × 50 trades (log wealth model)

    Why temporal utility matters:
      Two genomes with same long-term fitness may differ in:
        - Genome A: strong start, then stabilizes (good for trust building)
        - Genome B: slow start, then compounds faster (good for long run)
      Current fitness metrics lose this information.
    """

    def __init__(self) -> None:
        self._discount_rate = getattr(config, "UTILITY_DISCOUNT_RATE", 0.2)
        self._horizons      = [5, 20, 50]   # trade counts
        self._kelly         = KellyOptimizer()

    def compute(
        self,
        win_rate_pct  : float,
        profit_factor : float,
        n_trades      : int,
        kelly_frac    : float,
    ) -> Tuple[float, float, float, float]:
        """
        Compute short, medium, long-term, and temporal utility.

        Returns (short_u, medium_u, long_u, temporal_u) all in [0, 1].
        """
        p = float(np.clip(win_rate_pct / 100.0, 0.01, 0.99))
        b = getattr(config, "SIM_PAYOUT_RATIO", 0.85)

        # Growth rate per trade (Kelly model)
        g = self._kelly._growth_rate(p, b, kelly_frac)

        # Utility at each horizon = expected log wealth growth
        # Normalized: sigmoid of (g × horizon) for [0,1] range
        def horizon_utility(h: int) -> float:
            cumulative_log_return = g * h
            # Map to [0, 1] via logistic: 0.5 = no growth, 1 = good growth
            return float(1.0 / (1.0 + math.exp(-cumulative_log_return * 3.0)))

        short_u  = horizon_utility(self._horizons[0])
        medium_u = horizon_utility(self._horizons[1])
        long_u   = horizon_utility(self._horizons[2])

        # Discount weights
        weights = [math.exp(-self._discount_rate * i) for i in range(3)]
        total_w = sum(weights)
        utilities = [short_u, medium_u, long_u]

        temporal_u = sum(w * u for w, u in zip(weights, utilities)) / total_w

        return (
            round(short_u,   4),
            round(medium_u,  4),
            round(long_u,    4),
            round(temporal_u, 4),
        )

    def describe_preference(self) -> str:
        """Human-readable description of temporal preference."""
        λ = self._discount_rate
        if λ < 0.05:
            return "Không phân biệt thời gian (purely rational)"
        elif λ < 0.15:
            return "Cân bằng ngắn-dài hạn"
        elif λ < 0.35:
            return "Ưu tiên vừa phải cho ngắn hạn"
        else:
            return "Rất ưu tiên ngắn hạn (myopic)"


# ──────────────────────────────────────────────────────────────────
# 4. UtilityEvaluator — multi-dimensional utility scoring
# ──────────────────────────────────────────────────────────────────

class UtilityEvaluator:
    """
    Compute UtilityScore for each genome across all four utility axes.

    Data sources:
      GenePool        → raw fitness, win_rate, profit_factor, n_trades
      CausalReport    → which genes drive which utility axis
      RegimeFitnessMap→ survivability, robust_score (from causal_engine)

    Normalization strategy:
      All utilities mapped to [0, 1] using min-max across the evaluated population.
      This ensures fair comparison — a genome's utility is relative to peers.

    Axis definitions:
      Growth    = f(kelly_growth_rate) — maximize expected log wealth
      Trust     = f(survivability, consistency) — survive across regimes
      Speed     = f(n_trades × win_rate) — reach target quickly
      Stability = 1 − f(regime_sensitivity) — low volatility of returns
    """

    def __init__(self) -> None:
        self._kelly    = KellyOptimizer()
        self._temporal = TemporalDiscounter()
        self._payout   = getattr(config, "SIM_PAYOUT_RATIO", 0.85)

    def evaluate_pool(
        self,
        pool           : GenePool,
        causal_report  : Optional[dict] = None,
        regime_maps    : Optional[List[dict]] = None,
    ) -> List[UtilityScore]:
        """
        Evaluate all genomes in pool, returning list of UtilityScore.

        Sorted by weighted_utility (descending) using default weights.
        """
        entries = pool.top_winners(k=min(pool.size(), 200))
        if not entries:
            return []

        # Build lookup for regime data
        regime_lookup: Dict[str, dict] = {}
        if regime_maps:
            for rm in regime_maps:
                gid = rm.get("genome_id", "")
                if gid:
                    regime_lookup[gid] = rm

        # Raw score computation
        raw_scores: List[UtilityScore] = []
        for entry in entries:
            us = self._score_one(entry, regime_lookup)
            raw_scores.append(us)

        if not raw_scores:
            return []

        # Normalize all axes to [0, 1] using population min-max
        raw_scores = self._normalize(raw_scores)

        # Compute temporal utility (uses normalized values)
        for us in raw_scores:
            short_u, medium_u, long_u, temp_u = self._temporal.compute(
                win_rate_pct  = us.win_rate_pct,
                profit_factor = us.profit_factor,
                n_trades      = us.n_trades,
                kelly_frac    = us.kelly_fraction,
            )
            us.short_term_utility  = short_u
            us.medium_term_utility = medium_u
            us.long_term_utility   = long_u
            us.temporal_utility    = temp_u

        # Compute default weighted utility
        default_weights = UtilityWeights().normalize()
        for us in raw_scores:
            us.weighted_utility = round(default_weights.scalarize(us), 6)

        raw_scores.sort(key=lambda u: u.weighted_utility, reverse=True)
        return raw_scores

    def _score_one(
        self,
        entry         : dict,
        regime_lookup : Dict[str, dict],
    ) -> UtilityScore:
        """Score a single pool entry (dict) on all axes."""
        gid         = entry.get("genome_id", "?")
        fitness     = float(entry.get("fitness", 0.0))
        win_rate    = float(entry.get("win_rate_pct", 0.0))
        pf          = float(entry.get("profit_factor", 1.0))
        n_trades    = int(entry.get("n_trades", 0))

        # Kelly
        kelly_full, kelly_frac, growth_rate = self._kelly.compute(win_rate, self._payout)

        # Regime data
        rm = regime_lookup.get(gid, {})
        survivability = float(rm.get("survivability", 0.5))
        robust_score  = float(rm.get("robust_score", 0.0))

        # Raw utility (unnormalized, will be normalized across population)
        # Growth: Kelly growth rate (can be negative if EV < 0)
        growth_raw = max(0.0, growth_rate)

        # Trust: regime survivability × consistency
        # Consistency: low variance ↔ moderate profit factor (PF~1.5 best; very high PF = lucky)
        pf_consistency = 1.0 / (1.0 + abs(pf - 1.5) * 0.3)
        trust_raw = survivability * pf_consistency

        # Speed: trade frequency × win rate (more trades AND higher WR = faster)
        # Normalize n_trades with soft cap at 100
        trade_freq = math.log1p(n_trades) / math.log1p(100)
        speed_raw  = trade_freq * max(0.0, (win_rate / 100.0) - 0.40)  # above 40% wr

        # Stability: 1 / (1 + regime_sensitivity)
        # We use std_fitness from regime map if available, else from profit_factor variance proxy
        std_fitness    = float(rm.get("std_fitness", 0.0))
        mean_fitness   = float(rm.get("mean_fitness", fitness))
        if mean_fitness > 1e-9:
            sensitivity = std_fitness / (mean_fitness + 1e-9)
        else:
            sensitivity = 1.0
        stability_raw = 1.0 / (1.0 + sensitivity)

        return UtilityScore(
            genome_id         = gid,
            fitness           = round(fitness, 6),
            win_rate_pct      = round(win_rate, 2),
            profit_factor     = round(pf, 4),
            n_trades          = n_trades,
            # Raw (pre-normalization — will be replaced)
            growth_utility    = round(growth_raw, 6),
            trust_utility     = round(trust_raw, 6),
            speed_utility     = round(speed_raw, 6),
            stability_utility = round(stability_raw, 6),
            kelly_fraction    = round(kelly_frac, 4),
            kelly_growth_rate = round(growth_rate, 6),
            regime_survivability = round(survivability, 4),
            regime_robust_score  = round(robust_score, 4),
        )

    def _normalize(self, scores: List[UtilityScore]) -> List[UtilityScore]:
        """Min-max normalize all four utility axes across population."""
        axes = ["growth_utility", "trust_utility", "speed_utility", "stability_utility"]
        for axis in axes:
            vals = [getattr(s, axis) for s in scores]
            lo   = min(vals)
            hi   = max(vals)
            span = hi - lo if hi != lo else 1.0
            for s in scores:
                old = getattr(s, axis)
                setattr(s, axis, round((old - lo) / span, 4))
        return scores

    def rescore_with_weights(
        self,
        scores  : List[UtilityScore],
        weights : UtilityWeights,
    ) -> List[UtilityScore]:
        """Recompute weighted_utility for a different UtilityWeights."""
        w = weights.normalize()
        for s in scores:
            s.weighted_utility = round(w.scalarize(s), 6)
        return sorted(scores, key=lambda s: s.weighted_utility, reverse=True)


# ──────────────────────────────────────────────────────────────────
# 5. ParetoOptimizer — multi-objective efficient frontier
# ──────────────────────────────────────────────────────────────────

class ParetoOptimizer:
    """
    Non-dominated sorting for multi-objective utility optimization.

    Dominance: genome A dominates genome B if:
      A is >= B on ALL utility axes AND > B on AT LEAST ONE axis

    Pareto Front (Rank 1): genomes not dominated by anyone
      → The "efficient frontier" of utility space
      → All are valid choices — trade-offs within the front are preferences

    Algorithm: Fast Non-Dominated Sorting (NSGA-II style)
      1. Compute domination counts for each genome
      2. First front: count = 0 (no one dominates them)
      3. Remove first front, recompute → second front
      4. Repeat until all assigned a rank

    For 4D space with N≤200 genomes, O(N²·d) is acceptable.
    """

    def sort(self, scores: List[UtilityScore]) -> List[UtilityScore]:
        """
        Assign pareto_rank to each UtilityScore.

        Returns same list with pareto_rank and is_pareto_optimal set.
        """
        if not scores:
            return scores

        axes   = ["growth_utility", "trust_utility", "speed_utility", "stability_utility"]
        n      = len(scores)
        vals   = np.array([[getattr(s, a) for a in axes] for s in scores])

        # Domination count and dominated set
        dom_count = np.zeros(n, dtype=int)
        dom_set   = [[] for _ in range(n)]

        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                if _dominates(vals[j], vals[i]):
                    dom_count[i] += 1
                elif _dominates(vals[i], vals[j]):
                    dom_set[i].append(j)

        # Non-dominated sorting
        fronts    = []
        current   = [i for i in range(n) if dom_count[i] == 0]
        remaining = set(range(n))

        while current:
            fronts.append(current)
            remaining -= set(current)
            next_front = []
            for i in current:
                for j in dom_set[i]:
                    dom_count[j] -= 1
                    if dom_count[j] == 0 and j in remaining:
                        next_front.append(j)
            current = next_front

        # Assign ranks
        for rank, front in enumerate(fronts, start=1):
            for idx in front:
                scores[idx].pareto_rank      = rank
                scores[idx].is_pareto_optimal = (rank == 1)

        return scores

    def crowding_distance(
        self,
        front_scores: List[UtilityScore],
    ) -> Dict[str, float]:
        """
        Compute crowding distance for diversity preservation within a front.

        Genome with high crowding distance is in a "sparse" region of Pareto front
        → preferred over genome in dense region when ranks are equal.

        Returns {genome_id: crowding_distance}.
        """
        if len(front_scores) <= 2:
            return {s.genome_id: float("inf") for s in front_scores}

        axes    = ["growth_utility", "trust_utility", "speed_utility", "stability_utility"]
        n       = len(front_scores)
        dists   = {s.genome_id: 0.0 for s in front_scores}
        ids     = [s.genome_id for s in front_scores]

        for axis in axes:
            vals    = [(getattr(s, axis), s.genome_id) for s in front_scores]
            vals    = sorted(vals)
            span    = vals[-1][0] - vals[0][0]
            if span < 1e-9:
                continue
            # Boundary genomes get infinity
            dists[vals[0][1]]  = float("inf")
            dists[vals[-1][1]] = float("inf")
            for k in range(1, n - 1):
                dist = (vals[k + 1][0] - vals[k - 1][0]) / span
                if dists[vals[k][1]] != float("inf"):
                    dists[vals[k][1]] += dist

        return dists


def _dominates(a: np.ndarray, b: np.ndarray) -> bool:
    """Return True if vector a dominates vector b."""
    return bool(np.all(a >= b) and np.any(a > b))


# ──────────────────────────────────────────────────────────────────
# 6. RationalAgent — optimal strategy selection
# ──────────────────────────────────────────────────────────────────

class RationalAgent:
    """
    Integrates all layers to select the optimal strategy under given preferences.

    Decision pipeline:
      1. Load utility scores for all genomes (UtilityEvaluator)
      2. Sort by Pareto rank (ParetoOptimizer)
      3. Within Pareto front, select genome with max weighted utility
      4. If causal report available, verify selected genome uses causal genes
      5. Compute Kelly stake for selected genome
      6. Generate rationale: WHY this genome is optimal

    The "rational" part:
      - Consistent: same preferences → same choice
      - Complete: can rank any two strategies
      - Transitive: if A > B and B > C, then A > C
      - Respectful of Pareto: never choose dominated genome
    """

    def __init__(self, weights: UtilityWeights = None, seed: int = 42) -> None:
        self._weights  = (weights or UtilityWeights()).normalize()
        self._evaluator = UtilityEvaluator()
        self._pareto    = ParetoOptimizer()
        self._kelly     = KellyOptimizer()
        self._seed      = seed

    def decide(
        self,
        pool          : GenePool,
        causal_report : Optional[dict] = None,
        current_regime: Optional[str]  = None,
        verbose       : bool = True,
    ) -> "UtilityReport":
        """
        Run full decision pipeline.

        Returns UtilityReport with optimal genome and rationale.
        """
        if pool.size() < 4:
            return UtilityReport(
                pool_size=pool.size(), n_evaluated=0,
                weights=self._weights.to_dict(),
                optimal_genome_id="none",
                kelly_stake=0.0,
                pareto_front_size=0,
                scores=[], pareto_front=[],
                utility_breakdown={},
                causal_alignment={},
                temporal_analysis={},
                insights=["Pool quá nhỏ — cần ít nhất 1 evolution cycle"],
                timestamp=_now_iso(),
            )

        if verbose:
            print(f"\n  🎯 [RationalAgent] Evaluating {pool.size()} genomes "
                  f"with weights={self._weights.to_dict()}")

        # Get regime fitness maps from causal report if available
        regime_maps: List[dict] = []
        causal_genes: List[str] = []
        if causal_report and isinstance(causal_report, dict):
            regime_maps   = causal_report.get("regime_fitness", [])
            causal_genes  = causal_report.get("causal_genes", [])

        # Step 1: Evaluate utility scores
        if verbose:
            print("  🎯 Step 1: Computing utility scores…")
        scores = self._evaluator.evaluate_pool(pool, causal_report, regime_maps)
        if not scores:
            return self._empty_report(pool.size())

        # Step 2: Pareto sort
        if verbose:
            print("  🎯 Step 2: Pareto non-dominated sorting…")
        scores = self._pareto.sort(scores)

        # Step 3: Apply user weights (might differ from default)
        scores = self._evaluator.rescore_with_weights(scores, self._weights)

        # Step 4: Filter to Pareto front, then pick max weighted utility
        pareto_front_ids = [s.genome_id for s in scores if s.is_pareto_optimal]
        front_scores     = [s for s in scores if s.is_pareto_optimal]

        if not front_scores:
            front_scores = scores[:5]  # fallback

        # Optional: regime-aware filter
        if current_regime and regime_maps:
            front_scores = self._filter_by_regime(front_scores, regime_maps, current_regime)
            if not front_scores:
                front_scores = [s for s in scores if s.is_pareto_optimal] or scores[:5]

        # Best genome on Pareto front
        best = max(front_scores, key=lambda s: s.weighted_utility)

        # Step 5: Kelly stake for optimal genome
        _, kelly_stake, _ = self._kelly.compute(best.win_rate_pct)

        # Step 6: Causal alignment analysis
        causal_alignment = self._analyze_causal_alignment(
            best, causal_report, causal_genes
        )

        # Step 7: Temporal analysis
        temporal_analysis = {
            "short_term_utility" : best.short_term_utility,
            "medium_term_utility": best.medium_term_utility,
            "long_term_utility"  : best.long_term_utility,
            "temporal_utility"   : best.temporal_utility,
            "discount_rate"      : getattr(config, "UTILITY_DISCOUNT_RATE", 0.2),
            "preference_type"    : TemporalDiscounter().describe_preference(),
        }

        # Step 8: Insights
        insights = self._generate_insights(
            best, scores, front_scores, causal_genes, current_regime, kelly_stake
        )

        if verbose:
            print(f"\n  🎯 Optimal genome: {best.genome_id}")
            print(f"     Utility: growth={best.growth_utility:.3f} "
                  f"trust={best.trust_utility:.3f} "
                  f"speed={best.speed_utility:.3f} "
                  f"stability={best.stability_utility:.3f}")
            print(f"     Weighted: {best.weighted_utility:.4f}  Kelly stake: {kelly_stake:.2%}")
            for ins in insights:
                print(f"     • {ins}")

        report = UtilityReport(
            pool_size         = pool.size(),
            n_evaluated       = len(scores),
            weights           = self._weights.to_dict(),
            optimal_genome_id = best.genome_id,
            kelly_stake       = round(kelly_stake, 4),
            pareto_front_size = len(pareto_front_ids),
            scores            = [asdict(s) for s in scores[:20]],
            pareto_front      = pareto_front_ids,
            utility_breakdown = {
                "growth"   : best.growth_utility,
                "trust"    : best.trust_utility,
                "speed"    : best.speed_utility,
                "stability": best.stability_utility,
                "weighted" : best.weighted_utility,
            },
            causal_alignment  = causal_alignment,
            temporal_analysis = temporal_analysis,
            insights          = insights,
            timestamp         = _now_iso(),
        )

        return report

    def _filter_by_regime(
        self,
        front_scores : List[UtilityScore],
        regime_maps  : List[dict],
        current_regime: str,
    ) -> List[UtilityScore]:
        """Prefer genomes that perform well in current regime."""
        regime_performance: Dict[str, float] = {}
        for rm in regime_maps:
            gid = rm.get("genome_id", "")
            rf  = rm.get("regime_fitness", {})
            regime_performance[gid] = float(rf.get(current_regime, 0.0))

        # Filter: keep only genomes with positive performance in current regime
        regime_fit_threshold = getattr(config, "CAUSAL_FIT_THRESHOLD", 0.01)
        filtered = [s for s in front_scores
                    if regime_performance.get(s.genome_id, 0.0) >= regime_fit_threshold]
        return filtered or front_scores

    def _analyze_causal_alignment(
        self,
        best          : UtilityScore,
        causal_report : Optional[dict],
        causal_genes  : List[str],
    ) -> dict:
        """
        Analyze how well the optimal genome aligns with causal findings.

        Returns dict mapping utility axes to causal gene drivers.
        """
        if not causal_report or not isinstance(causal_report, dict):
            return {}

        effects = causal_report.get("effects", [])
        if not effects:
            return {}

        # Map each causal gene to utility axis (heuristic by gene type)
        axis_map = {
            "growth"   : ["wave_weight", "lookahead_candles", "rsi_weight"],
            "speed"    : ["min_signal_score", "min_wave_score", "bb_tight_threshold"],
            "stability": ["rsi_oversold", "rsi_overbought", "rsi_near_os", "rsi_near_ob"],
            "trust"    : ["momentum_weight", "macd_weight", "bb_weight"],
        }

        alignment: dict = {}
        for axis, axis_genes in axis_map.items():
            causal_in_axis = [g for g in causal_genes if g in axis_genes]
            spurious_in_axis = [
                e["gene"] for e in effects
                if e.get("is_spurious") and e["gene"] in axis_genes
            ]
            alignment[axis] = {
                "causal_drivers" : causal_in_axis,
                "spurious_present": spurious_in_axis,
                "alignment_quality": "good" if causal_in_axis and not spurious_in_axis
                                    else "partial" if causal_in_axis
                                    else "unknown",
            }
        return alignment

    def _generate_insights(
        self,
        best          : UtilityScore,
        all_scores    : List[UtilityScore],
        front_scores  : List[UtilityScore],
        causal_genes  : List[str],
        current_regime: Optional[str],
        kelly_stake   : float,
    ) -> List[str]:
        """Generate human-readable decision rationale."""
        insights = []
        w = self._weights

        # What the current preferences prioritize
        priority = max(
            [("growth", w.growth), ("trust", w.trust),
             ("speed", w.speed), ("stability", w.stability)],
            key=lambda x: x[1]
        )[0]
        insights.append(
            f"Chế độ tối ưu: '{priority}' "
            f"(g={w.growth:.0%} t={w.trust:.0%} sp={w.speed:.0%} st={w.stability:.0%})"
        )

        # Optimal genome breakdown
        insights.append(
            f"Genome tối ưu: {best.genome_id} "
            f"| growth={best.growth_utility:.3f} trust={best.trust_utility:.3f} "
            f"speed={best.speed_utility:.3f} stability={best.stability_utility:.3f}"
        )

        # Kelly stake
        breakeven = self._kelly.breakeven_winrate()
        if kelly_stake > 0:
            insights.append(
                f"Kelly stake tối ưu: {kelly_stake:.1%} bankroll "
                f"(WR={best.win_rate_pct:.1f}% vs breakeven {breakeven:.1f}%)"
            )
        else:
            insights.append(
                f"Kelly stake = 0 — WR={best.win_rate_pct:.1f}% dưới breakeven {breakeven:.1f}%"
            )

        # Pareto front
        insights.append(
            f"Pareto front: {len(front_scores)} genomes không bị dominated "
            f"(trong {len(all_scores)} đánh giá)"
        )

        # Causal alignment
        if causal_genes:
            insights.append(
                f"Genome được hỗ trợ bởi {len(causal_genes)} causal gene(s): "
                f"{', '.join(causal_genes[:3])}"
            )

        # Regime awareness
        if current_regime:
            insights.append(
                f"Đã lọc theo regime hiện tại: {current_regime}"
            )

        # Temporal preference
        td = TemporalDiscounter()
        insights.append(
            f"Temporal preference: {td.describe_preference()} "
            f"(λ={getattr(config, 'UTILITY_DISCOUNT_RATE', 0.2):.2f})"
        )

        # Trade-off warning
        if best.growth_utility > 0.8 and best.stability_utility < 0.3:
            insights.append(
                "⚠️  Growth cao nhưng stability thấp — genome này rủi ro khi regime đổi"
            )
        elif best.trust_utility > 0.8:
            insights.append(
                "✅ Trust/stability cao — genome này ổn định qua nhiều điều kiện"
            )

        return insights

    def _empty_report(self, pool_size: int) -> UtilityReport:
        return UtilityReport(
            pool_size=pool_size, n_evaluated=0,
            weights=self._weights.to_dict(),
            optimal_genome_id="none", kelly_stake=0.0,
            pareto_front_size=0, scores=[], pareto_front=[],
            utility_breakdown={}, causal_alignment={}, temporal_analysis={},
            insights=["Không đủ dữ liệu để tối ưu"], timestamp=_now_iso(),
        )


# ──────────────────────────────────────────────────────────────────
# 7. UtilityEngine — orchestrator
# ──────────────────────────────────────────────────────────────────

class UtilityEngine:
    """
    Orchestrator for the full Decision Theory + Utility Optimization pipeline.

    Workflow:
      1. Load gene pool (GenePool)
      2. Load causal report if available (get_causal_report)
      3. Evaluate utility scores (UtilityEvaluator)
      4. Pareto sort (ParetoOptimizer)
      5. Select optimal genome under given weights (RationalAgent)
      6. Save UtilityReport to Redis + file
    """

    def __init__(
        self,
        weights       : UtilityWeights = None,
        seed          : int = 42,
    ) -> None:
        self._pool    = GenePool()
        self._pool.load()
        self._weights = (weights or UtilityWeights()).normalize()
        self._agent   = RationalAgent(weights=self._weights, seed=seed)

    def ingest_population(self, population: List[StrategyGenome]) -> None:
        """Add evaluated population to gene pool."""
        self._pool.add_population(population)

    def run(
        self,
        population     : List[StrategyGenome] = None,
        weights        : UtilityWeights = None,
        current_regime : str = None,
        verbose        : bool = True,
    ) -> UtilityReport:
        """
        Run full utility optimization pipeline.

        Parameters
        ----------
        population    : Optional population to ingest first.
        weights       : Trade-off preferences. Uses instance default if None.
        current_regime: Current market regime (from WorldModel forecast).
        verbose       : Print progress.
        """
        if population:
            self.ingest_population(population)

        # Reload weights if provided
        if weights:
            self._weights = weights.normalize()
            self._agent   = RationalAgent(weights=self._weights)

        # Load causal report for causal-aware utility scoring
        causal_report: Optional[dict] = None
        try:
            from causal_engine import get_causal_report
            raw = get_causal_report()
            if "status" not in raw:
                causal_report = raw
        except Exception:
            pass

        report = self._agent.decide(
            pool          = self._pool,
            causal_report = causal_report,
            current_regime= current_regime,
            verbose       = verbose,
        )

        self._save_report(report)
        self._pool.save()
        return report

    def pareto_analysis(
        self,
        weight_presets: List[str] = None,
    ) -> dict:
        """
        Run Pareto analysis under multiple weight presets.

        Returns dict: {preset_name: optimal_genome_id + utility_breakdown}.
        """
        presets = weight_presets or ["aggressive", "conservative", "balanced", "speed", "stable"]
        results: dict = {}

        causal_report = None
        try:
            from causal_engine import get_causal_report
            raw = get_causal_report()
            if "status" not in raw:
                causal_report = raw
        except Exception:
            pass

        evaluator = UtilityEvaluator()
        pareto_opt = ParetoOptimizer()
        regime_maps = []
        if causal_report:
            regime_maps = causal_report.get("regime_fitness", [])

        scores = evaluator.evaluate_pool(self._pool, causal_report, regime_maps)
        if not scores:
            return {}
        scores = pareto_opt.sort(scores)

        for preset in presets:
            w = UtilityWeights.preset(preset)
            scored_copy = [copy.copy(s) for s in scores]
            scored_copy = evaluator.rescore_with_weights(scored_copy, w)
            best = scored_copy[0]
            results[preset] = {
                "optimal_genome_id": best.genome_id,
                "weighted_utility" : best.weighted_utility,
                "growth"   : best.growth_utility,
                "trust"    : best.trust_utility,
                "speed"    : best.speed_utility,
                "stability": best.stability_utility,
                "kelly_fraction" : best.kelly_fraction,
                "weights"  : w.to_dict(),
            }

        return results

    def _save_report(self, report: UtilityReport) -> None:
        """Save utility report to Redis + file."""
        payload = asdict(report)
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_UTILITY_REPORT_KEY, json.dumps(payload))
        except Exception:
            pass
        try:
            os.makedirs("models", exist_ok=True)
            with open("models/utility_report.json", "w") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────
# 8. API helpers
# ──────────────────────────────────────────────────────────────────

def get_utility_report() -> dict:
    """Load and return latest utility report from Redis or file."""
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw = r.get(_REDIS_UTILITY_REPORT_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open("models/utility_report.json") as f:
            return json.load(f)
    except Exception:
        return {"status": "no_report"}


def run_utility_optimization(
    population     : List[StrategyGenome] = None,
    weights        : UtilityWeights = None,
    current_regime : str = None,
    seed           : int = 42,
    verbose        : bool = True,
) -> UtilityReport:
    """
    Top-level entry point for utility optimization.

    Parameters
    ----------
    population    : Optional evaluated population to add to pool.
    weights       : Trade-off preferences (UtilityWeights or None for defaults).
    current_regime: Current market regime name.
    seed          : Random seed.
    verbose       : Print progress.

    Returns
    -------
    UtilityReport with optimal genome, Kelly stake, Pareto front, insights.

    Ví dụ:
    ------
    >>> from utility_engine import run_utility_optimization, UtilityWeights
    >>> w = UtilityWeights(growth=0.5, trust=0.3, speed=0.1, stability=0.1)
    >>> report = run_utility_optimization(weights=w)
    >>> print(report.optimal_genome_id)
    >>> print(f"Kelly stake: {report.kelly_stake:.1%}")
    """
    engine = UtilityEngine(weights=weights, seed=seed)
    return engine.run(
        population     = population,
        current_regime = current_regime,
        verbose        = verbose,
    )


# ──────────────────────────────────────────────────────────────────
# 9. Helpers
# ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    try:
        import pandas as _pd
        return str(_pd.Timestamp.now().isoformat())
    except Exception:
        import datetime
        return datetime.datetime.utcnow().isoformat()


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Decision Theory + Utility Optimization Engine")
    parser.add_argument("--mode", choices=["report", "optimize", "pareto", "demo", "kelly"],
                        default="report")
    parser.add_argument("--preset", default="balanced",
                        help="Utility weight preset: aggressive/conservative/balanced/speed/stable")
    parser.add_argument("--regime", default=None, help="Current market regime")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    if args.mode == "report":
        print("\n═══ UTILITY REPORT ═══")
        rpt = get_utility_report()
        if "status" in rpt:
            print("No report found. Run 'demo' or 'optimize' first.")
        else:
            print(f"Optimal genome : {rpt.get('optimal_genome_id', '?')}")
            print(f"Kelly stake    : {rpt.get('kelly_stake', 0):.1%}")
            print(f"Pareto front   : {rpt.get('pareto_front_size', 0)} genomes")
            print(f"Breakdown      : {rpt.get('utility_breakdown', {})}")
            print("\nInsights:")
            for i in rpt.get("insights", []):
                print(f"  • {i}")

    elif args.mode == "kelly":
        print("\n═══ KELLY CRITERION ═══")
        opt = KellyOptimizer()
        for wr in [48, 52, 55, 58, 60, 65]:
            kf, kf_frac, gr = opt.compute(wr)
            print(f"  WR={wr}%:  full_kelly={kf:.1%}  "
                  f"quarter_kelly={kf_frac:.1%}  growth_rate={gr:.5f}/trade")
        print(f"\n  Breakeven WR: {opt.breakeven_winrate():.1f}%")

    elif args.mode == "demo":
        print(f"\n═══ UTILITY DEMO (preset={args.preset}) ═══")
        from evolution_engine import GeneticOperators
        rng = np.random.default_rng(42)
        ops = GeneticOperators(seed=42)

        pop = []
        for _ in range(60):
            g = ops.generate_random()
            wave_b = (g.wave_weight - 1.0) * 0.05
            g.fitness       = max(0.0, float(rng.uniform(0, 0.15) + wave_b))
            g.win_rate_pct  = float(rng.uniform(45, 68))
            g.profit_factor = float(rng.uniform(0.9, 2.5))
            g.n_trades      = int(rng.integers(15, 120))
            g.evaluated     = True
            pop.append(g)

        weights = UtilityWeights.preset(args.preset)
        print(f"Weights: {weights.to_dict()}")

        report = run_utility_optimization(
            population=pop, weights=weights, current_regime=args.regime, verbose=True
        )
        print(f"\n{'═'*50}")
        print(f"Optimal genome : {report.optimal_genome_id}")
        print(f"Kelly stake    : {report.kelly_stake:.1%}")
        print(f"Pareto front   : {report.pareto_front_size} genomes")
        print(f"Breakdown      : {report.utility_breakdown}")
        print(f"\nInsights:")
        for i in report.insights:
            print(f"  • {i}")

    elif args.mode == "pareto":
        print("\n═══ PARETO ANALYSIS ACROSS PRESETS ═══")
        from evolution_engine import GeneticOperators
        rng = np.random.default_rng(99)
        ops = GeneticOperators(seed=99)
        pop = []
        for _ in range(60):
            g = ops.generate_random()
            g.fitness       = max(0.0, float(rng.uniform(0, 0.2)))
            g.win_rate_pct  = float(rng.uniform(45, 68))
            g.profit_factor = float(rng.uniform(0.9, 2.5))
            g.n_trades      = int(rng.integers(10, 100))
            g.evaluated     = True
            pop.append(g)
        engine = UtilityEngine()
        engine.ingest_population(pop)
        results = engine.pareto_analysis()
        for preset, res in results.items():
            print(f"  {preset:12s}: {res['optimal_genome_id']} "
                  f"(utility={res['weighted_utility']:.4f}  "
                  f"g={res['growth']:.3f} t={res['trust']:.3f} "
                  f"sp={res['speed']:.3f} st={res['stability']:.3f})")
