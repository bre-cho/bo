"""
gametheory_engine.py
====================
MULTI-AGENT GAME THEORY + MARKET ECOSYSTEM ENGINE

Bước chuyển từ rational agent → strategic ecosystem intelligence.

Vấn đề với single-agent optimization (utility_engine.py):
  • Tối ưu giả định môi trường là cố định (stationary)
  • Không mô hình hóa phản ứng của đối thủ / nền tảng
  • Bỏ qua "crowding effect": nhiều bot cùng chiến lược → edge suy giảm
  • Platform algorithm có thể adapt chống lại strategy pattern của chúng ta

Multi-Agent Framework:
  • Ecosystem = us + opponent bots + platform algorithm
  • Mỗi player có strategy type + action distribution
  • Game lặp lại (repeated game) → học + adapt lẫn nhau
  • Nash Equilibrium: không ai muốn unilaterally deviate

Ba tầng phân tích:
  1. Payoff Matrix Analysis (game theory classical):
     - 3×3 matrix: actions {CALL, PUT, SKIP}
     - Crowding discount: cùng side → reduced payout
     - Dominance elimination → simplify game
     - Nash Equilibrium: mixed strategy support enumeration

  2. Bayesian Opponent Modeling (belief updating):
     - Prior: P(opponent_type) = uniform over known types
     - Likelihood: P(action | opponent_type) = type-specific distribution
     - Posterior: Dirichlet-Categorical conjugate update
     - Best response: argmax_a Σ_t P(t) × E[payoff | a, t]

  3. Regret Minimization — EXP3 (adversarial bandit):
     - Worst-case optimal algorithm for non-stationary adversary
     - Weight update: w_k ← w_k × exp(η × r̂_k)  where r̂_k = r_k / p_k
     - Mixed strategy: p_k = (1-γ)/K × w_k/Σw + γ/K
     - Cumulative regret bound: O(√(K·T·ln(K)))
     - Used when opponent is adversarial / platform adapts against us

Thành phần:
  AgentType          — enum: BOT, TREND_FOLLOWER, MEAN_REVERTER, MOMENTUM, RANDOM, PLATFORM
  ActionType         — enum: CALL=0, PUT=1, SKIP=2
  PayoffMatrix       — strategic-form matrix + dominance elimination
  NashSolver         — pure + mixed Nash equilibria
  OpponentModel      — Bayesian belief + Dirichlet posterior update
  EXP3Optimizer      — adversarial bandit with exponential weights
  EcosystemSimulator — N-round multi-agent repeated game
  PlatformPressureDetector — statistical platform adaptation detection
  GameTheoryReport   — full report dataclass
  GameTheoryEngine   — orchestrator
  get_gametheory_report()  — API helper
  run_gametheory_analysis()— entry point

Cách dùng:
  >>> from gametheory_engine import run_gametheory_analysis
  >>> report = run_gametheory_analysis()
  >>> print(report.nash_strategy)   # recommended mixed strategy
  >>> print(report.exp3_weights)    # EXP3 current action weights
  >>> print(report.insights)         # ecosystem intelligence
"""

from __future__ import annotations

import copy
import json
import math
import os
import random
import uuid
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config


# ──────────────────────────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────────────────────────

_REDIS_GAMETHEORY_KEY   = "Deriv_GameTheory_Report"
_REDIS_EXP3_STATE_KEY   = "Deriv_EXP3_State"


# ──────────────────────────────────────────────────────────────────
# 1. Enumerations
# ──────────────────────────────────────────────────────────────────

class ActionType(IntEnum):
    """The three pure strategies available to each agent."""
    CALL = 0   # Bet price goes UP
    PUT  = 1   # Bet price goes DOWN
    SKIP = 2   # No bet (sit out this round)

    @classmethod
    def names(cls) -> List[str]:
        return ["CALL", "PUT", "SKIP"]


class AgentType(IntEnum):
    """
    Archetypes of market participants in the ecosystem.

    Each type has a characteristic distribution over actions P(action | type).
    Used for Bayesian opponent modeling.
    """
    OUR_BOT       = 0  # Us — optimized by utility_engine
    TREND_FOLLOWER = 1  # Follows recent price direction → CALL in up-trend, PUT in down-trend
    MEAN_REVERTER  = 2  # Bets against extremes → PUT at peaks, CALL at troughs
    MOMENTUM       = 3  # Like trend follower but chases momentum bursts
    RANDOM_BOT     = 4  # No pattern → uniform distribution
    PLATFORM       = 5  # The platform algorithm → adversarial response

    @classmethod
    def opponent_types(cls) -> List["AgentType"]:
        """All opponent types (not us, not platform)."""
        return [cls.TREND_FOLLOWER, cls.MEAN_REVERTER, cls.MOMENTUM, cls.RANDOM_BOT]


# ──────────────────────────────────────────────────────────────────
# 2. PayoffMatrix — strategic-form game representation
# ──────────────────────────────────────────────────────────────────

class PayoffMatrix:
    """
    2-player strategic-form payoff matrix.

    Rows: our actions {CALL, PUT, SKIP}
    Cols: opponent actions {CALL, PUT, SKIP}
    Entry: our expected payoff per unit stake

    Economic model:
      base_payoff = base_win_prob × payout - (1 - base_win_prob)
      crowding_discount: if both players bet same direction,
        payout is reduced by GAME_CROWDING_DISCOUNT (e.g., 0.10)
        because platform sees more action on that side

    SKIP always yields 0 (no stake, no reward).
    Platform is modeled as a stochastic "nature" + strategic responder.
    """

    K = 3  # number of actions

    def __init__(
        self,
        base_win_prob      : float = None,
        payout             : float = None,
        crowding_discount  : float = None,
        platform_pressure  : float = 0.0,
    ) -> None:
        self._p   = float(base_win_prob or getattr(config, "GAME_BASE_WIN_PROB", 0.55))
        self._b   = float(payout or getattr(config, "SIM_PAYOUT_RATIO", 0.85))
        self._cd  = float(crowding_discount or getattr(config, "GAME_CROWDING_DISCOUNT", 0.10))
        self._pp  = float(platform_pressure)
        self._M   = self._build()

    def _build(self) -> np.ndarray:
        """
        Construct 3×3 payoff matrix for our agent.

        Rows: us (CALL, PUT, SKIP)
        Cols: opponent (CALL, PUT, SKIP)

        Our payoff per trade:
          hit_prob(direction, vs_opponent) × effective_payout
            - (1 - hit_prob) × stake_loss

        Where:
          effective_payout = b × (1 - crowding_discount)  if both same side
                           = b                             otherwise
          platform_pressure: reduces effective win_prob when platform adapts
            effective_p = p × (1 - platform_pressure)
        """
        p  = self._p  * (1.0 - self._pp)
        q  = 1.0 - p
        b  = self._b
        cd = self._cd

        # Our payoff for each (us, opponent) pair
        # (CALL, CALL): crowded same side → reduced payout
        # (CALL, PUT):  no crowding
        # (CALL, SKIP): no crowding
        # (PUT, CALL):  no crowding (opponent on other side)
        # (PUT, PUT):   crowded same side → reduced payout
        # (PUT, SKIP):  no crowding
        # (SKIP, *):    0 (no bet)

        def payoff(our_action: int, opp_action: int) -> float:
            if our_action == ActionType.SKIP:
                return 0.0
            crowded = (our_action == opp_action) and (opp_action != ActionType.SKIP)
            eff_b   = b * (1.0 - cd) if crowded else b
            # For CALL: we win if price goes up (prob p); for PUT: we win if price goes down (prob q)
            win_p   = p if our_action == ActionType.CALL else q
            return win_p * eff_b - (1.0 - win_p)

        M = np.array([[payoff(r, c) for c in range(self.K)] for r in range(self.K)])
        return M

    @property
    def matrix(self) -> np.ndarray:
        return self._M.copy()

    def eliminate_dominated(self) -> Tuple[np.ndarray, List[int], List[int]]:
        """
        Iteratively eliminate strictly dominated strategies.

        Returns (reduced_matrix, remaining_row_indices, remaining_col_indices).
        """
        M     = self._M.copy()
        rows  = list(range(self.K))
        cols  = list(range(self.K))

        changed = True
        while changed:
            changed = False
            # Eliminate dominated rows (our strictly dominated strategies)
            to_remove = []
            for i in range(len(rows)):
                dominated = any(
                    all(M[j, c] > M[i, c] for c in range(len(cols)))
                    for j in range(len(rows)) if j != i
                )
                if dominated:
                    to_remove.append(i)
                    changed = True
            if to_remove:
                keep  = [i for i in range(len(rows)) if i not in to_remove]
                M     = M[np.ix_(keep, list(range(len(cols))))]
                rows  = [rows[i] for i in keep]
            # Eliminate dominated columns (opponent's strictly dominated strategies)
            # Opponent payoff = -our payoff (zero-sum approximation)
            to_remove = []
            for j in range(len(cols)):
                # Col j dominated if ∃ col k: M[:, k] < M[:, j] (bad for opponent means good for us)
                dominated = any(
                    all(M[i, k] < M[i, j] for i in range(len(rows)))
                    for k in range(len(cols)) if k != j
                )
                if dominated:
                    to_remove.append(j)
                    changed = True
            if to_remove:
                keep  = [j for j in range(len(cols)) if j not in to_remove]
                M     = M[np.ix_(list(range(len(rows))), keep)]
                cols  = [cols[j] for j in keep]

        return M, rows, cols

    def pure_nash_equilibria(self) -> List[Tuple[int, int]]:
        """
        Find all pure Nash equilibria.

        (i, j) is a pure NE if:
          - i is best response to j (max over rows given col j)
          - j is best response to i (min over cols given row i — zero-sum)
        """
        M   = self._M
        ne  = []
        for i in range(self.K):
            for j in range(self.K):
                # Our best response to j: max over rows
                if M[i, j] == M[:, j].max():
                    # Opponent's best response to i: min over cols (zero-sum)
                    if M[i, j] == M[i, :].min():
                        ne.append((i, j))
        return ne

    def expected_payoff(self, our_mix: np.ndarray, opp_mix: np.ndarray) -> float:
        """
        Compute expected payoff under mixed strategies.

        our_mix, opp_mix: probability vectors over {CALL, PUT, SKIP}
        """
        return float(our_mix @ self._M @ opp_mix)

    def best_response(self, opp_mix: np.ndarray) -> int:
        """Return pure best response (action index) against opponent mixed strategy."""
        return int(np.argmax(self._M @ opp_mix))


# ──────────────────────────────────────────────────────────────────
# 3. NashSolver — mixed Nash equilibrium computation
# ──────────────────────────────────────────────────────────────────

class NashSolver:
    """
    Find mixed Nash equilibria in 2-player finite games.

    Algorithm: Support Enumeration + Indifference Conditions
      For each pair of supports (S_r, S_c) where S_r ⊆ rows, S_c ⊆ cols:
        1. Find mixed x such that: A[S_r, S_c]^T · x = v · 1_S_r,  Σx=1, x≥0
        2. Find mixed y such that: A[S_r, S_c] · y = v · 1_S_c,    Σy=1, y≥0
        3. Verify no profitable deviation outside support

    Fallback: Fictitious Play (iterative best response)
      - Converges to Nash in zero-sum games (guaranteed)
      - In practice stops after GAME_FP_ITERATIONS iterations

    Returns list of (our_strategy, opponent_strategy, payoff) tuples.
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng = np.random.default_rng(seed)

    def solve(
        self,
        matrix   : PayoffMatrix,
        max_ne   : int = 3,
    ) -> List[dict]:
        """
        Solve for Nash equilibria. Returns list of NE dicts.

        Each dict: {our_strategy, opp_strategy, payoff, type, support}
        """
        M    = matrix.matrix
        K    = matrix.K
        ne_list: List[dict] = []

        # 1. Pure NE
        pure_nes = matrix.pure_nash_equilibria()
        for (i, j) in pure_nes:
            x = np.zeros(K); x[i] = 1.0
            y = np.zeros(K); y[j] = 1.0
            ne_list.append({
                "type"           : "pure",
                "our_strategy"   : x.tolist(),
                "opp_strategy"   : y.tolist(),
                "payoff"         : round(float(M[i, j]), 5),
                "support_us"     : [ActionType.names()[i]],
                "support_opp"    : [ActionType.names()[j]],
            })

        # 2. Mixed NE via support enumeration (for non-degenerate 2×2 case)
        if K == 3 and not pure_nes:
            mixed = self._support_enumeration(M)
            ne_list.extend(mixed)

        # 3. Fallback: Fictitious Play
        if not ne_list:
            fp = self._fictitious_play(M)
            if fp:
                ne_list.append(fp)

        return ne_list[:max_ne]

    def _support_enumeration(self, M: np.ndarray) -> List[dict]:
        """
        Support enumeration for 3×3 matrix game.

        Try all non-empty support subsets of size 2 for both players.
        Full 3×3 support as well.
        """
        from itertools import combinations
        K       = M.shape[0]
        names   = ActionType.names()
        results = []

        for r_size in range(2, K + 1):
            for c_size in range(2, K + 1):
                for r_supp in combinations(range(K), r_size):
                    for c_supp in combinations(range(K), c_size):
                        ne = self._check_support(M, list(r_supp), list(c_supp))
                        if ne:
                            results.append({
                                "type"         : "mixed",
                                "our_strategy" : ne[0].tolist(),
                                "opp_strategy" : ne[1].tolist(),
                                "payoff"       : round(ne[2], 5),
                                "support_us"   : [names[i] for i in r_supp],
                                "support_opp"  : [names[j] for j in c_supp],
                            })
                            if len(results) >= 3:
                                return results
        return results

    def _check_support(
        self,
        M     : np.ndarray,
        r_supp: List[int],
        c_supp: List[int],
    ) -> Optional[Tuple[np.ndarray, np.ndarray, float]]:
        """
        Check if (r_supp, c_supp) constitutes a mixed Nash equilibrium.

        Conditions:
          A[r_supp, c_supp]^T · x_supp = v · 1  → opponent indifferent
          A[r_supp, c_supp]   · y_supp = w · 1  → we are indifferent (zero-sum: w = -v)
          All non-support actions must not be profitable deviations.
        """
        rs = len(r_supp)
        cs = len(c_supp)
        A  = M[np.ix_(r_supp, c_supp)]

        # Solve for our mixed strategy x over r_supp:
        # A^T · x = v → A^T · x - v·1 = 0
        # [A^T | -1] · [x; v] = 0,  with Σx = 1
        # Setup: A_r (cs × rs) system for x
        # Rows of A^T (cs × rs): A[r, c] for c in c_supp, r in r_supp
        # We want: for each c in c_supp, Σ_r x_r A[r,c] = v
        #        → A^T x = v·1 → (A^T | -1_cs) [x; -v] = 0
        #        → augment with Σx = 1

        try:
            # Solve for opponent's strategy y over c_supp s.t. A·y = v·1_rs, Σy=1
            # A (rs × cs): A · y = v → [A | -1_rs] [y; -v] = 0, Σy=1
            # → least squares
            y, v = self._solve_indifference(A, cs)
            if y is None:
                return None

            x, w = self._solve_indifference(A.T, rs)
            if x is None:
                return None

            # Build full-size strategies
            x_full = np.zeros(M.shape[0])
            y_full = np.zeros(M.shape[1])
            for i, ri in enumerate(r_supp):
                x_full[ri] = x[i]
            for j, cj in enumerate(c_supp):
                y_full[cj] = y[j]

            # Verify: non-support actions must not be profitable deviations
            for r in range(M.shape[0]):
                if r not in r_supp:
                    if M[r, :] @ y_full > v + 1e-7:
                        return None

            return x_full, y_full, float(x_full @ M @ y_full)

        except Exception:
            return None

    def _solve_indifference(
        self,
        A  : np.ndarray,
        k  : int,
    ) -> Tuple[Optional[np.ndarray], float]:
        """
        Solve A · x = v·1, Σx = 1, x >= 0.

        Returns (x, v) or (None, 0) if no valid solution.
        """
        # Reformulate: A·x = v·1, Σx = 1
        # → [A - 1·A[0]] · x = 0, x[0] = 1 - Σ_{i>0} x_i
        # Direct: use least-norm solution via pseudoinverse
        n = A.shape[1]
        # Augment system: rows of [A; 1^T], RHS = [v·1; 1]
        # Let z = [x; v], dim = n+1
        # [A | -1] · z = 0, [1^T | 0] · z = 1
        C = np.hstack([A, -np.ones((A.shape[0], 1))])
        # Null space of C, constrained to Σx=1, x>=0
        # Use lstsq with normalization constraint
        b = np.zeros(A.shape[0])
        # Add constraint row: [1,...,1, 0] z = 1
        C_aug = np.vstack([C, np.append(np.ones(n), 0)])
        b_aug = np.append(b, 1.0)
        z, res, rank, sv = np.linalg.lstsq(C_aug, b_aug, rcond=None)
        x = z[:n]
        v = float(-z[n])

        if np.any(x < -1e-8) or abs(sum(x) - 1.0) > 1e-6:
            return None, 0.0
        x = np.clip(x, 0, 1)
        x /= x.sum()
        return x, v

    def _fictitious_play(
        self,
        M   : np.ndarray,
        T   : int = None,
    ) -> Optional[dict]:
        """
        Fictitious play: each player best-responds to opponent's empirical average.

        Converges to Nash in zero-sum games.
        Returns last-round strategies as approximate Nash.
        """
        T     = T or getattr(config, "GAME_FP_ITERATIONS", 500)
        K     = M.shape[0]
        names = ActionType.names()

        freq_row = np.ones(K) / K
        freq_col = np.ones(K) / K
        cnt_row  = np.ones(K)
        cnt_col  = np.ones(K)

        for _ in range(T):
            br_row = int(np.argmax(M @ freq_col))
            br_col = int(np.argmin(M[br_row, :] - np.max(M[br_row, :])))  # opponent minimizes
            br_col = int(np.argmax(-M.T @ freq_row))
            cnt_row[br_row] += 1
            cnt_col[br_col] += 1
            freq_row = cnt_row / cnt_row.sum()
            freq_col = cnt_col / cnt_col.sum()

        payoff = float(freq_row @ M @ freq_col)
        return {
            "type"         : "fictitious_play",
            "our_strategy" : freq_row.tolist(),
            "opp_strategy" : freq_col.tolist(),
            "payoff"       : round(payoff, 5),
            "support_us"   : [names[i] for i in range(K) if freq_row[i] > 0.05],
            "support_opp"  : [names[j] for j in range(K) if freq_col[j] > 0.05],
        }

    def recommended_action(self, nash_list: List[dict]) -> str:
        """
        Given Nash solutions, recommend a concrete action.

        If Nash is mixed, sample from the distribution.
        If Nash has SKIP dominant, that's notable.
        """
        if not nash_list:
            return "SKIP"
        ne = nash_list[0]
        probs = ne.get("our_strategy", [1/3, 1/3, 1/3])
        if all(abs(p - probs[0]) < 0.01 for p in probs):
            return "SKIP"  # uniform → no advantage
        action_idx = int(np.random.default_rng().choice(3, p=np.array(probs) / sum(probs)))
        return ActionType.names()[action_idx]


# ──────────────────────────────────────────────────────────────────
# 4. OpponentModel — Bayesian belief over opponent type
# ──────────────────────────────────────────────────────────────────

# Characteristic action distributions for each opponent type
# P(action | type) = [P(CALL), P(PUT), P(SKIP)]
OPPONENT_LIKELIHOODS: Dict[int, np.ndarray] = {
    AgentType.TREND_FOLLOWER : np.array([0.55, 0.35, 0.10]),  # biased toward CALL in trending
    AgentType.MEAN_REVERTER  : np.array([0.35, 0.55, 0.10]),  # biased toward PUT (contrarian)
    AgentType.MOMENTUM       : np.array([0.60, 0.25, 0.15]),  # strong bias toward trend
    AgentType.RANDOM_BOT     : np.array([0.33, 0.33, 0.34]),  # uniform
}


class OpponentModel:
    """
    Bayesian opponent model using Dirichlet-Categorical conjugate.

    Prior: uniform over opponent types (AgentType opponents)
    Update: observe action → multiply likelihood × prior → renormalize

    Also tracks:
      - Empirical action frequency (counts per action)
      - Surprise score: KL divergence between expected and observed
      - Concentration: how confident we are in opponent type
    """

    def __init__(self, seed: int = 42) -> None:
        self._types   = AgentType.opponent_types()
        n             = len(self._types)
        self._prior   = np.ones(n) / n           # uniform prior over types
        self._beliefs = self._prior.copy()        # posterior
        self._counts  = np.zeros(3)              # action observation counts [CALL, PUT, SKIP]
        self._n_obs   = 0
        self._rng     = np.random.default_rng(seed)

    def update(self, observed_action: int) -> None:
        """
        Update posterior beliefs given one observed opponent action.

        Bayesian update: P(type | a) ∝ P(a | type) × P(type)
        """
        if observed_action not in range(3):
            return
        likelihoods = np.array([
            float(OPPONENT_LIKELIHOODS[int(t)][observed_action])
            for t in self._types
        ])
        self._beliefs = self._beliefs * likelihoods
        total = self._beliefs.sum()
        if total > 1e-12:
            self._beliefs /= total
        else:
            self._beliefs = self._prior.copy()

        self._counts[observed_action] += 1
        self._n_obs += 1

    def update_batch(self, actions: List[int]) -> None:
        """Update beliefs from a sequence of observed actions."""
        for a in actions:
            self.update(a)

    def predicted_action_dist(self) -> np.ndarray:
        """
        Posterior predictive: P(next_action) = Σ_t P(t) × P(action | t)

        Returns probability vector over [CALL, PUT, SKIP].
        """
        pred = np.zeros(3)
        for i, t in enumerate(self._types):
            pred += self._beliefs[i] * OPPONENT_LIKELIHOODS[int(t)]
        return pred / pred.sum()

    def best_response(self, matrix: PayoffMatrix) -> int:
        """
        Our best response against the predicted opponent distribution.

        Returns action index (CALL=0, PUT=1, SKIP=2).
        """
        opp_dist = self.predicted_action_dist()
        return matrix.best_response(opp_dist)

    def dominant_type(self) -> Tuple[str, float]:
        """Return (type_name, probability) of most likely opponent type."""
        idx = int(np.argmax(self._beliefs))
        t   = self._types[idx]
        return AgentType(t).name, round(float(self._beliefs[idx]), 4)

    def concentration(self) -> float:
        """
        How concentrated the posterior is.

        1.0 = perfectly certain about type (Dirac)
        0.0 = uniform (no information)
        """
        K    = len(self._beliefs)
        h    = -np.sum(self._beliefs * np.log(self._beliefs + 1e-12))
        h_max = math.log(K)
        return round(1.0 - h / (h_max + 1e-12), 4)

    def surprise_score(self, action: int) -> float:
        """
        Self-information of observing this action under current beliefs.

        High surprise → action inconsistent with our model → opponent may be adapting.
        """
        p = float(self.predicted_action_dist()[action])
        return round(-math.log(p + 1e-12), 4)

    def to_dict(self) -> dict:
        return {
            "beliefs"       : {
                AgentType(int(t)).name: round(float(b), 4)
                for t, b in zip(self._types, self._beliefs)
            },
            "dominant_type" : self.dominant_type(),
            "concentration" : self.concentration(),
            "n_observations": self._n_obs,
            "action_counts" : {
                ActionType.names()[i]: int(self._counts[i]) for i in range(3)
            },
            "predicted_next": {
                ActionType.names()[i]: round(float(v), 4)
                for i, v in enumerate(self.predicted_action_dist())
            },
        }

    def reset(self) -> None:
        """Reset to uniform prior."""
        n             = len(self._types)
        self._beliefs = self._prior.copy()
        self._counts  = np.zeros(3)
        self._n_obs   = 0


# ──────────────────────────────────────────────────────────────────
# 5. EXP3Optimizer — adversarial bandit with exponential weights
# ──────────────────────────────────────────────────────────────────

class EXP3Optimizer:
    """
    EXP3: Exponential-weight algorithm for Exploration and Exploitation.

    Designed for adversarial (worst-case) opponents — guarantees sub-linear
    regret even when opponent adapts against us.

    Algorithm:
      Initialization: w_k = 1 for all k ∈ {CALL, PUT, SKIP}
      Each round t:
        1. Mixed strategy: p_k = (1-γ)/K × (w_k / Σw) + γ/K
        2. Sample action A_t ~ p  (or use argmax for deterministic mode)
        3. Observe reward r_t ∈ [-1, +payout]
        4. Importance-weighted reward: r̂_k = r_t / p_k  (only for chosen k)
        5. Weight update: w_k ← w_k × exp(η × r̂_k / K)
      where γ = exploration rate, η = learning rate

    Regret bound: E[max_k Σ r_k(t)] − E[Σ r_{At}(t)] ≤ O(√(K·T·ln(K)))

    Persistence: weights saved to Redis for warm-start across sessions.
    """

    def __init__(self, K: int = 3, seed: int = 42) -> None:
        self._K     = K
        self._rng   = np.random.default_rng(seed)
        self._gamma = float(getattr(config, "GAME_EXP3_GAMMA", 0.10))  # exploration
        self._eta   = float(getattr(config, "GAME_EXP3_ETA",   0.10))  # learning rate
        self._w     = np.ones(K)   # weights (unnormalized)
        self._t     = 0            # round counter
        # Tracking
        self._cumulative_rewards : List[float] = []
        self._regret_history     : List[float] = []
        self._best_arm_reward    : float = 0.0  # oracle best arm in hindsight

        # Try load from Redis
        self._load()

    def mixed_strategy(self) -> np.ndarray:
        """
        Compute current mixed strategy probability vector.

        p_k = (1-γ) × w_k/Σw + γ/K
        """
        W  = self._w.sum()
        p  = (1.0 - self._gamma) * self._w / (W + 1e-12) + self._gamma / self._K
        return p / p.sum()

    def sample_action(self) -> int:
        """Sample action from current mixed strategy."""
        p = self.mixed_strategy()
        return int(self._rng.choice(self._K, p=p))

    def greedy_action(self) -> int:
        """Return highest-weight action (greedy/exploitation)."""
        return int(np.argmax(self._w))

    def update(self, action: int, reward: float) -> None:
        """
        Update weights after observing reward for the chosen action.

        Parameters
        ----------
        action : 0=CALL, 1=PUT, 2=SKIP
        reward : observed return (e.g., +0.85 for win, -1 for loss, 0 for skip)
        """
        p    = self.mixed_strategy()
        rhat = reward / (p[action] + 1e-12)     # importance-weighted
        self._w[action] *= math.exp(self._eta * rhat / self._K)
        # Clip weights to prevent overflow/underflow
        self._w = np.clip(self._w, 1e-12, 1e12)
        # Normalize periodically
        if self._t % 100 == 0:
            self._w /= self._w.max()

        self._t += 1
        self._cumulative_rewards.append(reward)
        # Pseudo-regret: best single arm vs EXP3
        self._best_arm_reward = max(
            self._best_arm_reward,
            float(np.array(self._cumulative_rewards[-100:]).mean()) if self._cumulative_rewards else 0.0
        )

        self._save()

    def update_batch(self, actions: List[int], rewards: List[float]) -> None:
        """Batch update from a sequence of (action, reward) pairs."""
        for a, r in zip(actions, rewards):
            self.update(a, r)

    def estimated_regret(self) -> float:
        """
        Estimated cumulative regret so far.

        Regret ≈ T × best_arm_rate - Σ r_t
        """
        if not self._cumulative_rewards:
            return 0.0
        T        = len(self._cumulative_rewards)
        total_r  = sum(self._cumulative_rewards)
        best_per_round = self._best_arm_reward
        return round(max(0.0, T * best_per_round - total_r), 4)

    def regret_bound(self) -> float:
        """Theoretical EXP3 regret bound: O(√(K·T·ln(K)))."""
        T = max(1, self._t)
        K = self._K
        return round(math.sqrt(K * T * math.log(K + 1)), 2)

    @property
    def weights(self) -> np.ndarray:
        return self._w.copy()

    @property
    def rounds(self) -> int:
        return self._t

    def to_dict(self) -> dict:
        p = self.mixed_strategy()
        return {
            "weights"          : {ActionType.names()[i]: round(float(self._w[i]), 4)
                                  for i in range(self._K)},
            "mixed_strategy"   : {ActionType.names()[i]: round(float(p[i]), 4)
                                  for i in range(self._K)},
            "greedy_action"    : ActionType.names()[self.greedy_action()],
            "rounds"           : self._t,
            "estimated_regret" : self.estimated_regret(),
            "regret_bound"     : self.regret_bound(),
            "gamma"            : self._gamma,
            "eta"              : self._eta,
        }

    def _save(self) -> None:
        """Persist state to Redis (fire-and-forget)."""
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            state = {
                "w": self._w.tolist(), "t": self._t,
                "gamma": self._gamma, "eta": self._eta,
            }
            r.set(_REDIS_EXP3_STATE_KEY, json.dumps(state))
        except Exception:
            pass

    def _load(self) -> None:
        """Load persisted state from Redis if available."""
        try:
            import redis as _redis
            r   = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.get(_REDIS_EXP3_STATE_KEY)
            if raw:
                state        = json.loads(raw)
                self._w      = np.array(state.get("w", [1.0, 1.0, 1.0]))
                self._t      = state.get("t", 0)
                self._gamma  = state.get("gamma", self._gamma)
                self._eta    = state.get("eta",   self._eta)
        except Exception:
            pass

    def reset(self) -> None:
        """Reset to uniform weights."""
        self._w = np.ones(self._K)
        self._t = 0
        self._cumulative_rewards = []


# ──────────────────────────────────────────────────────────────────
# 6. PlatformPressureDetector — detect platform adaptation
# ──────────────────────────────────────────────────────────────────

class PlatformPressureDetector:
    """
    Statistical detection of platform adapting against our strategy.

    Mechanisms the platform might use:
      1. Streak detection: if we win N in a row, next trade more likely to lose
      2. Payout adjustment: reduce payout ratio when our win rate is high
      3. Timing pressure: trades at peak win-rate times increasingly lossy

    Detection methods:
      - Win rate drift test: compare recent vs historical win rate (Z-test)
      - Streak pattern: P(loss | prev N wins) vs P(loss) — Bayesian test
      - Implied payout estimation: back-calculate from win rate

    Output:
      pressure_score ∈ [0, 1]
      0 = no detectable pressure
      1 = strong evidence platform is adapting against us
    """

    def __init__(self) -> None:
        self._min_samples = getattr(config, "GAME_PRESSURE_MIN_SAMPLES", 20)
        self._alpha       = 0.05   # significance level

    def compute(
        self,
        trade_outcomes   : List[int],  # 1=win, 0=loss (recent-first)
        historical_wr    : float = 0.55,
        payout           : float = None,
    ) -> dict:
        """
        Compute platform pressure indicators.

        Parameters
        ----------
        trade_outcomes : Recent trade outcomes, most recent first.
        historical_wr  : Long-run win rate without pressure.
        payout         : Platform payout ratio.
        """
        payout     = payout or getattr(config, "SIM_PAYOUT_RATIO", 0.85)
        n          = len(trade_outcomes)
        pressure   = 0.0
        signals    = []

        if n < self._min_samples:
            return {
                "pressure_score"  : 0.0,
                "signals"         : ["Không đủ dữ liệu — cần ≥ {} giao dịch".format(
                    self._min_samples)],
                "win_rate_recent" : None,
                "streak_bias"     : 0.0,
                "payout_implied"  : payout,
            }

        recent_wr = sum(trade_outcomes[:20]) / 20.0  # last 20 trades

        # 1. Win rate drift: Z-test (binomial)
        n_recent  = min(50, n)
        wins      = sum(trade_outcomes[:n_recent])
        wr        = wins / n_recent
        z_score   = (wr - historical_wr) / math.sqrt(historical_wr * (1 - historical_wr) / n_recent + 1e-12)
        if z_score < -1.96:  # significant drop (95% CI)
            pressure  += 0.40
            signals.append(
                f"Win rate giảm đáng kể: {wr:.1%} vs lịch sử {historical_wr:.1%} "
                f"(Z={z_score:.2f})"
            )
        elif z_score < -1.0:
            pressure += 0.20
            signals.append(f"Win rate có xu hướng giảm: {wr:.1%} (Z={z_score:.2f})")

        # 2. Streak pattern: P(loss | prev 3 wins) vs P(loss)
        streak_bias = self._streak_analysis(trade_outcomes)
        if streak_bias > 0.20:
            pressure += 0.35
            signals.append(
                f"Pattern streak bất thường: xác suất thua sau chuỗi thắng cao hơn "
                f"{streak_bias:.1%} so với cơ sở"
            )
        elif streak_bias > 0.10:
            pressure += 0.15
            signals.append(f"Streak bias nhẹ: {streak_bias:.1%}")

        # 3. Run test for randomness (too many alternating wins/losses = suspicious)
        run_score = self._run_test(trade_outcomes[:30])
        if run_score < 0.02:  # p-value very low
            pressure += 0.25
            signals.append(
                f"Run test: chuỗi W/L quá không ngẫu nhiên (p={run_score:.3f}) "
                f"→ pattern có thể bị khai thác"
            )

        # 4. Implied payout estimate
        # If platform pressure reduces effective payout, we can estimate
        # E[payoff] = wr × payout - (1-wr) ≈ 0 at Nash → payout_implied = (1-wr)/wr
        payout_implied = (1.0 - wr) / (wr + 1e-12) if wr > 0.01 else payout
        if payout_implied < payout * 0.80:
            pressure += 0.10
            signals.append(
                f"Implied payout thấp: {payout_implied:.2f} vs nominal {payout:.2f}"
            )

        pressure = min(1.0, pressure)

        if not signals:
            signals.append("Không phát hiện áp lực từ nền tảng — edge vẫn ổn định")

        return {
            "pressure_score"  : round(pressure, 3),
            "signals"         : signals,
            "win_rate_recent" : round(recent_wr, 4),
            "win_rate_historical": round(historical_wr, 4),
            "streak_bias"     : round(streak_bias, 4),
            "payout_implied"  : round(payout_implied, 4),
            "z_score"         : round(z_score, 3),
            "n_trades"        : n,
        }

    def _streak_analysis(self, outcomes: List[int]) -> float:
        """
        Compute P(loss | prev 3 consecutive wins) − P(loss overall).

        Positive value → higher loss probability after win streaks.
        """
        if len(outcomes) < 6:
            return 0.0

        p_loss_global = 1.0 - sum(outcomes) / len(outcomes)
        streaks        = 0
        losses_after   = 0

        for i in range(3, len(outcomes)):
            if all(outcomes[j] == 1 for j in range(i - 3, i)):
                streaks += 1
                if outcomes[i] == 0:
                    losses_after += 1

        if streaks < 3:
            return 0.0

        p_loss_after_streak = losses_after / streaks
        return max(0.0, p_loss_after_streak - p_loss_global)

    def _run_test(self, outcomes: List[int]) -> float:
        """
        Runs test for independence.

        Returns approximate p-value (low = suspicious non-randomness).
        """
        if len(outcomes) < 10:
            return 1.0

        n  = len(outcomes)
        n1 = sum(outcomes)
        n0 = n - n1
        if n1 == 0 or n0 == 0:
            return 1.0

        runs  = 1
        for i in range(1, n):
            if outcomes[i] != outcomes[i - 1]:
                runs += 1

        # Expected runs and variance under H0 (randomness)
        mu    = (2 * n1 * n0) / n + 1
        var   = (2 * n1 * n0 * (2 * n1 * n0 - n)) / (n * n * (n - 1) + 1e-12)
        if var < 1e-9:
            return 1.0
        z     = (runs - mu) / math.sqrt(var)
        # Two-tailed p-value approximation (normal)
        p     = 2.0 * (1.0 - min(0.9999, abs(z) * 0.3989 * math.exp(-0.5 * z * z) * 2.0))
        return max(0.001, min(1.0, float(abs(p))))


# ──────────────────────────────────────────────────────────────────
# 7. EcosystemSimulator — multi-agent repeated game
# ──────────────────────────────────────────────────────────────────

@dataclass
class AgentState:
    """State of one agent across the ecosystem simulation."""
    agent_id     : str
    agent_type   : str
    total_pnl    : float = 0.0
    n_trades     : int   = 0
    wins         : int   = 0
    losses       : int   = 0
    actions      : List[int]  = field(default_factory=list)
    payoffs      : List[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / max(1, self.n_trades)

    def to_dict(self) -> dict:
        return {
            "agent_id"   : self.agent_id,
            "agent_type" : self.agent_type,
            "total_pnl"  : round(self.total_pnl, 4),
            "n_trades"   : self.n_trades,
            "win_rate"   : round(self.win_rate, 4),
            "last_actions": [ActionType.names()[a] for a in self.actions[-5:]],
        }


class EcosystemSimulator:
    """
    Multi-agent repeated game simulation.

    Agents:
      - Us (OUR_BOT): uses EXP3 + best response
      - N opponent bots (sampled from AgentType pool)
      - Platform (implicit): adjusts win probs based on crowding

    Each round:
      1. All agents choose actions (independently)
      2. Market outcome sampled (Bernoulli with crowding-adjusted probability)
      3. Payoffs computed; platform gets spread
      4. Beliefs and weights updated
      5. Platform pressure escalates if our win rate is high

    Key metrics:
      - Crowding index: fraction of bots on same side as us
      - Platform pressure: escalates over time if we keep winning
      - Nash equilibrium proximity: how close current strategies are to NE
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng     = np.random.default_rng(seed)
        self._payout  = getattr(config, "SIM_PAYOUT_RATIO", 0.85)
        self._base_p  = getattr(config, "GAME_BASE_WIN_PROB", 0.55)

    def run(
        self,
        n_rounds      : int  = None,
        n_opponents   : int  = None,
        opponent_types: List[int] = None,
        verbose       : bool = False,
    ) -> dict:
        """
        Run ecosystem simulation.

        Returns dict with all agent states, crowding history, pressure history.
        """
        n_rounds    = n_rounds    or getattr(config, "GAME_SIM_ROUNDS", 100)
        n_opponents = n_opponents or getattr(config, "GAME_N_OPPONENTS", 4)

        # Initialize agents
        agents: List[AgentState] = []

        # Our bot
        us = AgentState(agent_id="us", agent_type="OUR_BOT")
        agents.append(us)
        exp3 = EXP3Optimizer(seed=int(self._rng.integers(0, 10000)))
        opp_model = OpponentModel(seed=int(self._rng.integers(0, 10000)))

        # Opponent bots
        opp_agents: List[AgentState] = []
        actual_types = opponent_types or [
            int(self._rng.choice([
                AgentType.TREND_FOLLOWER, AgentType.MEAN_REVERTER,
                AgentType.MOMENTUM, AgentType.RANDOM_BOT
            ]))
            for _ in range(n_opponents)
        ]
        for i, t in enumerate(actual_types):
            opp = AgentState(
                agent_id=f"opp_{i}",
                agent_type=AgentType(t).name
            )
            opp_agents.append(opp)
            agents.append(opp)

        # Simulation state
        crowding_history   : List[float] = []
        pressure_history   : List[float] = []
        platform_pressure  = 0.0
        our_recent_wins    : List[int]   = []

        for round_idx in range(n_rounds):
            # 1. All agents choose actions
            # Our action: EXP3 mixed strategy
            our_action = exp3.sample_action()

            # Opponent actions: sample from type distribution (+ noise)
            opp_actions: List[int] = []
            for i, opp in enumerate(opp_agents):
                t   = actual_types[i]
                p   = OPPONENT_LIKELIHOODS[t].copy()
                p   = (p + 0.05) / (p + 0.05).sum()  # slight noise
                opp_action = int(self._rng.choice(3, p=p))
                opp_actions.append(opp_action)
                opp_model.update(opp_action)

            # 2. Crowding: fraction of non-SKIP agents on same side as us
            if our_action != ActionType.SKIP:
                same_side = sum(
                    1 for a in opp_actions
                    if a == our_action and a != ActionType.SKIP
                )
                total_active = sum(1 for a in opp_actions if a != ActionType.SKIP) + 1
                crowding     = same_side / max(1, total_active)
            else:
                crowding = 0.0
            crowding_history.append(crowding)

            # 3. Platform adjusts win prob
            cd         = getattr(config, "GAME_CROWDING_DISCOUNT", 0.10)
            eff_p      = self._base_p * (1.0 - platform_pressure * 0.5) * (1.0 - cd * crowding)
            eff_p      = float(np.clip(eff_p, 0.40, 0.75))
            pressure_history.append(platform_pressure)

            # 4. Market outcome (CALL = price goes up with prob eff_p)
            price_up = int(self._rng.random() < eff_p)

            # 5. Payoffs
            # Our payoff
            our_win, our_payoff = _compute_trade_payoff(
                our_action, price_up, self._payout, crowding, cd
            )
            exp3.update(our_action, our_payoff)
            us.n_trades += 1
            us.total_pnl += our_payoff
            if our_win:
                us.wins += 1
                our_recent_wins.append(1)
            else:
                if our_action != ActionType.SKIP:
                    us.losses += 1
                our_recent_wins.append(0)
            us.actions.append(our_action)
            us.payoffs.append(our_payoff)

            # Opponent payoffs
            for i, opp in enumerate(opp_agents):
                opp_win, opp_payoff = _compute_trade_payoff(
                    opp_actions[i], price_up, self._payout, 0.0, 0.0
                )
                opp.n_trades += 1
                opp.total_pnl += opp_payoff
                if opp_win:
                    opp.wins += 1
                else:
                    if opp_actions[i] != ActionType.SKIP:
                        opp.losses += 1
                opp.actions.append(opp_actions[i])
                opp.payoffs.append(opp_payoff)

            # 6. Platform pressure escalation
            if len(our_recent_wins) >= 10:
                recent_wr = sum(our_recent_wins[-10:]) / 10.0
                if recent_wr > 0.60:
                    platform_pressure = min(0.80, platform_pressure + 0.02)
                elif recent_wr < 0.45:
                    platform_pressure = max(0.0, platform_pressure - 0.01)

        # Final analysis
        avg_crowding   = float(np.mean(crowding_history)) if crowding_history else 0.0
        max_pressure   = float(max(pressure_history)) if pressure_history else 0.0
        final_pressure = platform_pressure

        # Nash equilibrium check: how close is our EXP3 strategy to NE?
        matrix     = PayoffMatrix(
            base_win_prob     = self._base_p,
            payout            = self._payout,
            platform_pressure = final_pressure,
        )
        solver     = NashSolver(seed=42)
        nash_list  = solver.solve(matrix)
        our_strat  = exp3.mixed_strategy()
        nash_dist  = 0.0
        if nash_list:
            nash_strat = np.array(nash_list[0]["our_strategy"])
            nash_dist  = float(np.linalg.norm(our_strat - nash_strat))

        return {
            "n_rounds"         : n_rounds,
            "n_opponents"      : n_opponents,
            "agent_states"     : [a.to_dict() for a in agents],
            "our_state"        : us.to_dict(),
            "opp_beliefs"      : opp_model.to_dict(),
            "exp3_state"       : exp3.to_dict(),
            "crowding_avg"     : round(avg_crowding, 4),
            "crowding_history" : [round(c, 3) for c in crowding_history[-20:]],
            "pressure_final"   : round(final_pressure, 4),
            "pressure_max"     : round(max_pressure, 4),
            "pressure_history" : [round(p, 3) for p in pressure_history[-20:]],
            "nash_distance"    : round(nash_dist, 4),
            "nash_solutions"   : nash_list[:2],
            "ecosystem_insights": self._insights(
                us, avg_crowding, final_pressure, nash_dist, nash_list, exp3
            ),
        }

    def _insights(
        self,
        us            : AgentState,
        avg_crowding  : float,
        final_pressure: float,
        nash_dist     : float,
        nash_list     : List[dict],
        exp3          : EXP3Optimizer,
    ) -> List[str]:
        insights = []

        # Our performance
        wr = us.win_rate
        insights.append(
            f"Hiệu suất cuối simulation: WR={wr:.1%}  PnL={us.total_pnl:.3f}  "
            f"Trades={us.n_trades}"
        )

        # Crowding
        if avg_crowding > 0.60:
            insights.append(
                f"⚠️  Crowding cao ({avg_crowding:.0%}) — nhiều đối thủ cùng chiều "
                f"→ edge bị giảm; xem xét fade/SKIP"
            )
        elif avg_crowding > 0.35:
            insights.append(
                f"ℹ️  Crowding vừa ({avg_crowding:.0%}) — market vẫn hợp lý"
            )
        else:
            insights.append(
                f"✅ Crowding thấp ({avg_crowding:.0%}) — edge không bị pha loãng"
            )

        # Platform pressure
        if final_pressure > 0.50:
            insights.append(
                f"🚨 Platform pressure cao ({final_pressure:.0%}) — nền tảng đang adapt "
                f"chống lại chúng ta; cân nhắc thay đổi pattern"
            )
        elif final_pressure > 0.20:
            insights.append(
                f"⚠️  Platform pressure tăng ({final_pressure:.0%}) — theo dõi chặt"
            )
        else:
            insights.append(
                f"✅ Platform pressure thấp ({final_pressure:.0%}) — edge còn nguyên"
            )

        # Nash equilibrium proximity
        if nash_list:
            ne = nash_list[0]
            if nash_dist < 0.10:
                insights.append(
                    f"📍 EXP3 đã hội tụ gần Nash Equilibrium (dist={nash_dist:.3f}) "
                    f"→ strategy là optimal response"
                )
            else:
                insights.append(
                    f"🔄 EXP3 còn cách Nash Equilibrium {nash_dist:.3f} — "
                    f"tiếp tục học"
                )
            strat = ne.get("our_strategy", [])
            if len(strat) == 3 and strat[2] > 0.35:
                insights.append(
                    f"💡 Nash khuyến nghị SKIP {strat[2]:.0%} thời gian — platform edge cao"
                )

        # EXP3 regret
        regret = exp3.estimated_regret()
        bound  = exp3.regret_bound()
        insights.append(
            f"EXP3 Regret: {regret:.2f} / bound {bound:.0f} "
            f"(T={exp3.rounds} rounds)"
        )

        return insights


def _compute_trade_payoff(
    action    : int,
    price_up  : int,
    payout    : float,
    crowding  : float,
    cd        : float,
) -> Tuple[bool, float]:
    """
    Compute win/loss and payoff for a single trade.

    Returns (won: bool, payoff: float).
    """
    if action == ActionType.SKIP:
        return False, 0.0

    eff_payout = payout * (1.0 - cd * crowding)
    won        = (action == ActionType.CALL and price_up == 1) or \
                 (action == ActionType.PUT  and price_up == 0)
    payoff     = eff_payout if won else -1.0
    return won, payoff


# ──────────────────────────────────────────────────────────────────
# 8. GameTheoryReport
# ──────────────────────────────────────────────────────────────────

@dataclass
class GameTheoryReport:
    """Complete multi-agent game theory analysis report."""
    # Game setup
    n_agents           : int
    n_rounds           : int
    current_regime     : Optional[str]
    # Nash equilibrium
    nash_solutions     : List[dict]
    recommended_action : str
    nash_payoff        : float
    # Opponent model
    opponent_beliefs   : dict
    dominant_opponent  : str
    opponent_concentration: float
    # EXP3 state
    exp3_weights       : dict
    exp3_regret        : float
    # Ecosystem simulation
    ecosystem_state    : dict
    crowding_index     : float
    platform_pressure  : float
    # Platform pressure analysis
    pressure_analysis  : dict
    # Insights
    insights           : List[str]
    timestamp          : str = ""


# ──────────────────────────────────────────────────────────────────
# 9. GameTheoryEngine — orchestrator
# ──────────────────────────────────────────────────────────────────

class GameTheoryEngine:
    """
    Orchestrator for the Multi-Agent Game Theory + Market Ecosystem Engine.

    Workflow:
      1. Load recent trade outcomes from Redis/file for pressure analysis
      2. Construct PayoffMatrix with current regime win probabilities
      3. Solve for Nash equilibrium
      4. Load/update OpponentModel from pool observations
      5. Run EcosystemSimulator (N-round multi-agent game)
      6. Detect platform pressure
      7. Synthesize GameTheoryReport + save to Redis

    Integration with utility_engine:
      - GameTheoryReport informs RationalAgent about ecosystem state
      - Platform pressure → adjust Kelly stake downward
      - Crowding → prefer SKIP or FADE over crowded direction
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng        = np.random.default_rng(seed)
        self._seed       = seed
        self._payout     = getattr(config, "SIM_PAYOUT_RATIO", 0.85)
        self._base_p     = getattr(config, "GAME_BASE_WIN_PROB", 0.55)
        self._simulator  = EcosystemSimulator(seed=seed)
        self._pressure   = PlatformPressureDetector()

    def run(
        self,
        trade_outcomes : List[int]  = None,
        current_regime : str        = None,
        n_rounds       : int        = None,
        verbose        : bool       = True,
    ) -> GameTheoryReport:
        """
        Run full game theory analysis.

        Parameters
        ----------
        trade_outcomes : Recent outcomes (1=win, 0=loss). Loaded from pool if None.
        current_regime : Current market regime (affects win probabilities).
        n_rounds       : Simulation rounds. Uses config default if None.
        verbose        : Print progress.
        """
        n_rounds = n_rounds or getattr(config, "GAME_SIM_ROUNDS", 100)

        if verbose:
            print(f"\n  🕹️  [GameTheoryEngine] regime={current_regime or 'unknown'} "
                  f"  rounds={n_rounds}")

        # Load trade outcomes if not provided
        if trade_outcomes is None:
            trade_outcomes = self._load_trade_history()

        # Adjust win probability by regime
        regime_p = self._regime_adjusted_winprob(current_regime)

        # 1. PayoffMatrix
        if verbose:
            print("  🕹️  Step 1: Building PayoffMatrix…")
        matrix = PayoffMatrix(
            base_win_prob     = regime_p,
            payout            = self._payout,
        )
        M_reduced, rows, cols = matrix.eliminate_dominated()

        # 2. Nash Equilibrium
        if verbose:
            print("  🕹️  Step 2: Solving Nash Equilibria…")
        solver   = NashSolver(seed=self._seed)
        nash_list = solver.solve(matrix)
        rec_action = solver.recommended_action(nash_list)

        nash_payoff = nash_list[0]["payoff"] if nash_list else 0.0

        # 3. Opponent model (from gene pool observations)
        if verbose:
            print("  🕹️  Step 3: Bayesian opponent modeling…")
        opp_model = self._build_opponent_model(trade_outcomes)
        dom_type, dom_prob = opp_model.dominant_type()

        # 4. Ecosystem simulation
        if verbose:
            print("  🕹️  Step 4: Ecosystem simulation…")
        sim_result = self._simulator.run(
            n_rounds  = n_rounds,
            verbose   = False,
        )

        # 5. Platform pressure
        if verbose:
            print("  🕹️  Step 5: Platform pressure detection…")
        pressure_analysis = self._pressure.compute(
            trade_outcomes = trade_outcomes,
            historical_wr  = float(self._base_p),
            payout         = self._payout,
        )

        # 6. EXP3 state (persistent across runs)
        exp3 = EXP3Optimizer(seed=self._seed)

        # 7. Aggregate insights
        insights = self._generate_insights(
            nash_list       = nash_list,
            rec_action      = rec_action,
            opp_model       = opp_model,
            sim_result      = sim_result,
            pressure        = pressure_analysis,
            current_regime  = current_regime,
        )

        if verbose:
            print(f"\n  🕹️  Nash payoff={nash_payoff:.4f}  "
                  f"Platform pressure={pressure_analysis['pressure_score']:.2f}  "
                  f"Crowding={sim_result['crowding_avg']:.2f}")
            print(f"  🕹️  Recommended action: {rec_action}")
            for ins in insights[:3]:
                print(f"     • {ins}")

        report = GameTheoryReport(
            n_agents              = sim_result["n_opponents"] + 1,
            n_rounds              = n_rounds,
            current_regime        = current_regime or "unknown",
            nash_solutions        = nash_list,
            recommended_action    = rec_action,
            nash_payoff           = round(nash_payoff, 5),
            opponent_beliefs      = opp_model.to_dict(),
            dominant_opponent     = dom_type,
            opponent_concentration= opp_model.concentration(),
            exp3_weights          = exp3.to_dict(),
            exp3_regret           = exp3.estimated_regret(),
            ecosystem_state       = sim_result,
            crowding_index        = sim_result["crowding_avg"],
            platform_pressure     = pressure_analysis["pressure_score"],
            pressure_analysis     = pressure_analysis,
            insights              = insights,
            timestamp             = _now_iso(),
        )

        self._save_report(report)
        return report

    def _regime_adjusted_winprob(self, regime: Optional[str]) -> float:
        """Adjust base win probability by market regime."""
        regime_map = {
            "trend_up"      : 0.58,
            "trend_down"    : 0.58,
            "choppy"        : 0.52,
            "high_vol_choppy": 0.50,
            "crash"         : 0.53,
            "spike"         : 0.54,
            "recovery"      : 0.56,
        }
        return regime_map.get(str(regime).lower(), self._base_p)

    def _load_trade_history(self) -> List[int]:
        """Load recent trade outcomes from Redis."""
        try:
            import redis as _redis
            r   = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            raw = r.lrange("Deriv_Trade_History", 0, 99)
            if raw:
                outcomes = []
                for item in raw:
                    try:
                        d = json.loads(item)
                        outcomes.append(1 if d.get("result", "").lower() == "win" else 0)
                    except Exception:
                        pass
                return outcomes
        except Exception:
            pass
        return []

    def _build_opponent_model(self, trade_outcomes: List[int]) -> OpponentModel:
        """
        Build opponent model from observable market data.

        In absence of direct opponent observations, we use proxy signals:
        - When we win, opponent likely bet PUT (opposite)
        - When we lose, opponent likely bet CALL (same side, but won)
        This is a rough proxy; real data from market book would be better.
        """
        model = OpponentModel(seed=self._seed)
        for outcome in trade_outcomes[:50]:
            # Proxy: if we win → infer opponent was on opposite side
            proxy_opp_action = ActionType.PUT if outcome == 1 else ActionType.CALL
            model.update(int(proxy_opp_action))
        return model

    def _generate_insights(
        self,
        nash_list      : List[dict],
        rec_action     : str,
        opp_model      : OpponentModel,
        sim_result     : dict,
        pressure       : dict,
        current_regime : Optional[str],
    ) -> List[str]:
        """Generate multi-layer strategic insights."""
        insights = []

        # Regime context
        if current_regime:
            insights.append(f"Regime hiện tại: {current_regime}")

        # Nash recommendation
        if nash_list:
            ne = nash_list[0]
            ne_type = ne.get("type", "unknown")
            strat   = ne.get("our_strategy", [])
            if len(strat) == 3:
                insights.append(
                    f"Nash Equilibrium ({ne_type}): "
                    f"CALL={strat[0]:.0%} PUT={strat[1]:.0%} SKIP={strat[2]:.0%}  "
                    f"payoff={ne.get('payoff', 0):.4f}"
                )
            insights.append(f"Hành động khuyến nghị: {rec_action}")

        # Opponent model
        dom_type, dom_prob = opp_model.dominant_type()
        insights.append(
            f"Đối thủ chính: {dom_type} (xác suất {dom_prob:.0%}), "
            f"tập trung={opp_model.concentration():.2f}"
        )
        pred = opp_model.predicted_action_dist()
        insights.append(
            f"Dự đoán đối thủ tiếp theo: CALL={pred[0]:.0%} PUT={pred[1]:.0%} SKIP={pred[2]:.0%}"
        )

        # Crowding insight
        crowding = sim_result.get("crowding_avg", 0.0)
        if crowding > 0.50:
            insights.append(
                f"🔴 Crowding {crowding:.0%} — hầu hết bot cùng chiều "
                f"→ payout giảm → ưu tiên SKIP hoặc fade"
            )
        else:
            insights.append(
                f"🟢 Crowding {crowding:.0%} — đa dạng chiến lược, edge không bị pha"
            )

        # Platform pressure
        p_score = pressure.get("pressure_score", 0.0)
        for sig in pressure.get("signals", [])[:2]:
            insights.append(sig)

        # Strategic recommendation
        if p_score > 0.50 and crowding > 0.50:
            insights.append(
                "⚡ Cảnh báo kép: platform pressure cao + crowding cao "
                "→ SKIP / giảm kích thước stake ngay"
            )
        elif p_score > 0.30:
            insights.append(
                "ℹ️  Áp lực vừa: cân nhắc thay đổi pattern "
                "(thời điểm vào lệnh, lookahead)"
            )

        # Ecosystem simulation insights
        for eco_ins in sim_result.get("ecosystem_insights", [])[:3]:
            insights.append(eco_ins)

        return insights

    def _save_report(self, report: GameTheoryReport) -> None:
        """Save report to Redis + file."""
        payload = asdict(report)
        try:
            import redis as _redis
            r = _redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
            )
            r.set(_REDIS_GAMETHEORY_KEY, json.dumps(payload))
        except Exception:
            pass
        try:
            os.makedirs("models", exist_ok=True)
            with open("models/gametheory_report.json", "w") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────
# 10. API helpers
# ──────────────────────────────────────────────────────────────────

def get_gametheory_report() -> dict:
    """Load and return latest game theory report from Redis or file."""
    try:
        import redis as _redis
        r   = _redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB
        )
        raw = r.get(_REDIS_GAMETHEORY_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open("models/gametheory_report.json") as f:
            return json.load(f)
    except Exception:
        return {"status": "no_report"}


def run_gametheory_analysis(
    trade_outcomes : List[int]  = None,
    current_regime : str        = None,
    n_rounds       : int        = None,
    seed           : int        = 42,
    verbose        : bool       = True,
) -> GameTheoryReport:
    """
    Top-level entry point for game theory analysis.

    Parameters
    ----------
    trade_outcomes : Recent trade outcomes (1=win, 0=loss).
    current_regime : Current market regime.
    n_rounds       : Ecosystem simulation rounds.
    seed           : Random seed.
    verbose        : Print progress.

    Returns
    -------
    GameTheoryReport with Nash equilibrium, opponent beliefs, EXP3 weights,
    ecosystem state, platform pressure, and strategic insights.

    Ví dụ:
    ------
    >>> from gametheory_engine import run_gametheory_analysis
    >>> outcomes = [1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 0, 1] * 5
    >>> report = run_gametheory_analysis(trade_outcomes=outcomes, current_regime="trend_up")
    >>> print(report.recommended_action)
    >>> print(report.platform_pressure)
    >>> for ins in report.insights: print(f"  • {ins}")
    """
    engine = GameTheoryEngine(seed=seed)
    return engine.run(
        trade_outcomes = trade_outcomes,
        current_regime = current_regime,
        n_rounds       = n_rounds,
        verbose        = verbose,
    )


# ──────────────────────────────────────────────────────────────────
# 11. Helpers
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

    parser = argparse.ArgumentParser(description="Multi-Agent Game Theory + Market Ecosystem Engine")
    parser.add_argument("--mode", choices=["report", "analyze", "nash", "simulate", "demo"],
                        default="report")
    parser.add_argument("--regime", default=None, help="Market regime")
    parser.add_argument("--rounds", type=int, default=100)
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    if args.mode == "report":
        print("\n═══ GAME THEORY REPORT ═══")
        rpt = get_gametheory_report()
        if "status" in rpt:
            print("No report found. Run 'demo' first.")
        else:
            print(f"Recommended action: {rpt.get('recommended_action', '?')}")
            print(f"Platform pressure : {rpt.get('platform_pressure', 0):.1%}")
            print(f"Crowding index    : {rpt.get('crowding_index', 0):.1%}")
            print(f"Dominant opponent : {rpt.get('dominant_opponent', '?')}")
            print("\nInsights:")
            for i in rpt.get("insights", []):
                print(f"  • {i}")

    elif args.mode in ("analyze", "demo"):
        print(f"\n═══ GAME THEORY DEMO (regime={args.regime}, rounds={args.rounds}) ═══")
        rng = np.random.default_rng(42)
        # Simulate some trade history
        outcomes = [int(rng.random() < 0.55) for _ in range(80)]
        # Add some platform pressure signal: last 20 trades dip
        outcomes[:20] = [int(rng.random() < 0.45) for _ in range(20)]

        report = run_gametheory_analysis(
            trade_outcomes = outcomes,
            current_regime = args.regime or "trend_up",
            n_rounds       = args.rounds,
            verbose        = True,
        )
        print(f"\n{'═'*55}")
        print(f"Recommended action: {report.recommended_action}")
        print(f"Nash payoff        : {report.nash_payoff:.5f}")
        print(f"Platform pressure  : {report.platform_pressure:.1%}")
        print(f"Crowding index     : {report.crowding_index:.1%}")
        print(f"Dominant opponent  : {report.dominant_opponent} "
              f"({report.opponent_concentration:.0%} confidence)")
        print(f"\nEXP3 weights:")
        for k, v in report.exp3_weights.get("weights", {}).items():
            print(f"  {k}: {v:.3f}")
        print(f"\nInsights:")
        for i in report.insights:
            print(f"  • {i}")

    elif args.mode == "nash":
        print("\n═══ NASH EQUILIBRIUM ANALYSIS ═══")
        for p in [0.50, 0.52, 0.55, 0.58, 0.62]:
            matrix    = PayoffMatrix(base_win_prob=p)
            solver    = NashSolver()
            nash_list = solver.solve(matrix)
            if nash_list:
                ne = nash_list[0]
                s  = ne.get("our_strategy", [])
                print(f"  WP={p:.2f}:  {ne['type']:15s}  "
                      f"CALL={s[0]:.2f} PUT={s[1]:.2f} SKIP={s[2]:.2f}  "
                      f"payoff={ne['payoff']:.4f}")
            else:
                print(f"  WP={p:.2f}:  No NE found")

    elif args.mode == "simulate":
        print(f"\n═══ ECOSYSTEM SIMULATION ({args.rounds} rounds) ═══")
        sim    = EcosystemSimulator(seed=42)
        result = sim.run(n_rounds=args.rounds, verbose=True)
        us     = result["our_state"]
        print(f"  Our WR: {us['win_rate']:.1%}  PnL: {us['total_pnl']:.3f}")
        print(f"  Crowding: {result['crowding_avg']:.1%}  "
              f"Pressure: {result['pressure_final']:.1%}")
        print(f"  Nash distance: {result['nash_distance']:.4f}")
        for ins in result["ecosystem_insights"]:
            print(f"  • {ins}")
