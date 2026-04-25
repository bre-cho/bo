"""
causal_engine.py
================
WORLD MODEL + CAUSAL STRATEGY ENGINE

Nâng hệ từ strategy genetics (học gene nào hay thắng)
lên causal strategic intelligence (học gene nào THẬT SỰ GÂY RA thắng lợi).

Vấn đề với correlation-only approach (strategy_genetics.py):
  • Correlation ≠ causation — gene có thể "có mặt" ở winner nhưng không phải
    nguyên nhân thắng (spurious correlation)
  • Thay đổi môi trường → correlation collapse (gene "giỏi" trong trending
    trở thành "tệ" trong choppy)
  • Không thể suy diễn chiến lược mới khi thiếu dữ liệu trực tiếp

Ba câu hỏi hệ này trả lời:
  1. Gene nào THẬT SỰ gây ra thắng lợi?   → InterventionSimulator (do-calculus)
  2. Gene nào chỉ là tương quan giả?       → SpuriousCorrelationDetector
  3. Nếu môi trường đổi, gene nào còn sống?→ WorldModel + CounterfactualReasoner

Phương pháp:
  InterventionSimulator:
    - "Do-calculus light": cố định gene G = v, giữ nguyên tất cả gene khác
    - Re-evaluate genome trên N môi trường (regime-diverse)
    - Tính Average Causal Effect (ACE): E[fitness | do(G=high)] − E[fitness | do(G=low)]
    - ACE ≠ 0 → gene có nhân quả thực

  SpuriousCorrelationDetector:
    - Compute partial correlation: ρ(gene_i, fitness | gene_j, gene_k, ...)
    - Nếu |partial ρ| << |simple ρ| → correlation bị confound bởi gene khác
    - Spurious score = |simple_ρ| - |partial_ρ|; threshold → flag

  WorldModel:
    - Lưu per-regime fitness cho từng genome (7 regimes)
    - Regime transition matrix: P(regime_t+1 | regime_t) học từ dữ liệu
    - Dự báo regime tiếp theo → pre-select genome phù hợp

  CounterfactualReasoner:
    - Với genome G trong regime A: "nếu regime chuyển sang B, fitness còn bao nhiêu?"
    - Gene regime-stability: std(fitness across regimes) / mean(fitness) — thấp = ổn định
    - Survivability score: % regimes mà genome đạt fitness > threshold
    - Cross-regime ranking → chọn robust genome (không chỉ tốt 1 regime)

Thành phần:
  CausalEffect         — dataclass lưu kết quả 1 gene causal analysis
  RegimeFitnessMap     — per-genome fitness breakdown qua 7 regimes
  InterventionSimulator — tính Average Causal Effect bằng intervention
  SpuriousDetector     — phân biệt causal vs spurious correlation
  WorldModel           — regime transition + regime-conditional strategy
  CounterfactualReasoner — "what if" env changes, dự báo survival
  CausalEngine         — orchestrator pipeline đầy đủ
  get_causal_report()  — top-level API helper
  run_causal_analysis()— top-level entry point

Cách dùng:
  >>> from causal_engine import run_causal_analysis
  >>> report = run_causal_analysis(population=last_pop)
  >>> print(report.causal_genes)   # genes with real causal effect
  >>> print(report.spurious_genes) # genes that are just correlated
"""

from __future__ import annotations

import copy
import json
import os
import math
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config
from evolution_engine import (
    GENE_BOUNDS,
    StrategyGenome,
    SimEnvironment,
    GeneticOperators,
    _clip_genome,
    _compute_fitness,
)
from strategy_genetics import GenePool


# ──────────────────────────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────────────────────────

_REDIS_CAUSAL_REPORT_KEY  = "Deriv_Causal_Report"
_REDIS_WORLD_MODEL_KEY    = "Deriv_World_Model"
_REDIS_REGIME_FITNESS_KEY = "Deriv_Regime_Fitness"


# ──────────────────────────────────────────────────────────────────
# Market regime definitions (matching synthetic_engine.py)
# ──────────────────────────────────────────────────────────────────

REGIMES = [
    "trend_up",
    "trend_down",
    "choppy",
    "high_vol_choppy",
    "crash",
    "spike",
    "recovery",
]

N_REGIMES = len(REGIMES)


# ──────────────────────────────────────────────────────────────────
# 1. Data structures
# ──────────────────────────────────────────────────────────────────

@dataclass
class CausalEffect:
    """
    Kết quả causal analysis cho một gene.

    Phân biệt:
      causal_ace:    Average Causal Effect (từ intervention)
      spurious_score: Mức độ "correlation giả" (simple_ρ - partial_ρ)
      regime_ace:    ACE per regime (gene có causal trong trending nhưng không trong choppy?)
      is_causal:     True nếu ACE đủ mạnh
      is_spurious:   True nếu correlation chủ yếu là giả
    """
    gene           : str
    simple_rho     : float   # Spearman ρ với fitness (full pool)
    partial_rho    : float   # Partial correlation controlling other genes
    causal_ace     : float   # Average Causal Effect from do-intervention
    spurious_score : float   # simple_rho - partial_rho magnitude
    is_causal      : bool    # |ACE| > CAUSAL_ACE_THRESHOLD
    is_spurious    : bool    # spurious_score > CAUSAL_SPURIOUS_THRESHOLD
    regime_ace     : Dict[str, float] = field(default_factory=dict)  # per-regime ACE
    regime_stable  : float = 0.0   # Stability across regimes (0=unstable, 1=stable)
    high_val_effect : float = 0.0  # ACE when gene is high
    low_val_effect  : float = 0.0  # ACE when gene is low
    optimal_range  : Tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))


@dataclass
class RegimeFitnessMap:
    """
    Per-genome fitness across each of the 7 market regimes.
    Used by CounterfactualReasoner and WorldModel.
    """
    genome_id      : str
    regime_fitness : Dict[str, float]   # {regime_name: fitness}
    mean_fitness   : float
    std_fitness    : float
    survivability  : float   # fraction of regimes where fitness > threshold
    regime_champion: str     # regime where this genome performs best
    robust_score   : float   # Sharpe-like: mean/std across regimes


@dataclass
class CausalReport:
    """Complete causal analysis report."""
    pool_size        : int
    n_evaluated      : int
    causal_genes     : List[str]     # genes with real causal effect (sorted by |ACE|)
    spurious_genes   : List[str]     # genes that are correlational only
    neutral_genes    : List[str]     # genes with no clear signal
    effects          : List[dict]    # CausalEffect as dicts
    regime_fitness   : List[dict]    # RegimeFitnessMap as dicts (top genomes)
    world_model      : dict          # Regime transition probabilities
    counterfactuals  : List[dict]    # Counterfactual survival predictions
    insights         : List[str]     # Human-readable causal insights
    timestamp        : str = ""


# ──────────────────────────────────────────────────────────────────
# 2. InterventionSimulator — Average Causal Effect via do-calculus
# ──────────────────────────────────────────────────────────────────

class InterventionSimulator:
    """
    Tính Average Causal Effect (ACE) của mỗi gene bằng intervention.

    Nguyên lý do-calculus (Pearl):
      Thay vì hỏi P(fitness | gene = v)         [observational]
      Hỏi       P(fitness | do(gene = v))        [interventional]

    Cách implement:
      1. Lấy N representative genomes từ pool
      2. Với mỗi gene G:
         a. Tạo phiên bản "high G": set G = p75(G across pool)
         b. Tạo phiên bản "low G":  set G = p25(G across pool)
         c. Đánh giá cả 2 trên CAUSAL_N_ENVS môi trường (đa dạng regime)
         d. ACE = mean(fitness_high_G) − mean(fitness_low_G)
      3. Lặp lại với K genomes → tính mean ACE

    Điểm quan trọng:
      - Chỉ thay đổi 1 gene, giữ nguyên tất cả gene khác
      - Đánh giá trên nhiều môi trường để không bị bias 1 regime
      - Kết quả: ACE > 0 → tăng gene này giúp fitness tăng
    """

    def __init__(self, seed: int = 42) -> None:
        self.seed        = seed
        self._rng        = np.random.default_rng(seed)
        self._n_probe    = getattr(config, "CAUSAL_N_PROBE_GENOMES", 8)
        self._n_envs     = getattr(config, "CAUSAL_N_ENVS", 5)
        self._candles    = getattr(config, "CAUSAL_ENV_CANDLES", 150)
        self._envs : List[Tuple[str, SimEnvironment]] = []

    def _build_envs(self) -> None:
        """Build diverse regime environments for intervention tests."""
        if self._envs:
            return
        try:
            from synthetic_engine import CandleGenerator
        except ImportError:
            self._envs = []
            return

        rng      = np.random.default_rng(self.seed + 99)
        regimes  = REGIMES
        gen      = CandleGenerator(seed=int(rng.integers(0, 10000)))
        self._envs = []
        for regime in regimes[:self._n_envs]:
            try:
                df = gen.generate(n=self._candles, regime=regime)
                self._envs.append((regime, SimEnvironment(df, env_id=regime)))
            except Exception:
                pass

    def _eval_genome_multi_env(self, genome: StrategyGenome) -> float:
        """Evaluate genome across all intervention environments; return mean fitness."""
        self._build_envs()
        if not self._envs:
            return 0.0
        scores = []
        for _reg, env in self._envs:
            try:
                result = env.evaluate(genome)
                scores.append(result.fitness)
            except Exception:
                scores.append(0.0)
        return float(np.mean(scores)) if scores else 0.0

    def compute_ace(
        self,
        pool      : GenePool,
        gene      : str,
        n_probe   : int = None,
    ) -> Tuple[float, float, float]:
        """
        Compute Average Causal Effect (ACE) for one gene.

        Returns (ace, high_effect, low_effect) where:
          ace         = E[fitness|do(G=high)] − E[fitness|do(G=low)]
          high_effect = E[fitness|do(G=high)]
          low_effect  = E[fitness|do(G=low)]
        """
        n_probe  = n_probe or self._n_probe
        winners  = pool.top_winners(k=n_probe * 3)   # oversample then take diverse
        if len(winners) < 2:
            return 0.0, 0.0, 0.0

        # Select diverse probe genomes (every 3rd to get spread)
        probe_dicts = winners[::max(1, len(winners) // n_probe)][:n_probe]

        lo_b, hi_b = GENE_BOUNDS[gene]
        gene_vals  = [float(w.get(gene, 0.0)) for w in pool._pool]
        if len(gene_vals) < 4:
            return 0.0, 0.0, 0.0

        high_setpoint = float(np.percentile(gene_vals, 75))
        low_setpoint  = float(np.percentile(gene_vals, 25))

        high_fitnesses : List[float] = []
        low_fitnesses  : List[float] = []

        for pd_entry in probe_dicts:
            # Build genome from pool entry
            g = _dict_to_genome(pd_entry)

            # High intervention
            g_high = copy.deepcopy(g)
            setattr(g_high, gene, high_setpoint)
            g_high = _clip_genome(g_high)
            g_high.genome_id = str(uuid.uuid4())[:8]
            high_fitnesses.append(self._eval_genome_multi_env(g_high))

            # Low intervention
            g_low = copy.deepcopy(g)
            setattr(g_low, gene, low_setpoint)
            g_low = _clip_genome(g_low)
            g_low.genome_id = str(uuid.uuid4())[:8]
            low_fitnesses.append(self._eval_genome_multi_env(g_low))

        high_mean = float(np.mean(high_fitnesses)) if high_fitnesses else 0.0
        low_mean  = float(np.mean(low_fitnesses))  if low_fitnesses  else 0.0
        ace       = round(high_mean - low_mean, 6)
        return ace, round(high_mean, 6), round(low_mean, 6)

    def compute_all_aces(
        self,
        pool    : GenePool,
        verbose : bool = False,
    ) -> Dict[str, Tuple[float, float, float]]:
        """
        Compute ACE for all 13 genes.

        Returns {gene: (ace, high_effect, low_effect)}.
        """
        results: Dict[str, Tuple[float, float, float]] = {}
        for gene in GENE_BOUNDS:
            if verbose:
                print(f"  [Intervention] Gene: {gene}...")
            ace, hi, lo = self.compute_ace(pool, gene)
            results[gene] = (ace, hi, lo)
            if verbose:
                print(f"    ACE={ace:+.5f}  high={hi:.5f}  low={lo:.5f}")
        return results

    def compute_regime_ace(
        self,
        pool  : GenePool,
        gene  : str,
    ) -> Dict[str, float]:
        """
        Compute ACE per regime for a gene.

        Returns {regime: ace_in_that_regime}.
        """
        try:
            from synthetic_engine import CandleGenerator
        except ImportError:
            return {}

        winners  = pool.top_winners(k=self._n_probe * 2)
        if len(winners) < 2:
            return {}

        probe_dicts = winners[::max(1, len(winners) // self._n_probe)][:self._n_probe]
        lo_b, hi_b  = GENE_BOUNDS[gene]
        gene_vals   = [float(w.get(gene, 0.0)) for w in pool._pool]
        if len(gene_vals) < 4:
            return {}

        high_setpoint = float(np.percentile(gene_vals, 75))
        low_setpoint  = float(np.percentile(gene_vals, 25))

        rng  = np.random.default_rng(self.seed + 777)
        gen  = CandleGenerator(seed=int(rng.integers(0, 10000)))
        regime_aces: Dict[str, float] = {}

        for regime in REGIMES:
            try:
                df  = gen.generate(n=self._candles, regime=regime)
                env = SimEnvironment(df, env_id=regime)
            except Exception:
                continue

            hi_fits: List[float] = []
            lo_fits: List[float] = []
            for pd_entry in probe_dicts:
                g = _dict_to_genome(pd_entry)
                g_h = copy.deepcopy(g);  setattr(g_h, gene, high_setpoint)
                g_h = _clip_genome(g_h); g_h.genome_id = str(uuid.uuid4())[:8]
                g_l = copy.deepcopy(g);  setattr(g_l, gene, low_setpoint)
                g_l = _clip_genome(g_l); g_l.genome_id = str(uuid.uuid4())[:8]
                try:
                    hi_fits.append(env.evaluate(g_h).fitness)
                    lo_fits.append(env.evaluate(g_l).fitness)
                except Exception:
                    pass

            if hi_fits and lo_fits:
                regime_aces[regime] = round(float(np.mean(hi_fits)) - float(np.mean(lo_fits)), 6)

        return regime_aces


# ──────────────────────────────────────────────────────────────────
# 3. SpuriousCorrelationDetector — causal vs spurious
# ──────────────────────────────────────────────────────────────────

class SpuriousDetector:
    """
    Phân biệt tương quan nhân quả thực vs tương quan giả (spurious).

    Phương pháp — Partial Correlation:
      Tương quan đơn giản:  ρ(gene_i, fitness)
      Partial correlation:  ρ(gene_i, fitness | other_genes)

      Nếu |partial_ρ| << |simple_ρ|:
        → Tương quan bị confound bởi gene khác
        → gene_i là spurious confounder

    Algorithm:
      Dùng Regression Residual Method:
        1. Regress fitness ~ all_other_genes → get fitness_resid
        2. Regress gene_i ~ all_other_genes  → get gene_i_resid
        3. partial_ρ(gene_i, fitness) = ρ(gene_i_resid, fitness_resid)

      Spurious score = |simple_ρ| - |partial_ρ|
        > CAUSAL_SPURIOUS_THRESHOLD → flag as spurious
    """

    def __init__(self) -> None:
        self._threshold = getattr(config, "CAUSAL_SPURIOUS_THRESHOLD", 0.15)

    def _ols_residuals(
        self,
        X : np.ndarray,   # (N, p) — predictors
        y : np.ndarray,   # (N,)   — target
    ) -> np.ndarray:
        """Return OLS residuals of y ~ X using normal equations."""
        try:
            Xb   = np.column_stack([np.ones(len(X)), X])
            beta = np.linalg.lstsq(Xb, y, rcond=None)[0]
            return y - Xb @ beta
        except Exception:
            return y - y.mean()

    def _pearson_r(self, x: np.ndarray, y: np.ndarray) -> float:
        """Pearson correlation coefficient."""
        if len(x) < 3:
            return 0.0
        try:
            xm = x - x.mean()
            ym = y - y.mean()
            denom = (np.sqrt((xm**2).sum()) * np.sqrt((ym**2).sum()))
            if denom < 1e-12:
                return 0.0
            return float(np.clip(np.dot(xm, ym) / denom, -1.0, 1.0))
        except Exception:
            return 0.0

    def _spearman_r(self, x: np.ndarray, y: np.ndarray) -> float:
        """Spearman rank correlation."""
        from strategy_genetics import _spearman_rho
        return _spearman_rho(x, y)

    def analyze(
        self,
        pool : GenePool,
    ) -> Dict[str, Tuple[float, float, float]]:
        """
        Analyze all genes for spurious vs causal correlation.

        Returns {gene: (simple_rho, partial_rho, spurious_score)}.
        """
        X, y, gene_names = pool.as_arrays()
        if len(X) < 15:
            return {}

        results: Dict[str, Tuple[float, float, float]] = {}
        n_genes = len(gene_names)

        for gi, gene in enumerate(gene_names):
            gene_col = X[:, gi]

            # Simple Spearman ρ
            simple_rho = self._spearman_r(gene_col, y)

            # Partial correlation: regress out other genes
            other_cols = np.delete(X, gi, axis=1)  # (N, p-1)

            gene_resid    = self._ols_residuals(other_cols, gene_col)
            fitness_resid = self._ols_residuals(other_cols, y)

            partial_rho = self._pearson_r(gene_resid, fitness_resid)

            spurious_score = abs(simple_rho) - abs(partial_rho)
            spurious_score = round(max(0.0, spurious_score), 4)

            results[gene] = (
                round(float(simple_rho), 4),
                round(float(partial_rho), 4),
                spurious_score,
            )

        return results

    def is_spurious(self, gene: str, analysis: Dict) -> bool:
        """Return True if gene's correlation is likely spurious."""
        if gene not in analysis:
            return False
        _, _, sp_score = analysis[gene]
        return sp_score > self._threshold


# ──────────────────────────────────────────────────────────────────
# 4. WorldModel — regime transition + regime-conditional strategies
# ──────────────────────────────────────────────────────────────────

class WorldModel:
    """
    Mô hình thế giới: học cấu trúc market regime + transition dynamics.

    Hai thành phần:
      A. Regime Transition Matrix P(r_t+1 | r_t):
         - Học từ lịch sử regime sequences trong pool
         - P[i, j] = xác suất chuyển từ regime i sang regime j
         - Nếu không có lịch sử, dùng prior đều (uniform)

      B. Regime-Conditional Fitness Mapping:
         Với mỗi genome và mỗi regime:
           fitness_g_r = đánh giá genome g trong regime r
         → Tìm genome "tổng quát" nhất (tốt trên nhiều regime)
         → Tìm genome "chuyên gia" từng regime (best per regime)
         → Tính robust_score = mean_fitness / (std_fitness + ε)

      C. Regime Forecast:
         - Dựa vào sequence regime gần đây
         - P(next_regime) = P[current_regime, :] từ transition matrix
         - Recommend genome có expected fitness cao nhất dựa trên forecast
    """

    def __init__(self, seed: int = 42) -> None:
        self.seed              = seed
        self._rng              = np.random.default_rng(seed)
        self._transition       = np.ones((N_REGIMES, N_REGIMES)) / N_REGIMES  # uniform prior
        self._regime_idx       = {r: i for i, r in enumerate(REGIMES)}
        self._idx_regime       = {i: r for i, r in enumerate(REGIMES)}
        self._n_envs_per_regime= getattr(config, "CAUSAL_REGIME_ENVS", 2)
        self._candles          = getattr(config, "CAUSAL_ENV_CANDLES", 150)

    def build_regime_fitness(
        self,
        pool    : GenePool,
        verbose : bool = False,
    ) -> List[RegimeFitnessMap]:
        """
        Evaluate top winners across all 7 regimes.

        Returns list of RegimeFitnessMap, sorted by robust_score desc.
        """
        try:
            from synthetic_engine import CandleGenerator
        except ImportError:
            return []

        top_k    = getattr(config, "CAUSAL_TOP_K_GENOMES", 15)
        winners  = pool.top_winners(k=top_k)
        if not winners:
            return []

        gen = CandleGenerator(seed=int(self._rng.integers(0, 10000)))
        fit_threshold = getattr(config, "CAUSAL_FIT_THRESHOLD", 0.01)

        # Build per-regime environments (1 per regime)
        regime_envs: Dict[str, SimEnvironment] = {}
        for regime in REGIMES:
            try:
                df = gen.generate(n=self._candles, regime=regime)
                regime_envs[regime] = SimEnvironment(df, env_id=regime)
            except Exception:
                pass

        if not regime_envs:
            return []

        maps: List[RegimeFitnessMap] = []
        for w_dict in winners:
            g = _dict_to_genome(w_dict)

            r_fitness: Dict[str, float] = {}
            for regime, env in regime_envs.items():
                try:
                    r_fitness[regime] = round(env.evaluate(g).fitness, 6)
                except Exception:
                    r_fitness[regime] = 0.0

            vals   = list(r_fitness.values())
            mean_f = float(np.mean(vals))
            std_f  = float(np.std(vals))
            surv   = float(sum(v > fit_threshold for v in vals)) / len(vals)
            robust = round(mean_f / (std_f + 1e-6), 4)
            best_r = max(r_fitness, key=lambda k: r_fitness[k])

            maps.append(RegimeFitnessMap(
                genome_id       = g.genome_id,
                regime_fitness  = r_fitness,
                mean_fitness    = round(mean_f, 6),
                std_fitness     = round(std_f, 6),
                survivability   = round(surv, 4),
                regime_champion = best_r,
                robust_score    = robust,
            ))

        maps.sort(key=lambda m: m.robust_score, reverse=True)
        if verbose:
            for m in maps[:3]:
                print(f"  [WorldModel] {m.genome_id} "
                      f"robust={m.robust_score:.4f} "
                      f"surv={m.survivability:.0%}")
        return maps

    def update_transition(self, regime_sequence: List[str]) -> None:
        """
        Update transition matrix from observed regime sequence.

        regime_sequence: ordered list of regime names observed.
        """
        if len(regime_sequence) < 2:
            return
        counts = np.zeros((N_REGIMES, N_REGIMES))
        for t in range(len(regime_sequence) - 1):
            ri = self._regime_idx.get(regime_sequence[t])
            rj = self._regime_idx.get(regime_sequence[t + 1])
            if ri is not None and rj is not None:
                counts[ri, rj] += 1
        row_sums = counts.sum(axis=1, keepdims=True)
        row_sums = np.where(row_sums == 0, 1, row_sums)
        self._transition = counts / row_sums

    def forecast_next_regime(self, current_regime: str) -> Dict[str, float]:
        """
        Predict distribution over next regimes given current regime.

        Returns {regime_name: probability}.
        """
        ri = self._regime_idx.get(current_regime)
        if ri is None:
            return {r: 1 / N_REGIMES for r in REGIMES}
        probs = self._transition[ri]
        return {self._idx_regime[i]: round(float(probs[i]), 4) for i in range(N_REGIMES)}

    def recommend_genome(
        self,
        current_regime  : str,
        regime_maps     : List[RegimeFitnessMap],
        forecast_horizon: int = 3,
    ) -> Optional[str]:
        """
        Recommend the genome with highest expected fitness for next regimes.

        Uses regime transition matrix to compute expected fitness under
        forecast_horizon-step regime distribution.

        Returns genome_id of recommended genome.
        """
        if not regime_maps:
            return None

        next_dist = self.forecast_next_regime(current_regime)
        ri = self._regime_idx.get(current_regime)

        # Multi-step forecast: P^horizon[current_regime, :]
        if ri is not None and forecast_horizon > 1:
            P_h = np.linalg.matrix_power(self._transition, forecast_horizon)
            next_dist = {self._idx_regime[j]: round(float(P_h[ri, j]), 4)
                         for j in range(N_REGIMES)}

        # Expected fitness for each genome
        best_genome_id = None
        best_expected  = -float("inf")
        for rm in regime_maps:
            exp_fit = sum(
                next_dist.get(regime, 0.0) * rm.regime_fitness.get(regime, 0.0)
                for regime in REGIMES
            )
            if exp_fit > best_expected:
                best_expected  = exp_fit
                best_genome_id = rm.genome_id

        return best_genome_id

    def get_transition_dict(self) -> Dict:
        """Return transition matrix as nested dict for JSON serialization."""
        return {
            REGIMES[i]: {REGIMES[j]: round(float(self._transition[i, j]), 4)
                         for j in range(N_REGIMES)}
            for i in range(N_REGIMES)
        }


# ──────────────────────────────────────────────────────────────────
# 5. CounterfactualReasoner — "what if" environment changes
# ──────────────────────────────────────────────────────────────────

class CounterfactualReasoner:
    """
    Dự đoán chiến lược nào sống sót nếu môi trường thay đổi.

    Counterfactual: "Nếu thị trường chuyển từ trending sang choppy,
                     genome nào vẫn còn profitable?"

    Hai loại counterfactual:
      1. Regime counterfactual:
         - So sánh fitness của genome trong regime A vs regime B
         - Tính "regime sensitivity": |fitness_A - fitness_B| / mean_fitness
         - Genome có sensitivity thấp → robust (general purpose)
         - Genome có sensitivity cao → specialist (regime-specific)

      2. Gene counterfactual (interaction with regime):
         - Trong regime A, gene G hướng positive (+0.3 fitness)
         - Trong regime B, gene G hướng negative (-0.2 fitness)
         - → gene G có "causal interaction" với regime
         - Phát hiện: cross-regime ACE variance cao

    Outputs:
      survival_table: genome × regime → survived (bool)
      regime_sensitivity: genome → sensitivity score (0=robust, 1=specialist)
      cross_regime_insights: text insights
    """

    def __init__(self) -> None:
        self._threshold = getattr(config, "CAUSAL_FIT_THRESHOLD", 0.01)

    def compute_counterfactuals(
        self,
        regime_maps: List[RegimeFitnessMap],
        world_model: WorldModel,
        query_regime_from: str = "trend_up",
        query_regime_to  : str = "choppy",
    ) -> List[dict]:
        """
        Answer: "If market switches from regime_from to regime_to,
                 which genomes survive?"

        Returns list of {genome_id, survived, fitness_before, fitness_after, delta}.
        """
        results = []
        for rm in regime_maps:
            fit_before = rm.regime_fitness.get(query_regime_from, 0.0)
            fit_after  = rm.regime_fitness.get(query_regime_to, 0.0)
            survived   = fit_after > self._threshold
            results.append({
                "genome_id"     : rm.genome_id,
                "regime_from"   : query_regime_from,
                "regime_to"     : query_regime_to,
                "fitness_before": round(fit_before, 6),
                "fitness_after" : round(fit_after, 6),
                "delta"         : round(fit_after - fit_before, 6),
                "survived"      : survived,
                "robust_score"  : rm.robust_score,
            })
        # Sort by fit_after descending
        results.sort(key=lambda x: x["fitness_after"], reverse=True)
        return results

    def regime_sensitivity(
        self,
        regime_maps: List[RegimeFitnessMap],
    ) -> Dict[str, float]:
        """
        Compute regime sensitivity for each genome.

        sensitivity = std(fitness_across_regimes) / (mean_fitness + ε)
        Low = robust generalist | High = regime specialist
        """
        result: Dict[str, float] = {}
        for rm in regime_maps:
            vals = list(rm.regime_fitness.values())
            mean = float(np.mean(vals))
            std  = float(np.std(vals))
            result[rm.genome_id] = round(std / (mean + 1e-6), 4)
        return result

    def gene_regime_stability(
        self,
        causal_effects  : List[CausalEffect],
    ) -> Dict[str, float]:
        """
        For each gene, compute how stable its causal effect is across regimes.

        Stability = 1 - (std(regime_aces) / (|mean_regime_ace| + ε))
        1.0 = perfectly stable effect | 0.0 = completely unstable

        High stability → gene causal effect is regime-agnostic
        Low stability  → gene is a regime specialist trigger
        """
        result: Dict[str, float] = {}
        for ce in causal_effects:
            if not ce.regime_ace:
                result[ce.gene] = 0.5  # unknown
                continue
            aces = list(ce.regime_ace.values())
            mean = float(np.mean(aces))
            std  = float(np.std(aces))
            stability = 1.0 - std / (abs(mean) + 1e-6)
            stability = float(np.clip(stability, 0.0, 1.0))
            result[ce.gene] = round(stability, 4)
        return result

    def generate_counterfactual_insights(
        self,
        regime_maps     : List[RegimeFitnessMap],
        causal_effects  : List[CausalEffect],
    ) -> List[str]:
        """Generate human-readable counterfactual insights."""
        insights = []

        if not regime_maps:
            return ["Chưa đủ dữ liệu để phân tích counterfactual"]

        # Most robust genome
        best = regime_maps[0]
        insights.append(
            f"Genome bền nhất: {best.genome_id} "
            f"(robust={best.robust_score:.3f}, sống sót {best.survivability:.0%} regimes)"
        )

        # Regime champion breakdown
        champ_by_regime = {}
        for rm in regime_maps:
            champ_by_regime[rm.regime_champion] = rm.genome_id
        if champ_by_regime:
            insights.append(
                "Genome chuyên biệt: " +
                ", ".join(f"{r}→{g[:6]}" for r, g in list(champ_by_regime.items())[:4])
            )

        # Gene regime-stability
        gene_stab = self.gene_regime_stability(causal_effects)
        if gene_stab:
            stable_genes   = sorted(gene_stab, key=gene_stab.get, reverse=True)[:3]
            unstable_genes = sorted(gene_stab, key=gene_stab.get)[:3]
            insights.append(
                f"Gene ổn định qua mọi regime: {', '.join(stable_genes)}"
            )
            insights.append(
                f"Gene nhạy cảm với regime (specialist): {', '.join(unstable_genes)}"
            )

        # Average survival across all regime pairs
        n_robust = sum(1 for rm in regime_maps if rm.survivability >= 0.7)
        insights.append(
            f"{n_robust}/{len(regime_maps)} genome sống sót ≥70% regimes "
            f"(fitness > threshold)"
        )

        return insights


# ──────────────────────────────────────────────────────────────────
# 6. CausalEngine — orchestrates full causal analysis pipeline
# ──────────────────────────────────────────────────────────────────

class CausalEngine:
    """
    Orchestrator toàn bộ causal analysis pipeline.

    Pipeline:
      1. Load gene pool (từ strategy_genetics.GenePool)
      2. Detect spurious correlations (SpuriousDetector)
      3. Compute Average Causal Effects via intervention (InterventionSimulator)
      4. Build regime fitness maps (WorldModel.build_regime_fitness)
      5. Compute counterfactual survival (CounterfactualReasoner)
      6. Generate CausalReport with insights
      7. Save report → Redis + file

    Chú ý: InterventionSimulator chạy thực actual simulations → có thể
    mất 30–60 giây tùy CAUSAL_N_PROBE_GENOMES × CAUSAL_N_ENVS × genes.
    Dùng CAUSAL_FAST_MODE = True để chỉ chạy spurious detection + world model.
    """

    def __init__(self, seed: int = 42) -> None:
        self.pool         = GenePool()
        self.pool.load()
        self.seed         = seed
        self._fast_mode   = getattr(config, "CAUSAL_FAST_MODE", True)

    def ingest_population(self, population: List[StrategyGenome]) -> None:
        """Add evaluated population to gene pool."""
        self.pool.add_population(population)

    def run(
        self,
        population : List[StrategyGenome] = None,
        verbose    : bool = True,
    ) -> CausalReport:
        """
        Run full causal analysis pipeline.

        Parameters
        ----------
        population : Optional evaluated population to add to pool first.
        verbose    : Print progress.

        Returns
        -------
        CausalReport with all causal findings.
        """
        if population:
            self.ingest_population(population)

        if self.pool.size() < 8:
            empty_report = CausalReport(
                pool_size=0, n_evaluated=0,
                causal_genes=[], spurious_genes=[], neutral_genes=[],
                effects=[], regime_fitness=[], world_model={},
                counterfactuals=[],
                insights=["Pool quá nhỏ — cần chạy ít nhất 1 evolution cycle trước"],
                timestamp=_now_iso(),
            )
            return empty_report

        if verbose:
            print(f"\n  ⚗️  [CausalEngine] Analyzing {self.pool.size()} genomes "
                  f"(fast_mode={self._fast_mode})...")

        # ── Step 1: Spurious Detection ─────────────────────────────
        if verbose:
            print("  ⚗️  Step 1: Spurious correlation detection...")
        spurious_det = SpuriousDetector()
        spurious_analysis = spurious_det.analyze(self.pool)

        # ── Step 2: ACE Intervention (skip in fast mode) ───────────
        ace_results: Dict[str, Tuple[float, float, float]] = {}
        regime_ace_results: Dict[str, Dict[str, float]] = {}

        if not self._fast_mode:
            if verbose:
                print("  ⚗️  Step 2: Intervention simulations (ACE)...")
            sim = InterventionSimulator(seed=self.seed)
            ace_results = sim.compute_all_aces(self.pool, verbose=verbose)
            # Compute regime ACE for top-3 most important genes
            from strategy_genetics import GeneImportanceAnalyzer
            imp_analyzer = GeneImportanceAnalyzer(self.pool)
            top_genes = [r.gene for r in imp_analyzer.analyze()[:3]]
            for gene in top_genes:
                regime_ace_results[gene] = sim.compute_regime_ace(self.pool, gene)
        else:
            # Fast mode: estimate ACE from correlation direction + strength
            X, y, gene_names = self.pool.as_arrays()
            for gene in GENE_BOUNDS:
                if gene in spurious_analysis:
                    _, partial_rho, _ = spurious_analysis[gene]
                else:
                    partial_rho = 0.0
                lo, hi = GENE_BOUNDS[gene]
                span   = hi - lo if hi != lo else 1.0
                # Proxy ACE: partial_rho × IQR of gene × fitness_scale
                gene_vals = [float(w.get(gene, 0.0)) for w in self.pool._pool]
                iqr = float(np.percentile(gene_vals, 75) - np.percentile(gene_vals, 25)) \
                    if len(gene_vals) >= 4 else 0.0
                ace_proxy = partial_rho * iqr
                ace_results[gene] = (
                    round(ace_proxy, 6),
                    round(ace_proxy / 2, 6),  # proxy high
                    round(-ace_proxy / 2, 6), # proxy low
                )

        # ── Step 3: Build CausalEffect objects ────────────────────
        ace_threshold = getattr(config, "CAUSAL_ACE_THRESHOLD", 0.002)
        spur_threshold = getattr(config, "CAUSAL_SPURIOUS_THRESHOLD", 0.15)

        X, y, gene_names = self.pool.as_arrays()
        from strategy_genetics import _spearman_rho
        causal_effects: List[CausalEffect] = []

        for gi, gene in enumerate(gene_names):
            x_col = X[:, gi] if len(X) > 0 else np.array([])
            simple_rho = _spearman_rho(x_col, y) if len(x_col) >= 3 else 0.0

            sp_data = spurious_analysis.get(gene, (0.0, 0.0, 0.0))
            partial_rho    = sp_data[1]
            spurious_score = sp_data[2]

            ace, hi_eff, lo_eff = ace_results.get(gene, (0.0, 0.0, 0.0))
            r_ace = regime_ace_results.get(gene, {})

            is_causal   = abs(ace) > ace_threshold
            is_spurious = spurious_score > spur_threshold

            # Optimal range: find bounds where ACE is positive
            lo_b, hi_b = GENE_BOUNDS[gene]
            gene_vals  = [float(w.get(gene, 0.0)) for w in self.pool._pool]
            if len(gene_vals) >= 4:
                if ace > 0:  # Higher is better
                    opt_range = (float(np.percentile(gene_vals, 60)),
                                 float(np.percentile(gene_vals, 90)))
                else:        # Lower is better
                    opt_range = (float(np.percentile(gene_vals, 10)),
                                 float(np.percentile(gene_vals, 40)))
            else:
                opt_range = (lo_b, hi_b)

            # Regime stability: std of regime ACEs normalized
            if r_ace:
                r_vals = list(r_ace.values())
                r_std  = float(np.std(r_vals))
                r_mean = float(np.mean([abs(v) for v in r_vals]))
                stability = 1.0 - r_std / (r_mean + 1e-6)
                stability = float(np.clip(stability, 0.0, 1.0))
            else:
                stability = 0.5  # unknown

            causal_effects.append(CausalEffect(
                gene           = gene,
                simple_rho     = round(float(simple_rho), 4),
                partial_rho    = round(float(partial_rho), 4),
                causal_ace     = round(float(ace), 6),
                spurious_score = round(float(spurious_score), 4),
                is_causal      = is_causal,
                is_spurious    = is_spurious,
                regime_ace     = {k: round(v, 6) for k, v in r_ace.items()},
                regime_stable  = round(stability, 4),
                high_val_effect= round(float(hi_eff), 6),
                low_val_effect = round(float(lo_eff), 6),
                optimal_range  = (round(opt_range[0], 3), round(opt_range[1], 3)),
            ))

        causal_effects.sort(key=lambda ce: abs(ce.causal_ace), reverse=True)

        # ── Step 4: World Model ────────────────────────────────────
        if verbose:
            print("  ⚗️  Step 4: Building world model (regime fitness)...")
        wm = WorldModel(seed=self.seed)
        regime_maps = wm.build_regime_fitness(self.pool, verbose=verbose)

        # ── Step 5: Counterfactuals ────────────────────────────────
        if verbose:
            print("  ⚗️  Step 5: Computing counterfactuals...")
        cf_reasoner = CounterfactualReasoner()
        counterfactuals = cf_reasoner.compute_counterfactuals(
            regime_maps,
            wm,
            query_regime_from="trend_up",
            query_regime_to  ="choppy",
        )

        # ── Step 6: Classify genes ────────────────────────────────
        causal_genes  = [ce.gene for ce in causal_effects if ce.is_causal and not ce.is_spurious]
        spurious_genes = [ce.gene for ce in causal_effects if ce.is_spurious]
        neutral_genes  = [ce.gene for ce in causal_effects
                         if not ce.is_causal and not ce.is_spurious]

        # ── Step 7: Insights ──────────────────────────────────────
        insights = _generate_causal_insights(causal_effects, regime_maps, counterfactuals)

        if verbose:
            print(f"\n  ⚗️  Causal genes:   {causal_genes}")
            print(f"  ⚗️  Spurious genes: {spurious_genes}")
            for insight in insights:
                print(f"     • {insight}")

        report = CausalReport(
            pool_size        = self.pool.size(),
            n_evaluated      = self.pool.size(),
            causal_genes     = causal_genes,
            spurious_genes   = spurious_genes,
            neutral_genes    = neutral_genes,
            effects          = [asdict(ce) for ce in causal_effects],
            regime_fitness   = [asdict(rm) for rm in regime_maps[:15]],
            world_model      = wm.get_transition_dict(),
            counterfactuals  = counterfactuals[:10],
            insights         = insights,
            timestamp        = _now_iso(),
        )

        self._save_report(report)
        self.pool.save()
        return report

    def _save_report(self, report: CausalReport) -> None:
        """Save causal report to Redis + file."""
        payload = asdict(report)
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_CAUSAL_REPORT_KEY, json.dumps(payload))
        except Exception:
            pass
        try:
            os.makedirs("models", exist_ok=True)
            with open("models/causal_report.json", "w") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────
# 7. API helpers
# ──────────────────────────────────────────────────────────────────

def get_causal_report() -> dict:
    """Load and return latest causal report from Redis or file."""
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw = r.get(_REDIS_CAUSAL_REPORT_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open("models/causal_report.json") as f:
            return json.load(f)
    except Exception:
        return {"status": "no_report"}


def run_causal_analysis(
    population : List[StrategyGenome] = None,
    seed       : int = 42,
    verbose    : bool = True,
) -> CausalReport:
    """
    Top-level entry point for causal analysis.

    Parameters
    ----------
    population : Optional evaluated population to add to pool.
    seed       : Random seed.
    verbose    : Print progress.

    Returns
    -------
    CausalReport with causal findings.

    Ví dụ:
    ------
    >>> from causal_engine import run_causal_analysis
    >>> report = run_causal_analysis(population=last_pop)
    >>> print(report.causal_genes)    # genes that CAUSE winning
    >>> print(report.spurious_genes)  # genes that only correlate
    """
    engine = CausalEngine(seed=seed)
    return engine.run(population=population, verbose=verbose)


# ──────────────────────────────────────────────────────────────────
# 8. Integration: feed causal findings back into breeding
# ──────────────────────────────────────────────────────────────────

def causal_bias_for_recombinator(
    causal_report: "CausalReport | dict",
) -> Dict[str, float]:
    """
    Convert causal report into importance weights for GuidedRecombinator.

    Causal genes      → weight × 1.5 (treat as high-importance)
    Spurious genes    → weight × 0.3 (treat as noise)
    Neutral genes     → weight × 1.0 (unchanged)

    Returns {gene_name: adjusted_importance} suitable for
    GuidedRecombinator(importance=...).
    """
    if isinstance(causal_report, CausalReport):
        causal_genes  = causal_report.causal_genes
        spurious_genes = causal_report.spurious_genes
        effects        = causal_report.effects
    else:
        causal_genes   = causal_report.get("causal_genes", [])
        spurious_genes = causal_report.get("spurious_genes", [])
        effects        = causal_report.get("effects", [])

    # Base importance from |ACE|
    base: Dict[str, float] = {}
    for eff in effects:
        if isinstance(eff, dict):
            gene = eff["gene"]
            base[gene] = abs(eff.get("causal_ace", 0.0))
        else:
            base[eff.gene] = abs(eff.causal_ace)

    # Normalize
    max_imp = max(base.values()) if base else 1.0
    if max_imp < 1e-9:
        max_imp = 1.0

    adjusted: Dict[str, float] = {}
    for gene in GENE_BOUNDS:
        base_w = base.get(gene, 0.0) / max_imp
        if gene in causal_genes:
            adjusted[gene] = round(base_w * 1.5, 4)
        elif gene in spurious_genes:
            adjusted[gene] = round(base_w * 0.3, 4)
        else:
            adjusted[gene] = round(base_w, 4)

    return adjusted


# ──────────────────────────────────────────────────────────────────
# 9. Helpers
# ──────────────────────────────────────────────────────────────────

def _dict_to_genome(d: dict) -> StrategyGenome:
    """Convert pool entry dict → StrategyGenome."""
    g = StrategyGenome(genome_id=d.get("genome_id", str(uuid.uuid4())[:8]))
    for gene in GENE_BOUNDS:
        if gene in d:
            setattr(g, gene, float(d[gene]))
    return _clip_genome(g)


def _generate_causal_insights(
    causal_effects  : List[CausalEffect],
    regime_maps     : List[RegimeFitnessMap],
    counterfactuals : List[dict],
) -> List[str]:
    """Generate human-readable causal insights."""
    insights = []

    if causal_effects:
        top_causal = [ce for ce in causal_effects if ce.is_causal and not ce.is_spurious]
        top_spur   = [ce for ce in causal_effects if ce.is_spurious]

        if top_causal:
            best = top_causal[0]
            direction = "tăng" if best.causal_ace > 0 else "giảm"
            insights.append(
                f"Gene nhân quả mạnh nhất: {best.gene} "
                f"(ACE={best.causal_ace:+.4f}, {direction} giá trị → thắng nhiều hơn)"
            )
        else:
            insights.append("Chưa phát hiện gene nhân quả rõ ràng — cần thêm dữ liệu")

        if top_spur:
            insights.append(
                f"Gene correlation giả (spurious): {', '.join(ce.gene for ce in top_spur[:3])} "
                f"— đừng bảo toàn chúng khi breed"
            )

        # Stable vs unstable causal genes
        stable_causal = [ce.gene for ce in top_causal if ce.regime_stable > 0.6]
        unstable_causal = [ce.gene for ce in top_causal if ce.regime_stable < 0.4]
        if stable_causal:
            insights.append(
                f"Gene nhân quả bền vững (mọi regime): {', '.join(stable_causal[:3])}"
            )
        if unstable_causal:
            insights.append(
                f"Gene nhân quả chỉ trong 1 regime (specialist): {', '.join(unstable_causal[:3])}"
            )

    if regime_maps:
        best_robust = regime_maps[0]
        insights.append(
            f"Genome tổng quát nhất (robust): {best_robust.genome_id} "
            f"sống sót {best_robust.survivability:.0%} regimes"
        )

        most_sensitive = max(regime_maps, key=lambda m: m.std_fitness)
        if most_sensitive.genome_id != best_robust.genome_id:
            insights.append(
                f"Genome chuyên biệt nhất: {most_sensitive.genome_id} "
                f"(giỏi 1 regime nhưng tệ khi regime đổi)"
            )

    if counterfactuals:
        survived = [cf for cf in counterfactuals if cf.get("survived")]
        insights.append(
            f"Counterfactual (trend→choppy): {len(survived)}/{len(counterfactuals)} "
            f"genomes vẫn profitable khi thị trường chuyển regime"
        )
        if survived:
            best_cf = survived[0]
            insights.append(
                f"Best genome khi chuyển sang choppy: {best_cf['genome_id']} "
                f"(fitness={best_cf['fitness_after']:.4f})"
            )

    return insights


def _now_iso() -> str:
    """Return current UTC time as ISO string."""
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

    parser = argparse.ArgumentParser(description="World Model + Causal Strategy Engine")
    parser.add_argument("--mode", choices=["report", "analyze", "demo"],
                        default="report")
    parser.add_argument("--fast", action="store_true", default=True,
                        help="Fast mode: skip full intervention simulations")
    parser.add_argument("--full", action="store_true", default=False,
                        help="Full mode: run intervention simulations")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    if args.mode == "report":
        print("\n═══ CAUSAL REPORT ═══")
        rpt = get_causal_report()
        if "status" in rpt:
            print("No report found. Run 'demo' or 'analyze' first.")
        else:
            print(f"Pool size    : {rpt.get('pool_size', 0)}")
            print(f"Causal genes : {rpt.get('causal_genes', [])}")
            print(f"Spurious     : {rpt.get('spurious_genes', [])}")
            print("\nInsights:")
            for i in rpt.get("insights", []):
                print(f"  • {i}")

    elif args.mode == "demo":
        print("\n═══ CAUSAL DEMO (with synthetic pool) ═══")
        if args.full:
            config.CAUSAL_FAST_MODE = False
        ops = GeneticOperators(seed=42)
        rng = np.random.default_rng(42)
        pop = []
        for _ in range(60):
            g = ops.generate_random()
            # Bias: high wave_weight AND low min_signal_score → higher fitness
            wave_boost = (g.wave_weight - 1.0) * 0.04
            score_pen  = max(0, (g.min_signal_score - 60) / 100) * 0.03
            g.fitness       = max(0.0, float(rng.uniform(0, 0.2) + wave_boost - score_pen))
            g.win_rate_pct  = min(75, max(40, float(rng.uniform(48, 65) + wave_boost * 50)))
            g.profit_factor = float(rng.uniform(0.9, 2.0) + wave_boost * 3)
            g.n_trades      = int(rng.integers(20, 150))
            g.evaluated     = True
            pop.append(g)

        report = run_causal_analysis(population=pop, verbose=True)
        print(f"\n{'═'*50}")
        print(f"Causal genes : {report.causal_genes}")
        print(f"Spurious     : {report.spurious_genes}")
        print(f"Neutral      : {report.neutral_genes}")
        print(f"Regime maps  : {len(report.regime_fitness)} genomes evaluated")
        print(f"\nInsights:")
        for i in report.insights:
            print(f"  • {i}")

    elif args.mode == "analyze":
        print("\n═══ CAUSAL ANALYZE (from saved pool) ═══")
        if args.full:
            config.CAUSAL_FAST_MODE = False
        report = run_causal_analysis(verbose=True)
        print(f"\nCausal genes : {report.causal_genes}")
        print(f"Spurious     : {report.spurious_genes}")
