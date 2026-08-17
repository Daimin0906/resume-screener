"""
轻量简历智能体 - 状态持久化（防重复推送）

记录已处理简历的指纹（文件内容哈希），避免每次运行重复推送。
"""
import hashlib
import json
import os
from pathlib import Path
from typing import Set


class StateStore:
    """已处理简历指纹存储（JSON，原子写）。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "processed.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Set[str]:
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return set(data.get("processed", []))
        except (json.JSONDecodeError, OSError):
            return set()

    def _save(self, processed: Set[str]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"processed": sorted(processed)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def is_processed(self, fingerprint: str) -> bool:
        return fingerprint in self._load()

    def mark_processed(self, fingerprints: list[str]) -> None:
        processed = self._load()
        processed.update(fingerprints)
        # 上限保护：最多保留 10000 个指纹
        if len(processed) > 10000:
            processed = set(sorted(processed)[-10000:])
        self._save(processed)


def fingerprint(text: str) -> str:
    """简历内容指纹（sha256 前 16 位）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
