# ClassDojo Daily Digest

A Python script that logs into ClassDojo, intercepts the story feed API, downloads and OCR-extracts attachment content, summarises new posts with Claude AI, and emails you a daily digest — with zero DOM scraping.

## How it works

```
cron (daily)
    └── Playwright opens ClassDojo in a headless browser
        └── Logs in with email + password
            └── Intercepts storyFeed API JSON response
                └── Filters out already-seen posts (SQLite)
                    └── Downloads attachments (PDFs, images)
                        └── Extracts text via OCR pipeline
                            └── Saves full post + attachment data to SQLite
                                └── (Optional) Summarises via Claude API + sends email
```

Instead of scraping HTML elements (which break whenever ClassDojo updates their frontend), the script listens for the internal API call that ClassDojo's own app makes — `storyFeed?withStudentCommentsAndLikes=true` — and parses the structured JSON directly. This makes it robust to UI changes.

## Database

All data is persisted in `classdojo_seen.db` (SQLite). Three tables are created automatically:

| Table | Purpose |
|---|---|
| `seen_posts` | Deduplication — `post_id` + `seen_at` |
| `posts` | Full post metadata — author, school, body, timestamps, type, counts |
| `attachments` | Per-attachment rows — filename, mimetype, download URL, extracted OCR text, OCR method used |

## OCR pipeline

For each attachment the script tries these steps in order, stopping at the first that produces text:

1. **pdfplumber** — fast text extraction for text-based PDFs (no external tools needed)
2. **pytesseract + pdf2image** — rasterises pages and runs Tesseract OCR (for scanned PDFs or images)
3. **Claude vision API** — disabled by default; uncomment in `extract_attachment_text()` to enable as a final fallback

## Requirements

- Python 3.10+
- A ClassDojo parent account (email + password login)
- An [Anthropic API key](https://console.anthropic.com/)
- A Gmail account (or any SMTP provider) for sending the digest
- `tesseract-ocr` system package (for image/scanned-PDF OCR)

## Installation

```bash
# 1. Clone the repo
git clone <your-repo> && cd classdojo-digest

# 2. Install system dependency (Debian/Ubuntu)
sudo apt install tesseract-ocr

# 3. Install Python dependencies
pip install playwright anthropic python-dotenv requests pdfplumber pytesseract Pillow pdf2image

# 4. Install the Chromium browser for Playwright
playwright install chromium
```

## Configuration

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `CLASSDOJO_EMAIL` | ✅ | Your ClassDojo login email |
| `CLASSDOJO_PASSWORD` | ✅ | Your ClassDojo password |
| `ANTHROPIC_API_KEY` | ✅ | From [console.anthropic.com](https://console.anthropic.com/) |
| `SMTP_USER` | ✅ | Your Gmail address |
| `SMTP_PASSWORD` | ✅ | Gmail App Password (not your regular password) |
| `EMAIL_TO` | ✅ | Recipient email(s), comma-separated |
| `SMTP_HOST` | — | Default: `smtp.gmail.com` |
| `SMTP_PORT` | — | Default: `587` |
| `EMAIL_FROM` | — | Default: same as `SMTP_USER` |
| `DB_PATH` | — | Path to SQLite file. Default: `classdojo_seen.db` in current dir |
| `HEADLESS` | — | `true` (default) or `false` to watch the browser |

### Gmail App Password setup

Gmail requires an App Password when 2FA is enabled (recommended):

1. Enable 2-Step Verification at [myaccount.google.com/security](https://myaccount.google.com/security)
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create a new app password — use this as `SMTP_PASSWORD`

## Usage

### Test run (watch the browser)

```bash
HEADLESS=false python3 classdojo_digest.py
```

### Normal run

```bash
python3 classdojo_digest.py
```

### Schedule with cron

Run daily at 7am:

```bash
crontab -e
```

Add this line (update the path):

```
0 7 * * * /usr/bin/python3 /path/to/classdojo_digest.py >> /path/to/classdojo.log 2>&1
```

## What's saved to the database

Each run saves all new posts with:

- Full message body
- Author, school, timestamp
- Attachment download URLs (signed CloudFront URLs — valid for ~24 hours from when the feed was fetched)
- Extracted text from each attachment (`ocr_text` column) and which method extracted it (`ocr_method`)

Query example:

```sql
SELECT p.author, p.time_str, p.body, a.filename, a.ocr_text
FROM posts p
LEFT JOIN attachments a ON a.post_id = p.post_id
ORDER BY p.time_raw DESC;
```

## What's in the email (when enabled)

Each digest includes:

- **AI Summary** — Claude reads all new posts (including OCR-extracted attachment text) and produces a concise, parent-friendly summary with headlines and any action items called out
- **Raw posts** — each post shown with teacher name, school, timestamp, full message body, attachment links (PDF memos etc.), and like/comment counts
- **Direct link** — opens ClassDojo home in one tap

The AI summary and email sending are currently **disabled** in `main()`. Uncomment those lines to re-enable.

## File structure

```
classdojo-digest/
├── classdojo_digest.py   # main script
├── .env                  # your credentials (never commit this)
├── .env.example          # template
├── classdojo_seen.db     # auto-created on first run
└── classdojo.log         # if you redirect cron output here
```

## Troubleshooting

**Script runs but captures 0 posts**
Run with `HEADLESS=false` to watch the browser. The storyFeed API call should fire within a few seconds of landing on the home feed. If ClassDojo has changed their routing, check the browser network tab in a real session to confirm the API URL is still `home.classdojo.com/api/storyFeed`.

**Login fails / times out**
ClassDojo may show a CAPTCHA or 2FA prompt on new devices. Run with `HEADLESS=false` once to complete any one-time verification, then the session cookies should work for subsequent headless runs.

**Attachment download fails**
The signed CloudFront URLs in the feed expire after a few hours. The script must be run while the session is active for downloads to succeed. If you are re-running against old captured data, the URLs may already be expired.

**No OCR text extracted from a PDF**
The PDF is likely a scanned document where pdfplumber finds no embedded text. Ensure `tesseract-ocr`, `pytesseract`, and `pdf2image` are installed. For complex layouts, consider enabling the Claude vision fallback in `extract_attachment_text()`.

**Email not sending**
Check that you are using a Gmail App Password, not your regular Google account password. Regular passwords are blocked by Gmail for SMTP.

**Duplicate posts appearing**
The SQLite database (`classdojo_seen.db`) tracks seen post IDs. Do not delete this file between runs. If you need to reset and re-receive all posts, delete the database and run again.

## Notes

- The storyFeed API response includes signed CloudFront URLs for attachments (PDFs, images). These URLs expire after some hours.
- AI summary and email sending are disabled by default. The script runs as a scraper/archiver.
- ClassDojo's internal API is undocumented and may change without notice. If the script stops working after a ClassDojo update, the most likely fix is updating the login URL or identifying the new feed API URL via browser DevTools.
