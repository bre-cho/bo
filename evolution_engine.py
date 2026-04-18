"""
evolution_engine.py
===================
SELF-PLAY + SIMULATION ENVIRONMENT ENGINE

Biến hệ thống từ optimization system → evolutionary system.

Hệ tự:
  1. Sinh ra nhiều chiến lược (genomes) với tham số khác nhau
  2. Tạo môi trường thị trường đa dạng (synthetic + regime-aware)
  3. Cho các chiến lược cạnh tranh trong cùng môi trường (self-play)
  4. Đo fitness: win_rate, profit_factor, expectancy
  5. Chọn kẻ thắng, loại bỏ kẻ thua (selection)
  6. Lai ghép + đột biến để tạo thế hệ mới (crossover + mutation)
  7. Thăng chức chiến lược tốt nhất lên config sống (promote_champion)

Thuật toán: Genetic Algorithm + Tournament Selection + Elitism

Thành phần:
  StrategyGenome   — DNA của một chiến lược giao dịch
  FitnessResult    — Kết quả đánh giá một genome
  GenomeScorer     — Chấm điểm tín hiệu theo tham số genome (không phụ thuộc brain.py)
  SimEnvironment   — Môi trường mô phỏng cô lập cho từng genome
  SelfPlayArena    — Đấu trường: n_genome × n_env → bảng xếp hạng
  GeneticOperators — Selection, crossover, mutation, elitism
  EvolutionEngine  — Vòng lặp tiến hóa hoàn chỉnh
  promote_champion()  — Ghi winner genome lên Redis để engine live dùng
  run_evolution_cycle() — Top-level entry point

Cách dùng:
  >>> from evolution_engine import run_evolution_cycle
  >>> champion = run_evolution_cycle(generations=10, pop_size=30)
  >>> print(champion.summary())
"""

from __future__ import annotations

import copy
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import config


# ──────────────────────────────────────────────────────────────────
# Redis key for champion genome
# ──────────────────────────────────────────────────────────────────

_REDIS_CHAMPION_KEY    = "Deriv_Champion_Genome"
_REDIS_EVOL_HISTORY    = "Deriv_Evolution_History"
_REDIS_EVOL_STATUS_KEY = "Deriv_Evolution_Status"


# ──────────────────────────────────────────────────────────────────
# 1. StrategyGenome — DNA of a trading strategy
# ──────────────────────────────────────────────────────────────────

@dataclass
class StrategyGenome:
    """
    DNA của một chiến lược giao dịch.

    Tất cả tham số có thể tiến hóa (floating point or integer).
    Bounds được định nghĩa trong GENE_BOUNDS để mutation + crossover
    không tạo giá trị vô lý.

    Các nhóm gene:
      RSI group      — ngưỡng phân tích RSI
      Signal weights — trọng số từng thành phần tín hiệu
      Entry filters  — ngưỡng min để vào lệnh
      Trade params   — lookahead, thời hạn hợp đồng
    """
    # Định danh
    genome_id  : str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    generation : int = 0
    parent_ids : List[str] = field(default_factory=list)

    # ── RSI thresholds ─────────────────────────────────────────────
    rsi_oversold       : float = 30.0   # RSI < ngưỡng này → oversold (CALL bias)
    rsi_overbought     : float = 70.0   # RSI > ngưỡng này → overbought (PUT bias)
    rsi_near_os        : float = 35.0   # "Near" oversold band
    rsi_near_ob        : float = 65.0   # "Near" overbought band

    # ── Signal component weights (multipliers on raw score) ────────
    rsi_weight         : float = 1.0    # Weight for RSI layer (max 18 pts raw)
    momentum_weight    : float = 1.0    # Weight for Momentum layer (max 12 pts)
    macd_weight        : float = 1.0    # Weight for MACD layer (max 15 pts)
    bb_weight          : float = 1.0    # Weight for Bollinger layer (max 15 pts)
    wave_weight        : float = 1.0    # Weight for Wave/Fib layer (max 40 pts)

    # ── Entry filters ──────────────────────────────────────────────
    min_signal_score   : float = 60.0   # Minimum score to take a trade
    min_wave_score     : float = 0.0    # Min wave entry_score (0 = ignore)
    bb_tight_threshold : float = 0.10   # BB position near boundary to score

    # ── Trade parameters ───────────────────────────────────────────
    lookahead_candles  : int   = 5      # Bars after entry to measure result

    # ── Fitness (set after evaluation, not evolved) ────────────────
    fitness            : float = 0.0
    win_rate_pct       : float = 0.0
    profit_factor      : float = 0.0
    n_trades           : int   = 0
    evaluated          : bool  = False

    def genes(self) -> Dict[str, float]:
        """Return only the evolvable float/int genes (no metadata)."""
        return {
            "rsi_oversold"       : self.rsi_oversold,
            "rsi_overbought"     : self.rsi_overbought,
            "rsi_near_os"        : self.rsi_near_os,
            "rsi_near_ob"        : self.rsi_near_ob,
            "rsi_weight"         : self.rsi_weight,
            "momentum_weight"    : self.momentum_weight,
            "macd_weight"        : self.macd_weight,
            "bb_weight"          : self.bb_weight,
            "wave_weight"        : self.wave_weight,
            "min_signal_score"   : self.min_signal_score,
            "min_wave_score"     : self.min_wave_score,
            "bb_tight_threshold" : self.bb_tight_threshold,
            "lookahead_candles"  : float(self.lookahead_candles),
        }

    def summary(self) -> str:
        return (
            f"Genome[{self.genome_id}] gen={self.generation} "
            f"fitness={self.fitness:.4f} wr={self.win_rate_pct:.1f}% "
            f"pf={self.profit_factor:.2f} n={self.n_trades} "
            f"min_score={self.min_signal_score:.1f} "
            f"lookahead={self.lookahead_candles}"
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyGenome":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})

    @classmethod
    def from_config(cls) -> "StrategyGenome":
        """Create genome seeded from current config (default params)."""
        return cls(
            genome_id          = "config_seed",
            rsi_oversold       = float(config.RSI_OVERSOLD),
            rsi_overbought     = float(config.RSI_OVERBOUGHT),
            rsi_near_os        = float(config.RSI_OVERSOLD) + 5,
            rsi_near_ob        = float(config.RSI_OVERBOUGHT) - 5,
            min_signal_score   = float(config.MIN_SIGNAL_SCORE),
            lookahead_candles  = int(config.SIM_LOOKAHEAD_CANDLES),
        )


# ──────────────────────────────────────────────────────────────────
# Gene bounds — enforced during mutation + crossover
# ──────────────────────────────────────────────────────────────────

GENE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "rsi_oversold"       : (15.0,  45.0),
    "rsi_overbought"     : (55.0,  85.0),
    "rsi_near_os"        : (25.0,  50.0),
    "rsi_near_ob"        : (50.0,  75.0),
    "rsi_weight"         : (0.3,   2.5),
    "momentum_weight"    : (0.3,   2.5),
    "macd_weight"        : (0.3,   2.5),
    "bb_weight"          : (0.3,   2.5),
    "wave_weight"        : (0.0,   3.0),
    "min_signal_score"   : (40.0,  85.0),
    "min_wave_score"     : (0.0,   30.0),
    "bb_tight_threshold" : (0.05,  0.25),
    "lookahead_candles"  : (2.0,   15.0),
}


def _clip_genome(g: StrategyGenome) -> StrategyGenome:
    """Clamp all gene values to their bounds."""
    for gene, (lo, hi) in GENE_BOUNDS.items():
        val = getattr(g, gene)
        val = float(np.clip(val, lo, hi))
        if gene == "lookahead_candles":
            val = max(2, int(round(val)))
        setattr(g, gene, val)
    # Ensure rsi_oversold < rsi_near_os < rsi_near_ob < rsi_overbought
    g.rsi_oversold  = min(g.rsi_oversold,  g.rsi_near_os - 1.0)
    g.rsi_overbought = max(g.rsi_overbought, g.rsi_near_ob + 1.0)
    return g


# ──────────────────────────────────────────────────────────────────
# 2. FitnessResult
# ──────────────────────────────────────────────────────────────────

@dataclass
class FitnessResult:
    genome_id      : str
    n_trades       : int
    wins           : int
    losses         : int
    win_rate_pct   : float
    profit_factor  : float
    expectancy     : float   # avg pnl per trade
    total_pnl      : float
    fitness        : float   # composite score (higher = better)

    @staticmethod
    def empty(genome_id: str) -> "FitnessResult":
        return FitnessResult(
            genome_id     = genome_id,
            n_trades      = 0,
            wins          = 0,
            losses        = 0,
            win_rate_pct  = 0.0,
            profit_factor = 0.0,
            expectancy    = 0.0,
            total_pnl     = 0.0,
            fitness       = 0.0,
        )


def _compute_fitness(
    n_trades    : int,
    win_rate_pct: float,
    profit_factor: float,
    expectancy  : float,
    min_trades  : int = 5,
    payout      : float = None,
) -> float:
    """
    Composite fitness score for a genome.

    Formula:
      fitness = win_rate_excess × profit_factor_bonus × trade_volume_bonus
      - win_rate_excess: how far above break-even (52% for 85% payout)
      - profit_factor_bonus: ln(1 + pf) for diminishing returns
      - trade_volume_bonus: log scale on n_trades (encourages active strategies)

    Penalties:
      - n_trades < min_trades → fitness = 0 (not enough evidence)
    """
    payout = payout or config.SIM_PAYOUT_RATIO
    breakeven_wr = 1.0 / (1.0 + payout)  # ~54% for 85% payout

    if n_trades < min_trades:
        return 0.0

    wr_excess  = max(0.0, (win_rate_pct / 100.0) - breakeven_wr)
    pf_bonus   = float(np.log1p(max(0.0, profit_factor)))
    vol_bonus  = float(np.log1p(n_trades) / np.log1p(50))   # normalize at 50 trades

    fitness = wr_excess * pf_bonus * vol_bonus * 100.0
    return round(float(fitness), 6)


# ──────────────────────────────────────────────────────────────────
# 3. GenomeScorer — parameterized signal scoring using genome values
# ──────────────────────────────────────────────────────────────────

class GenomeScorer:
    """
    Chấm điểm tín hiệu theo tham số của genome.

    Đây là version tham số hóa của brain._score_signal:
      - Dùng genome.rsi_oversold thay vì config.RSI_OVERSOLD
      - Dùng genome.*_weight thay vì cố định tỉ lệ
      - KHÔNG import brain.py → tránh circular dependency

    Returns: (direction: str, score: float)
    """

    def __init__(self, genome: StrategyGenome) -> None:
        self.genome = genome

    def _rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0)
        loss  = -delta.clip(upper=0)
        avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
        rs    = avg_g / (avg_l + 1e-9)
        return 100 - (100 / (1 + rs))

    def _momentum_z(self, series: pd.Series, period: int = 10) -> pd.Series:
        mom = series - series.shift(period)
        std = mom.rolling(20).std()
        return mom / (std + 1e-9)

    def _macd_hist(self, series: pd.Series, fast=12, slow=26, sig=9) -> pd.Series:
        ema_f  = series.ewm(span=fast, adjust=False).mean()
        ema_s  = series.ewm(span=slow, adjust=False).mean()
        macd   = ema_f - ema_s
        signal = macd.ewm(span=sig, adjust=False).mean()
        return macd - signal

    def _bollinger_pos(self, series: pd.Series, period=20, num_std=2.0) -> pd.Series:
        mid   = series.rolling(period).mean()
        std   = series.rolling(period).std()
        upper = mid + num_std * std
        lower = mid - num_std * std
        return (series - lower) / (upper - lower + 1e-9)

    def score(self, df: pd.DataFrame) -> Tuple[str, float]:
        """
        Score the last candle in df using this genome's parameters.

        Returns (direction, score) where direction is 'CALL', 'PUT', or 'NONE'.
        """
        g     = self.genome
        close = df["close"]

        if len(close) < 30:
            return "NONE", 0.0

        rsi_s   = self._rsi(close)
        mom_s   = self._momentum_z(close)
        macd_s  = self._macd_hist(close)
        bb_pos_s = self._bollinger_pos(close)

        rsi_now   = float(rsi_s.iloc[-1])
        rsi_prev  = float(rsi_s.iloc[-2]) if len(rsi_s) > 1 else rsi_now
        mom_now   = float(mom_s.iloc[-1])
        hist_now  = float(macd_s.iloc[-1])
        hist_prev = float(macd_s.iloc[-2]) if len(macd_s) > 1 else hist_now
        bb_pos    = float(bb_pos_s.iloc[-1])

        call_score = 0.0
        put_score  = 0.0

        # ── RSI (max 18 pts × weight) ─────────────────────────────
        rsi_cross_up   = rsi_prev < g.rsi_oversold  and rsi_now >= g.rsi_oversold
        rsi_cross_down = rsi_prev > g.rsi_overbought and rsi_now <= g.rsi_overbought
        rsi_near_os    = rsi_now < g.rsi_near_os
        rsi_near_ob    = rsi_now > g.rsi_near_ob

        if rsi_cross_up:
            call_score += 18.0 * g.rsi_weight
        elif rsi_near_os and g.rsi_near_os > 0:
            call_score += 9.0 * g.rsi_weight * (g.rsi_near_os - rsi_now) / max(g.rsi_near_os, 1)

        if rsi_cross_down:
            put_score += 18.0 * g.rsi_weight
        elif rsi_near_ob:
            put_score  += 9.0 * g.rsi_weight * (rsi_now - g.rsi_near_ob) / max(100 - g.rsi_near_ob, 1)

        # ── Momentum (max 12 pts × weight) ────────────────────────
        mom_abs = abs(mom_now)
        mom_pts = min(12.0, 6.0 * mom_abs) * g.momentum_weight
        if mom_now > 0.05:
            call_score += mom_pts
        elif mom_now < -0.05:
            put_score  += mom_pts

        # ── MACD (max 15 pts × weight) ────────────────────────────
        macd_cross_up   = hist_prev < 0 and hist_now >= 0
        macd_cross_down = hist_prev > 0 and hist_now <= 0
        macd_trend_up   = hist_now > 0 and hist_now > hist_prev
        macd_trend_down = hist_now < 0 and hist_now < hist_prev

        if macd_cross_up:
            call_score += 15.0 * g.macd_weight
        elif macd_trend_up:
            call_score += 6.0 * g.macd_weight

        if macd_cross_down:
            put_score += 15.0 * g.macd_weight
        elif macd_trend_down:
            put_score += 6.0 * g.macd_weight

        # ── Bollinger Bands (max 15 pts × weight) ─────────────────
        thr = g.bb_tight_threshold
        if bb_pos <= thr and thr > 0:
            call_score += 15.0 * g.bb_weight * (1 - bb_pos / thr)
        elif bb_pos <= thr * 3:
            call_score += 6.0 * g.bb_weight

        if bb_pos >= (1 - thr) and thr > 0:
            put_score += 15.0 * g.bb_weight * ((bb_pos - (1 - thr)) / thr)
        elif bb_pos >= (1 - thr * 3):
            put_score += 6.0 * g.bb_weight

        # ── Wave analysis (max 40 pts × weight) ───────────────────
        try:
            from wave_analyzer import analyze_waves
            wave = analyze_waves(df)
            if wave and wave.is_wave_entry():
                wave_pts = wave.entry_score * g.wave_weight
                if g.min_wave_score > 0 and wave.entry_score < g.min_wave_score:
                    wave_pts = 0.0
                if wave.entry_direction == "CALL":
                    call_score += wave_pts
                elif wave.entry_direction == "PUT":
                    put_score  += wave_pts
        except Exception:
            pass

        # ── Decision ──────────────────────────────────────────────
        if call_score >= put_score and call_score >= g.min_signal_score:
            return "CALL", round(min(100.0, call_score), 2)
        if put_score > call_score and put_score >= g.min_signal_score:
            return "PUT", round(min(100.0, put_score), 2)
        return "NONE", round(max(call_score, put_score), 2)


# ──────────────────────────────────────────────────────────────────
# 4. SimEnvironment — isolated evaluation sandbox per genome
# ──────────────────────────────────────────────────────────────────

class SimEnvironment:
    """
    Môi trường mô phỏng cô lập cho từng genome.

    Mỗi genome được đánh giá trong cùng môi trường (candle DataFrame)
    nhưng với bộ tham số riêng (GenomeScorer).

    Đây là "đấu trường công bằng": cùng data, khác chiến lược.
    """

    def __init__(
        self,
        df          : pd.DataFrame,
        env_id      : str = "env",
        payout_ratio: float = None,
        stake       : float = None,
        warmup      : int = 60,
    ) -> None:
        self.df          = df
        self.env_id      = env_id
        self.payout_ratio= payout_ratio or config.SIM_PAYOUT_RATIO
        self.stake       = stake        or config.SIM_STAKE_USD
        self.warmup      = warmup

    def evaluate(self, genome: StrategyGenome) -> FitnessResult:
        """
        Run genome through walk-forward simulation on this environment.
        Returns FitnessResult with fitness, win_rate, profit_factor.
        """
        scorer    = GenomeScorer(genome)
        lookahead = max(2, int(genome.lookahead_candles))
        df        = self.df

        wins_pnl   = 0.0
        losses_pnl = 0.0
        n_wins     = 0
        n_losses   = 0

        for i in range(self.warmup, len(df) - lookahead):
            window = df.iloc[: i + 1]
            try:
                direction, score = scorer.score(window)
            except Exception:
                continue

            if direction == "NONE":
                continue

            entry = float(df.iloc[i]["close"])
            exit_ = float(df.iloc[i + lookahead]["close"])

            if direction == "CALL":
                won = exit_ > entry
            else:
                won = exit_ < entry

            if won:
                pnl = self.stake * self.payout_ratio
                wins_pnl += pnl
                n_wins   += 1
            else:
                pnl = -self.stake
                losses_pnl += abs(pnl)
                n_losses   += 1

        n_trades  = n_wins + n_losses
        total_pnl = wins_pnl - losses_pnl
        win_rate  = n_wins / n_trades * 100.0 if n_trades > 0 else 0.0
        pf        = wins_pnl / losses_pnl if losses_pnl > 0 else (float("inf") if wins_pnl > 0 else 0.0)
        pf        = min(pf, 10.0)  # Cap at 10 to avoid inf inflating fitness
        exp       = total_pnl / n_trades if n_trades > 0 else 0.0

        fitness = _compute_fitness(n_trades, win_rate, pf, exp)

        return FitnessResult(
            genome_id     = genome.genome_id,
            n_trades      = n_trades,
            wins          = n_wins,
            losses        = n_losses,
            win_rate_pct  = round(win_rate, 2),
            profit_factor = round(pf, 4),
            expectancy    = round(exp, 4),
            total_pnl     = round(total_pnl, 2),
            fitness       = fitness,
        )


# ──────────────────────────────────────────────────────────────────
# 5. SelfPlayArena — population vs environment matrix
# ──────────────────────────────────────────────────────────────────

class SelfPlayArena:
    """
    Đấu trường tự cạnh tranh: N chiến lược × M môi trường thị trường.

    Quy trình:
      1. Tạo M môi trường từ synthetic candle data (đa dạng regime)
      2. Chạy tất cả N genome qua tất cả M môi trường
      3. Fitness của genome = trung bình fitness trên tất cả môi trường
      4. Trả về danh sách genome đã được đánh giá, sắp xếp theo fitness

    Tại sao dùng nhiều môi trường:
      - Tránh overfitting một regime cụ thể
      - Genome mạnh thật sự phải hoạt động tốt trên cả trending, chop, crash
    """

    def __init__(
        self,
        n_envs      : int = None,
        candles_per_env: int = None,
        seed        : int = 42,
    ) -> None:
        self.n_envs         = n_envs         or config.EVOL_N_ENVIRONMENTS
        self.candles_per_env= candles_per_env or config.EVOL_ENV_CANDLES
        self.seed           = seed
        self._environments  : List[SimEnvironment] = []

    def _build_environments(self) -> None:
        """Create diverse synthetic market environments."""
        from synthetic_engine import CandleGenerator

        rng = np.random.default_rng(self.seed)
        self._environments = []

        # Ensure all 7 regime types are represented
        regime_generators = [
            ("trend_up",    lambda bp, s: CandleGenerator(base_price=bp, seed=s).trending_up(self.candles_per_env, strength=1.0)),
            ("trend_up_s",  lambda bp, s: CandleGenerator(base_price=bp, seed=s).trending_up(self.candles_per_env, strength=2.0)),
            ("trend_down",  lambda bp, s: CandleGenerator(base_price=bp, seed=s).trending_down(self.candles_per_env, strength=1.0)),
            ("trend_down_s",lambda bp, s: CandleGenerator(base_price=bp, seed=s).trending_down(self.candles_per_env, strength=2.0)),
            ("choppy",      lambda bp, s: CandleGenerator(base_price=bp, seed=s).choppy(self.candles_per_env)),
            ("choppy_v",    lambda bp, s: CandleGenerator(base_price=bp, seed=s).choppy(self.candles_per_env, volatility=2.0)),
            ("crash",       lambda bp, s: CandleGenerator(base_price=bp, seed=s).crash(self.candles_per_env)),
            ("spike",       lambda bp, s: CandleGenerator(base_price=bp, seed=s).spike(self.candles_per_env)),
            ("recovery",    lambda bp, s: CandleGenerator(base_price=bp, seed=s).recovery(self.candles_per_env)),
            ("mixed",       lambda bp, s: CandleGenerator(base_price=bp, seed=s).mixed(self.candles_per_env)),
        ]

        for i in range(self.n_envs):
            regime_name, gen_fn = regime_generators[i % len(regime_generators)]
            base_price = float(rng.uniform(500, 2000))
            seed_i     = self.seed + i * 7
            try:
                df   = gen_fn(base_price, seed_i)
                env  = SimEnvironment(df=df, env_id=f"{regime_name}_{i}")
                self._environments.append(env)
            except Exception as exc:
                print(f"[Arena] Warning: failed to build env {regime_name}_{i}: {exc}")

    def run(
        self,
        population : List[StrategyGenome],
        verbose    : bool = False,
    ) -> List[StrategyGenome]:
        """
        Run all genomes through all environments.

        Returns population with fitness, win_rate_pct, profit_factor,
        n_trades populated and sorted by fitness descending.
        """
        if not self._environments:
            self._build_environments()

        n_envs = len(self._environments)
        if n_envs == 0:
            return population

        for genome in population:
            results: List[FitnessResult] = []
            for env in self._environments:
                try:
                    r = env.evaluate(genome)
                    results.append(r)
                except Exception:
                    pass

            if results:
                genome.fitness       = float(np.mean([r.fitness       for r in results]))
                genome.win_rate_pct  = float(np.mean([r.win_rate_pct  for r in results]))
                genome.profit_factor = float(np.mean([r.profit_factor for r in results]))
                genome.n_trades      = int(np.sum(  [r.n_trades       for r in results]))
                genome.evaluated     = True

            if verbose:
                print(f"  {genome.summary()}")

        population.sort(key=lambda g: g.fitness, reverse=True)
        return population

    @property
    def n_environments(self) -> int:
        return len(self._environments)


# ──────────────────────────────────────────────────────────────────
# 6. GeneticOperators — selection, crossover, mutation, elitism
# ──────────────────────────────────────────────────────────────────

class GeneticOperators:
    """
    Các toán tử tiến hóa — biến đổi và kết hợp genomes.

    Selection    : Tournament selection (k contestants → 1 winner)
    Crossover    : Uniform crossover (gene-by-gene coin flip)
    Mutation     : Gaussian perturbation + occasional large jump
    Elitism      : Top-K survive unchanged into next generation
    """

    def __init__(
        self,
        mutation_rate       : float = None,
        mutation_sigma      : float = None,
        crossover_rate      : float = None,
        tournament_k        : int   = None,
        large_mutation_prob : float = 0.10,
        seed                : Optional[int] = None,
    ) -> None:
        self.mutation_rate       = mutation_rate    or config.EVOL_MUTATION_RATE
        self.mutation_sigma      = mutation_sigma   or config.EVOL_MUTATION_SIGMA
        self.crossover_rate      = crossover_rate   or config.EVOL_CROSSOVER_RATE
        self.tournament_k        = tournament_k     or config.EVOL_TOURNAMENT_K
        self.large_mutation_prob = large_mutation_prob
        self._rng = np.random.default_rng(seed)

    def tournament_select(self, population: List[StrategyGenome]) -> StrategyGenome:
        """
        Chọn 1 genome bằng tournament selection.
        Chọn ngẫu nhiên k genomes, trả về genome có fitness cao nhất.
        """
        k = min(self.tournament_k, len(population))
        contestants = self._rng.choice(len(population), size=k, replace=False)
        best_idx = max(contestants, key=lambda i: population[i].fitness)
        return copy.deepcopy(population[best_idx])

    def crossover(
        self,
        parent_a: StrategyGenome,
        parent_b: StrategyGenome,
    ) -> Tuple[StrategyGenome, StrategyGenome]:
        """
        Uniform crossover: mỗi gene được chọn ngẫu nhiên từ parent_a hoặc parent_b.
        Tạo 2 offspring với genes pha trộn từ 2 cha mẹ.
        """
        gene_names = list(GENE_BOUNDS.keys())
        child_a    = copy.deepcopy(parent_a)
        child_b    = copy.deepcopy(parent_b)

        child_a.genome_id  = str(uuid.uuid4())[:8]
        child_b.genome_id  = str(uuid.uuid4())[:8]
        child_a.generation = parent_a.generation + 1
        child_b.generation = parent_b.generation + 1
        child_a.parent_ids = [parent_a.genome_id, parent_b.genome_id]
        child_b.parent_ids = [parent_a.genome_id, parent_b.genome_id]
        child_a.evaluated  = False
        child_b.evaluated  = False

        for gene in gene_names:
            if self._rng.random() < 0.5:
                # Swap gene between children
                val_a = getattr(parent_a, gene)
                val_b = getattr(parent_b, gene)
                setattr(child_a, gene, val_b)
                setattr(child_b, gene, val_a)

        return _clip_genome(child_a), _clip_genome(child_b)

    def blend_crossover(
        self,
        parent_a: StrategyGenome,
        parent_b: StrategyGenome,
        alpha: float = 0.5,
    ) -> StrategyGenome:
        """
        BLX-α crossover: blend gene values with random alpha factor.
        More gradual than uniform crossover.
        """
        child = copy.deepcopy(parent_a)
        child.genome_id  = str(uuid.uuid4())[:8]
        child.generation = parent_a.generation + 1
        child.parent_ids = [parent_a.genome_id, parent_b.genome_id]
        child.evaluated  = False

        for gene in GENE_BOUNDS.keys():
            va    = float(getattr(parent_a, gene))
            vb    = float(getattr(parent_b, gene))
            lo    = min(va, vb) - alpha * abs(va - vb)
            hi    = max(va, vb) + alpha * abs(va - vb)
            val   = float(self._rng.uniform(lo, hi))
            setattr(child, gene, val)

        return _clip_genome(child)

    def mutate(self, genome: StrategyGenome) -> StrategyGenome:
        """
        Gaussian mutation with occasional large jump.
        Each gene is mutated independently with probability mutation_rate.
        """
        mutant = copy.deepcopy(genome)
        mutant.genome_id  = str(uuid.uuid4())[:8]
        mutant.generation = genome.generation + 1
        mutant.parent_ids = [genome.genome_id]
        mutant.evaluated  = False

        for gene, (lo, hi) in GENE_BOUNDS.items():
            if self._rng.random() < self.mutation_rate:
                current = float(getattr(mutant, gene))
                span    = hi - lo

                if self._rng.random() < self.large_mutation_prob:
                    # Large jump: completely random value in bounds
                    new_val = float(self._rng.uniform(lo, hi))
                else:
                    # Small perturbation
                    noise   = float(self._rng.normal(0, self.mutation_sigma * span))
                    new_val = current + noise

                setattr(mutant, gene, new_val)

        return _clip_genome(mutant)

    def generate_random(self, generation: int = 0) -> StrategyGenome:
        """Create a completely random genome within bounds."""
        g = StrategyGenome(generation=generation)
        for gene, (lo, hi) in GENE_BOUNDS.items():
            val = float(self._rng.uniform(lo, hi))
            setattr(g, gene, val)
        return _clip_genome(g)

    def next_generation(
        self,
        population  : List[StrategyGenome],
        pop_size    : int,
        n_elites    : int = None,
    ) -> List[StrategyGenome]:
        """
        Create the next generation from evaluated population.

        Strategy:
          - Keep top n_elites unchanged (elitism)
          - Fill remaining slots with:
            * 40% uniform crossover offspring
            * 30% BLX-α blend crossover offspring
            * 20% mutations of elites
            * 10% completely random new genomes (diversity injection)
        """
        n_elites = n_elites or config.EVOL_N_ELITES
        n_elites = min(n_elites, len(population))

        elites = population[:n_elites]  # Already sorted by fitness
        new_pop: List[StrategyGenome] = list(elites)

        remaining = pop_size - n_elites
        if remaining <= 0:
            return new_pop

        # Quotas
        n_cross      = int(remaining * 0.40)
        n_blend      = int(remaining * 0.30)
        n_mutants    = int(remaining * 0.20)
        n_random_new = remaining - n_cross - n_blend - n_mutants

        # Uniform crossover
        for _ in range(n_cross):
            pa = self.tournament_select(population)
            pb = self.tournament_select(population)
            child_a, _ = self.crossover(pa, pb)
            new_pop.append(child_a)

        # BLX-α blend crossover
        for _ in range(n_blend):
            pa = self.tournament_select(population)
            pb = self.tournament_select(population)
            child = self.blend_crossover(pa, pb, alpha=0.3)
            new_pop.append(child)

        # Mutations of elites
        for _ in range(n_mutants):
            base = random.choice(elites)
            new_pop.append(self.mutate(base))

        # Fresh random individuals (diversity injection)
        gen = population[0].generation + 1 if population else 1
        for _ in range(n_random_new):
            new_pop.append(self.generate_random(generation=gen))

        return new_pop[:pop_size]


# ──────────────────────────────────────────────────────────────────
# 7. EvolutionHistory — track per-generation statistics
# ──────────────────────────────────────────────────────────────────

@dataclass
class GenerationStats:
    generation    : int
    best_fitness  : float
    mean_fitness  : float
    best_win_rate : float
    best_pf       : float
    best_n_trades : int
    champion_id   : str
    timestamp     : str = field(default_factory=lambda: str(pd.Timestamp.now().isoformat()))


@dataclass
class EvolutionHistory:
    generations    : List[GenerationStats] = field(default_factory=list)
    champion       : Optional[StrategyGenome] = None

    def record(self, generation: int, population: List[StrategyGenome]) -> None:
        """Record stats for current generation."""
        if not population:
            return
        best = population[0]
        fitnesses = [g.fitness for g in population if g.evaluated]
        self.generations.append(GenerationStats(
            generation    = generation,
            best_fitness  = best.fitness,
            mean_fitness  = float(np.mean(fitnesses)) if fitnesses else 0.0,
            best_win_rate = best.win_rate_pct,
            best_pf       = best.profit_factor,
            best_n_trades = best.n_trades,
            champion_id   = best.genome_id,
        ))

    def is_converged(self, window: int = 3, threshold: float = 0.001) -> bool:
        """Return True if best_fitness hasn't improved in last `window` generations."""
        if len(self.generations) < window:
            return False
        recent = [g.best_fitness for g in self.generations[-window:]]
        return (max(recent) - min(recent)) < threshold


# ──────────────────────────────────────────────────────────────────
# 8. EvolutionEngine — orchestrates the full evolution loop
# ──────────────────────────────────────────────────────────────────

class EvolutionEngine:
    """
    Engine tiến hóa hoàn chỉnh.

    Vòng lặp:
      1. Khởi tạo quần thể (seeded from config + random)
      2. [Loop] Đánh giá trong arena → rank
      3. In leaderboard
      4. Ghi lịch sử
      5. Kiểm tra hội tụ (converge) → dừng sớm nếu cần
      6. Tạo thế hệ mới (genetic operators)
      7. Lặp lại
      8. Promote champion → Redis

    Thread-safe: mỗi run độc lập (không chia sẻ state).
    """

    def __init__(
        self,
        pop_size   : int  = None,
        n_gens     : int  = None,
        n_envs     : int  = None,
        env_candles: int  = None,
        n_elites   : int  = None,
        seed       : int  = 42,
        verbose    : bool = True,
    ) -> None:
        self.pop_size    = pop_size    or config.EVOL_POP_SIZE
        self.n_gens      = n_gens      or config.EVOL_GENERATIONS
        self.n_envs      = n_envs      or config.EVOL_N_ENVIRONMENTS
        self.env_candles = env_candles or config.EVOL_ENV_CANDLES
        self.n_elites    = n_elites    or config.EVOL_N_ELITES
        self.seed        = seed
        self.verbose     = verbose

        self.arena   = SelfPlayArena(n_envs=self.n_envs, candles_per_env=self.env_candles, seed=seed)
        self.ops     = GeneticOperators(seed=seed)
        self.history = EvolutionHistory()

    # ── Population initialization ─────────────────────────────────

    def _init_population(self) -> List[StrategyGenome]:
        """
        Initialize population with:
          - 1 config-seeded genome (baseline)
          - 1 genome from current champion (if exists in Redis)
          - rest: random
        """
        pop: List[StrategyGenome] = []

        # Config baseline
        pop.append(StrategyGenome.from_config())

        # Previous champion (if available)
        champ = load_champion()
        if champ:
            champ.genome_id  = "prev_champion"
            champ.generation = 0
            champ.evaluated  = False
            pop.append(champ)

        # Fill with random genomes
        while len(pop) < self.pop_size:
            pop.append(self.ops.generate_random(generation=0))

        return pop[:self.pop_size]

    # ── Main evolution loop ───────────────────────────────────────

    def run(self) -> StrategyGenome:
        """
        Run full evolution and return the champion genome.

        Prints progress table after each generation.
        Returns the all-time best genome.
        """
        print("\n" + "═" * 60)
        print("  🧬  EVOLUTION ENGINE — START")
        print(f"     pop={self.pop_size}  gens={self.n_gens}  "
              f"envs={self.n_envs}  candles/env={self.env_candles}")
        print("═" * 60)

        t0  = time.time()
        pop = self._init_population()
        all_time_best: Optional[StrategyGenome] = None

        for gen in range(1, self.n_gens + 1):
            t_gen = time.time()

            # Evaluate
            pop = self.arena.run(pop, verbose=False)
            self.history.record(gen, pop)

            best = pop[0]
            if all_time_best is None or best.fitness > all_time_best.fitness:
                all_time_best = copy.deepcopy(best)

            # Print leaderboard
            if self.verbose:
                self._print_generation(gen, pop, time.time() - t_gen)

            # Check convergence
            if gen >= 5 and self.history.is_converged(window=4, threshold=0.0005):
                print(f"\n  🏁 Converged at generation {gen} — stopping early.")
                break

            # Next generation (except last)
            if gen < self.n_gens:
                pop = self.ops.next_generation(pop, self.pop_size, self.n_elites)

        total_time = time.time() - t0
        champion   = all_time_best or pop[0]
        self.history.champion = champion

        print(f"\n  🏆  CHAMPION: {champion.summary()}")
        print(f"  ⏱  Total time: {total_time:.1f}s")
        print("═" * 60)

        # Promote champion
        promote_champion(champion, self.history)

        return champion

    def _print_generation(
        self,
        gen  : int,
        pop  : List[StrategyGenome],
        dt   : float,
    ) -> None:
        """Print a summary row for the current generation."""
        top3    = pop[:3]
        fitnesses = [g.fitness for g in pop if g.evaluated]
        mean_fit  = np.mean(fitnesses) if fitnesses else 0.0
        print(
            f"  Gen {gen:3d} | "
            f"best={pop[0].fitness:.4f}  "
            f"mean={mean_fit:.4f}  "
            f"wr={pop[0].win_rate_pct:.1f}%  "
            f"pf={pop[0].profit_factor:.2f}  "
            f"n={pop[0].n_trades:4d}  "
            f"[{dt:.1f}s]"
        )


# ──────────────────────────────────────────────────────────────────
# 9. Champion persistence — Redis + file backup
# ──────────────────────────────────────────────────────────────────

def promote_champion(
    champion : StrategyGenome,
    history  : Optional[EvolutionHistory] = None,
) -> None:
    """
    Lưu champion genome lên Redis + file backup.
    decision_engine.py sẽ đọc và áp dụng tham số này.
    """
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        payload = champion.to_dict()
        r.set(_REDIS_CHAMPION_KEY, json.dumps(payload))

        # Save history summary
        if history and history.generations:
            hist_data = [
                {
                    "generation"   : s.generation,
                    "best_fitness" : s.best_fitness,
                    "mean_fitness" : s.mean_fitness,
                    "best_win_rate": s.best_win_rate,
                    "best_pf"      : s.best_pf,
                    "champion_id"  : s.champion_id,
                    "timestamp"    : s.timestamp,
                }
                for s in history.generations
            ]
            r.set(_REDIS_EVOL_HISTORY, json.dumps(hist_data[-50:]))  # Keep last 50 gens

        print(f"  [Evolution] Champion promoted: {champion.genome_id} "
              f"fitness={champion.fitness:.4f}")
    except Exception as exc:
        print(f"  [Evolution] Warning: could not save champion to Redis: {exc}")

    # File backup
    try:
        os.makedirs("models", exist_ok=True)
        with open("models/champion_genome.json", "w") as f:
            json.dump(champion.to_dict(), f, indent=2)
    except Exception:
        pass


def load_champion() -> Optional[StrategyGenome]:
    """Load champion genome from Redis (or file fallback)."""
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw = r.get(_REDIS_CHAMPION_KEY)
        if raw:
            return StrategyGenome.from_dict(json.loads(raw))
    except Exception:
        pass

    # File fallback
    try:
        with open("models/champion_genome.json") as f:
            return StrategyGenome.from_dict(json.load(f))
    except Exception:
        return None


def get_evolution_status() -> dict:
    """Return evolution history summary and current champion from Redis."""
    status: dict = {
        "champion" : None,
        "history"  : [],
        "status"   : "no_data",
    }
    try:
        import redis as _redis
        r = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw_champ = r.get(_REDIS_CHAMPION_KEY)
        if raw_champ:
            champ_data = json.loads(raw_champ)
            status["champion"] = {
                k: champ_data.get(k)
                for k in [
                    "genome_id", "generation", "fitness", "win_rate_pct",
                    "profit_factor", "n_trades", "min_signal_score",
                    "lookahead_candles", "rsi_oversold", "rsi_overbought",
                    "rsi_weight", "momentum_weight", "macd_weight",
                    "bb_weight", "wave_weight",
                ]
            }
            status["status"] = "champion_available"

        raw_hist = r.get(_REDIS_EVOL_HISTORY)
        if raw_hist:
            status["history"] = json.loads(raw_hist)
    except Exception:
        pass
    return status


# ──────────────────────────────────────────────────────────────────
# 10. Top-level convenience functions
# ──────────────────────────────────────────────────────────────────

def run_evolution_cycle(
    generations : int  = None,
    pop_size    : int  = None,
    n_envs      : int  = None,
    env_candles : int  = None,
    seed        : int  = 42,
    verbose     : bool = True,
) -> StrategyGenome:
    """
    Run one full evolution cycle and return champion.

    Parameters
    ----------
    generations : Number of generations (default: config.EVOL_GENERATIONS)
    pop_size    : Population size per generation (default: config.EVOL_POP_SIZE)
    n_envs      : Number of market environments (default: config.EVOL_N_ENVIRONMENTS)
    env_candles : Candles per environment (default: config.EVOL_ENV_CANDLES)
    seed        : Random seed for reproducibility
    verbose     : Print per-generation stats

    Returns
    -------
    Champion StrategyGenome with best fitness over all generations.

    Ví dụ:
    ------
    >>> from evolution_engine import run_evolution_cycle
    >>> champion = run_evolution_cycle(generations=15, pop_size=40)
    >>> print(champion.summary())
    """
    engine = EvolutionEngine(
        pop_size    = pop_size,
        n_gens      = generations,
        n_envs      = n_envs,
        env_candles = env_candles,
        seed        = seed,
        verbose     = verbose,
    )
    return engine.run()


def apply_champion_to_config() -> Optional[StrategyGenome]:
    """
    Load champion genome and apply its key params to the running config.
    Called by decision_engine at the start of each cycle.

    Returns the champion genome, or None if no champion exists.
    """
    champion = load_champion()
    if champion is None:
        return None

    # Apply only params with sufficient fitness evidence
    if champion.fitness < 0.01:
        return None

    # Apply key params
    config.MIN_SIGNAL_SCORE  = float(champion.min_signal_score)
    config.RSI_OVERSOLD      = float(champion.rsi_oversold)
    config.RSI_OVERBOUGHT    = float(champion.rsi_overbought)
    config.SIM_LOOKAHEAD_CANDLES = int(champion.lookahead_candles)
    return champion


# ──────────────────────────────────────────────────────────────────
# CLI — Run evolution from command line
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Self-Play Evolution Engine")
    parser.add_argument("--mode",       choices=["run", "status", "demo", "arena_test"],
                        default="demo")
    parser.add_argument("--gens",       type=int, default=None, help="Generations")
    parser.add_argument("--pop",        type=int, default=None, help="Population size")
    parser.add_argument("--envs",       type=int, default=None, help="N environments")
    parser.add_argument("--candles",    type=int, default=None, help="Candles per env")
    parser.add_argument("--seed",       type=int, default=42)
    parser.add_argument("--verbose",    action="store_true", default=True)
    args = parser.parse_args()

    if args.mode == "demo":
        print("\n═══ SELF-PLAY EVOLUTION DEMO ═══")
        print("Quick run: 3 generations, pop=10, 4 envs, 150 candles\n")
        champion = run_evolution_cycle(
            generations = args.gens or 3,
            pop_size    = args.pop  or 10,
            n_envs      = args.envs or 4,
            env_candles = args.candles or 150,
            seed        = args.seed,
            verbose     = args.verbose,
        )
        print(f"\nChampion genes:")
        for k, v in champion.genes().items():
            print(f"  {k:22s} = {v:.4f}")

    elif args.mode == "run":
        print("\n═══ FULL EVOLUTION RUN ═══")
        champion = run_evolution_cycle(
            generations = args.gens,
            pop_size    = args.pop,
            n_envs      = args.envs,
            env_candles = args.candles,
            seed        = args.seed,
            verbose     = args.verbose,
        )
        print(f"\nFinal champion: {champion.summary()}")

    elif args.mode == "status":
        print("\n═══ EVOLUTION STATUS ═══")
        status = get_evolution_status()
        if status["champion"]:
            c = status["champion"]
            print(f"Champion: {c['genome_id']}")
            print(f"  fitness={c['fitness']:.4f}  wr={c['win_rate_pct']:.1f}%  pf={c['profit_factor']:.2f}")
            print(f"  min_score={c['min_signal_score']:.1f}  lookahead={c['lookahead_candles']}")
            print(f"\nHistory ({len(status['history'])} generations):")
            for row in status["history"][-5:]:
                print(f"  Gen {row['generation']:3d}: best={row['best_fitness']:.4f}  "
                      f"mean={row['mean_fitness']:.4f}  wr={row['best_win_rate']:.1f}%")
        else:
            print("No champion found — run evolution first.")

    elif args.mode == "arena_test":
        print("\n═══ ARENA TEST ═══")
        from synthetic_engine import CandleGenerator
        gen = CandleGenerator(seed=42)

        # Create 3 test genomes
        ops = GeneticOperators(seed=42)
        g_base   = StrategyGenome.from_config()
        g_random = ops.generate_random()
        g_aggro  = StrategyGenome(
            genome_id="aggressive", min_signal_score=45.0, wave_weight=2.0,
            rsi_weight=1.5, macd_weight=1.5
        )

        arena = SelfPlayArena(n_envs=4, candles_per_env=150, seed=42)
        pop   = arena.run([g_base, g_random, g_aggro], verbose=True)
        print("\nLeaderboard:")
        for rank, g in enumerate(pop, 1):
            print(f"  #{rank}: {g.summary()}")
