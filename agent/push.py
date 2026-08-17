"""
轻量简历智能体 - 飞书机器人 Webhook 推送

把筛选通过的候选人名单推送为飞书群消息（免费自定义机器人）。
失败重试 1 次；未配置 webhook 时输出到控制台（便于本地调试）。
"""
import json
from typing import Dict, List

import requests


class FeishuPusher:
    """飞书机器人推送器（Webhook）。"""

    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url

    def push_candidates(self, candidates: List[Dict]) -> bool:
        """推送候选人名单卡片。candidates: [{name, phone, email, skills, reasons, summary}]"""
        if not candidates:
            return True
        if not self.webhook_url:
            # 未配置 webhook：控制台输出（本地调试模式）
            print("=" * 40)
            print(f"【飞书未配置，候选人名单输出到控制台】共 {len(candidates)} 人")
            for c in candidates:
                print(f"  {c.get('name', '?')} | {c.get('phone', '')} | 技能: {', '.join(c.get('skills', [])[:5])}")
            print("=" * 40)
            return True

        lines = [f"📋 简历筛选结果（通过 {len(candidates)} 人）", ""]
        for i, c in enumerate(candidates, 1):
            lines.append(
                f"{i}. {c.get('name', '未知')}  {c.get('phone', '')}"
                f"{'  ' + c.get('email', '') if c.get('email') else ''}"
            )
            if c.get("skills"):
                lines.append(f"   技能：{'、'.join(c.get('skills', [])[:6])}")
            if c.get("summary"):
                lines.append(f"   摘要：{c['summary'][:60]}")
        text = "\n".join(lines)

        payload = {"msg_type": "text", "content": {"text": text}}
        return self._post(payload)

    def push_text(self, text: str) -> bool:
        if not self.webhook_url:
            print(text)
            return True
        return self._post({"msg_type": "text", "content": {"text": text}})

    def _post(self, payload: Dict) -> bool:
        for attempt in range(2):  # 失败重试 1 次
            try:
                resp = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=10,
                    headers={"Content-Type": "application/json"},
                )
                data = resp.json()
                if resp.ok and data.get("code", 0) == 0:
                    return True
                print(f"[push] 飞书返回异常: {data}")
            except Exception as e:
                print(f"[push] 第 {attempt + 1} 次失败: {e}")
        return False
