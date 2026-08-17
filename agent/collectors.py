"""
轻量简历智能体 - 简历收集适配器

- FolderCollector：扫描本地目录（txt/md/pdf，支持子目录），返回新简历
- ImapCollector：IMAP 邮箱未读附件抓取（参考现有 email_fetcher.py 轻量实现）
- 预留 BossCollector：招聘软件投递（待用户调研后实现）
"""
import imaplib
import email
from email.header import decode_header
from pathlib import Path
from typing import Dict, List, Optional

from agent.state import StateStore, fingerprint

SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf")


def _decode_header_value(value) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(str(text))
    return "".join(out)


def _read_text_file(path: Path) -> str:
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def _read_pdf(path: Path) -> str:
    """PDF 文本提取（扫描件返回空文本）。"""
    try:
        import pypdf
        import io
        reader = pypdf.PdfReader(io.BytesIO(path.read_bytes()))
        text = "".join(page.extract_text() or "" for page in reader.pages)
        # 过滤控制字符
        import re
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    except Exception:
        return ""


class FolderCollector:
    """扫描本地文件夹中的简历文件。"""

    def __init__(self, folder_path: str | Path, state: StateStore):
        self.folder = Path(folder_path)
        self.state = state

    def collect(self) -> List[Dict]:
        """返回新简历列表：[{filename, text, fingerprint}]（跳过已处理的）。"""
        if not self.folder.exists():
            return []
        results = []
        for path in sorted(self.folder.rglob("*")):
            if not path.is_file() or not path.name.lower().endswith(SUPPORTED_EXTENSIONS):
                continue
            if path.name.lower().endswith(".pdf"):
                text = _read_pdf(path)
            else:
                text = _read_text_file(path)
            if not text.strip():
                continue
            fp = fingerprint(text)
            if self.state.is_processed(fp):
                continue
            results.append({"filename": path.name, "text": text, "fingerprint": fp})
        return results


class ImapCollector:
    """从 IMAP 邮箱抓取未读邮件中的简历附件。"""

    def __init__(self, host: str, user: str, password: str, state: StateStore,
                 port: int = 993, mailbox: str = "INBOX"):
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.mailbox = mailbox
        self.state = state

    def collect(self, limit: int = 20) -> List[Dict]:
        """返回新简历列表；抓取成功的邮件标记已读。"""
        conn = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            conn.login(self.user, self.password)
            conn.select(self.mailbox, readonly=False)
            _, data = conn.search(None, "UNSEEN")
            ids = (data[0].split() if data and data[0] else [])[-limit:]
            results = []
            for email_id in ids:
                try:
                    _, msg_data = conn.fetch(email_id, "(RFC822)")
                    raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                    msg = email.message_from_bytes(raw)
                    for part in msg.walk():
                        filename = part.get_filename()
                        if not filename:
                            continue
                        filename = _decode_header_value(filename)
                        if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
                            continue
                        content = part.get_payload(decode=True)
                        if content is None:
                            continue
                        try:
                            text = content.decode("utf-8", errors="replace")
                        except Exception:
                            continue
                        fp = fingerprint(text)
                        if self.state.is_processed(fp):
                            continue
                        results.append({"filename": filename, "text": text, "fingerprint": fp})
                    # 该邮件有简历附件才标已读
                    if any(r for r in results if r.get("_email") == email_id):
                        conn.store(email_id, "+FLAGS", "\\Seen")
                except Exception:
                    continue
            return results
        finally:
            try:
                conn.logout()
            except Exception:
                pass


class BossCollector:
    """Boss 直聘 / 51Job 投递简历收集（待调研真实拉取方式后实现）。"""

    def __init__(self, config: dict, state: StateStore):
        self.config = config
        self.state = state

    def collect(self) -> List[Dict]:
        raise NotImplementedError("Boss 直聘拉取方式待调研后实现（config: collect.boss）")
