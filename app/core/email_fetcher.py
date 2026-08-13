"""
邮箱抓取器（IMAP，标准库实现）

职责边界：连接邮箱 → 搜索未读邮件 → 下载简历附件（.pdf/.txt/.md）→ 标记已读。
不负责简历解析、不做邮件分类（解析由上传管线负责）。

安全：密码只保存在私有属性 _password，任何日志/异常路径均不包含它。
"""
import email
import imaplib
from email.header import decode_header
from typing import Any, Dict, List, Optional

from loguru import logger

# 支持的简历附件扩展名
SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".md")


def _decode_header_value(value: Any) -> str:
    """解码邮件头（Subject/From），兼容 RFC2047 编码。"""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                decoded.append(text.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded.append(text.decode("utf-8", errors="replace"))
        else:
            decoded.append(str(text))
    return "".join(decoded)


class EmailFetcher:
    """IMAP 邮箱抓取器。"""

    def __init__(self, host: str, port: int = 993, ssl: bool = True,
                 user: str = "", password: str = "", mailbox: str = "INBOX",
                 mark_read: bool = True,
                 max_attachment_bytes: int = 10 * 1024 * 1024):
        self.host = host
        self.port = port
        self.ssl = ssl
        self.user = user
        self._password = password  # 仅私有属性持有，不入日志
        self.mailbox = mailbox
        # 注意：不存 self.mark_read（会覆盖同名方法），标记已读由调用方负责
        self.max_attachment_bytes = max_attachment_bytes
        self._conn: Optional[imaplib.IMAP4] = None

    def _connect(self) -> imaplib.IMAP4:
        """建立 IMAP 连接并登录。密码仅用于 login，不进入任何日志。"""
        if self.ssl:
            conn = imaplib.IMAP4_SSL(self.host, self.port)
        else:
            conn = imaplib.IMAP4(self.host, self.port)
        conn.login(self.user, self._password)
        self._conn = conn
        return conn

    def fetch_new(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        抓取未读邮件中的简历附件。

        Returns:
            [{"email_id", "subject", "sender", "attachments":
              [{"filename", "content_bytes"}]}]

        本方法不标记已读（由调用方在入库成功后调用 mark_read）。
        """
        conn = self._connect()
        try:
            conn.select(self.mailbox, readonly=False)
            _, data = conn.search(None, "UNSEEN")
            ids = data[0].split() if data and data[0] else []
            # 取最近的 limit 封
            ids = ids[-limit:]
            logger.info(f"Found {len(ids)} unseen emails")

            results = []
            for email_id in ids:
                try:
                    item = self._fetch_one(conn, email_id)
                    if item and item["attachments"]:
                        results.append(item)
                except Exception as e:
                    # 单封邮件失败不中断整体（错误不含密码）
                    logger.warning(f"Failed to fetch email {email_id!r}: {e}")
            return results
        finally:
            self.close()

    def _fetch_one(self, conn: imaplib.IMAP4, email_id: bytes) -> Optional[Dict[str, Any]]:
        _, msg_data = conn.fetch(email_id, "(RFC822)")
        if not msg_data or msg_data[0] is None:
            return None

        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
        msg = email.message_from_bytes(raw)

        attachments = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = part.get_filename()
            if not filename:
                continue
            filename = _decode_header_value(filename)
            if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
                logger.debug(f"Skipping non-resume attachment: {filename}")
                continue
            content = part.get_payload(decode=True)
            if content is None:
                continue
            if len(content) > self.max_attachment_bytes:
                logger.warning(f"Skipping oversized attachment: {filename} "
                               f"({len(content)} bytes > {self.max_attachment_bytes})")
                continue
            attachments.append({"filename": filename, "content_bytes": content})

        if not attachments:
            return None

        return {
            "email_id": email_id.decode("utf-8", errors="replace"),
            "subject": _decode_header_value(msg.get("Subject", "")),
            "sender": _decode_header_value(msg.get("From", "")),
            "attachments": attachments,
        }

    def mark_read(self, email_ids: List[bytes]) -> None:
        """把指定邮件标记为已读。单封失败仅告警，不影响已入库数据。"""
        if not email_ids:
            return
        conn = self._connect()
        try:
            conn.select(self.mailbox, readonly=False)
            for email_id in email_ids:
                try:
                    conn.store(email_id, "+FLAGS", "\\Seen")
                except Exception as e:
                    logger.warning(f"Failed to mark email {email_id!r} as read: {e}")
        finally:
            self.close()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None
