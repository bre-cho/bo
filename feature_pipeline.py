"""
feature_pipeline.py
===================
Pipeline trích xuất features chuẩn hóa dùng chung cho:
  - Train WinClassifier (XGBoost / Logistic Regression)
  - Train LSTMWaveClassifier
  - Realtime inference trong predictor

Features trích xuất:
  Tầng 1 — Technical indicators:
    RSI(14), Momentum(10) z-score, MACD histogram delta,
    BB position, BB width, Volatility (ATR ratio)
  Tầng 2 — Wave features:
    wave_active, fib_zone_num, correction_depth, at_sr
  Tầng 3 — Candle pattern features:
    body_ratio, upper_shadow, lower_shadow, candle_direction,
    consecutive_up/down count, volume (if available)
  Tầng 4 — Regime features:
    trend_strength, range_size, recent_wr_bias

Output: numpy array (for ML) + named dict (for logging/debug)
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd

import config


# ──────────────────────────────────────────────────────────────────
# Technical indicator helpers
# ──────────────────────────────────────────────────────────────────

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta  = series.diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_g  = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l  = loss.ewm(com=period - 1, min_periods=period).mean()
    rs     = avg_g / (avg_l + 1e-9)
    return 100 - (100 / (1 + rs))


def _momentum_zscore(series: pd.Series, period: int = 10) -> pd.Series:
    mom = series - series.shift(period)
    std = mom.rolling(20).std()
    return mom / (std + 1e-9)


def _macd_hist(series: pd.Series, fast=12, slow=26, sig=9) -> pd.Series:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd     = ema_fast - ema_slow
    signal   = macd.ewm(span=sig, adjust=False).mean()
    return macd - signal


def _bollinger(series: pd.Series, period=20, num_std=2.0):
    mid   = series.rolling(period).mean()
    std   = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    bb_range = upper - lower
    bb_pos   = (series - lower) / (bb_range + 1e-9)
    bb_width = bb_range / (mid + 1e-9)
    return bb_pos, bb_width


def _atr_ratio(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high  = df["high"]  if "high"  in df.columns else df["close"]
    low   = df["low"]   if "low"   in df.columns else df["close"]
    close = df["close"]
    prev  = close.shift(1)
    tr    = pd.concat([
        high - low,
        (high - prev).abs(),
        (low  - prev).abs(),
    ], axis=1).max(axis=1)
    atr   = tr.rolling(period).mean()
    return atr / (close + 1e-9)


# ──────────────────────────────────────────────────────────────────
# Candle pattern features
# ──────────────────────────────────────────────────────────────────

def _candle_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body        = (c - o).abs()
    total_range = h - l + 1e-9
    body_ratio  = body / total_range
    upper_shadow = (h - pd.concat([c, o], axis=1).max(axis=1)) / total_range
    lower_shadow = (pd.concat([c, o], axis=1).min(axis=1) - l) / total_range
    candle_dir   = (c > o).astype(float)  # 1=bullish, 0=bearish

    # Count consecutive up/down candles
    consecutive = (c > o).astype(int)
    # Simple rolling sum for last 5 candles
    consec_up   = consecutive.rolling(5).sum().fillna(0) / 5.0
    consec_down = (1 - consecutive).rolling(5).sum().fillna(0) / 5.0

    return {
        "body_ratio"   : body_ratio,
        "upper_shadow" : upper_shadow,
        "lower_shadow" : lower_shadow,
        "candle_dir"   : candle_dir,
        "consec_up"    : consec_up,
        "consec_down"  : consec_down,
    }


# ──────────────────────────────────────────────────────────────────
# Wave feature encoding
# ──────────────────────────────────────────────────────────────────

_FIB_ZONE_MAP = {
    "NONE": 0.0, "F236": 0.236, "F382": 0.382,
    "F500": 0.5, "F618": 0.618, "F786": 0.786, "DEEP": 0.9,
}


def _encode_wave(wave) -> dict[str, float]:
    """Encode WaveContext object to numeric features."""
    if wave is None:
        return {
            "wave_active"       : 0.0,
            "fib_zone_val"      : 0.0,
            "correction_depth"  : 0.0,
            "at_sr"             : 0.0,
            "wave_entry_score"  : 0.0,
            "main_dir_up"       : 0.0,
            "main_dir_down"     : 0.0,
        }
    return {
        "wave_active"       : float(wave.correction_active),
        "fib_zone_val"      : _FIB_ZONE_MAP.get(wave.fib_zone, 0.0),
        "correction_depth"  : wave.correction_depth_pct / 100.0,
        "at_sr"             : float(wave.at_support_resistance),
        "wave_entry_score"  : wave.entry_score / 40.0,  # normalize to [0,1]
        "main_dir_up"       : float(wave.main_direction == "UP"),
        "main_dir_down"     : float(wave.main_direction == "DOWN"),
    }


# ──────────────────────────────────────────────────────────────────
# Feature names (for model interpretability)
# ──────────────────────────────────────────────────────────────────

FEATURE_NAMES = [
    # Technical
    "rsi_norm",          # RSI / 100
    "momentum_z",        # Momentum z-score (clipped)
    "macd_hist_norm",    # MACD histogram delta (clipped)
    "bb_pos",            # Bollinger position [0,1]
    "bb_width",          # Bollinger width (volatility proxy)
    "atr_ratio",         # ATR/price
    # Wave
    "wave_active",
    "fib_zone_val",
    "correction_depth",
    "at_sr",
    "wave_entry_score",
    "main_dir_up",
    "main_dir_down",
    # Candle
    "body_ratio",
    "upper_shadow",
    "lower_shadow",
    "candle_dir",
    "consec_up",
    "consec_down",
]

N_FEATURES = len(FEATURE_NAMES)


# ──────────────────────────────────────────────────────────────────
# Main extraction function
# ──────────────────────────────────────────────────────────────────

def extract_features(
    df: pd.DataFrame,
    wave=None,
    signal=None,
) -> Tuple[np.ndarray, dict]:
    """
    Extract a flat feature vector from a candle DataFrame.

    Parameters
    ----------
    df     : DataFrame with OHLC (at least 30 candles)
    wave   : WaveContext from wave_analyzer (optional)
    signal : MarketSignal from brain (optional, for cross-check)

    Returns
    -------
    (feature_vector: np.ndarray shape [N_FEATURES],
     feature_dict: dict with named values for logging)
    """
    if len(df) < 30:
        return np.zeros(N_FEATURES), {}

    close = df["close"]

    # ── Technical indicators ──────────────────────────────────────
    rsi_s   = _rsi(close)
    mom_z_s = _momentum_zscore(close)
    macd_s  = _macd_hist(close)
    bb_pos_s, bb_width_s = _bollinger(close)
    atr_s   = _atr_ratio(df)

    rsi_val      = float(np.clip(rsi_s.iloc[-1] / 100.0, 0, 1))
    mom_z_val    = float(np.clip(mom_z_s.iloc[-1], -3, 3) / 3.0)
    macd_delta   = float(macd_s.iloc[-1]) - float(macd_s.iloc[-2]) if len(macd_s) > 1 else 0.0
    macd_val     = float(np.clip(macd_delta * 100, -1, 1))
    bb_pos_val   = float(np.clip(bb_pos_s.iloc[-1], 0, 1))
    bb_width_val = float(np.clip(bb_width_s.iloc[-1] * 10, 0, 1))  # scale
    atr_val      = float(np.clip(atr_s.iloc[-1] * 100, 0, 1))

    # ── Wave features ─────────────────────────────────────────────
    # Try to get wave from signal first
    if wave is None and signal is not None:
        wave = getattr(signal, "wave", None)
    wave_feats = _encode_wave(wave)

    # ── Candle features ───────────────────────────────────────────
    candle_f = _candle_features(df)
    body_r    = float(candle_f["body_ratio"].iloc[-1])
    upper_s   = float(candle_f["upper_shadow"].iloc[-1])
    lower_s   = float(candle_f["lower_shadow"].iloc[-1])
    cdir      = float(candle_f["candle_dir"].iloc[-1])
    cup       = float(candle_f["consec_up"].iloc[-1])
    cdown     = float(candle_f["consec_down"].iloc[-1])

    feat_dict = {
        "rsi_norm"        : rsi_val,
        "momentum_z"      : mom_z_val,
        "macd_hist_norm"  : macd_val,
        "bb_pos"          : bb_pos_val,
        "bb_width"        : bb_width_val,
        "atr_ratio"       : atr_val,
        **wave_feats,
        "body_ratio"      : body_r,
        "upper_shadow"    : upper_s,
        "lower_shadow"    : lower_s,
        "candle_dir"      : cdir,
        "consec_up"       : cup,
        "consec_down"     : cdown,
    }

    feat_vec = np.array([feat_dict[k] for k in FEATURE_NAMES], dtype=np.float32)
    return feat_vec, feat_dict


def extract_sequence(
    df: pd.DataFrame,
    seq_len: int = None,
    wave=None,
) -> np.ndarray:
    """
    Extract a sequence of feature vectors for LSTM input.

    Returns array shape [seq_len, N_FEATURES]
    """
    seq_len = seq_len or config.ML_FEATURE_WINDOW
    if len(df) < seq_len + 30:
        return np.zeros((seq_len, N_FEATURES), dtype=np.float32)

    sequences = []
    for i in range(len(df) - seq_len, len(df)):
        window = df.iloc[max(0, i - 30): i + 1]
        feat_vec, _ = extract_features(window, wave=wave if i == len(df) - 1 else None)
        sequences.append(feat_vec)

    arr = np.array(sequences[-seq_len:], dtype=np.float32)
    if len(arr) < seq_len:
        # Pad with zeros at the beginning
        pad = np.zeros((seq_len - len(arr), N_FEATURES), dtype=np.float32)
        arr = np.vstack([pad, arr])
    return arr


def build_training_dataset(
    df: pd.DataFrame,
    lookahead: int = None,
    min_score: float = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build labeled dataset from historical candle data.
    Label = 1 if direction (CALL/PUT) was correct after lookahead candles.

    Returns (X: [n_samples, N_FEATURES], y: [n_samples])
    """
    from brain import _score_signal  # lazy import

    lookahead = lookahead or config.SIM_LOOKAHEAD_CANDLES
    min_score = min_score or config.MIN_SIGNAL_SCORE
    warmup    = 60

    X_list, y_list = [], []

    for i in range(warmup, len(df) - lookahead):
        window = df.iloc[:i + 1].copy()
        try:
            sig = _score_signal(window)
        except Exception:
            continue

        if not sig.is_tradeable() or sig.score < min_score:
            continue

        feat_vec, _ = extract_features(window, wave=sig.wave)

        entry = float(df.iloc[i]["close"])
        exit_ = float(df.iloc[i + lookahead]["close"])

        if sig.direction == "CALL":
            label = 1 if exit_ > entry else 0
        elif sig.direction == "PUT":
            label = 1 if exit_ < entry else 0
        else:
            continue

        X_list.append(feat_vec)
        y_list.append(label)

    if not X_list:
        return np.empty((0, N_FEATURES)), np.empty(0)

    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.int32)


def augment_training_dataset(
    X: np.ndarray,
    y: np.ndarray,
    target_multiplier: int = 3,
    seed: Optional[int] = 42,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Augment an existing (X, y) dataset using SignalAugmentor.

    Shortcut wrapper — delegates to SignalAugmentor.augment_all().

    Parameters
    ----------
    X                  : Feature matrix [n, N_FEATURES]
    y                  : Labels [n]
    target_multiplier  : Target size = len(X) × multiplier
    seed               : Random seed

    Returns
    -------
    (X_augmented, y_augmented)
    """
    from synthetic_engine import SignalAugmentor
    aug          = SignalAugmentor(seed=seed)
    target_n     = len(X) * target_multiplier
    return aug.augment_all(X, y, target_n=target_n)


if __name__ == "__main__":
    import deriv_data
    df = deriv_data.fetch_candles(count=200)
    vec, feat_dict = extract_features(df)
    print(f"Feature vector shape: {vec.shape}")
    print("Features:", {k: f"{v:.4f}" for k, v in feat_dict.items()})
    X, y = build_training_dataset(df)
    print(f"Training dataset: X={X.shape} y={y.shape} win_rate={y.mean():.1%}")
