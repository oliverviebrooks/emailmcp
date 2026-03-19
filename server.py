"""
email_mcp — FastMCP server for reading and sending email via IMAP/SMTP.

Usage:
    python server.py

Environment variables (set in .env):
    EMAIL_IMAP_HOST     IMAP server hostname
    EMAIL_IMAP_PORT     IMAP port (default: 993)
    EMAIL_SMTP_HOST     SMTP server hostname
    EMAIL_SMTP_PORT     SMTP port (default: 587)
    EMAIL_USER          Login username (usually your email address)
    EMAIL_PASSWORD      Login password or app password
    EMAIL_FROM          Display name + address for outbound mail, e.g.
                        "Bender Bending Rodriguez <bender@example.com>"
                        Defaults to EMAIL_USER if not set.
"""

import email as emaillib
import imaplib
import os
import smtplib
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("email")

# ── Config ────────────────────────────────────────────────────────────────────

IMAP_HOST = os.environ.get("EMAIL_IMAP_HOST", "")
IMAP_PORT = int(os.environ.get("EMAIL_IMAP_PORT", "993"))
SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", EMAIL_USER)


# ── Connection helpers ─────────────────────────────────────────────────────────

def _imap() -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_USER, EMAIL_PASSWORD)
    return mail


def _smtp():
    if SMTP_PORT == 465:
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    else:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        smtp.starttls()
    smtp.login(EMAIL_USER, EMAIL_PASSWORD)
    return smtp


# ── Message parsing helpers ────────────────────────────────────────────────────

def _parse_headers(msg) -> dict:
    """Extract key headers from a parsed email message."""
    date_str = msg.get("Date", "")
    try:
        date = parsedate_to_datetime(date_str).isoformat()
    except Exception:
        date = date_str
    return {
        "message_id": msg.get("Message-ID", "").strip(),
        "subject": msg.get("Subject", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "date": date,
    }


def _get_text_body(msg) -> str:
    """Extract plain-text body from a (possibly multipart) email."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = part.get("Content-Disposition", "")
            if ct == "text/plain" and "attachment" not in cd:
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
        # Fall back to first text/html part
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        return msg.get_payload(decode=True).decode(charset, errors="replace")
    return ""


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def send_email(to: str, subject: str, body: str) -> dict:
    """
    Send an email.

    Args:
        to: Recipient address, e.g. "user@example.com" or "Name <user@example.com>"
        subject: Email subject line.
        body: Plain-text body.
    """
    try:
        msg = EmailMessage()
        msg["From"] = EMAIL_FROM
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        with _smtp() as smtp:
            smtp.send_message(msg)

        return {"status": "sent", "to": to, "subject": subject}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_inbox(folder: str = "INBOX", limit: int = 10) -> dict:
    """
    List the most recent emails in a folder.

    Args:
        folder: Mailbox folder name (default: INBOX).
        limit: Maximum number of messages to return (default: 10, max: 50).
    """
    try:
        limit = min(limit, 50)
        mail = _imap()
        mail.select(folder, readonly=True)

        _, data = mail.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-limit:][::-1]  # most recent first

        results = []
        for uid in ids:
            _, msg_data = mail.fetch(uid, "(BODY[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
            raw = msg_data[0][1]
            msg = emaillib.message_from_bytes(raw)
            entry = _parse_headers(msg)
            entry["uid"] = uid.decode()
            results.append(entry)

        mail.logout()
        return {"folder": folder, "count": len(results), "messages": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def read_email(uid: str, folder: str = "INBOX") -> dict:
    """
    Read the full content of an email by its UID.

    Args:
        uid: Message UID (from list_inbox or search_emails).
        folder: Mailbox folder containing the message (default: INBOX).
    """
    try:
        mail = _imap()
        mail.select(folder, readonly=True)

        _, msg_data = mail.fetch(uid.encode(), "(RFC822)")
        raw = msg_data[0][1]
        msg = emaillib.message_from_bytes(raw)

        result = _parse_headers(msg)
        result["uid"] = uid
        result["body"] = _get_text_body(msg)

        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get("Content-Disposition", "").startswith("attachment"):
                    attachments.append(part.get_filename() or "unnamed")
        result["attachments"] = attachments

        mail.logout()
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def search_emails(query: str, folder: str = "INBOX", limit: int = 10) -> dict:
    """
    Search for emails matching a query.

    Args:
        query: Search string matched against subject and body (IMAP TEXT search).
               Use ALL to list everything, UNSEEN for unread, FROM name, etc.
        folder: Folder to search in (default: INBOX).
        limit: Maximum results to return (default: 10, max: 50).
    """
    try:
        limit = min(limit, 50)
        mail = _imap()
        mail.select(folder, readonly=True)

        # IMAP search criteria: TEXT searches subject+body, or pass raw criteria
        criteria = f'TEXT "{query}"' if not query.isupper() else query
        _, data = mail.search(None, criteria)
        ids = data[0].split()
        ids = ids[-limit:][::-1]

        results = []
        for uid in ids:
            _, msg_data = mail.fetch(uid, "(BODY[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
            raw = msg_data[0][1]
            msg = emaillib.message_from_bytes(raw)
            entry = _parse_headers(msg)
            entry["uid"] = uid.decode()
            results.append(entry)

        mail.logout()
        return {"query": query, "folder": folder, "count": len(results), "messages": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def reply_to_email(uid: str, body: str, folder: str = "INBOX") -> dict:
    """
    Reply to an email.

    Args:
        uid: UID of the message to reply to.
        body: Plain-text reply body.
        folder: Folder containing the original message (default: INBOX).
    """
    try:
        mail = _imap()
        mail.select(folder, readonly=True)

        _, msg_data = mail.fetch(uid.encode(), "(RFC822)")
        raw = msg_data[0][1]
        orig = emaillib.message_from_bytes(raw)
        mail.logout()

        subject = orig.get("Subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        reply_to = orig.get("Reply-To") or orig.get("From", "")

        msg = EmailMessage()
        msg["From"] = EMAIL_FROM
        msg["To"] = reply_to
        msg["Subject"] = subject
        msg["In-Reply-To"] = orig.get("Message-ID", "")
        msg["References"] = orig.get("Message-ID", "")
        msg.set_content(body)

        with _smtp() as smtp:
            smtp.send_message(msg)

        return {"status": "sent", "to": reply_to, "subject": subject}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_folders() -> dict:
    """List all available mailbox folders."""
    try:
        mail = _imap()
        _, folders = mail.list()
        names = []
        for f in folders:
            parts = f.decode().split('"')
            names.append(parts[-1].strip().strip('"'))
        mail.logout()
        return {"folders": names}
    except Exception as e:
        return {"error": str(e)}


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not all([IMAP_HOST, SMTP_HOST, EMAIL_USER, EMAIL_PASSWORD]):
        raise RuntimeError(
            "Missing required env vars. Set EMAIL_IMAP_HOST, EMAIL_SMTP_HOST, "
            "EMAIL_USER, EMAIL_PASSWORD in .env"
        )
    mcp.run()


if __name__ == "__main__":
    main()
