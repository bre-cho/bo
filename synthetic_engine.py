"""
synthetic_engine.py
===================
DATA GENERATION + SYNTHETIC SIGNAL ENGINE

Hệ thống không chỉ học từ data có sẵn — mà còn TẠO data để ép
model/system học nhanh hơn.

Thành phần:
  CandleGenerator       — Sinh nến OHLCV tổng hợp theo regime
                           (trending, choppy, crash, spike, recovery, GBM)
  SignalAugmentor       — Augment feature vectors từ data thật
                           (noise, dropout, mixup, scale, cutout)
  SyntheticScenarioLib  — Thư viện kịch bản với outcome BIẾT TRƯỚC
  SyntheticTrainer      — Tạo (X, y) sẵn dùng cho WinClassifier + LSTM
  generate_training_boost() — Top-level: blend synthetic + real → train

Tại sao cần:
  - Dữ liệu live ban đầu quá ít (< ML_MIN_TRAIN_SAMPLES)
  - Edge cases (crash, spike) hiếm trong data thật → model không học được
  - Dataset mất cân bằng (win ≠ loss) → synthetic cân bằng 50/50
  - Offline training trước live → model sẵn sàng ngay từ lệnh đầu tiên

Cách dùng:
  >>> from synthetic_engine import generate_training_boost
  >>> X, y = generate_training_boost(n_per_regime=200, blend_real_df=df)
  >>> win_clf.train(X, y)
"""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import config
from feature_pipeline import (
    FEATURE_NAMES,
    N_FEATURES,
    extract_features,
)


# ──────────────────────────────────────────────────────────────────
# Market Regime enum
# ──────────────────────────────────────────────────────────────────

REGIME_TREND_UP    = "trend_up"
REGIME_TREND_DOWN  = "trend_down"
REGIME_CHOPPY      = "choppy"
REGIME_CRASH       = "crash"
REGIME_SPIKE       = "spike"
REGIME_RECOVERY    = "recovery"
REGIME_MIXED       = "mixed"


# ──────────────────────────────────────────────────────────────────
# 1. CandleGenerator — OHLCV synthetic candle sequences
# ──────────────────────────────────────────────────────────────────

class CandleGenerator:
    """
    Tạo chuỗi nến OHLCV tổng hợp theo từng regime thị trường.

    Phương pháp:
      - Geometric Brownian Motion (GBM) với drift + volatility tunable
      - Regime-switching Markov chain cho mixed sequences
      - Spread model thực tế: High/Low lấy từ volatility-scaled range
      - Volume tương quan với volatility

    Parameters
    ----------
    base_price  : Giá khởi đầu (default 1000)
    granularity : Khung thời gian nến (giây, default = config.GRANULARITY)
    seed        : Random seed để reproducible (None = random)
    """

    def __init__(
        self,
        base_price: float = 1000.0,
        granularity: int = None,
        seed: Optional[int] = None,
    ) -> None:
        self.base_price  = base_price
        self.granularity = granularity or config.GRANULARITY
        self._rng        = np.random.default_rng(seed)

    # ── Core GBM price path ───────────────────────────────────────

    def _gbm_path(
        self,
        n: int,
        mu: float,     # per-candle drift (e.g. 0.003 = +0.3%/candle)
        sigma: float,  # per-candle volatility (e.g. 0.012 = 1.2%/candle)
        s0: float,
    ) -> np.ndarray:
        """
        Generate n prices using Geometric Brownian Motion.

        Parameters use PER-CANDLE scale (not annualized), so:
          mu=+0.005  → +0.5%/candle expected drift (bullish)
          mu=-0.005  → -0.5%/candle expected drift (bearish)
          sigma=0.01 → ~1% candle-to-candle volatility
        """
        dW    = self._rng.normal(0, 1, n)
        log_r = (mu - 0.5 * sigma**2) + sigma * dW
        prices = s0 * np.exp(np.cumsum(log_r))
        return prices

    # ── OHLCV from close prices ───────────────────────────────────

    def _make_ohlcv(
        self,
        closes: np.ndarray,
        spread_factor: float = 0.002,
    ) -> pd.DataFrame:
        """
        Convert close price array into OHLCV DataFrame.
        Opens are previous close; High/Low derived from intra-bar spread.
        """
        n      = len(closes)
        opens  = np.empty(n)
        opens[0] = self.base_price
        opens[1:] = closes[:-1]

        # Intra-bar spread scaled by local volatility
        vol    = np.abs(np.diff(np.log(closes + 1e-9), prepend=np.log(opens[0])))
        spread = np.maximum(spread_factor * closes, vol * closes * 0.5)
        highs  = np.maximum(opens, closes) + spread * self._rng.uniform(0.5, 1.5, n)
        lows   = np.minimum(opens, closes) - spread * self._rng.uniform(0.5, 1.5, n)
        lows   = np.maximum(lows, closes * 0.0001)   # never negative

        # Volume: correlated with abs change
        base_vol = 1000.0
        volume   = base_vol * (1 + 3 * np.abs(closes / opens - 1)) * self._rng.uniform(0.7, 1.3, n)

        # Timestamps: evenly spaced going back from now
        end_ts   = pd.Timestamp.now().floor("min")
        times    = pd.date_range(
            end=end_ts, periods=n, freq=pd.Timedelta(seconds=self.granularity)
        )

        return pd.DataFrame({
            "datetime" : times,
            "open"     : opens.round(4),
            "high"     : highs.round(4),
            "low"      : lows.round(4),
            "close"    : closes.round(4),
            "volume"   : volume.astype(int),
        })

    # ── Named regimes ─────────────────────────────────────────────

    def trending_up(self, n: int = 200, strength: float = 1.0) -> pd.DataFrame:
        """
        Xu hướng tăng mạnh.
        strength: 1.0 = normal (+0.4%/candle), 2.0 = very strong (+0.8%/candle)
        """
        mu    = 0.004 * strength   # +0.4% per candle expected drift
        sigma = 0.008              # 0.8% per-candle volatility
        closes = self._gbm_path(n, mu, sigma, self.base_price)
        return self._make_ohlcv(closes)

    def trending_down(self, n: int = 200, strength: float = 1.0) -> pd.DataFrame:
        """Xu hướng giảm mạnh."""
        mu    = -0.004 * strength
        sigma = 0.008
        closes = self._gbm_path(n, mu, sigma, self.base_price)
        return self._make_ohlcv(closes)

    def choppy(self, n: int = 200, volatility: float = 1.0) -> pd.DataFrame:
        """
        Thị trường sideway/chop — không có xu hướng rõ.
        Drift gần 0 nhưng volatility cao → nhiều nhiễu, khó đoán.
        """
        mu    = 0.0
        sigma = 0.018 * volatility   # high noise
        closes = self._gbm_path(n, mu, sigma, self.base_price)
        return self._make_ohlcv(closes, spread_factor=0.003)

    def crash(self, n: int = 200, crash_pct: float = 0.25) -> pd.DataFrame:
        """
        Crash đột ngột giữa chuỗi, sau đó hồi nhẹ.
        crash_pct: % giảm trong crash candle (default 25%)
        """
        crash_at = n // 2
        # Before crash: gentle uptrend
        pre   = self._gbm_path(crash_at, 0.002, 0.006, self.base_price)
        # Crash: single dramatic drop
        crash_price = pre[-1] * (1 - crash_pct)
        # After crash: choppy with downward bias
        post  = self._gbm_path(n - crash_at - 1, -0.002, 0.015, crash_price)
        closes = np.concatenate([pre, [crash_price], post])
        return self._make_ohlcv(closes, spread_factor=0.005)

    def spike(self, n: int = 200, spike_pct: float = 0.20) -> pd.DataFrame:
        """
        Spike đột ngột lên, sau đó mean-reverting.
        spike_pct: % tăng trong spike candle (default 20%)
        """
        spike_at = n // 2
        pre        = self._gbm_path(spike_at, 0.0, 0.008, self.base_price)
        spike_price = pre[-1] * (1 + spike_pct)
        # After spike: mean-revert downward
        post = self._gbm_path(n - spike_at - 1, -0.004, 0.012, spike_price)
        closes = np.concatenate([pre, [spike_price], post])
        return self._make_ohlcv(closes, spread_factor=0.005)

    def recovery(self, n: int = 200) -> pd.DataFrame:
        """
        Giảm mạnh sau đó hồi phục mạnh — V-shape.
        """
        half = n // 2
        down = self._gbm_path(half, -0.005, 0.010, self.base_price)
        up   = self._gbm_path(n - half, 0.005, 0.010, down[-1])
        closes = np.concatenate([down, up])
        return self._make_ohlcv(closes)

    def false_breakout(self, n: int = 200) -> pd.DataFrame:
        """
        Breakout giả: giá phá kháng cự rồi quay đầu.
        """
        third = n // 3
        # Consolidation
        consol = self._gbm_path(third, 0.0, 0.006, self.base_price)
        # False breakout up
        breakout_high = consol[-1] * 1.05
        fakeout = self._gbm_path(third, 0.002, 0.008, breakout_high)
        # Reversal
        reversal = self._gbm_path(n - 2 * third, -0.005, 0.010, fakeout[-1])
        closes = np.concatenate([consol, fakeout, reversal])
        return self._make_ohlcv(closes)

    def mixed(self, n: int = 500, seed_offset: int = 0) -> pd.DataFrame:
        """
        Chuỗi regime ngẫu nhiên sử dụng Markov chain.
        Regime transitions: trending→choppy→crash→recovery→trending
        """
        # Transition matrix [from_regime] → probabilities
        regimes   = [REGIME_TREND_UP, REGIME_TREND_DOWN, REGIME_CHOPPY, REGIME_CRASH]
        trans     = {
            REGIME_TREND_UP   : [0.60, 0.10, 0.25, 0.05],
            REGIME_TREND_DOWN : [0.10, 0.55, 0.25, 0.10],
            REGIME_CHOPPY     : [0.30, 0.30, 0.35, 0.05],
            REGIME_CRASH      : [0.05, 0.30, 0.30, 0.35],
        }
        current_regime = REGIME_CHOPPY
        dfs: list[pd.DataFrame] = []
        generated = 0
        s0 = self.base_price

        while generated < n:
            chunk = min(self._rng.integers(30, 80).item(), n - generated)
            if current_regime == REGIME_TREND_UP:
                sub = CandleGenerator(base_price=s0, granularity=self.granularity,
                                       seed=seed_offset + generated).trending_up(chunk)
            elif current_regime == REGIME_TREND_DOWN:
                sub = CandleGenerator(base_price=s0, granularity=self.granularity,
                                       seed=seed_offset + generated).trending_down(chunk)
            elif current_regime == REGIME_CRASH:
                sub = CandleGenerator(base_price=s0, granularity=self.granularity,
                                       seed=seed_offset + generated).crash(chunk)
            else:
                sub = CandleGenerator(base_price=s0, granularity=self.granularity,
                                       seed=seed_offset + generated).choppy(chunk)

            dfs.append(sub)
            s0 = float(sub["close"].iloc[-1])
            generated += chunk

            # Markov transition
            probs = trans[current_regime]
            current_regime = self._rng.choice(regimes, p=probs).item()

        df = pd.concat(dfs, ignore_index=True)
        # Re-assign timestamps correctly
        end_ts = pd.Timestamp.now().floor("min")
        df["datetime"] = pd.date_range(
            end=end_ts, periods=len(df), freq=pd.Timedelta(seconds=self.granularity)
        )
        return df.iloc[:n].reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────
# 2. SignalAugmentor — Feature vector augmentation
# ──────────────────────────────────────────────────────────────────

class SignalAugmentor:
    """
    Augment feature vectors từ real data để tạo thêm training samples.

    Phương pháp:
      gaussian_noise  — Thêm nhiễu Gauss nhỏ (giữ label)
      feature_dropout — Random zeroing features (như Dropout trong NN)
      feature_scale   — Scale toàn bộ vector bởi factor ngẫu nhiên
      mixup           — Linear blend 2 samples cùng class
      cutout          — Zero một dải features liên tiếp
      time_warp       — Biến dạng time axis của sequence (cho LSTM)
      jitter          — Random walk noise trên mỗi chiều

    Tất cả augmentation GIỮ NGUYÊN label — chỉ biến đổi feature space.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = np.random.default_rng(seed)

    def gaussian_noise(
        self,
        X: np.ndarray,
        y: np.ndarray,
        sigma: float = 0.03,
        n_copies: int = 2,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Tạo n_copies bản sao với nhiễu Gauss.
        sigma: độ lớn nhiễu (tương đối so với std của từng feature)
        """
        X_aug_list = [X]
        for _ in range(n_copies):
            noise = self._rng.normal(0, sigma, X.shape).astype(np.float32)
            X_noisy = np.clip(X + noise, 0.0, 1.0)
            X_aug_list.append(X_noisy)
        X_out = np.vstack(X_aug_list)
        y_out = np.tile(y, n_copies + 1)
        return X_out.astype(np.float32), y_out.astype(np.int32)

    def feature_dropout(
        self,
        X: np.ndarray,
        y: np.ndarray,
        drop_rate: float = 0.15,
        n_copies: int = 2,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Randomly zero features (drop_rate fraction) để simulate missing data.
        """
        X_aug_list = [X]
        for _ in range(n_copies):
            mask  = self._rng.random(X.shape).astype(np.float32)
            mask  = (mask > drop_rate).astype(np.float32)
            X_aug_list.append(X * mask)
        return np.vstack(X_aug_list).astype(np.float32), np.tile(y, n_copies + 1).astype(np.int32)

    def feature_scale(
        self,
        X: np.ndarray,
        y: np.ndarray,
        scale_range: Tuple[float, float] = (0.85, 1.15),
        n_copies: int = 2,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Scale toàn bộ vector — simulate different market magnitudes."""
        X_aug_list = [X]
        for _ in range(n_copies):
            scale  = self._rng.uniform(*scale_range, size=(len(X), 1)).astype(np.float32)
            X_aug_list.append(np.clip(X * scale, 0.0, 1.0))
        return np.vstack(X_aug_list).astype(np.float32), np.tile(y, n_copies + 1).astype(np.int32)

    def mixup(
        self,
        X: np.ndarray,
        y: np.ndarray,
        alpha: float = 0.2,
        n_samples: int = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Mixup: blend 2 samples từ cùng class.
        α=0.2 → blending factor beta(0.2,0.2) gần 0 hoặc 1.
        """
        if len(X) < 4:
            return X, y
        n_samples = n_samples or len(X)

        X_mix_list, y_mix_list = [], []
        for _ in range(n_samples):
            # Pick 2 samples from same class
            label = int(self._rng.integers(0, 2))
            idxs  = np.where(y == label)[0]
            if len(idxs) < 2:
                continue
            i, j = self._rng.choice(idxs, size=2, replace=False)
            lam   = float(self._rng.beta(alpha, alpha))
            x_new = lam * X[i] + (1 - lam) * X[j]
            X_mix_list.append(x_new.astype(np.float32))
            y_mix_list.append(label)

        if not X_mix_list:
            return X, y

        X_out = np.vstack([X, np.array(X_mix_list)])
        y_out = np.concatenate([y, np.array(y_mix_list)])
        return X_out.astype(np.float32), y_out.astype(np.int32)

    def cutout(
        self,
        X: np.ndarray,
        y: np.ndarray,
        cut_size: int = 4,
        n_copies: int = 2,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Zero một dải cut_size features liên tiếp ngẫu nhiên.
        Tương đương Cutout regularization.
        """
        n_feat = X.shape[1]
        X_aug_list = [X]
        for _ in range(n_copies):
            X_c     = X.copy()
            max_start = max(0, n_feat - cut_size)
            start   = int(self._rng.integers(0, max_start + 1))
            X_c[:, start: start + cut_size] = 0.0
            X_aug_list.append(X_c)
        return np.vstack(X_aug_list).astype(np.float32), np.tile(y, n_copies + 1).astype(np.int32)

    def time_warp_sequence(
        self,
        seq: np.ndarray,
        sigma: float = 0.1,
    ) -> np.ndarray:
        """
        Warp time axis of a sequence [T, F] bằng cách nội suy phi tuyến.
        seq: shape [T, N_FEATURES]
        """
        T = seq.shape[0]
        if T < 4:
            return seq

        # Generate smooth warping path
        tt     = np.linspace(0, 1, T)
        warp_t = tt + sigma * self._rng.standard_normal(T)
        warp_t = np.sort(np.clip(warp_t, 0, 1))

        # Interpolate each feature
        warped = np.empty_like(seq)
        orig_t = np.linspace(0, 1, T)
        for f in range(seq.shape[1]):
            warped[:, f] = np.interp(orig_t, warp_t, seq[:, f])
        return warped.astype(np.float32)

    def augment_all(
        self,
        X: np.ndarray,
        y: np.ndarray,
        target_n: int = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Apply tất cả augmentation methods để đạt target_n samples.
        Trả về dataset đã shuffle + cân bằng.
        """
        if len(X) == 0:
            return X, y

        target_n = target_n or max(len(X) * 4, 500)

        # Apply each augmentation
        Xa, ya = self.gaussian_noise(X, y, n_copies=1)
        Xb, yb = self.feature_dropout(X, y, n_copies=1)
        Xc, yc = self.feature_scale(X, y, n_copies=1)
        Xd, yd = self.mixup(X, y, n_samples=len(X))
        Xe, ye = self.cutout(X, y, n_copies=1)

        X_all = np.vstack([Xa, Xb, Xc, Xd, Xe])
        y_all = np.concatenate([ya, yb, yc, yd, ye])

        # Trim to target or repeat to fill target
        if len(X_all) < target_n:
            n_repeat = math.ceil(target_n / len(X_all))
            X_all = np.tile(X_all, (n_repeat, 1))[:target_n]
            y_all = np.tile(y_all, n_repeat)[:target_n]
        else:
            X_all = X_all[:target_n]
            y_all = y_all[:target_n]

        # Shuffle
        idx = np.random.permutation(len(X_all))
        return X_all[idx].astype(np.float32), y_all[idx].astype(np.int32)


# ──────────────────────────────────────────────────────────────────
# 3. SyntheticScenarioLibrary — Edge case scenarios with known labels
# ──────────────────────────────────────────────────────────────────

@dataclass
class ScenarioSample:
    """Một mẫu training với regime + outcome biết trước."""
    regime    : str
    direction : str   # "CALL" | "PUT" | "SKIP"
    feat_vec  : np.ndarray
    label     : int   # 1=win, 0=loss
    df_window : pd.DataFrame   # candle window (for LSTM)


class SyntheticScenarioLibrary:
    """
    Tạo thư viện kịch bản thị trường với OUTCOME BIẾT TRƯỚC.

    Mỗi scenario:
      1. Sinh chuỗi nến phù hợp regime
      2. Điểm vào (entry) tại nến N
      3. Kết quả đóng lệnh (exit) tại nến N + lookahead
      4. Label = 1 nếu direction đúng hướng giá

    Vì chúng ta THIẾT KẾ chuỗi nến, kết quả có thể được kiểm soát
    để đạt tỉ lệ win/loss mong muốn.
    """

    def __init__(
        self,
        lookahead: int = None,
        window: int = None,
        seed: Optional[int] = None,
    ) -> None:
        self.lookahead = lookahead or config.SIM_LOOKAHEAD_CANDLES
        self.window    = window or config.ML_FEATURE_WINDOW
        self._gen      = CandleGenerator(seed=seed)
        self._rng      = np.random.default_rng(seed)

    def _make_sample(
        self,
        df: pd.DataFrame,
        direction: str,
        entry_idx: int,
    ) -> Optional[ScenarioSample]:
        """Extract feature vector and compute label from a candle sequence."""
        if entry_idx < 30 or entry_idx + self.lookahead >= len(df):
            return None

        window_df = df.iloc[max(0, entry_idx - self.window): entry_idx + 1].copy()
        if len(window_df) < 30:
            return None

        try:
            feat_vec, _ = extract_features(window_df)
        except Exception:
            return None

        entry  = float(df.iloc[entry_idx]["close"])
        exit_  = float(df.iloc[entry_idx + self.lookahead]["close"])

        if direction == "CALL":
            label = 1 if exit_ > entry else 0
        elif direction == "PUT":
            label = 1 if exit_ < entry else 0
        else:
            label = 0

        regime_map = {
            "trend_up"   : REGIME_TREND_UP,
            "trend_down" : REGIME_TREND_DOWN,
            "choppy"     : REGIME_CHOPPY,
            "crash"      : REGIME_CRASH,
        }
        return ScenarioSample(
            regime    = REGIME_TREND_UP if direction == "CALL" else REGIME_TREND_DOWN,
            direction = direction,
            feat_vec  = feat_vec.astype(np.float32),
            label     = label,
            df_window = window_df,
        )

    def generate_trend_calls(self, n: int = 100) -> List[ScenarioSample]:
        """
        CALL trong xu hướng tăng → tỉ lệ win cao (>70%).
        Đây là "easy positive" examples — model cần nhận ra xu hướng.
        """
        total  = n * 3 + self.lookahead + 50
        df     = self._gen.trending_up(total, strength=1.5)
        samples = []
        step    = 3
        for i in range(50, len(df) - self.lookahead, step):
            s = self._make_sample(df, "CALL", i)
            if s:
                samples.append(s)
            if len(samples) >= n:
                break
        return samples

    def generate_trend_puts(self, n: int = 100) -> List[ScenarioSample]:
        """PUT trong xu hướng giảm → tỉ lệ win cao."""
        total  = n * 3 + self.lookahead + 50
        df     = self._gen.trending_down(total, strength=1.5)
        samples = []
        step    = 3
        for i in range(50, len(df) - self.lookahead, step):
            s = self._make_sample(df, "PUT", i)
            if s:
                samples.append(s)
            if len(samples) >= n:
                break
        return samples

    def generate_choppy_losses(self, n: int = 100) -> List[ScenarioSample]:
        """
        CALL + PUT trong choppy market → nhiều loss examples.
        Model cần học: khi volatile/choppy → đừng vào lệnh.
        """
        total  = n * 4 + self.lookahead + 50
        df     = self._gen.choppy(total, volatility=1.5)
        samples = []
        step    = 3
        for i in range(50, len(df) - self.lookahead, step):
            dir_   = self._rng.choice(["CALL", "PUT"]).item()
            s = self._make_sample(df, dir_, i)
            if s and s.label == 0:  # Keep only losses from chop
                samples.append(s)
            if len(samples) >= n:
                break
        return samples

    def generate_crash_responses(self, n: int = 50) -> List[ScenarioSample]:
        """
        Trước + sau crash — POST-crash PUT signals đúng hướng.
        """
        total  = n * 5 + self.lookahead + 100
        df     = self._gen.crash(total, crash_pct=0.30)
        samples = []
        crash_at = total // 2
        # Entry zone: right before + during crash
        for i in range(max(30, crash_at - 20), min(crash_at + 20, len(df) - self.lookahead)):
            s = self._make_sample(df, "PUT", i)
            if s:
                samples.append(s)
            if len(samples) >= n:
                break
        return samples

    def generate_recovery_calls(self, n: int = 50) -> List[ScenarioSample]:
        """V-shape recovery — CALL signals trong giai đoạn hồi."""
        total = n * 5 + self.lookahead + 100
        df    = self._gen.recovery(total)
        half  = total // 2
        samples = []
        for i in range(half, min(half + n * 3, len(df) - self.lookahead)):
            s = self._make_sample(df, "CALL", i)
            if s:
                samples.append(s)
            if len(samples) >= n:
                break
        return samples

    def generate_false_breakouts(self, n: int = 50) -> List[ScenarioSample]:
        """False breakout → PUT signal đúng sau fakeout."""
        total = n * 5 + self.lookahead + 100
        df    = self._gen.false_breakout(total)
        third = total // 3
        samples = []
        # Zone: after fakeout — PUT is correct
        for i in range(third + 10, min(third * 2, len(df) - self.lookahead)):
            s = self._make_sample(df, "PUT", i)
            if s:
                samples.append(s)
            if len(samples) >= n:
                break
        return samples

    def generate_mixed_scenarios(self, n: int = 200) -> List[ScenarioSample]:
        """Mixed regime — general training diversity."""
        df      = self._gen.mixed(n * 4 + self.lookahead + 100)
        samples = []
        step    = 3
        for i in range(50, len(df) - self.lookahead, step):
            dir_ = self._rng.choice(["CALL", "PUT"]).item()
            s    = self._make_sample(df, dir_, i)
            if s:
                samples.append(s)
            if len(samples) >= n:
                break
        return samples

    def build_dataset(
        self,
        n_per_regime: int = 100,
        balance: bool = True,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Tạo full training dataset từ tất cả scenario types.

        Parameters
        ----------
        n_per_regime : Số mẫu mỗi loại regime
        balance      : Cân bằng win/loss về 50/50

        Returns
        -------
        (X: [n, N_FEATURES], y: [n])
        """
        print(f"[SyntheticScenario] Generating {n_per_regime} samples × 7 regime types...")

        all_samples: List[ScenarioSample] = []
        all_samples += self.generate_trend_calls(n_per_regime)
        all_samples += self.generate_trend_puts(n_per_regime)
        all_samples += self.generate_choppy_losses(n_per_regime)
        all_samples += self.generate_crash_responses(n_per_regime // 2)
        all_samples += self.generate_recovery_calls(n_per_regime // 2)
        all_samples += self.generate_false_breakouts(n_per_regime // 2)
        all_samples += self.generate_mixed_scenarios(n_per_regime)

        if not all_samples:
            return np.empty((0, N_FEATURES), dtype=np.float32), np.empty(0, dtype=np.int32)

        X = np.array([s.feat_vec for s in all_samples], dtype=np.float32)
        y = np.array([s.label   for s in all_samples], dtype=np.int32)

        print(f"[SyntheticScenario] Total: {len(X)} samples  "
              f"win_rate={y.mean():.1%}  "
              f"n_wins={y.sum()}  n_losses={len(y)-y.sum()}")

        if balance:
            X, y = _balance_dataset(X, y)
            print(f"[SyntheticScenario] After balance: {len(X)} samples  win_rate={y.mean():.1%}")

        # Shuffle
        idx = np.random.permutation(len(X))
        return X[idx].astype(np.float32), y[idx].astype(np.int32)


# ──────────────────────────────────────────────────────────────────
# 4. Dataset balance helper
# ──────────────────────────────────────────────────────────────────

def _balance_dataset(X: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Under-sample majority class để cân bằng 50/50."""
    idx_win  = np.where(y == 1)[0]
    idx_loss = np.where(y == 0)[0]
    n_min    = min(len(idx_win), len(idx_loss))
    if n_min == 0:
        return X, y
    rng = np.random.default_rng()
    win_keep  = rng.choice(idx_win,  n_min, replace=False)
    loss_keep = rng.choice(idx_loss, n_min, replace=False)
    idx = np.concatenate([win_keep, loss_keep])
    return X[idx], y[idx]


# ──────────────────────────────────────────────────────────────────
# 5. SyntheticTrainer — top-level integration
# ──────────────────────────────────────────────────────────────────

class SyntheticTrainer:
    """
    Kết hợp synthetic data + real data để train WinClassifier + LSTM.

    Workflow:
      1. Tạo synthetic dataset từ scenario library
      2. Augment real data (nếu có)
      3. Blend synthetic + real theo tỉ lệ (synthetic_ratio)
      4. Train WinClassifier + LSTMWaveClassifier
      5. Lưu trained models

    Parameters
    ----------
    n_per_regime    : Số synthetic samples mỗi regime
    synthetic_ratio : % data tổng là synthetic (0.5 = 50/50 real/synth)
    balance         : Cân bằng win/loss
    seed            : Random seed
    """

    def __init__(
        self,
        n_per_regime: int = None,
        synthetic_ratio: float = None,
        balance: bool = True,
        seed: Optional[int] = 42,
    ) -> None:
        self.n_per_regime    = n_per_regime    or config.SYNTH_N_PER_REGIME
        self.synthetic_ratio = synthetic_ratio or config.SYNTH_BLEND_RATIO
        self.balance         = balance
        self.seed            = seed

    def build_blended_dataset(
        self,
        real_X: Optional[np.ndarray] = None,
        real_y: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Tạo synthetic dataset và blend với real data.

        Parameters
        ----------
        real_X : Feature matrix từ real trades (shape [n, N_FEATURES])
        real_y : Labels từ real trades (shape [n])

        Returns
        -------
        (X_blended, y_blended)
        """
        # 1. Generate synthetic
        lib      = SyntheticScenarioLibrary(seed=self.seed)
        X_synth, y_synth = lib.build_dataset(
            n_per_regime=self.n_per_regime,
            balance=self.balance,
        )

        # 2. Augment synthetic
        aug = SignalAugmentor(seed=self.seed)
        X_synth, y_synth = aug.gaussian_noise(X_synth, y_synth, n_copies=1)

        # 3. Blend with real data
        if real_X is not None and len(real_X) > 0:
            # Augment real data too
            X_real_aug, y_real_aug = aug.augment_all(real_X, real_y)
            # Determine target sizes
            n_real  = len(X_real_aug)
            n_synth = int(n_real * self.synthetic_ratio / (1 - self.synthetic_ratio + 1e-9))
            n_synth = min(n_synth, len(X_synth))

            X_use_synth = X_synth[:n_synth]
            y_use_synth = y_synth[:n_synth]

            X_all = np.vstack([X_real_aug, X_use_synth])
            y_all = np.concatenate([y_real_aug, y_use_synth])
            print(
                f"[SyntheticTrainer] Blended: real_aug={len(X_real_aug)} "
                f"+ synthetic={n_synth} = {len(X_all)} total"
            )
        else:
            X_all = X_synth
            y_all = y_synth
            print(f"[SyntheticTrainer] Synthetic only: {len(X_all)} samples")

        # 4. Balance final dataset
        if self.balance:
            X_all, y_all = _balance_dataset(X_all, y_all)

        # 5. Shuffle
        idx = np.random.permutation(len(X_all))
        return X_all[idx].astype(np.float32), y_all[idx].astype(np.int32)

    def train_win_classifier(
        self,
        real_X: Optional[np.ndarray] = None,
        real_y: Optional[np.ndarray] = None,
    ) -> float:
        """
        Train WinClassifier trên blended dataset.
        Returns AUC score.
        """
        from ml_models import WinClassifier

        X, y = self.build_blended_dataset(real_X, real_y)
        if len(X) < config.ML_MIN_TRAIN_SAMPLES:
            print(f"[SyntheticTrainer] Not enough samples: {len(X)} < {config.ML_MIN_TRAIN_SAMPLES}")
            return 0.0

        clf = WinClassifier()
        auc = clf.train(X, y)
        print(f"[SyntheticTrainer] WinClassifier AUC={auc:.4f}")
        return auc

    def train_lstm(
        self,
        real_df: Optional[pd.DataFrame] = None,
    ) -> None:
        """
        Train LSTMWaveClassifier trên synthetic OHLCV sequences.
        """
        from ml_models import LSTMWaveClassifier
        from feature_pipeline import extract_sequence

        seq_len = config.ML_FEATURE_WINDOW
        lstm    = LSTMWaveClassifier(seq_len=seq_len)

        # Generate diverse synthetic sequences
        gen       = CandleGenerator(seed=self.seed)
        n_total   = max(self.n_per_regime * 5, 200)
        dfs_seqs  = [
            gen.trending_up(n_total // 5),
            gen.trending_down(n_total // 5),
            gen.choppy(n_total // 5),
            gen.crash(n_total // 5),
            gen.mixed(n_total // 5),
        ]

        X_seqs, y_seqs = [], []
        lookahead = config.SIM_LOOKAHEAD_CANDLES

        for df in dfs_seqs:
            if real_df is not None:
                df = pd.concat([df, real_df], ignore_index=True).iloc[:n_total]
            for i in range(seq_len + 30, len(df) - lookahead, 3):
                window = df.iloc[:i + 1].copy()
                seq    = extract_sequence(window, seq_len=seq_len)
                entry  = float(df.iloc[i]["close"])
                exit_  = float(df.iloc[i + lookahead]["close"])
                # Use CALL direction: label=1 if UP
                label  = 1 if exit_ > entry else 0
                X_seqs.append(seq)
                y_seqs.append(label)

        if X_seqs:
            X_seq = np.array(X_seqs, dtype=np.float32)
            y_seq = np.array(y_seqs, dtype=np.int32)
            # Balance
            X_seq, y_seq = _balance_dataset(X_seq, y_seq)
            idx = np.random.permutation(len(X_seq))
            X_seq, y_seq = X_seq[idx], y_seq[idx]
            print(f"[SyntheticTrainer] LSTM training: {len(X_seq)} sequences")
            lstm.train(X_seq, y_seq)

    def train_all(
        self,
        real_X: Optional[np.ndarray] = None,
        real_y: Optional[np.ndarray] = None,
        real_df: Optional[pd.DataFrame] = None,
    ) -> dict:
        """
        Train tất cả models và trả về metrics.

        Returns
        -------
        dict với keys: win_clf_auc, lstm_trained, n_samples
        """
        print("\n" + "=" * 55)
        print("  🏋️  SYNTHETIC TRAINER — START")
        print("=" * 55)

        auc = self.train_win_classifier(real_X, real_y)

        try:
            self.train_lstm(real_df)
            lstm_ok = True
        except Exception as exc:
            print(f"[SyntheticTrainer] LSTM training failed: {exc}")
            lstm_ok = False

        X_total, _ = self.build_blended_dataset(real_X, real_y)

        print(f"  ✅ Training complete: WinCLF AUC={auc:.4f}  LSTM={'ok' if lstm_ok else 'skip'}")
        print("=" * 55)

        return {
            "win_clf_auc"  : round(auc, 4),
            "lstm_trained" : lstm_ok,
            "n_samples"    : len(X_total),
        }


# ──────────────────────────────────────────────────────────────────
# 6. Top-level convenience function
# ──────────────────────────────────────────────────────────────────

def generate_training_boost(
    n_per_regime: int = None,
    blend_real_df: Optional[pd.DataFrame] = None,
    real_X: Optional[np.ndarray] = None,
    real_y: Optional[np.ndarray] = None,
    balance: bool = True,
    seed: int = 42,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Top-level function: tạo blended training dataset sẵn dùng.

    Parameters
    ----------
    n_per_regime  : Số synthetic samples mỗi regime
    blend_real_df : DataFrame nến thật (nếu có) để extract real features
    real_X / real_y : Feature matrix + labels từ real trades
    balance       : Cân bằng 50/50

    Returns
    -------
    (X: np.ndarray, y: np.ndarray) sẵn dùng cho WinClassifier.train()

    Ví dụ:
    ------
    >>> X, y = generate_training_boost(n_per_regime=150)
    >>> win_clf.train(X, y)
    """
    n_per_regime = n_per_regime or config.SYNTH_N_PER_REGIME

    # Extract features from real candles if provided
    if blend_real_df is not None and real_X is None:
        from feature_pipeline import build_training_dataset
        real_X, real_y = build_training_dataset(blend_real_df)
        print(f"[generate_training_boost] Real data: {len(real_X)} samples from candle DF")

    trainer = SyntheticTrainer(
        n_per_regime    = n_per_regime,
        synthetic_ratio = config.SYNTH_BLEND_RATIO,
        balance         = balance,
        seed            = seed,
    )
    return trainer.build_blended_dataset(real_X, real_y)


def run_full_synthetic_training(
    real_df: Optional[pd.DataFrame] = None,
    real_X: Optional[np.ndarray] = None,
    real_y: Optional[np.ndarray] = None,
    n_per_regime: int = None,
) -> dict:
    """
    Chạy full training pipeline: WinClassifier + LSTM + model registry.

    Dùng trực tiếp từ decision_engine.trigger_learning() hoặc script.
    """
    trainer = SyntheticTrainer(
        n_per_regime=n_per_regime or config.SYNTH_N_PER_REGIME,
        seed=42,
    )
    metrics = trainer.train_all(real_X=real_X, real_y=real_y, real_df=real_df)

    # Register in model registry if available
    try:
        from model_registry import ModelRegistry
        reg = ModelRegistry()
        reg.register(
            "win_classifier",
            n_train    = metrics["n_samples"],
            train_score= metrics["win_clf_auc"],
        )
    except Exception:
        pass

    return metrics


# ──────────────────────────────────────────────────────────────────
# CLI — Chạy trực tiếp để sinh data và train
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Synthetic Signal Engine")
    parser.add_argument("--mode", choices=["generate", "train", "benchmark", "demo"],
                        default="demo")
    parser.add_argument("--n", type=int, default=100,
                        help="n_per_regime")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if args.mode == "demo":
        print("\n═══ Candle Generator Demo ═══")
        gen = CandleGenerator(seed=args.seed)

        for regime_name, df in [
            ("Trend UP",   gen.trending_up(120)),
            ("Trend DOWN", gen.trending_down(120)),
            ("Choppy",     gen.choppy(120)),
            ("Crash",      gen.crash(120)),
            ("Spike",      gen.spike(120)),
            ("Recovery",   gen.recovery(120)),
        ]:
            price_change = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
            print(f"  {regime_name:12s}: {len(df)} candles  "
                  f"price_change={price_change:+.1f}%  "
                  f"vol={(df['close'].pct_change().std()*100):.2f}%")

        print("\n═══ Scenario Library Demo ═══")
        lib = SyntheticScenarioLibrary(seed=args.seed)
        X, y = lib.build_dataset(n_per_regime=args.n)
        print(f"\nFinal dataset: X={X.shape}  win_rate={y.mean():.1%}")

        print("\n═══ Augmentor Demo ═══")
        aug = SignalAugmentor(seed=args.seed)
        X2, y2 = aug.augment_all(X, y, target_n=len(X) * 4)
        print(f"Augmented: {X.shape} → {X2.shape}  win_rate={y2.mean():.1%}")

    elif args.mode == "generate":
        print(f"\n═══ Generating {args.n}/regime synthetic dataset ═══")
        X, y = generate_training_boost(n_per_regime=args.n, seed=args.seed)
        print(f"Dataset: X={X.shape}  win_rate={y.mean():.1%}")

    elif args.mode == "train":
        print(f"\n═══ Full Synthetic Training (n/regime={args.n}) ═══")
        metrics = run_full_synthetic_training(n_per_regime=args.n)
        print(f"\nResults: {metrics}")

    elif args.mode == "benchmark":
        print("\n═══ Augmentation Benchmark ═══")
        lib     = SyntheticScenarioLibrary(seed=args.seed)
        aug     = SignalAugmentor(seed=args.seed)
        X, y    = lib.build_dataset(n_per_regime=50)

        for method_name, method in [
            ("gaussian_noise",  lambda: aug.gaussian_noise(X, y, n_copies=2)),
            ("feature_dropout", lambda: aug.feature_dropout(X, y, n_copies=2)),
            ("feature_scale",   lambda: aug.feature_scale(X, y, n_copies=2)),
            ("mixup",           lambda: aug.mixup(X, y)),
            ("cutout",          lambda: aug.cutout(X, y, n_copies=2)),
            ("augment_all",     lambda: aug.augment_all(X, y)),
        ]:
            Xa, ya = method()
            print(f"  {method_name:20s}: {len(X)} → {len(Xa)} samples  "
                  f"win_rate={ya.mean():.1%}")
