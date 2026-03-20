# CLAUDE.md — ClassDojo Scraper

Project context for Claude Code sessions.

## What this project does

`classdojo_digest.py` is a single-file Python script that:
1. Logs into ClassDojo via Playwright (headless Chromium)
2. Intercepts the `storyFeed` API JSON response (no DOM scraping)
3. Downloads PDF and image attachments from signed CloudFront URLs
4. Extracts text from attachments via an OCR fallback chain
5. Saves everything to a local SQLite database (`classdojo_seen.db`)
6. Optionally summarises posts with Claude API and sends an email digest (currently disabled)

## Key files

| File | Purpose |
|---|---|
| `classdojo_digest.py` | Main script — all logic lives here |
| `classdojo_seen.db` | SQLite database (auto-created, do not commit) |
| `.env` | Secrets/config (never commit — see `.env.example`) |
| `.env.example` | Template for `.env` |

## Database schema

```sql
seen_posts (post_id TEXT PK, seen_at TEXT)

posts (
  post_id TEXT PK, seen_at TEXT,
  author TEXT, school TEXT,
  time_raw TEXT, time_str TEXT,
  body TEXT, type TEXT,
  like_count INT, comment_count INT, avatar_url TEXT
)

attachments (
  id INT PK AUTOINCREMENT, post_id TEXT FK,
  filename TEXT, mimetype TEXT, url TEXT, att_type TEXT,
  local_path TEXT, ocr_text TEXT, ocr_method TEXT, downloaded_at TEXT
)
```

## OCR pipeline (in `extract_attachment_text`)

Order of attempts, stopping at first success:
1. `pdfplumber` — text-based PDFs
2. `pytesseract + pdf2image` — scanned PDFs / images
3. Claude vision API — **disabled by default** (commented out)

To re-enable Claude vision fallback, uncomment the block at the bottom of `extract_attachment_text()`.

## Currently disabled features

The following are commented out in `main()`:
- `summarise_posts()` — Claude API summarisation
- `send_email()` — email digest sending

These are fully implemented and only need the comments removed to activate.

## Dependencies

Python: `playwright anthropic python-dotenv requests pdfplumber pytesseract Pillow pdf2image`

System — Tesseract OCR:
- **Windows**: `winget install UB-Mannheim.TesseractOCR` (or `choco install tesseract`)
  - If not on PATH, set `pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"`
- **Linux**: `sudo apt install tesseract-ocr`
- **macOS**: `brew install tesseract`

## Development conventions

- Keep all logic in `classdojo_digest.py` — do not split into multiple files unless the file exceeds ~600 lines
- Functions follow the pattern: fetch → parse → filter → process → save → (notify)
- All DB writes go through `save_posts()` and `mark_seen()` — do not write directly in other functions
- Log at `INFO` level for normal flow, `WARNING` for recoverable errors, `ERROR` for fatal ones
- Signed CloudFront URLs in `attachments.url` expire after a few hours; do not rely on them being valid after the same day
