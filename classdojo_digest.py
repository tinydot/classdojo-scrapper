#!/usr/bin/env python3
"""
ClassDojo Daily Digest
- Logs in with email + password via Playwright
- Intercepts the storyFeed API JSON response (no DOM scraping)
- Summarises new posts with Claude API
- Sends email digest
- Tracks seen posts in SQLite

Setup:
  pip install playwright anthropic python-dotenv
  playwright install chromium

Config:
  Copy .env.example to .env and fill in your values.

Cron (daily at 7am):
  0 7 * * * /usr/bin/python3 /path/to/classdojo_digest.py >> /path/to/classdojo.log 2>&1
"""

import os
import re
import json
import sqlite3
import smtplib
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import anthropic

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
load_dotenv()

CLASSDOJO_EMAIL    = os.environ["CLASSDOJO_EMAIL"]
CLASSDOJO_PASSWORD = os.environ["CLASSDOJO_PASSWORD"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]

SMTP_HOST     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
EMAIL_FROM    = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO      = os.environ["EMAIL_TO"]   # comma-separated for multiple recipients

DB_PATH  = Path(os.environ.get("DB_PATH", "classdojo_seen.db"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"

# ── Database ──────────────────────────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_posts (
            post_id  TEXT PRIMARY KEY,
            seen_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def filter_new(posts: list[dict], conn: sqlite3.Connection) -> list[dict]:
    if not posts:
        return []
    ids = [p["id"] for p in posts]
    placeholders = ",".join("?" * len(ids))
    seen = {row[0] for row in conn.execute(
        f"SELECT post_id FROM seen_posts WHERE post_id IN ({placeholders})", ids
    )}
    return [p for p in posts if p["id"] not in seen]


def mark_seen(posts: list[dict], conn: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO seen_posts (post_id, seen_at) VALUES (?, ?)",
        [(p["id"], now) for p in posts],
    )
    conn.commit()

# ── Feed parser ───────────────────────────────────────────────────────────────
def parse_feed(data: dict) -> list[dict]:
    """
    Parse _items from the storyFeed API response into clean post dicts.

    Sample item shape (from the API):
      _id, time, senderName, headerText, headerSubtext, headerAvatarURL,
      type, contents.body, contents.attachments[], likeCount, commentCount
    """
    posts = []
    for item in data.get("_items", []):
        contents = item.get("contents", {})
        body     = contents.get("body", "").strip()

        # Skip items with no readable content
        if not body:
            continue

        # Attachments
        attachments = []
        for att in contents.get("attachments", []):
            meta = att.get("metadata", {})
            attachments.append({
                "filename": meta.get("filename", "attachment"),
                "mimetype": meta.get("mimetype", ""),
                "url":      att.get("path", ""),
                "type":     att.get("type", ""),
            })

        image_urls = [
            a["url"] for a in attachments if a["mimetype"].startswith("image/")
        ]

        # Format timestamp
        raw_time = item.get("time", "")
        try:
            dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            time_str = dt.strftime("%-d %b %Y, %-I:%M %p")
        except Exception:
            time_str = raw_time

        posts.append({
            "id":            item.get("_id", ""),
            "author":        item.get("senderName") or item.get("headerText", "Unknown"),
            "school":        item.get("headerSubtext", ""),
            "time":          time_str,
            "time_raw":      raw_time,
            "body":          body,
            "attachments":   attachments,
            "image_urls":    image_urls,
            "like_count":    item.get("likeCount", 0),
            "comment_count": item.get("commentCount", 0),
            "type":          item.get("type", ""),
            "avatar_url":    item.get("headerAvatarURL", ""),
        })

    log.info(f"Parsed {len(posts)} posts from feed.")
    return posts

# ── Fetcher: login + intercept API ───────────────────────────────────────────
def fetch_feed(email: str, password: str) -> list[dict]:
    """
    1. Opens ClassDojo in a headless browser and logs in
    2. Listens for the storyFeed API response and captures the JSON
    3. Returns parsed posts — no DOM scraping needed
    """
    captured: dict = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        # Intercept the storyFeed API response
        def handle_response(response):
            if "storyFeed" in response.url and response.status == 200:
                try:
                    captured["data"] = response.json()
                    log.info(f"✓ Captured storyFeed API ({response.url.split('?')[0]})")
                except Exception as exc:
                    log.warning(f"Failed to parse storyFeed JSON: {exc}")

        page.on("response", handle_response)

        try:
            log.info("Navigating to ClassDojo login…")
            page.goto(
                "https://home.classdojo.com/",
                wait_until="networkidle",
                timeout=30_000,
            )

            # Dismiss cookie banner if present
            try:
                page.click("button:has-text('Accept')", timeout=4_000)
            except PlaywrightTimeout:
                pass

            # Fill credentials and submit
            page.fill('input[type="email"], input[name="email"]', email)
            page.fill('input[type="password"], input[name="password"]', password)
            page.click('button[type="submit"]')

            log.info("Submitted login, waiting for redirect…")
            page.wait_for_url(
                re.compile(r"classdojo\.com/(activity|home|app|logged-in)"),
                timeout=25_000,
            )
            log.info(f"Logged in — at {page.url}")

            # If the feed API wasn't triggered on the landing page, navigate to home
            if "data" not in captured:
                log.info("Feed not yet captured — navigating to home feed…")
                page.goto("https://home.classdojo.com/", wait_until="networkidle", timeout=20_000)
                page.wait_for_timeout(4_000)

            # Last resort: try the activity hash route
            if "data" not in captured:
                log.info("Trying #/activity route…")
                page.goto("https://home.classdojo.com/#/activity", wait_until="networkidle", timeout=20_000)
                page.wait_for_timeout(3_000)

        except PlaywrightTimeout as exc:
            log.error(f"Timed out: {exc}")
        except Exception as exc:
            log.error(f"Error during login/fetch: {exc}", exc_info=True)
        finally:
            browser.close()

    if "data" not in captured:
        log.error("storyFeed API response was never captured.")
        return []

    return parse_feed(captured["data"])

# ── Summariser ────────────────────────────────────────────────────────────────
def summarise_posts(posts: list[dict]) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    posts_text = "\n\n---\n\n".join(
        "\n".join([
            f"From: {p['author']} ({p['school']})",
            f"Time: {p['time']}",
            f"Message:\n{p['body']}",
            *(
                [f"Attachments: {', '.join(a['filename'] for a in p['attachments'])}"]
                if p["attachments"] else []
            ),
        ])
        for p in posts
    )

    prompt = f"""You are summarising ClassDojo school updates for a parent in Singapore.
Here are {len(posts)} new post(s) from the school story feed.

For each post:
1. **Headline** — one short bold line
2. Summary — 2-3 warm, plain-English sentences explaining the update
3. Action required — note if the parent needs to do anything (bring items, sign forms, reply, etc.)

If attachments are mentioned, note them. Group similar posts if relevant. Keep the tone friendly.

POSTS:
{posts_text}
"""

    log.info("Calling Claude API…")
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text

# ── Email ─────────────────────────────────────────────────────────────────────
def build_html(summary: str, posts: list[dict]) -> str:
    date_str = datetime.now().strftime("%A, %-d %B %Y")

    def attachment_chips(atts: list[dict]) -> str:
        if not atts:
            return ""
        chips = "".join(
            f'<a href="{a["url"]}" style="display:inline-block;margin:4px 4px 0 0;'
            f'padding:4px 10px;background:#ede9fe;color:#4f46e5;border-radius:99px;'
            f'font-size:12px;text-decoration:none;font-weight:500;">📎 {a["filename"]}</a>'
            for a in atts
        )
        return f'<div style="margin-top:10px;">{chips}</div>'

    posts_html = ""
    for p in posts:
        imgs = "".join(
            f'<img src="{u}" style="max-width:100%;border-radius:6px;margin-top:8px;">'
            for u in p.get("image_urls", [])[:3]
        )
        body_safe = p["body"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        avatar_html = (
            f'<img src="{p["avatar_url"]}" '
            f'style="width:38px;height:38px;border-radius:50%;object-fit:cover;flex-shrink:0;">'
            if p.get("avatar_url") else
            '<div style="width:38px;height:38px;border-radius:50%;background:#e0e7ff;'
            'display:flex;align-items:center;justify-content:center;font-size:18px;">👤</div>'
        )
        posts_html += f"""
        <div style="border:1px solid #e5e7eb;border-radius:10px;padding:16px;
                    margin-bottom:14px;background:white;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
            {avatar_html}
            <div>
              <div style="font-weight:600;color:#111827;font-size:14px;">{p['author']}</div>
              <div style="font-size:12px;color:#6b7280;">{p['school']} &nbsp;·&nbsp; {p['time']}</div>
            </div>
          </div>
          <div style="color:#374151;white-space:pre-wrap;line-height:1.65;font-size:14px;">{body_safe[:900]}{'…' if len(p['body'])>900 else ''}</div>
          {attachment_chips(p.get('attachments', []))}
          {imgs}
          <div style="margin-top:10px;font-size:12px;color:#9ca3af;">
            ❤️ {p['like_count']} &nbsp;&nbsp; 💬 {p['comment_count']}
          </div>
        </div>"""

    summary_html = summary.replace("\n", "<br>")

    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,sans-serif;
                max-width:640px;margin:0 auto;color:#1f2937;">
      <!-- Header -->
      <div style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);
                  padding:28px 24px;border-radius:14px 14px 0 0;">
        <h1 style="margin:0;font-size:22px;font-weight:700;color:white;">📚 ClassDojo Digest</h1>
        <p style="margin:6px 0 0;color:rgba(255,255,255,.8);font-size:14px;">{date_str}</p>
      </div>
      <!-- Body -->
      <div style="background:#f9fafb;padding:24px;border-radius:0 0 14px 14px;">
        <!-- AI Summary -->
        <h2 style="font-size:14px;font-weight:600;color:#4f46e5;
                   margin:0 0 10px;text-transform:uppercase;letter-spacing:.05em;">
          ✨ AI Summary
        </h2>
        <div style="background:white;padding:18px;border-radius:10px;
                    margin-bottom:24px;line-height:1.7;font-size:14px;
                    border:1px solid #e5e7eb;">
          {summary_html}
        </div>
        <!-- Posts -->
        <h2 style="font-size:14px;font-weight:600;color:#4f46e5;
                   margin:0 0 12px;text-transform:uppercase;letter-spacing:.05em;">
          📋 {len(posts)} New Post{'s' if len(posts) != 1 else ''}
        </h2>
        {posts_html}
        <!-- Footer -->
        <p style="font-size:12px;color:#9ca3af;text-align:center;margin-top:20px;">
          <a href="https://home.classdojo.com/" style="color:#6366f1;text-decoration:none;">
            Open ClassDojo
          </a>
          &nbsp;·&nbsp; classdojo_digest.py
        </p>
      </div>
    </div>"""


def send_email(subject: str, body_text: str, body_html: str) -> None:
    recipients = [r.strip() for r in EMAIL_TO.split(",")]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    log.info(f"Sending email to {recipients}…")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())
    log.info("Email sent.")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=== ClassDojo Digest starting ===")
    conn = get_db()

    all_posts = fetch_feed(CLASSDOJO_EMAIL, CLASSDOJO_PASSWORD)
    new_posts  = filter_new(all_posts, conn)
    log.info(f"{len(new_posts)} new post(s) since last run.")

    if not new_posts:
        log.info("Nothing new — no email sent.")
        conn.close()
        return

    summary  = summarise_posts(new_posts)
    date_str = datetime.now().strftime("%A %-d %b")
    n        = len(new_posts)
    subject  = f"📚 ClassDojo – {date_str} ({n} new post{'s' if n != 1 else ''})"

    body_text = (
        f"ClassDojo Digest – {date_str}\n\n{summary}\n\n"
        + "\n\n---\n\n".join(
            f"{p['author']} ({p['time']})\n{p['body']}" for p in new_posts
        )
    )

    send_email(subject, body_text, build_html(summary, new_posts))
    mark_seen(new_posts, conn)
    conn.close()
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
