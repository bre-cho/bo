"""
ml_models.py
============
ML/DL Model Stack cho hệ thống giao dịch tự động.

Models:
  WinClassifier      — XGBoost + Logistic Regression ensemble (sklearn VotingClassifier)
  QLearningAgent     — Tabular Q-Learning với state = feature fingerprint
  LSTMWaveClassifier — PyTorch LSTM cho chuỗi nến
  EnsembleScorer     — Kết hợp tất cả models với trọng số

Fallback: nếu XGBoost hoặc PyTorch không có → dùng Logistic Regression + random
"""

from __future__ import annotations

import json
import os
import pickle
import warnings
from collections import defaultdict
from typing import Optional, Tuple

import numpy as np
import pandas as pd

import config

warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────────────────────────
# Optional imports with graceful fallback
# ──────────────────────────────────────────────────────────────────

try:
    import xgboost as xgb
    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False
    print("[ML] XGBoost not found — WinClassifier will use LogisticRegression only")

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import VotingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.calibration import CalibratedClassifierCV
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False
    print("[ML] scikit-learn not found — ML models disabled")

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False
    print("[ML] PyTorch not found — LSTMWaveClassifier disabled")


# ──────────────────────────────────────────────────────────────────
# WinClassifier — XGBoost + Logistic Regression
# ──────────────────────────────────────────────────────────────────

class WinClassifier:
    """
    Binary classifier: dự đoán xác suất thắng (win_prob).

    Architecture:
      - XGBoost (if available)
      - Logistic Regression (always)
      - VotingClassifier (soft voting) — combines both

    Calibration: Platt scaling để xác suất đáng tin cậy hơn.
    """

    MODEL_FILE = os.path.join(config.ML_MODELS_DIR, "win_classifier.pkl")

    def __init__(self) -> None:
        self._model    = None
        self._scaler   = None
        self._trained  = False
        os.makedirs(config.ML_MODELS_DIR, exist_ok=True)

    def _build_model(self):
        if not _HAS_SKLEARN:
            return None
        estimators = []
        if _HAS_XGB:
            estimators.append(("xgb", xgb.XGBClassifier(
                n_estimators=200, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                use_label_encoder=False, eval_metric="logloss",
                verbosity=0, random_state=42,
            )))
        estimators.append(("lr", LogisticRegression(
            C=1.0, max_iter=500, random_state=42, solver="lbfgs",
        )))
        if len(estimators) == 1:
            base = estimators[0][1]
        else:
            base = VotingClassifier(estimators=estimators, voting="soft")
        return CalibratedClassifierCV(base, cv=3, method="sigmoid")

    def train(self, X: np.ndarray, y: np.ndarray) -> float:
        """Train on labeled data. Returns CV accuracy."""
        if not _HAS_SKLEARN or len(X) < config.ML_MIN_TRAIN_SAMPLES:
            print(f"[WinClassifier] Skip training: samples={len(X)}, sklearn={_HAS_SKLEARN}")
            return 0.0

        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler

        self._scaler = StandardScaler()
        X_scaled     = self._scaler.fit_transform(X)
        self._model  = self._build_model()

        cv_scores = cross_val_score(
            self._build_model(), X_scaled, y, cv=min(5, len(X) // 20),
            scoring="roc_auc", error_score=0.5,
        )
        self._model.fit(X_scaled, y)
        self._trained = True
        acc = float(np.mean(cv_scores))
        print(f"[WinClassifier] Trained: n={len(X)} AUC={acc:.3f}")
        self._save()
        return acc

    def predict_proba(self, x: np.ndarray) -> float:
        """Returns win probability [0, 1] for a single sample."""
        if not self._trained or self._model is None:
            return 0.5  # Neutral fallback
        try:
            x_s = self._scaler.transform(x.reshape(1, -1))
            return float(self._model.predict_proba(x_s)[0][1])
        except Exception:
            return 0.5

    def _save(self) -> None:
        with open(self.MODEL_FILE, "wb") as f:
            pickle.dump({"model": self._model, "scaler": self._scaler}, f)

    def load(self) -> bool:
        if not os.path.exists(self.MODEL_FILE):
            return False
        try:
            with open(self.MODEL_FILE, "rb") as f:
                data = pickle.load(f)
            self._model   = data["model"]
            self._scaler  = data["scaler"]
            self._trained = True
            print(f"[WinClassifier] Loaded from {self.MODEL_FILE}")
            return True
        except Exception as exc:
            print(f"[WinClassifier] Load failed: {exc}")
            return False


# ──────────────────────────────────────────────────────────────────
# QLearningAgent — Tabular Q-Learning
# ──────────────────────────────────────────────────────────────────

class QLearningAgent:
    """
    Tabular Q-Learning agent.

    State = (score_band, fib_zone, wave_active, rsi_band, hour_bucket)
    Actions = [0=skip, 1=enter]
    Reward = pnl / stake (normalised)

    Q-table stored in Redis + local dict.
    """

    REDIS_KEY  = "Deriv_QTable"
    FILE_PATH  = os.path.join(config.ML_MODELS_DIR, "q_agent.json")

    def __init__(self, alpha=0.1, gamma=0.9, epsilon=0.1) -> None:
        self.alpha   = alpha    # Learning rate
        self.gamma   = gamma    # Discount factor
        self.epsilon = epsilon  # Exploration rate
        self._q: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        os.makedirs(config.ML_MODELS_DIR, exist_ok=True)
        self.load()

    # ── State encoding ────────────────────────────────────────────

    @staticmethod
    def _encode_state(feat_dict: dict) -> str:
        """Encode feature dict into discrete state string."""
        score    = feat_dict.get("rsi_norm", 0.5) * 100
        if score >= 80:   score_band = "80+"
        elif score >= 65: score_band = "65-80"
        else:             score_band = "50-65"

        fib = feat_dict.get("fib_zone_val", 0.0)
        if fib >= 0.6:    fib_band = "golden"
        elif fib >= 0.4:  fib_band = "mid"
        elif fib > 0:     fib_band = "shallow"
        else:             fib_band = "none"

        wave  = "W" if feat_dict.get("wave_active", 0) > 0.5 else "N"
        rsi   = feat_dict.get("rsi_norm", 0.5) * 100
        rbnd  = "OS" if rsi < 35 else ("OB" if rsi > 65 else "NT")
        from datetime import datetime
        hour  = datetime.utcnow().hour
        hbkt  = "night" if hour < 6 else ("morn" if hour < 12 else ("aft" if hour < 18 else "eve"))

        return f"{score_band}:{fib_band}:{wave}:{rbnd}:{hbkt}"

    # ── Q-Learning update ─────────────────────────────────────────

    def update(self, feat_dict: dict, action: int, reward: float, next_feat_dict: dict) -> None:
        """Update Q-table after a trade result."""
        state      = self._encode_state(feat_dict)
        next_state = self._encode_state(next_feat_dict) if next_feat_dict else state

        q_old  = self._q[state][action]
        q_next = max(self._q[next_state])
        q_new  = q_old + self.alpha * (reward + self.gamma * q_next - q_old)
        self._q[state][action] = q_new

    def choose_action(self, feat_dict: dict) -> Tuple[int, float]:
        """
        Choose action (0=skip, 1=enter) using epsilon-greedy policy.
        Returns (action, q_value_for_enter).
        """
        if np.random.random() < self.epsilon:
            return np.random.randint(0, 2), 0.5

        state  = self._encode_state(feat_dict)
        q_vals = self._q[state]
        action = int(np.argmax(q_vals))
        q_enter = q_vals[1]  # Q-value for action=1 (enter)
        return action, float(q_enter)

    def win_prob_estimate(self, feat_dict: dict) -> float:
        """Convert Q-value for enter action into [0,1] probability estimate."""
        _, q_enter = self.choose_action(feat_dict)
        # Q values can be negative/positive — map to [0.3, 0.7]
        return float(np.clip(0.5 + q_enter * 0.2, 0.3, 0.7))

    def train_from_history(self, trade_history: list[dict]) -> None:
        """Batch-update Q-table from trade log."""
        for i, record in enumerate(trade_history):
            feat_dict = {
                "rsi_norm"    : float(record.get("rsi", 50)) / 100,
                "wave_active" : 1.0 if record.get("wave_active") else 0.0,
                "fib_zone_val": 0.5 if record.get("fib_zone") else 0.0,
            }
            stake  = float(record.get("stake", 1.0))
            pnl    = float(record.get("pnl", 0.0))
            reward = pnl / stake if stake > 0 else 0.0
            # Use same state as next state at end of history (no future info)
            if i + 1 < len(trade_history):
                next_f = {
                    "rsi_norm"    : float(trade_history[i+1].get("rsi", 50)) / 100,
                    "wave_active" : 1.0 if trade_history[i+1].get("wave_active") else 0.0,
                    "fib_zone_val": 0.5 if trade_history[i+1].get("fib_zone") else 0.0,
                }
            else:
                next_f = feat_dict
            self.update(feat_dict, action=1, reward=reward, next_feat_dict=next_f)
        self.save()
        print(f"[QAgent] Trained on {len(trade_history)} trades. States={len(self._q)}")

    def save(self) -> None:
        with open(self.FILE_PATH, "w") as f:
            json.dump(dict(self._q), f)

    def load(self) -> bool:
        if not os.path.exists(self.FILE_PATH):
            return False
        try:
            with open(self.FILE_PATH) as f:
                data = json.load(f)
            valid = {k: v for k, v in data.items() if isinstance(v, list) and len(v) == 2}
            self._q = defaultdict(lambda: [0.0, 0.0], valid)
            print(f"[QAgent] Loaded Q-table: {len(self._q)} states")
            return True
        except Exception as exc:
            print(f"[QAgent] Load failed: {exc}")
            return False


# ──────────────────────────────────────────────────────────────────
# LSTMWaveClassifier — PyTorch LSTM
# ──────────────────────────────────────────────────────────────────

if _HAS_TORCH:
    class _LSTMModel(nn.Module):
        def __init__(self, input_size: int, hidden_size: int = 64, num_layers: int = 2):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size, hidden_size, num_layers=num_layers,
                batch_first=True, dropout=0.3,
            )
            self.fc = nn.Sequential(
                nn.Linear(hidden_size, 32),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.Linear(32, 1),
                nn.Sigmoid(),
            )

        def forward(self, x):
            _, (h_n, _) = self.lstm(x)
            return self.fc(h_n[-1]).squeeze(-1)


class LSTMWaveClassifier:
    """
    LSTM sequence classifier for wave pattern recognition.
    Input: sequence of [seq_len, N_FEATURES] feature vectors.
    Output: win probability [0, 1].
    """

    MODEL_FILE = os.path.join(config.ML_MODELS_DIR, "lstm_classifier.pt")

    def __init__(self, seq_len: int = None, n_features: int = None) -> None:
        from feature_pipeline import N_FEATURES as _NF
        self.seq_len   = seq_len  or config.ML_FEATURE_WINDOW
        self.n_features= n_features or _NF
        self._model    = None
        self._trained  = False
        os.makedirs(config.ML_MODELS_DIR, exist_ok=True)
        if _HAS_TORCH:
            self._model = _LSTMModel(self.n_features)

    def train(self, X_seq: np.ndarray, y: np.ndarray,
              epochs: int = 30, batch_size: int = 32) -> float:
        """
        Train LSTM.
        X_seq shape: [n_samples, seq_len, n_features]
        y shape: [n_samples]
        Returns final validation accuracy.
        """
        if not _HAS_TORCH or len(X_seq) < config.ML_MIN_TRAIN_SAMPLES:
            print(f"[LSTM] Skip training: samples={len(X_seq)}, torch={_HAS_TORCH}")
            return 0.0

        device     = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        X_t        = torch.FloatTensor(X_seq).to(device)
        y_t        = torch.FloatTensor(y).to(device)
        dataset    = TensorDataset(X_t, y_t)

        # Train/val split
        n_val      = max(1, int(len(dataset) * 0.2))
        n_train    = len(dataset) - n_val
        train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

        train_dl   = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
        val_dl     = DataLoader(val_ds, batch_size=batch_size)

        self._model = _LSTMModel(self.n_features).to(device)
        optimizer   = optim.Adam(self._model.parameters(), lr=1e-3, weight_decay=1e-4)
        criterion   = nn.BCELoss()
        scheduler   = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5)

        best_val_acc = 0.0
        for epoch in range(epochs):
            self._model.train()
            for xb, yb in train_dl:
                optimizer.zero_grad()
                pred = self._model(xb)
                loss = criterion(pred, yb)
                loss.backward()
                optimizer.step()

            # Validation
            self._model.eval()
            correct, total = 0, 0
            with torch.no_grad():
                for xb, yb in val_dl:
                    pred   = self._model(xb) > 0.5
                    correct += (pred.float() == yb).sum().item()
                    total   += len(yb)
            val_acc = correct / total if total > 0 else 0.0
            scheduler.step(1 - val_acc)
            if val_acc > best_val_acc:
                best_val_acc = val_acc
                torch.save(self._model.state_dict(), self.MODEL_FILE)

        self._trained = True
        print(f"[LSTM] Trained: n={len(X_seq)} best_val_acc={best_val_acc:.3f}")
        return best_val_acc

    def predict_proba(self, x_seq: np.ndarray) -> float:
        """Predict win probability for a sequence [seq_len, n_features]."""
        if not _HAS_TORCH or not self._trained or self._model is None:
            return 0.5
        try:
            self._model.eval()
            device = next(self._model.parameters()).device
            x_t    = torch.FloatTensor(x_seq).unsqueeze(0).to(device)
            with torch.no_grad():
                return float(self._model(x_t).item())
        except Exception:
            return 0.5

    def load(self) -> bool:
        if not _HAS_TORCH or not os.path.exists(self.MODEL_FILE):
            return False
        try:
            self._model = _LSTMModel(self.n_features)
            self._model.load_state_dict(torch.load(self.MODEL_FILE, map_location="cpu"))
            self._model.eval()
            self._trained = True
            print(f"[LSTM] Loaded from {self.MODEL_FILE}")
            return True
        except Exception as exc:
            print(f"[LSTM] Load failed: {exc}")
            return False


# ──────────────────────────────────────────────────────────────────
# EnsembleScorer — weighted voting of all models
# ──────────────────────────────────────────────────────────────────

class EnsembleScorer:
    """
    Kết hợp WinClassifier + QLearningAgent + LSTMWaveClassifier.

    Weights từ config (ML_ENSEMBLE_WEIGHT_WIN/QLEARN/LSTM).
    Nếu một model chưa train → trọng số của nó phân bổ cho các model còn lại.
    """

    def __init__(self) -> None:
        self.win_clf  = WinClassifier()
        self.q_agent  = QLearningAgent()
        self.lstm_clf = LSTMWaveClassifier()
        self._loaded  = False

    def load_all(self) -> None:
        """Try to load all models from disk."""
        self.win_clf.load()
        self.lstm_clf.load()
        self._loaded = True

    def score(
        self,
        feat_vec:  np.ndarray,
        feat_dict: dict,
        seq:       Optional[np.ndarray] = None,
    ) -> Tuple[float, dict]:
        """
        Compute ensemble win probability.

        Parameters
        ----------
        feat_vec  : flat feature vector [N_FEATURES]
        feat_dict : named feature dict (for Q-agent)
        seq       : sequence [seq_len, N_FEATURES] for LSTM (optional)

        Returns
        -------
        (ensemble_prob: float, breakdown: dict)
        """
        w_win   = config.ML_ENSEMBLE_WEIGHT_WIN
        w_q     = config.ML_ENSEMBLE_WEIGHT_QLEARN
        w_lstm  = config.ML_ENSEMBLE_WEIGHT_LSTM

        p_win   = self.win_clf.predict_proba(feat_vec)
        p_q     = self.q_agent.win_prob_estimate(feat_dict)
        p_lstm  = self.lstm_clf.predict_proba(seq) if seq is not None else 0.5

        # If LSTM not available / no seq → redistribute its weight
        if seq is None or not self.lstm_clf._trained:
            extra     = w_lstm / 2.0
            w_win    += extra
            w_q      += extra
            p_lstm    = 0.5
            w_lstm    = 0.0

        total  = w_win + w_q + w_lstm
        if total <= 0:
            ensemble = 0.5
        else:
            ensemble = (w_win * p_win + w_q * p_q + w_lstm * p_lstm) / total

        breakdown = {
            "win_clf_prob"  : round(p_win, 4),
            "q_agent_prob"  : round(p_q, 4),
            "lstm_prob"     : round(p_lstm, 4),
            "ensemble_prob" : round(float(ensemble), 4),
            "w_win"         : round(w_win, 3),
            "w_q"           : round(w_q, 3),
            "w_lstm"        : round(w_lstm, 3),
        }
        return float(ensemble), breakdown

    def retrain_all(self, df: pd.DataFrame, trade_history: list[dict]) -> None:
        """Retrain all models from current candle library + trade history."""
        from feature_pipeline import build_training_dataset, extract_sequence

        print("[Ensemble] Starting retrain cycle...")

        # 1. Build training dataset
        X, y = build_training_dataset(df)
        if len(X) >= config.ML_MIN_TRAIN_SAMPLES:
            self.win_clf.train(X, y)

        # 2. LSTM sequences (this is expensive — sample a subset)
        if _HAS_TORCH and len(df) >= config.ML_FEATURE_WINDOW + 100:
            try:
                self._retrain_lstm(df, y)
            except Exception as exc:
                print(f"[Ensemble] LSTM retrain failed: {exc}")

        # 3. Q-learning from trade history
        if trade_history:
            self.q_agent.train_from_history(trade_history)

        self._loaded = True
        print("[Ensemble] Retrain complete.")

    def _retrain_lstm(self, df: pd.DataFrame, y: np.ndarray) -> None:
        from feature_pipeline import extract_sequence, N_FEATURES, build_training_dataset
        seq_len = config.ML_FEATURE_WINDOW
        warmup  = 60
        X_seqs, y_list = [], []
        for i in range(warmup + seq_len, len(df) - config.SIM_LOOKAHEAD_CANDLES):
            window = df.iloc[:i + 1]
            try:
                from brain import _score_signal
                sig = _score_signal(window)
                if not sig.is_tradeable():
                    continue
                seq = extract_sequence(window, seq_len)
                entry = float(df.iloc[i]["close"])
                exit_ = float(df.iloc[i + config.SIM_LOOKAHEAD_CANDLES]["close"])
                if sig.direction == "CALL":
                    label = 1 if exit_ > entry else 0
                elif sig.direction == "PUT":
                    label = 1 if exit_ < entry else 0
                else:
                    continue
                X_seqs.append(seq)
                y_list.append(label)
            except Exception:
                continue

        if len(X_seqs) >= config.ML_MIN_TRAIN_SAMPLES:
            self.lstm_clf.train(
                np.array(X_seqs), np.array(y_list, dtype=np.float32)
            )


if __name__ == "__main__":
    import deriv_data
    from feature_pipeline import extract_features, build_training_dataset

    df = deriv_data.fetch_candles(count=200)
    X, y = build_training_dataset(df)
    print(f"Dataset: X={X.shape} y={y.shape}")

    scorer = EnsembleScorer()
    if len(X) >= config.ML_MIN_TRAIN_SAMPLES:
        scorer.win_clf.train(X, y)

    feat_vec, feat_dict = extract_features(df)
    prob, breakdown = scorer.score(feat_vec, feat_dict)
    print(f"Ensemble win prob: {prob:.3f}")
    print("Breakdown:", breakdown)
