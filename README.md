# ClassDojo Daily Digest

A Python script that logs into ClassDojo, intercepts the story feed API, summarises new posts with Claude AI, and emails you a daily digest — with zero DOM scraping.

## How it works

```
cron (daily)
    └── Playwright opens ClassDojo in a headless browser
        └── Logs in with email + password
            └── Intercepts storyFeed API JSON response
                └── Filters out already-seen posts (SQLite)
                    └── Summarises new posts via Claude API
                        └── Sends HTML email digest
                            └── Marks posts as seen in SQLite
```

Instead of scraping HTML elements (which break whenever ClassDojo updates their frontend), the script listens for the internal API call that ClassDojo's own app makes — `storyFeed?withStudentCommentsAndLikes=true` — and parses the structured JSON directly. This makes it robust to UI changes.

## Requirements

- Python 3.10+
- A ClassDojo parent account (email + password login)
- An [Anthropic API key](https://console.anthropic.com/)
- A Gmail account (or any SMTP provider) for sending the digest

## Installation

```bash
# 1. Clone or download the script
git clone <your-repo> && cd classdojo-digest

# 2. Install Python dependencies
pip install playwright anthropic python-dotenv

# 3. Install the Chromium browser for Playwright
playwright install chromium
```

## Configuration

Copy the example env file and fill in your values:

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

## What's in the email

Each digest includes:

- **AI Summary** — Claude reads all new posts and produces a concise, parent-friendly summary with headlines and any action items called out
- **Raw posts** — each post shown with teacher name, school, timestamp, full message body, attachment links (PDF memos etc.), and like/comment counts
- **Direct link** — opens ClassDojo home in one tap

Posts are deduplicated using SQLite — you will only ever receive a post once, no matter how many times the script runs.

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

**Email not sending**
Check that you are using a Gmail App Password, not your regular Google account password. Regular passwords are blocked by Gmail for SMTP.

**Duplicate posts appearing**
The SQLite database (`classdojo_seen.db`) tracks seen post IDs. Do not delete this file between runs. If you need to reset and re-receive all posts, delete the database and run the script again.

## Notes

- The storyFeed API response includes signed CloudFront URLs for attachments (PDFs, images). These URLs expire after some hours, so attachment links in older emails may stop working.
- This script uses the Claude Sonnet model via the Anthropic API. Costs are minimal — a typical digest with a few posts uses well under $0.01 of API credit.
- ClassDojo's internal API is undocumented and may change without notice. If the script stops working after a ClassDojo update, the most likely fix is updating the login URL or waiting for the feed API URL to be identified again via browser DevTools.
