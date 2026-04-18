"""
model_registry.py
=================
Model versioning, performance tracking, và rollback.

Chức năng:
  - Lưu snapshot model sau mỗi lần retrain
  - Ghi nhận performance metrics theo version
  - Tự động rollback về version tốt hơn nếu performance giảm
  - Hỗ trợ so sánh A/B giữa versions
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional

import config


@dataclass
class ModelVersion:
    version_id:    str
    created_at:    str
    model_type:    str          # "win_classifier" | "lstm" | "q_agent" | "ensemble"
    n_train:       int          # Số mẫu train
    train_score:   float        # AUC / accuracy khi train
    eval_score:    float = 0.0  # Live evaluation score (win rate)
    n_live_trades: int   = 0    # Số lệnh live dùng version này
    live_win_rate: float = 0.0  # Win rate thực tế
    is_active:     bool  = True
    notes:         str   = ""


@dataclass
class RegistryState:
    versions:       list[dict] = field(default_factory=list)
    active_version: str        = ""
    last_retrain:   str        = ""
    total_retrains: int        = 0


class ModelRegistry:
    """
    Registry quản lý model versions và rollback.

    Usage:
        reg = ModelRegistry()
        vid = reg.register("win_classifier", n_train=500, train_score=0.72)
        reg.record_live_result(vid, won=True)
        reg.maybe_rollback("win_classifier")
    """

    REGISTRY_FILE = os.path.join(config.ML_MODELS_DIR, "registry.json")
    BACKUP_DIR    = os.path.join(config.ML_MODELS_DIR, "backups")

    def __init__(self) -> None:
        os.makedirs(config.ML_MODELS_DIR, exist_ok=True)
        os.makedirs(self.BACKUP_DIR, exist_ok=True)
        self._state = self._load()

    # ── Persistence ───────────────────────────────────────────────

    def _load(self) -> RegistryState:
        if os.path.exists(self.REGISTRY_FILE):
            try:
                with open(self.REGISTRY_FILE) as f:
                    data = json.load(f)
                return RegistryState(**data)
            except Exception:
                pass
        return RegistryState()

    def _save(self) -> None:
        with open(self.REGISTRY_FILE, "w") as f:
            json.dump(asdict(self._state), f, indent=2)

    # ── Register new version ──────────────────────────────────────

    def register(
        self,
        model_type:  str,
        n_train:     int,
        train_score: float,
        notes:       str = "",
    ) -> str:
        """Register a new model version after training. Returns version_id."""
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        vid = f"{model_type}_{ts}"

        version = ModelVersion(
            version_id  = vid,
            created_at  = datetime.now().isoformat(),
            model_type  = model_type,
            n_train     = n_train,
            train_score = train_score,
            is_active   = True,
            notes       = notes,
        )

        # Back up current model files
        self._backup_current(model_type, vid)

        # Deactivate previous versions of same type
        for v in self._state.versions:
            if v.get("model_type") == model_type:
                v["is_active"] = False

        self._state.versions.append(asdict(version))
        self._state.active_version = vid
        self._state.last_retrain   = datetime.now().isoformat()
        self._state.total_retrains += 1
        self._save()

        print(f"[Registry] Registered {vid}: train_score={train_score:.3f}")
        return vid

    def _backup_current(self, model_type: str, version_id: str) -> None:
        """Backup current model files before overwriting."""
        backup_path = os.path.join(self.BACKUP_DIR, version_id)
        os.makedirs(backup_path, exist_ok=True)
        src_files = {
            "win_classifier": "win_classifier.pkl",
            "lstm"           : "lstm_classifier.pt",
            "q_agent"        : "q_agent.json",
        }
        fname = src_files.get(model_type)
        if fname:
            src = os.path.join(config.ML_MODELS_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(backup_path, fname))

    # ── Live performance tracking ─────────────────────────────────

    def record_live_result(self, version_id: str, won: bool) -> None:
        """Record live trade result for a specific version."""
        for v in self._state.versions:
            if v["version_id"] == version_id:
                v["n_live_trades"] += 1
                # Track wins separately to avoid floating-point accumulation errors
                v.setdefault("live_wins", 0)
                if won:
                    v["live_wins"] += 1
                v["live_win_rate"] = v["live_wins"] / v["n_live_trades"]
                break
        self._save()

    # ── Rollback ──────────────────────────────────────────────────

    def maybe_rollback(self, model_type: str, min_trades: int = 20, threshold: float = 0.48) -> bool:
        """
        Check if current version's live performance is poor.
        If win_rate < threshold after min_trades → rollback to previous best.
        Returns True if rollback was performed.
        """
        versions = [
            v for v in self._state.versions
            if v["model_type"] == model_type and v["n_live_trades"] >= min_trades
        ]
        if len(versions) < 2:
            return False

        # Sort by live_win_rate descending
        versions.sort(key=lambda v: v["live_win_rate"], reverse=True)
        best     = versions[0]
        current  = next((v for v in self._state.versions if v["is_active"] and v["model_type"] == model_type), None)

        if current and current["live_win_rate"] < threshold:
            if best["version_id"] != current["version_id"]:
                print(f"[Registry] Rollback {model_type}: {current['version_id']} (WR={current['live_win_rate']:.1%}) → {best['version_id']} (WR={best['live_win_rate']:.1%})")
                self._restore_backup(model_type, best["version_id"])
                for v in self._state.versions:
                    v["is_active"] = v["version_id"] == best["version_id"]
                self._save()
                return True
        return False

    def _restore_backup(self, model_type: str, version_id: str) -> None:
        backup_path = os.path.join(self.BACKUP_DIR, version_id)
        src_files = {
            "win_classifier": "win_classifier.pkl",
            "lstm"           : "lstm_classifier.pt",
            "q_agent"        : "q_agent.json",
        }
        fname = src_files.get(model_type)
        if fname:
            src = os.path.join(backup_path, fname)
            dst = os.path.join(config.ML_MODELS_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, dst)
                print(f"[Registry] Restored {dst} from backup {version_id}")

    # ── Report ────────────────────────────────────────────────────

    def report(self) -> str:
        lines = [
            f"{'─'*60}",
            f"📦 MODEL REGISTRY — {self._state.total_retrains} retrains",
            f"{'─'*60}",
        ]
        for v in sorted(self._state.versions, key=lambda x: x["created_at"], reverse=True)[:10]:
            active = "✅" if v["is_active"] else "  "
            wr = f"LiveWR={v['live_win_rate']:.1%}({v['n_live_trades']})" if v["n_live_trades"] > 0 else "no live data"
            lines.append(
                f"  {active} {v['version_id']}  "
                f"train={v['train_score']:.3f}  {wr}"
            )
        lines.append(f"{'─'*60}")
        return "\n".join(lines)

    def get_active_version(self, model_type: str) -> Optional[str]:
        for v in reversed(self._state.versions):
            if v["model_type"] == model_type and v["is_active"]:
                return v["version_id"]
        return None


if __name__ == "__main__":
    reg = ModelRegistry()
    vid = reg.register("win_classifier", n_train=500, train_score=0.72)
    reg.record_live_result(vid, won=True)
    reg.record_live_result(vid, won=False)
    print(reg.report())
