# email_mcp

A FastMCP server for reading and sending email over IMAP and SMTP. Supports
listing, reading, searching, replying, and sending messages, plus folder
enumeration. Designed to work with any standard mail provider — Gmail, Outlook,
Fastmail, self-hosted, etc.

---

## Files

| File | Purpose |
|------|---------|
| `server.py` | FastMCP server — IMAP/SMTP tools |
| `agent.py` | Interactive REPL for testing against Ollama |
| `requirements.txt` | Python dependencies |
| `.env.example` | Configuration template |

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your mail credentials
python server.py
```

---

## Configuration (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_IMAP_HOST` | *(required)* | IMAP server hostname |
| `EMAIL_IMAP_PORT` | `993` | IMAP port |
| `EMAIL_SMTP_HOST` | *(required)* | SMTP server hostname |
| `EMAIL_SMTP_PORT` | `587` | SMTP port |
| `EMAIL_USER` | *(required)* | Login username (usually your email address) |
| `EMAIL_PASSWORD` | *(required)* | Login password or app password |
| `EMAIL_FROM` | `EMAIL_USER` | Display name and address for outbound mail |

### SMTP port behaviour

- Port `465` → `SMTP_SSL` (implicit TLS)
- Any other port → `SMTP` with `STARTTLS`

### Gmail setup

Gmail requires an **App Password** — your account password will not work when
2FA is enabled.

1. Go to **Google Account → Security → 2-Step Verification → App passwords**
2. Generate a password for "Mail"
3. Use that 16-character password as `EMAIL_PASSWORD`

```dotenv
EMAIL_IMAP_HOST=imap.gmail.com
EMAIL_IMAP_PORT=993
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_USER=you@gmail.com
EMAIL_PASSWORD=abcd efgh ijkl mnop
EMAIL_FROM=Your Name <you@gmail.com>
```

### Other providers

| Provider | IMAP host | SMTP host | SMTP port |
|----------|-----------|-----------|-----------|
| Outlook / Hotmail | `outlook.office365.com` | `smtp.office365.com` | `587` |
| Fastmail | `imap.fastmail.com` | `smtp.fastmail.com` | `587` |
| iCloud | `imap.mail.me.com` | `smtp.mail.me.com` | `587` |

---

## Tools

### `list_inbox(folder, limit)`

List the most recent emails in a folder. Returns headers only — no body
content. Use `read_email` to fetch the full message.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `folder` | `str` | `"INBOX"` | Mailbox folder name |
| `limit` | `int` | `10` | Maximum messages to return (max 50) |

Returns messages in reverse chronological order (most recent first):

```json
{
  "folder": "INBOX",
  "count": 5,
  "messages": [
    {
      "uid": "42",
      "message_id": "<abc@mail.example.com>",
      "subject": "Hello",
      "from": "Alice <alice@example.com>",
      "to": "you@gmail.com",
      "date": "2026-03-19T12:00:00+00:00"
    }
  ]
}
```

---

### `read_email(uid, folder)`

Read the full content of an email by its UID. Returns headers, plain-text
body, and a list of attachment filenames.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | `str` | required | Message UID from `list_inbox` or `search_emails` |
| `folder` | `str` | `"INBOX"` | Folder containing the message |

Returns:

```json
{
  "uid": "42",
  "message_id": "<abc@mail.example.com>",
  "subject": "Hello",
  "from": "Alice <alice@example.com>",
  "to": "you@gmail.com",
  "date": "2026-03-19T12:00:00+00:00",
  "body": "Hi there!\n\nJust checking in...",
  "attachments": ["report.pdf"]
}
```

Body extraction prefers `text/plain`. If none is present, falls back to
`text/html`. Attachments are listed by filename only — content is not included.

---

### `search_emails(query, folder, limit)`

Search for emails using IMAP search criteria.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | required | Search string or IMAP keyword (see below) |
| `folder` | `str` | `"INBOX"` | Folder to search |
| `limit` | `int` | `10` | Maximum results (max 50) |

**Query behaviour:**

- Lowercase or mixed-case strings → `TEXT "query"` (searches subject + body)
- All-uppercase strings → passed directly as IMAP search criteria

Common IMAP keywords:

| Query | Matches |
|-------|---------|
| `UNSEEN` | Unread messages |
| `ALL` | All messages |
| `FROM alice` | Messages from alice |
| `SUBJECT invoice` | Messages with "invoice" in subject |
| `SINCE 19-Mar-2026` | Messages since a date |

Returns the same shape as `list_inbox` plus a `query` field.

---

### `send_email(to, subject, body)`

Send a plain-text email.

| Parameter | Type | Description |
|-----------|------|-------------|
| `to` | `str` | Recipient address — `user@example.com` or `Name <user@example.com>` |
| `subject` | `str` | Subject line |
| `body` | `str` | Plain-text body |

Returns:

```json
{"status": "sent", "to": "alice@example.com", "subject": "Hello"}
```

---

### `reply_to_email(uid, body, folder)`

Reply to an existing email. Fetches the original message to set `To`,
`Subject` (prefixed with `Re:` if not already), `In-Reply-To`, and
`References` headers correctly.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | `str` | required | UID of the message to reply to |
| `body` | `str` | required | Plain-text reply body |
| `folder` | `str` | `"INBOX"` | Folder containing the original message |

Replies to the `Reply-To` header if present, otherwise to `From`.

Returns:

```json
{"status": "sent", "to": "alice@example.com", "subject": "Re: Hello"}
```

---

### `list_folders()`

List all available mailbox folders on the server. Useful for finding the
correct folder name before calling `list_inbox` or `search_emails` with a
non-INBOX folder (e.g. `Sent`, `Drafts`, `[Gmail]/All Mail`).

Returns:

```json
{"folders": ["INBOX", "Sent", "Drafts", "Trash", "[Gmail]/All Mail"]}
```

---

## agent.py

Interactive REPL that connects to `server.py` as a subprocess (JSON-RPC over
stdio) and drives an Ollama model with its tools.

### Usage

```
python agent.py [--model <model>] [--url <url>]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--model` | `qwen2.5:latest` | Ollama model name |
| `--url` | `http://localhost:11434/v1` | Ollama API base URL |

### Example session

```
Email Agent ready (model: qwen2.5:latest). Type 'quit' to exit.

Tools: ['send_email', 'list_inbox', 'read_email', 'search_emails', 'reply_to_email', 'list_folders']

You: do I have any unread emails?
  [tool] search_emails({"query": "UNSEEN", "limit": 10})
  [result] {"query": "UNSEEN", "folder": "INBOX", "count": 2, "messages": [...]}
```

Type `quit` or `exit` to close the session.
