"""
轻量简历智能体 - 配置读取

配置文件：agent/config.yaml（或环境变量覆盖）
"""
import os
from pathlib import Path
from typing import Any, Dict

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    """读取配置；文件不存在时返回默认空配置（各模块自行兜底）。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return {"collect": {}, "screen": {}, "push": {}}
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # 环境变量覆盖：FEISHU_WEBHOOK 等
    if os.getenv("FEISHU_WEBHOOK"):
        cfg.setdefault("push", {})["feishu_webhook"] = os.getenv("FEISHU_WEBHOOK")
    return cfg
