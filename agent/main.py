"""
轻量简历智能体 - 入口

一条命令跑通：收集新简历 → 硬指标筛选 → 飞书推送 → 记录已处理。

用法：
    python agent/main.py                      # 完整链路
    python agent/main.py --only-collect       # 只收集（调试）
    python agent/main.py --only-screen        # 只筛选（配合 --collect 已收集的缓存？调试用）
    python agent/main.py --dry-run            # 不推送飞书、不标记已处理

部署：阿里云上 cron 定时执行（如每小时），或 Docker 容器内运行。
"""
import argparse
import sys
from pathlib import Path

# 保证可从仓库根目录或 agent/ 目录运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.collectors import FolderCollector, ImapCollector  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.push import FeishuPusher  # noqa: E402
from agent.screen import HardScreener, ScreenConfig, extract_resume  # noqa: E402
from agent.state import StateStore  # noqa: E402


def build_collector(cfg: dict, state: StateStore):
    """按配置构建收集器。"""
    collect_cfg = cfg.get("collect", {})
    source = collect_cfg.get("source", "folder")
    if source == "folder":
        folder = collect_cfg.get("folder_path", "./agent/inbox")
        return FolderCollector(folder, state)
    if source == "imap":
        return ImapCollector(
            host=collect_cfg.get("host", ""),
            user=collect_cfg.get("user", ""),
            password=collect_cfg.get("password", ""),
            port=int(collect_cfg.get("port", 993)),
            state=state,
        )
    if source == "boss":
        from agent.collectors import BossCollector
        return BossCollector(collect_cfg, state)
    raise ValueError(f"未知简历来源: {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description="轻量简历智能体：收集→硬指标筛选→飞书推送")
    parser.add_argument("--only-collect", action="store_true", help="只收集新简历并打印")
    parser.add_argument("--only-screen", action="store_true", help="只对收集的新简历做筛选（不推送）")
    parser.add_argument("--dry-run", action="store_true", help="不推送飞书、不标记已处理")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 agent/config.yaml）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    state = StateStore(cfg.get("collect", {}).get("data_dir", "./agent/data"))

    # ① 收集
    collector = build_collector(cfg, state)
    resumes = collector.collect()
    print(f"① 收集到 {len(resumes)} 份新简历")
    for r in resumes:
        print(f"   - {r['filename']}")
    if not resumes:
        print("（无新简历，结束）")
        return 0

    if args.only_collect:
        return 0

    # ② 硬指标筛选
    screener = HardScreener(ScreenConfig(cfg))
    passed_list, failed_list = [], []
    for r in resumes:
        result = screener.screen(extract_resume(r["text"]))
        item = {
            "filename": r["filename"],
            "name": result["resume"].name,
            "phone": result["resume"].phone,
            "email": result["resume"].email,
            "skills": result["resume"].skills,
            "education": result["resume"].education,
            "experience_years": result["resume"].experience_years,
            "passed_reasons": result["passed_reasons"],
            "failed_reasons": result["failed_reasons"],
            "fingerprint": r["fingerprint"],
        }
        (passed_list if result["passed"] else failed_list).append(item)

    print(f"② 筛选：通过 {len(passed_list)} 人，淘汰 {len(failed_list)} 人")
    for p in passed_list:
        print(f"   [通过] {p['name'] or p['filename']}（{', '.join(p['passed_reasons'])}）")
    for f in failed_list:
        print(f"   [淘汰] {f['name'] or f['filename']}（{', '.join(f['failed_reasons'])}）")

    if args.only_screen:
        return 0

    # ③ 飞书推送（仅通过名单）
    pusher = FeishuPusher(cfg.get("push", {}).get("feishu_webhook", ""))
    candidates = [
        {
            "name": p["name"] or p["filename"],
            "phone": p["phone"],
            "email": p["email"],
            "skills": p["skills"],
            "summary": f"学历 {p['education'] or '?'} · 经验 {p['experience_years']:.0f} 年",
        }
        for p in passed_list
    ]
    ok = pusher.push_candidates(candidates)
    print(f"③ 飞书推送：{'成功' if ok else '失败（未配置 webhook 时已输出到控制台）'}")

    # ④ 记录已处理（防重复推送）
    if not args.dry_run:
        state.mark_processed([r["fingerprint"] for r in resumes])
        print(f"④ 已记录 {len(resumes)} 份简历为已处理")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
