"""
strategy_genetics.py
====================
META-LEARNING + STRATEGY GENOME ENGINE

Bước tiếp theo sau evolutionary system: strategy genetics system.

Hệ không chỉ chọn winner và tiến hóa — mà còn HỌC:
  • Vì sao winner thắng           → GeneImportanceAnalyzer
  • Gene nào lặp lại qua nhiều winner → WinnerPatternExtractor
  • Gene nào nên giữ, gene nào loại  → GeneImportanceAnalyzer.rank()
  • Cách tái tổ hợp khôn ngoan hơn   → GuidedRecombinator

Quy trình đầy đủ (MetaLearner.run_meta_cycle):
  1. Thu thập lịch sử genome từ nhiều evolution runs (GenePool)
  2. Phân tích tương quan gene → fitness (GeneImportanceAnalyzer)
  3. Trích xuất patterns: vùng giá trị gene từ top winners (WinnerPatternExtractor)
  4. Cluster winners thành archetypes — chiến lược nền (GeneArchetypes)
  5. Breed thế hệ mới bằng guided recombination (GuidedRecombinator):
     - Ưu tiên giữ gene quan trọng từ winner
     - Phân bổ mutation theo importance (ít quan trọng → mutate nhiều hơn)
     - Blend từ nhiều archetype để tăng đa dạng có hướng
  6. Lưu seeds cho evolution cycle tiếp theo
  7. Publish báo cáo meta → Redis + file

Thành phần:
  GenePool              — Kho lưu trữ genome lịch sử (multi-run)
  GeneImportanceAnalyzer — Tương quan gene–fitness, xếp hạng độ quan trọng
  WinnerPatternExtractor — Pattern vùng giá trị gene của winner
  GeneArchetypes        — Cluster winners → archetype templates
  GuidedRecombinator    — Tái tổ hợp có hướng dựa theo importance
  MetaLearner           — Orchestrator toàn bộ meta-learning cycle
  get_meta_report()     — Top-level: report cho API
  run_meta_cycle()      — Top-level: chạy một meta-cycle đầy đủ

Cách dùng:
  >>> from strategy_genetics import run_meta_cycle
  >>> seeds = run_meta_cycle(n_seeds=10)
  >>> # seeds là list StrategyGenome được breed từ meta knowledge
  >>> # dùng làm init population cho EvolutionEngine tiếp theo
"""

from __future__ import annotations

import copy
import json
import math
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config
from evolution_engine import (
    GENE_BOUNDS,
    StrategyGenome,
    GeneticOperators,
    _clip_genome,
)


# ──────────────────────────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────────────────────────

_REDIS_GENE_POOL_KEY    = "Deriv_Gene_Pool"
_REDIS_META_REPORT_KEY  = "Deriv_Meta_Report"
_REDIS_ARCHETYPES_KEY   = "Deriv_Gene_Archetypes"
_REDIS_META_SEEDS_KEY   = "Deriv_Meta_Seeds"


# ──────────────────────────────────────────────────────────────────
# 1. GenePool — persistent storage of evaluated genomes
# ──────────────────────────────────────────────────────────────────

class GenePool:
    """
    Kho lưu trữ genome lịch sử qua nhiều evolution run.

    Mỗi genome được lưu một lần (dedup by genome_id).
    Chỉ giữ genome đã được đánh giá (evaluated=True).
    Lưu cả fitness, win_rate, profit_factor → dùng làm training data
    cho meta-learning.

    Tối đa META_POOL_MAX_SIZE entries (FIFO oldest removed).
    """

    def __init__(self) -> None:
        self._pool: List[Dict[str, Any]] = []
        self._ids_seen: set = set()
        self._max_size = getattr(config, "META_POOL_MAX_SIZE", 500)

    def add(self, genome: StrategyGenome) -> None:
        """Add a genome to the pool (skip duplicates and unevaluated)."""
        if not genome.evaluated:
            return
        if genome.genome_id in self._ids_seen:
            return
        self._ids_seen.add(genome.genome_id)
        self._pool.append({
            **genome.genes(),
            "fitness"         : genome.fitness,
            "win_rate_pct"    : genome.win_rate_pct,
            "profit_factor"   : genome.profit_factor,
            "n_trades"        : genome.n_trades,
            "genome_id"       : genome.genome_id,
            "generation"      : genome.generation,
        })
        # FIFO trim
        if len(self._pool) > self._max_size:
            oldest_id = self._pool[0].get("genome_id", "")
            self._ids_seen.discard(oldest_id)
            self._pool.pop(0)

    def add_population(self, population: List[StrategyGenome]) -> None:
        for g in population:
            self.add(g)

    def size(self) -> int:
        return len(self._pool)

    def top_winners(self, k: int = None) -> List[Dict[str, Any]]:
        """Return top-K entries sorted by fitness."""
        k = k or getattr(config, "META_TOP_K_WINNERS", 20)
        sorted_pool = sorted(self._pool, key=lambda x: x["fitness"], reverse=True)
        return sorted_pool[:k]

    def as_arrays(self) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        """
        Return (X, y, gene_names) where:
          X[i]  = gene values for genome i
          y[i]  = fitness of genome i
        """
        gene_names = list(GENE_BOUNDS.keys())
        rows, labels = [], []
        for entry in self._pool:
            row = [float(entry.get(g, 0.0)) for g in gene_names]
            rows.append(row)
            labels.append(float(entry.get("fitness", 0.0)))
        if not rows:
            return np.empty((0, len(gene_names))), np.empty(0), gene_names
        return np.array(rows), np.array(labels), gene_names

    # ── Redis persistence ─────────────────────────────────────────

    def save(self) -> None:
        """Persist pool to Redis."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_GENE_POOL_KEY, json.dumps(self._pool[-self._max_size:]))
        except Exception:
            pass
        # File backup
        try:
            os.makedirs("models", exist_ok=True)
            with open("models/gene_pool.json", "w") as f:
                json.dump(self._pool[-self._max_size:], f)
        except Exception:
            pass

    def load(self) -> None:
        """Load pool from Redis (or file fallback)."""
        data = None
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.get(_REDIS_GENE_POOL_KEY)
            if raw:
                data = json.loads(raw)
        except Exception:
            pass
        if data is None:
            try:
                with open("models/gene_pool.json") as f:
                    data = json.load(f)
            except Exception:
                data = []
        self._pool = data or []
        self._ids_seen = {e.get("genome_id", "") for e in self._pool}


# ──────────────────────────────────────────────────────────────────
# 2. GeneImportanceAnalyzer — why do winners win?
# ──────────────────────────────────────────────────────────────────

@dataclass
class GeneImportance:
    gene         : str
    spearman_rho : float    # Spearman rank correlation with fitness
    mutual_info  : float    # Normalized mutual information with fitness bins
    importance   : float    # Composite score (higher = more important)
    direction    : str      # 'positive' / 'negative' / 'neutral'
    winner_mean  : float    # Mean value among top winners
    winner_std   : float    # Std deviation among top winners
    pool_mean    : float    # Mean value across full pool
    pool_std     : float    # Std deviation across full pool


class GeneImportanceAnalyzer:
    """
    Phân tích tương quan gene ↔ fitness để hiểu vì sao winner thắng.

    Phương pháp:
      1. Spearman rank correlation (robust, non-parametric)
      2. Mutual information (non-linear relationship detection)
      3. Composite importance = |spearman_rho| × 0.6 + mutual_info × 0.4

    Kết quả:
      - Xếp hạng gene theo độ quan trọng
      - Xác định hướng tác động (positive / negative)
      - Phân biệt winner vs pool distribution
    """

    def __init__(self, pool: GenePool) -> None:
        self.pool = pool

    def analyze(self) -> List[GeneImportance]:
        """
        Analyze gene importance from pool data.

        Returns list of GeneImportance sorted by importance descending.
        """
        X, y, gene_names = self.pool.as_arrays()
        if len(X) < 10:
            return []

        winners = self.pool.top_winners(k=max(10, len(self.pool._pool) // 5))

        results: List[GeneImportance] = []

        for gi, gene in enumerate(gene_names):
            x_col = X[:, gi]
            lo, hi = GENE_BOUNDS[gene]
            span   = hi - lo if hi != lo else 1.0

            # Spearman rank correlation
            spearman_rho = _spearman_rho(x_col, y)

            # Mutual information (binned)
            mi = _mutual_info_binned(x_col, y, bins=8)

            # Composite
            importance = abs(spearman_rho) * 0.6 + mi * 0.4

            # Direction
            if spearman_rho > 0.05:
                direction = "positive"
            elif spearman_rho < -0.05:
                direction = "negative"
            else:
                direction = "neutral"

            # Winner distribution
            winner_vals = [float(w.get(gene, 0.0)) for w in winners]
            winner_mean = float(np.mean(winner_vals)) if winner_vals else 0.0
            winner_std  = float(np.std(winner_vals))  if winner_vals else 0.0

            # Pool distribution
            pool_mean = float(np.mean(x_col)) if len(x_col) > 0 else 0.0
            pool_std  = float(np.std(x_col))  if len(x_col) > 0 else 0.0

            results.append(GeneImportance(
                gene         = gene,
                spearman_rho = round(float(spearman_rho), 4),
                mutual_info  = round(float(mi), 4),
                importance   = round(float(importance), 4),
                direction    = direction,
                winner_mean  = round(winner_mean, 4),
                winner_std   = round(winner_std, 4),
                pool_mean    = round(pool_mean, 4),
                pool_std     = round(pool_std, 4),
            ))

        results.sort(key=lambda x: x.importance, reverse=True)
        return results

    def importance_dict(self) -> Dict[str, float]:
        """Return {gene_name: importance_score} for all genes."""
        return {r.gene: r.importance for r in self.analyze()}

    def ranked_genes(self) -> List[str]:
        """Return gene names sorted from most to least important."""
        return [r.gene for r in self.analyze()]


# ──────────────────────────────────────────────────────────────────
# 3. WinnerPatternExtractor — gene value patterns of winners
# ──────────────────────────────────────────────────────────────────

@dataclass
class GenePattern:
    gene          : str
    low_pct       : float    # 10th percentile of winner values
    median        : float    # 50th percentile
    high_pct      : float    # 90th percentile
    tight_range   : bool     # True if winners cluster tightly (std/range < 0.2)
    is_universal  : bool     # True if pattern holds across multiple archetypes
    n_winners     : int      # Number of winners this is based on


class WinnerPatternExtractor:
    """
    Trích xuất pattern vùng giá trị gene của top winners.

    Mục đích:
      - Xác định "vùng vàng" của mỗi gene: p10–p90 của winners
      - Phát hiện gene nào có distribution tập trung (tight range)
        → gene đó có giá trị "critical" cần bảo toàn khi breed
      - Phát hiện gene nào có distribution phân tán (không quan trọng về giá trị)
        → gene đó có thể mutate tự do

    Kết quả dùng trong GuidedRecombinator để:
      - Tạo offspring có gene quan trọng nằm trong winner range
      - Tập trung mutation trên gene ít quan trọng hơn
    """

    def __init__(self, pool: GenePool) -> None:
        self.pool = pool

    def extract(self, top_k: int = None) -> Dict[str, GenePattern]:
        """
        Extract gene patterns from top-K winners.

        Returns dict {gene_name → GenePattern}.
        """
        top_k = top_k or getattr(config, "META_TOP_K_WINNERS", 20)
        winners = self.pool.top_winners(k=top_k)
        n = len(winners)

        if n < 3:
            return {}

        gene_names = list(GENE_BOUNDS.keys())
        patterns: Dict[str, GenePattern] = {}

        for gene in gene_names:
            vals = np.array([float(w.get(gene, 0.0)) for w in winners])
            lo, hi = GENE_BOUNDS[gene]
            span   = (hi - lo) if hi != lo else 1.0

            p10    = float(np.percentile(vals, 10))
            median = float(np.percentile(vals, 50))
            p90    = float(np.percentile(vals, 90))
            std    = float(np.std(vals))

            tight_range = (std / span) < 0.15   # Tight if std < 15% of range

            patterns[gene] = GenePattern(
                gene         = gene,
                low_pct      = round(p10, 4),
                median       = round(median, 4),
                high_pct     = round(p90, 4),
                tight_range  = tight_range,
                is_universal = False,   # Set in archetype analysis
                n_winners    = n,
            )

        return patterns


# ──────────────────────────────────────────────────────────────────
# 4. GeneArchetypes — cluster winners into strategy personalities
# ──────────────────────────────────────────────────────────────────

@dataclass
class StrategyArchetype:
    archetype_id  : str
    label         : str         # Human-readable label (aggressive / conservative / wave / etc.)
    centroid      : Dict[str, float]   # Mean gene values for this archetype
    member_ids    : List[str]   # genome_ids in this cluster
    mean_fitness  : float
    mean_win_rate : float
    mean_pf       : float
    n_members     : int
    description   : str = ""    # Auto-generated text description


class GeneArchetypes:
    """
    Cluster top winners into K distinct strategy archetypes.

    Thuật toán: k-means (manual numpy implementation — no sklearn dependency).
    K = META_N_ARCHETYPES (default 4).

    Mỗi archetype có:
      - Centroid: mean gene values
      - Label: auto-assigned based on dominant genes
      - Statistics: mean fitness, win_rate, pf across members

    Archetypes dùng để:
      - Đa dạng hóa population có chủ đích (lấy 1 member từ mỗi archetype)
      - Hybrid breeding: child gets RSI from archetype A + weights from archetype B
      - Hiểu cấu trúc không gian chiến lược
    """

    def __init__(self, pool: GenePool) -> None:
        self.pool = pool
        self._archetypes: List[StrategyArchetype] = []

    def build(self, n_clusters: int = None) -> List[StrategyArchetype]:
        """
        Build archetypes from top winners using k-means clustering.

        Returns list of StrategyArchetype.
        """
        k = n_clusters or getattr(config, "META_N_ARCHETYPES", 4)
        top_k = getattr(config, "META_TOP_K_WINNERS", 40)
        winners = self.pool.top_winners(k=top_k)

        if len(winners) < k * 2:
            # Not enough data — create one archetype from all winners
            return self._single_archetype(winners)

        gene_names = list(GENE_BOUNDS.keys())
        X = np.array([[float(w.get(g, 0.0)) for g in gene_names] for w in winners])

        # Normalize to [0, 1] for clustering
        lo_arr = np.array([GENE_BOUNDS[g][0] for g in gene_names])
        hi_arr = np.array([GENE_BOUNDS[g][1] for g in gene_names])
        span   = hi_arr - lo_arr
        span[span == 0] = 1.0
        X_norm = (X - lo_arr) / span

        # Run k-means
        centroids, labels = _kmeans(X_norm, k=k, max_iter=50)

        self._archetypes = []
        for ci in range(k):
            mask    = labels == ci
            members = [winners[i] for i in range(len(winners)) if mask[i]]
            if not members:
                continue

            # De-normalize centroid
            centroid_raw = centroids[ci] * span + lo_arr
            centroid_dict = {g: round(float(centroid_raw[gi]), 4)
                             for gi, g in enumerate(gene_names)}

            fitnesses  = [m["fitness"]      for m in members]
            win_rates  = [m["win_rate_pct"] for m in members]
            pfs        = [m["profit_factor"] for m in members]

            archetype = StrategyArchetype(
                archetype_id  = f"arch_{ci}",
                label         = _label_archetype(centroid_dict, ci),
                centroid      = centroid_dict,
                member_ids    = [m.get("genome_id", "") for m in members],
                mean_fitness  = round(float(np.mean(fitnesses)), 4),
                mean_win_rate = round(float(np.mean(win_rates)), 2),
                mean_pf       = round(float(np.mean(pfs)), 3),
                n_members     = len(members),
            )
            archetype.description = _describe_archetype(archetype)
            self._archetypes.append(archetype)

        self._archetypes.sort(key=lambda a: a.mean_fitness, reverse=True)
        return self._archetypes

    def _single_archetype(self, winners: List[dict]) -> List[StrategyArchetype]:
        """Fallback when not enough data for k-means."""
        if not winners:
            return []
        gene_names = list(GENE_BOUNDS.keys())
        centroid = {g: round(float(np.mean([w.get(g, 0.0) for w in winners])), 4)
                    for g in gene_names}
        a = StrategyArchetype(
            archetype_id  = "arch_0",
            label         = "General",
            centroid      = centroid,
            member_ids    = [w.get("genome_id", "") for w in winners],
            mean_fitness  = round(float(np.mean([w["fitness"] for w in winners])), 4),
            mean_win_rate = round(float(np.mean([w["win_rate_pct"] for w in winners])), 2),
            mean_pf       = round(float(np.mean([w["profit_factor"] for w in winners])), 3),
            n_members     = len(winners),
        )
        a.description = _describe_archetype(a)
        self._archetypes = [a]
        return self._archetypes

    def get_archetype_genomes(self) -> List[StrategyGenome]:
        """Convert archetype centroids to StrategyGenome objects."""
        genomes = []
        for arch in self._archetypes:
            g = StrategyGenome(genome_id=f"seed_{arch.archetype_id}")
            for gene, val in arch.centroid.items():
                setattr(g, gene, val)
            g = _clip_genome(g)
            genomes.append(g)
        return genomes

    def to_dicts(self) -> List[dict]:
        return [asdict(a) for a in self._archetypes]


# ──────────────────────────────────────────────────────────────────
# 5. GuidedRecombinator — importance-aware crossover
# ──────────────────────────────────────────────────────────────────

class GuidedRecombinator:
    """
    Tái tổ hợp gene có hướng dựa trên:
      1. Gene importance   → gene quan trọng được bảo toàn, ít quan trọng được thay đổi
      2. Winner patterns   → offspring có gene nằm trong vùng winner
      3. Archetype blending → hybrid offspring từ nhiều archetype

    Khác với GeneticOperators.crossover (blind 50/50 coin flip):
      - Với gene importance > threshold → lấy từ parent có fitness cao hơn (85% xác suất)
      - Với gene importance < threshold → coin flip (50/50) để đa dạng hóa
      - Mutation sigma tỉ lệ nghịch với importance (gene quan trọng ít bị mutate)
      - Offspring được kéo về winner median range bằng "attraction force"
    """

    def __init__(
        self,
        importance   : Dict[str, float],
        patterns     : Dict[str, GenePattern],
        archetypes   : List[StrategyArchetype],
        seed         : int = 42,
    ) -> None:
        self.importance = importance    # {gene: importance_score}
        self.patterns   = patterns      # {gene: GenePattern}
        self.archetypes = archetypes
        self._rng       = np.random.default_rng(seed)

        # Importance percentile cutoffs
        imp_vals = list(importance.values()) if importance else [0.0]
        self._high_imp  = float(np.percentile(imp_vals, 70))  # top 30% = high
        self._low_imp   = float(np.percentile(imp_vals, 30))  # bottom 30% = low

    def guided_crossover(
        self,
        parent_a: StrategyGenome,
        parent_b: StrategyGenome,
    ) -> StrategyGenome:
        """
        Importance-guided crossover.

        High importance gene: inherit from fitter parent (85% prob)
        Low importance gene : coin flip (50/50)
        Then apply winner-range attraction.
        """
        better, worse = (parent_a, parent_b) if parent_a.fitness >= parent_b.fitness \
                        else (parent_b, parent_a)

        child = copy.deepcopy(better)
        child.genome_id  = str(uuid.uuid4())[:8]
        child.generation = max(better.generation, worse.generation) + 1
        child.parent_ids = [parent_a.genome_id, parent_b.genome_id]
        child.evaluated  = False

        for gene in GENE_BOUNDS.keys():
            imp  = self.importance.get(gene, 0.5)
            va   = float(getattr(better, gene))
            vb   = float(getattr(worse, gene))

            if imp >= self._high_imp:
                # High importance: strongly prefer better parent
                prob_take_better = 0.85
            elif imp <= self._low_imp:
                # Low importance: random — introduces diversity
                prob_take_better = 0.50
            else:
                # Medium importance: moderate preference
                prob_take_better = 0.70

            val = va if self._rng.random() < prob_take_better else vb
            setattr(child, gene, val)

        # Apply winner-range attraction
        child = self._attract_to_winner_range(child)
        return _clip_genome(child)

    def importance_aware_mutate(
        self,
        genome    : StrategyGenome,
        base_rate : float = None,
        base_sigma: float = None,
    ) -> StrategyGenome:
        """
        Mutation where:
          - High importance genes: mutate rarely (rate × 0.3) and gently (sigma × 0.4)
          - Low importance genes: mutate more (rate × 1.5) and broadly (sigma × 1.5)
          - Medium importance: default rate and sigma
        """
        base_rate  = base_rate  or getattr(config, "EVOL_MUTATION_RATE",  0.15)
        base_sigma = base_sigma or getattr(config, "EVOL_MUTATION_SIGMA", 0.12)

        mutant = copy.deepcopy(genome)
        mutant.genome_id  = str(uuid.uuid4())[:8]
        mutant.generation = genome.generation + 1
        mutant.parent_ids = [genome.genome_id]
        mutant.evaluated  = False

        for gene, (lo, hi) in GENE_BOUNDS.items():
            imp   = self.importance.get(gene, 0.5)
            span  = hi - lo

            # Scale mutation by importance
            if imp >= self._high_imp:
                rate  = base_rate  * 0.30
                sigma = base_sigma * 0.40
            elif imp <= self._low_imp:
                rate  = base_rate  * 1.50
                sigma = base_sigma * 1.50
            else:
                rate  = base_rate
                sigma = base_sigma

            if self._rng.random() < rate:
                current = float(getattr(mutant, gene))
                noise   = float(self._rng.normal(0, sigma * span))
                setattr(mutant, gene, current + noise)

        return _clip_genome(mutant)

    def archetype_hybrid(
        self,
        arch_a: StrategyArchetype,
        arch_b: StrategyArchetype,
    ) -> StrategyGenome:
        """
        Hybrid offspring blending two archetype centroids.

        RSI group from arch_a, Signal weights from arch_b,
        Filters and trade params guided by importance.
        """
        gene_groups = {
            "rsi"     : ["rsi_oversold", "rsi_overbought", "rsi_near_os", "rsi_near_ob"],
            "weights" : ["rsi_weight", "momentum_weight", "macd_weight", "bb_weight", "wave_weight"],
            "filters" : ["min_signal_score", "min_wave_score", "bb_tight_threshold"],
            "trade"   : ["lookahead_candles"],
        }

        # Assign groups randomly between arch_a and arch_b
        group_keys = list(gene_groups.keys())
        self._rng.shuffle(group_keys)
        group_a_keys = set(group_keys[:2])

        child = StrategyGenome(
            genome_id  = str(uuid.uuid4())[:8],
            generation = 0,
        )
        for group, genes in gene_groups.items():
            source = arch_a if group in group_a_keys else arch_b
            for gene in genes:
                setattr(child, gene, float(source.centroid.get(gene, 0.0)))

        child = _clip_genome(child)
        return child

    def _attract_to_winner_range(
        self,
        genome: StrategyGenome,
        attraction: float = 0.25,
    ) -> StrategyGenome:
        """
        Gently pull gene values toward winner median range.

        attraction: 0 = no pull, 1 = move fully to median
        Only apply if gene is outside winner p10–p90 range.
        """
        if not self.patterns:
            return genome
        for gene, pattern in self.patterns.items():
            val = float(getattr(genome, gene))
            if val < pattern.low_pct:
                target = pattern.low_pct
                setattr(genome, gene, val + attraction * (target - val))
            elif val > pattern.high_pct:
                target = pattern.high_pct
                setattr(genome, gene, val + attraction * (target - val))
        return genome

    def breed_meta_population(
        self,
        n_seeds: int = 10,
    ) -> List[StrategyGenome]:
        """
        Generate n_seeds meta-guided genomes using all recombination methods.

        Distribution:
          30% → guided_crossover from archetype centroids
          30% → archetype_hybrid from pairs of archetypes
          25% → importance_aware_mutate of best archetypes
          15% → winner-range-centered random
        """
        seeds: List[StrategyGenome] = []
        arch_genomes = (
            [StrategyGenome(genome_id=f"seed_{a.archetype_id}",
                            **{g: float(a.centroid.get(g, 0.0)) for g in GENE_BOUNDS})
             for a in self.archetypes]
            if self.archetypes else [StrategyGenome.from_config()]
        )
        # Clip all archetype genomes
        arch_genomes = [_clip_genome(g) for g in arch_genomes]

        n_cross   = int(n_seeds * 0.30)
        n_hybrid  = int(n_seeds * 0.30)
        n_mutant  = int(n_seeds * 0.25)
        n_random  = n_seeds - n_cross - n_hybrid - n_mutant

        # Guided crossover between archetype pairs
        for i in range(n_cross):
            if len(arch_genomes) >= 2:
                idx_a, idx_b = self._rng.choice(len(arch_genomes), 2, replace=False)
                child = self.guided_crossover(arch_genomes[idx_a], arch_genomes[idx_b])
            else:
                child = copy.deepcopy(arch_genomes[0])
                child.genome_id = str(uuid.uuid4())[:8]
            seeds.append(child)

        # Archetype hybrid
        for i in range(n_hybrid):
            if len(self.archetypes) >= 2:
                idx_a, idx_b = self._rng.choice(len(self.archetypes), 2, replace=False)
                child = self.archetype_hybrid(self.archetypes[idx_a], self.archetypes[idx_b])
            elif self.archetypes:
                # Fallback: mutate single archetype
                base  = arch_genomes[0]
                child = self.importance_aware_mutate(base)
            else:
                child = StrategyGenome(genome_id=str(uuid.uuid4())[:8])
                child = _clip_genome(child)
            seeds.append(child)

        # Importance-aware mutations
        for i in range(n_mutant):
            base  = arch_genomes[i % len(arch_genomes)]
            child = self.importance_aware_mutate(base)
            seeds.append(child)

        # Winner-range-centered random
        ops = GeneticOperators(seed=int(self._rng.integers(0, 10000)))
        for i in range(n_random):
            g = ops.generate_random()
            # Pull to winner range
            g = self._attract_to_winner_range(g, attraction=0.5)
            g = _clip_genome(g)
            seeds.append(g)

        return seeds[:n_seeds]


# ──────────────────────────────────────────────────────────────────
# 6. MetaLearner — orchestrates full meta-learning cycle
# ──────────────────────────────────────────────────────────────────

@dataclass
class MetaReport:
    """Full meta-learning report stored to Redis + file."""
    pool_size        : int
    n_winners_used   : int
    n_archetypes     : int
    top_genes        : List[str]                      # ranked by importance
    gene_importances : Dict[str, float]
    archetypes       : List[dict]
    winner_patterns  : Dict[str, dict]
    insights         : List[str]                      # human-readable insights
    n_seeds_generated: int
    timestamp        : str = ""


class MetaLearner:
    """
    Orchestrator của toàn bộ meta-learning pipeline.

    Chu trình:
      1. Load gene pool từ Redis (persistent across runs)
      2. Phân tích gene importance
      3. Trích xuất winner patterns
      4. Cluster → archetypes
      5. Breed meta-guided seeds
      6. Save report + seeds → Redis
      7. Return seeds cho evolution engine tiếp theo
    """

    def __init__(self, seed: int = 42) -> None:
        self.pool          = GenePool()
        self.pool.load()
        self.seed          = seed

    def ingest_population(self, population: List[StrategyGenome]) -> int:
        """Add evaluated population to the gene pool. Returns new pool size."""
        before = self.pool.size()
        self.pool.add_population(population)
        return self.pool.size() - before

    def analyze(self) -> Tuple[
        List[GeneImportance],
        Dict[str, GenePattern],
        List[StrategyArchetype],
    ]:
        """
        Run full analysis pipeline.

        Returns (importances, patterns, archetypes).
        """
        importance_analyzer = GeneImportanceAnalyzer(self.pool)
        pattern_extractor   = WinnerPatternExtractor(self.pool)
        archetype_builder   = GeneArchetypes(self.pool)

        importances = importance_analyzer.analyze()
        patterns    = pattern_extractor.extract()
        archetypes  = archetype_builder.build()

        # Mark is_universal for tight-range patterns that appear in all archetypes
        if archetypes and patterns:
            for gene, pat in patterns.items():
                arch_vals = [a.centroid.get(gene, 0.0) for a in archetypes]
                lo_g, hi_g = GENE_BOUNDS[gene]
                span = hi_g - lo_g if hi_g != lo_g else 1.0
                arch_std = float(np.std(arch_vals)) if arch_vals else span
                pat.is_universal = pat.tight_range and (arch_std / span) < 0.15

        return importances, patterns, archetypes

    def breed_seeds(
        self,
        n_seeds   : int = None,
        importances: List[GeneImportance] = None,
        patterns  : Dict[str, GenePattern] = None,
        archetypes: List[StrategyArchetype] = None,
    ) -> List[StrategyGenome]:
        """
        Breed n_seeds meta-guided genomes.
        Runs analyze() first if inputs not provided.
        """
        n_seeds = n_seeds or getattr(config, "META_N_SEEDS", 15)

        if importances is None or patterns is None or archetypes is None:
            importances, patterns, archetypes = self.analyze()

        imp_dict = {r.gene: r.importance for r in importances}

        recombinator = GuidedRecombinator(
            importance = imp_dict,
            patterns   = patterns,
            archetypes = archetypes,
            seed       = self.seed,
        )

        return recombinator.breed_meta_population(n_seeds=n_seeds)

    def run(self, n_seeds: int = None) -> Tuple[List[StrategyGenome], MetaReport]:
        """
        Run complete meta-learning cycle.

        Returns (seeds, report) where:
          seeds  = meta-guided StrategyGenomes for next evolution run
          report = MetaReport with insights
        """
        n_seeds = n_seeds or getattr(config, "META_N_SEEDS", 15)

        if self.pool.size() < 10:
            # Not enough data yet — return random seeds
            ops = GeneticOperators(seed=self.seed)
            seeds = [ops.generate_random() for _ in range(n_seeds)]
            report = MetaReport(
                pool_size=0, n_winners_used=0, n_archetypes=0,
                top_genes=[], gene_importances={}, archetypes=[],
                winner_patterns={}, insights=["Pool quá nhỏ — cần thêm evolution runs"],
                n_seeds_generated=len(seeds),
                timestamp=_now_iso(),
            )
            return seeds, report

        print(f"\n  🔬 [MetaLearner] Analyzing pool ({self.pool.size()} genomes)...")

        importances, patterns, archetypes = self.analyze()
        seeds = self.breed_seeds(n_seeds, importances, patterns, archetypes)

        # Build insights
        insights = _generate_insights(importances, patterns, archetypes)

        # Print summary
        print(f"  🔬 [MetaLearner] Gene importance ranking:")
        for imp in importances[:5]:
            print(f"     {imp.gene:22s}  importance={imp.importance:.3f}  "
                  f"dir={imp.direction}  ρ={imp.spearman_rho:+.3f}")

        if archetypes:
            print(f"  🔬 [MetaLearner] {len(archetypes)} archetypes found:")
            for a in archetypes:
                print(f"     [{a.label}]  fit={a.mean_fitness:.4f}  "
                      f"wr={a.mean_win_rate:.1f}%  n={a.n_members}")

        imp_dict      = {r.gene: r.importance for r in importances}
        patterns_dict = {
            g: {
                "low_pct"    : p.low_pct,
                "median"     : p.median,
                "high_pct"   : p.high_pct,
                "tight_range": p.tight_range,
                "is_universal": p.is_universal,
            }
            for g, p in patterns.items()
        }
        top_k_winners = getattr(config, "META_TOP_K_WINNERS", 20)
        report = MetaReport(
            pool_size        = self.pool.size(),
            n_winners_used   = min(top_k_winners, self.pool.size()),
            n_archetypes     = len(archetypes),
            top_genes        = [r.gene for r in importances[:5]],
            gene_importances = {g: round(v, 4) for g, v in imp_dict.items()},
            archetypes       = [asdict(a) for a in archetypes],
            winner_patterns  = patterns_dict,
            insights         = insights,
            n_seeds_generated= len(seeds),
            timestamp        = _now_iso(),
        )

        self._save_report(report)
        self.pool.save()

        print(f"  🔬 [MetaLearner] Bred {len(seeds)} meta-guided seeds")
        return seeds, report

    def _save_report(self, report: MetaReport) -> None:
        """Save meta report to Redis + file."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_META_REPORT_KEY, json.dumps(asdict(report)))
        except Exception:
            pass
        try:
            os.makedirs("models", exist_ok=True)
            with open("models/meta_report.json", "w") as f:
                json.dump(asdict(report), f, indent=2)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────
# 7. Integration hook: feed evolution winners into MetaLearner
# ──────────────────────────────────────────────────────────────────

def feed_evolution_results(
    population  : List[StrategyGenome],
    n_meta_seeds: int = None,
) -> List[StrategyGenome]:
    """
    Called at end of each EvolutionEngine.run() to:
      1. Add evaluated population to gene pool
      2. Run meta-learning cycle
      3. Return seeds for next evolution run

    These seeds will be injected into the next EvolutionEngine._init_population().

    Parameters
    ----------
    population   : Evaluated population from last evolution run
    n_meta_seeds : Number of meta-guided seeds to generate

    Returns
    -------
    List of StrategyGenome seeds for next evolution round.
    """
    meta = MetaLearner()
    added = meta.ingest_population(population)
    print(f"  🧬 [MetaLearner] Added {added} new genomes. Pool size: {meta.pool.size()}")

    seeds, _ = meta.run(n_seeds=n_meta_seeds)
    return seeds


# ──────────────────────────────────────────────────────────────────
# 8. API helpers
# ──────────────────────────────────────────────────────────────────

def get_meta_report() -> dict:
    """Load and return latest meta report from Redis or file."""
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw = r.get(_REDIS_META_REPORT_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open("models/meta_report.json") as f:
            return json.load(f)
    except Exception:
        return {"status": "no_report"}


def run_meta_cycle(
    population  : List[StrategyGenome] = None,
    n_seeds     : int = None,
    seed        : int = 42,
) -> Tuple[List[StrategyGenome], MetaReport]:
    """
    Top-level entry point for meta-learning.

    If population is provided, it will be added to the gene pool first.

    Returns (seeds, report).

    Ví dụ:
    ------
    >>> from strategy_genetics import run_meta_cycle
    >>> seeds, report = run_meta_cycle(population=last_run_pop, n_seeds=15)
    >>> # seeds → use as init_population for next EvolutionEngine run
    >>> print(report.insights)
    """
    meta = MetaLearner(seed=seed)
    if population:
        meta.ingest_population(population)
    return meta.run(n_seeds=n_seeds)


# ──────────────────────────────────────────────────────────────────
# 9. Math helpers (no scipy / sklearn dependency)
# ──────────────────────────────────────────────────────────────────

def _spearman_rho(x: np.ndarray, y: np.ndarray) -> float:
    """Compute Spearman rank correlation coefficient."""
    if len(x) < 3:
        return 0.0
    try:
        rx = _rank(x)
        ry = _rank(y)
        n  = len(x)
        d2 = np.sum((rx - ry) ** 2)
        return float(1.0 - 6.0 * d2 / (n * (n * n - 1)))
    except Exception:
        return 0.0


def _rank(x: np.ndarray) -> np.ndarray:
    """Return ranks of x (1-indexed, ties → average rank)."""
    n      = len(x)
    sorted_idx = np.argsort(x)
    ranks  = np.empty(n, dtype=float)
    i      = 0
    while i < n:
        j = i + 1
        while j < n and x[sorted_idx[j]] == x[sorted_idx[i]]:
            j += 1
        avg_rank = (i + j - 1) / 2 + 1
        for k in range(i, j):
            ranks[sorted_idx[k]] = avg_rank
        i = j
    return ranks


def _mutual_info_binned(
    x: np.ndarray,
    y: np.ndarray,
    bins: int = 8,
) -> float:
    """
    Compute normalized mutual information between x and y
    using histogram binning.

    Returns value in [0, 1].
    """
    if len(x) < 5:
        return 0.0
    try:
        # Bin both x and y
        x_bins = np.clip(
            np.digitize(x, np.linspace(x.min(), x.max() + 1e-9, bins + 1)) - 1,
            0, bins - 1
        )
        y_bins = np.clip(
            np.digitize(y, np.linspace(y.min(), y.max() + 1e-9, bins + 1)) - 1,
            0, bins - 1
        )
        n = len(x)

        # Joint + marginal distributions
        joint = np.zeros((bins, bins))
        for i in range(n):
            joint[x_bins[i], y_bins[i]] += 1
        joint /= n + 1e-12

        p_x = joint.sum(axis=1)
        p_y = joint.sum(axis=0)

        # Mutual information
        mi = 0.0
        for xi in range(bins):
            for yi in range(bins):
                pxy = joint[xi, yi]
                if pxy > 0 and p_x[xi] > 0 and p_y[yi] > 0:
                    mi += pxy * math.log(pxy / (p_x[xi] * p_y[yi]) + 1e-12)

        # Normalize by joint entropy
        h_joint = -np.sum(joint[joint > 0] * np.log(joint[joint > 0] + 1e-12))
        mi_norm = mi / (h_joint + 1e-12)
        return float(np.clip(mi_norm, 0.0, 1.0))
    except Exception:
        return 0.0


def _kmeans(
    X        : np.ndarray,
    k        : int,
    max_iter : int = 50,
    seed     : int = 42,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Simple k-means clustering (numpy only).

    Returns (centroids, labels).
    """
    rng = np.random.default_rng(seed)
    n   = len(X)
    if n == 0 or k == 0:
        return np.array([]), np.array([])

    k = min(k, n)

    # K-means++ initialization
    idx       = [int(rng.integers(0, n))]
    centroids = [X[idx[0]]]
    for _ in range(k - 1):
        dists = np.min(
            [np.sum((X - c) ** 2, axis=1) for c in centroids], axis=0
        )
        probs = dists / (dists.sum() + 1e-12)
        idx.append(int(rng.choice(n, p=probs)))
        centroids.append(X[idx[-1]])
    centroids = np.array(centroids)

    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        # Assign
        dists = np.array([np.sum((X - c) ** 2, axis=1) for c in centroids])
        new_labels = np.argmin(dists, axis=0)
        if np.all(new_labels == labels):
            break
        labels = new_labels
        # Update
        for ci in range(k):
            mask = labels == ci
            if mask.any():
                centroids[ci] = X[mask].mean(axis=0)

    return centroids, labels


def _label_archetype(centroid: Dict[str, float], idx: int) -> str:
    """Auto-assign a human-readable label to an archetype."""
    wave_w = centroid.get("wave_weight", 1.0)
    rsi_w  = centroid.get("rsi_weight", 1.0)
    macd_w = centroid.get("macd_weight", 1.0)
    bb_w   = centroid.get("bb_weight", 1.0)
    min_sc = centroid.get("min_signal_score", 60.0)
    look   = centroid.get("lookahead_candles", 5.0)

    labels = []
    if wave_w > 1.5:
        labels.append("Wave-Heavy")
    if min_sc > 72:
        labels.append("Conservative")
    elif min_sc < 52:
        labels.append("Aggressive")
    if look <= 3:
        labels.append("Fast")
    elif look >= 10:
        labels.append("Slow")
    if rsi_w > 1.4 and macd_w > 1.4:
        labels.append("Momentum")
    if bb_w > 1.4:
        labels.append("MeanReversion")

    return " + ".join(labels) if labels else f"Archetype-{idx}"


def _describe_archetype(arch: StrategyArchetype) -> str:
    """Generate description paragraph for an archetype."""
    lines = [f"[{arch.label}] {arch.n_members} members"]
    lines.append(f"Avg fitness={arch.mean_fitness:.4f}  "
                 f"WR={arch.mean_win_rate:.1f}%  PF={arch.mean_pf:.2f}")
    wave_w = arch.centroid.get("wave_weight", 1.0)
    min_sc = arch.centroid.get("min_signal_score", 60.0)
    look   = arch.centroid.get("lookahead_candles", 5.0)
    lines.append(f"wave_weight={wave_w:.2f}  "
                 f"min_score={min_sc:.1f}  lookahead={int(round(look))}")
    return "  ".join(lines)


def _generate_insights(
    importances : List[GeneImportance],
    patterns    : Dict[str, GenePattern],
    archetypes  : List[StrategyArchetype],
) -> List[str]:
    """Generate human-readable insight strings from analysis results."""
    insights = []

    if importances:
        top   = importances[0]
        bot   = importances[-1]
        insights.append(
            f"Gene quan trọng nhất: {top.gene} (ρ={top.spearman_rho:+.3f}, "
            f"hướng={top.direction})"
        )
        insights.append(
            f"Gene ít quan trọng nhất: {bot.gene} — có thể mutate tự do"
        )

        positive_genes = [i.gene for i in importances if i.direction == "positive"]
        negative_genes = [i.gene for i in importances if i.direction == "negative"]
        if positive_genes:
            insights.append(
                f"Tăng giá trị giúp thắng: {', '.join(positive_genes[:3])}"
            )
        if negative_genes:
            insights.append(
                f"Giảm giá trị giúp thắng: {', '.join(negative_genes[:3])}"
            )

    tight_genes = [g for g, p in patterns.items() if p.tight_range]
    if tight_genes:
        insights.append(
            f"Gene có vùng giá trị tập trung (cần bảo toàn): "
            f"{', '.join(tight_genes[:4])}"
        )

    universal_genes = [g for g, p in patterns.items() if p.is_universal]
    if universal_genes:
        insights.append(
            f"Gene universal (xuất hiện tương tự qua tất cả archetypes): "
            f"{', '.join(universal_genes)}"
        )

    if archetypes and len(archetypes) >= 2:
        best  = archetypes[0]
        insights.append(
            f"Archetype mạnh nhất: [{best.label}] "
            f"fitness={best.mean_fitness:.4f}"
        )
        insights.append(
            f"Tổng {len(archetypes)} archetype — "
            "nên breed hybrid từ best 2 archetypes để tận dụng đa dạng"
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
# CLI — run meta analysis from command line
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Meta-Learning Strategy Genome Engine")
    parser.add_argument("--mode", choices=["report", "breed", "demo", "fill_demo"],
                        default="report")
    parser.add_argument("--seeds", type=int, default=10, help="N meta seeds to breed")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    if args.mode == "report":
        print("\n═══ META REPORT ═══")
        report = get_meta_report()
        if "status" in report:
            print("No report found. Run 'demo' or 'fill_demo' first.")
        else:
            print(f"Pool size  : {report.get('pool_size', 0)}")
            print(f"Archetypes : {report.get('n_archetypes', 0)}")
            print(f"Top genes  : {', '.join(report.get('top_genes', []))}")
            print("\nInsights:")
            for insight in report.get("insights", []):
                print(f"  • {insight}")

    elif args.mode == "breed":
        print("\n═══ META BREED ═══")
        seeds, report = run_meta_cycle(n_seeds=args.seeds)
        print(f"Bred {len(seeds)} seeds:")
        for s in seeds[:5]:
            print(f"  {s.summary()}")

    elif args.mode == "demo":
        print("\n═══ META DEMO (with random pool data) ═══")
        # Generate random evaluated genomes to simulate real pool
        ops  = GeneticOperators(seed=42)
        pop  = []
        rng  = np.random.default_rng(42)
        for _ in range(60):
            g = ops.generate_random()
            g.fitness       = float(rng.uniform(0, 0.3))
            g.win_rate_pct  = float(rng.uniform(45, 70))
            g.profit_factor = float(rng.uniform(0.8, 2.5))
            g.n_trades      = int(rng.integers(10, 200))
            g.evaluated     = True
            pop.append(g)

        seeds, report = run_meta_cycle(population=pop, n_seeds=args.seeds)
        print(f"\nGenerated {len(seeds)} meta seeds")
        print(f"\nInsights:")
        for i in report.insights:
            print(f"  • {i}")
        print(f"\nTop 3 gene importances:")
        for gene in report.top_genes[:3]:
            imp = report.gene_importances.get(gene, 0.0)
            print(f"  {gene}: {imp:.4f}")

    elif args.mode == "fill_demo":
        print("\n═══ FILL DEMO POOL ═══")
        # Fill pool with structured data (winners have high wave_weight)
        ops = GeneticOperators(seed=99)
        rng = np.random.default_rng(99)
        pop = []
        for i in range(80):
            g = ops.generate_random()
            # Bias: high wave_weight → higher fitness
            wave_boost = (g.wave_weight - 1.0) * 0.05
            g.fitness       = max(0.0, float(rng.uniform(0, 0.2) + wave_boost))
            g.win_rate_pct  = min(75, max(40, float(rng.uniform(48, 66) + wave_boost * 50)))
            g.profit_factor = float(rng.uniform(0.9, 2.0) + wave_boost * 3)
            g.n_trades      = int(rng.integers(20, 150))
            g.evaluated     = True
            pop.append(g)

        seeds, report = run_meta_cycle(population=pop, n_seeds=args.seeds)
        print(f"\nBreed {len(seeds)} meta seeds")
        print("\nGene importances (top 5):")
        items = sorted(report.gene_importances.items(), key=lambda x: x[1], reverse=True)
        for gene, imp in items[:5]:
            print(f"  {gene:22s} → {imp:.4f}")
        print("\nArchetypes:")
        for arch in report.archetypes:
            print(f"  [{arch['label']}] fit={arch['mean_fitness']:.4f} "
                  f"n={arch['n_members']}")
