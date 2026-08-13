"""
测试邮箱抓取（P3）：EmailFetcher 单元测试 + /emails/fetch API
"""
import email
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.core.email_fetcher import EmailFetcher
from app.main import app
from config.config import settings

client = TestClient(app)


def make_email_with_attachment(att_filename="resume.pdf", content=b"%PDF-1.4 resume", subject="简历"):
    """构造一封带附件的 MIME 邮件原始字节。"""
    import email.encoders

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = "hr@example.com"
    msg.attach(MIMEText("请查收简历", "plain", "utf-8"))
    part = MIMEBase("application", "octet-stream")
    part.set_payload(content)
    email.encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=att_filename)
    msg.attach(part)
    return msg.as_bytes()


class FakeIMAP:
    """Fake IMAP4_SSL：search 返回指定未读 id，fetch 返回构造邮件。"""

    def __init__(self, host, port, *args, **kwargs):
        self.host = host
        self.port = port
        self.emails = {}  # id -> bytes
        self.read_flags = []
        self.selected = None
        self.logged_out = False

    def login(self, user, password):
        self.user = user
        self.password = password
        return ("OK", [b"LOGIN completed"])

    def select(self, mailbox, readonly=True):
        self.selected = mailbox
        return ("OK", [str(len(self.emails)).encode()])

    def search(self, charset, *criteria):
        ids = [k for k, v in self.emails.items()]
        return ("OK", [b" ".join(ids)])

    def fetch(self, email_id, *args):
        raw = self.emails[email_id]
        return ("OK", [(email_id, raw)])

    def store(self, email_id, flags, flag):
        self.read_flags.append((email_id, flag))
        return ("OK", [])

    def logout(self):
        self.logged_out = True
        return ("OK", [])


@pytest.fixture
def fake_imap(monkeypatch):
    """monkeypatch imaplib.IMAP4_SSL 为 FakeIMAP，返回实例引用。"""
    import imaplib

    fake = FakeIMAP("imap.test.com", 993)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, port: fake)
    return fake


class TestEmailFetcher:
    def test_fetch_new_extracts_attachments(self, fake_imap):
        """抓取未读邮件并提取简历附件"""
        fake_imap.emails = {
            b"1": make_email_with_attachment("resume.pdf", b"%PDF-1.4"),
            b"2": make_email_with_attachment("note.txt", b"hello resume"),
        }
        fetcher = EmailFetcher("imap.test.com", user="u", password="p", mark_read=True)
        results = fetcher.fetch_new(limit=10)

        assert len(results) == 2
        by_id = {r["email_id"]: r for r in results}
        assert by_id["1"]["subject"] == "简历"
        assert by_id["1"]["attachments"][0]["filename"] == "resume.pdf"
        assert by_id["1"]["attachments"][0]["content_bytes"] == b"%PDF-1.4"
        assert by_id["2"]["attachments"][0]["filename"] == "note.txt"
        # 未标记已读（由调用方 mark_read）
        assert fake_imap.read_flags == []

    def test_skips_non_resume_attachments(self, fake_imap):
        """非简历扩展名（.docx）被跳过"""
        fake_imap.emails = {
            b"1": make_email_with_attachment("report.docx", b"docx"),
        }
        fetcher = EmailFetcher("imap.test.com", user="u", password="p")
        results = fetcher.fetch_new()
        assert results == []  # 只有非简历附件 → 无结果

    def test_skips_oversized_attachment(self, fake_imap):
        """超限附件跳过"""
        fake_imap.emails = {
            b"1": make_email_with_attachment("big.pdf", b"x" * (11 * 1024 * 1024)),
        }
        fetcher = EmailFetcher("imap.test.com", user="u", password="p",
                               max_attachment_bytes=10 * 1024 * 1024)
        results = fetcher.fetch_new()
        assert results == []

    def test_mark_read(self, fake_imap):
        """mark_read 标记已读"""
        fetcher = EmailFetcher("imap.test.com", user="u", password="p")
        fetcher.mark_read([b"1", b"2"])
        assert len(fake_imap.read_flags) == 2
        assert all(flag == "\\Seen" for _, flag in fake_imap.read_flags)

    def test_connection_failure_raises(self, monkeypatch):
        """连接/认证失败正常抛异常，且异常消息不含密码"""
        class BoomIMAP(FakeIMAP):
            def login(self, user, password):
                raise RuntimeError("auth failed")

        import imaplib
        monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, port: BoomIMAP(host, port))

        fetcher = EmailFetcher("imap.test.com", user="u", password="SECRET")
        with pytest.raises(RuntimeError, match="auth failed"):
            fetcher.fetch_new()
        try:
            fetcher.fetch_new()
        except RuntimeError as e:
            assert "SECRET" not in str(e)


class TestEmailFetchAPI:
    def test_email_fetch_not_configured(self, monkeypatch):
        """IMAP 未配置 → 400"""
        monkeypatch.setattr(settings, "IMAP_ENABLED", False)
        resp = client.post("/api/v1/emails/fetch", json={"limit": 5})
        assert resp.status_code == 400

    def test_email_fetch_success(self, monkeypatch, fake_imap):
        """配置后触发抓取：简历入库且标记已读"""
        monkeypatch.setattr(settings, "IMAP_ENABLED", True)
        monkeypatch.setattr(settings, "IMAP_HOST", "imap.test.com")
        monkeypatch.setattr(settings, "IMAP_USER", "u")
        monkeypatch.setattr(settings, "IMAP_PASSWORD", "p")
        monkeypatch.setattr(settings, "IMAP_MARK_READ", True)
        monkeypatch.setattr(settings, "UPLOAD_ASYNC", False)

        fake_imap.emails = {b"1": make_email_with_attachment("resume.pdf", b"%PDF")}

        # _process_resume_sync 会真实执行解析——mock 组件避免真实 LLM
        with patch('app.api.routes.document_parser') as mock_dp, \
             patch('app.api.routes.metadata_extractor') as mock_me, \
             patch('app.api.routes.retriever') as mock_ret, \
             patch('app.api.routes.candidate_analyzer') as mock_an:
            mock_dp.parse_pdf.return_value = "简历文本"
            mock_me.extract_metadata.return_value = MagicMock(
                dict=MagicMock(return_value={"name": "张三"}))
            mock_ret.add_resume.return_value = None
            mock_an.analyze_candidate.return_value = {
                "classification": "review", "classification_reason": "ok",
                "classification_source": "llm", "assessment": {}, "strengths": [], "risks": [],
            }

            resp = client.post("/api/v1/emails/fetch", json={"limit": 5})

        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 1
        assert data["results"][0]["subject"] == "简历"
        assert data["results"][0]["resumes"][0]["filename"] == "resume.pdf"
        # 已标记已读
        assert len(fake_imap.read_flags) == 1
        # 简历已入库
        rid = data["results"][0]["resumes"][0]["resume_id"]
        assert client.get(f"/api/v1/resumes/{rid}").status_code == 200


if __name__ == "__main__":
    pytest.main([__file__])
