from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None

class BrainStore:
    """Redis-first storage with file fallback. Keeps patch safe in local/dev."""
    def __init__(self, namespace: str = "AI_TRADING_BRAIN", redis_client: Any = None, data_dir: str = "models/ai_trading_brain") -> None:
        self.namespace = namespace
        self.redis = redis_client
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def get_json(self, key: str, default: Any = None) -> Any:
        if self.redis is not None:
            raw = self.redis.get(self._key(key))
            if raw:
                if isinstance(raw, bytes): raw = raw.decode()
                return json.loads(raw)
        path = self.data_dir / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return default

    def set_json(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        if self.redis is not None:
            self.redis.set(self._key(key), payload)
        (self.data_dir / f"{key}.json").write_text(payload, encoding="utf-8")

    def lpush_json(self, key: str, value: Any, trim: int = 5000) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        if self.redis is not None:
            rk = self._key(key)
            self.redis.lpush(rk, payload)
            self.redis.ltrim(rk, 0, trim - 1)
        path = self.data_dir / f"{key}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(payload + "\n")

    def read_jsonl(self, key: str, limit: int = 1000) -> List[Dict[str, Any]]:
        if self.redis is not None:
            rows = self.redis.lrange(self._key(key), 0, limit - 1)
            out = []
            for raw in rows:
                if isinstance(raw, bytes): raw = raw.decode()
                out.append(json.loads(raw))
            return out
        path = self.data_dir / f"{key}.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
