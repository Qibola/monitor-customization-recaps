#!/usr/bin/env python3
"""
Slack Typeform Recap Bot
- DAILY  : Posts today's Typeform message count (00:00 local -> now)
- WEEKLY : Posts a 7-day bar chart of Typeform counts (last 7 full local days, ending yesterday)
- DRYRUN : Prints the same summaries to stdout (no Slack post)

Env vars (required):
  SLACK_BOT_TOKEN      xoxb-...
  CHANNEL_ID           Target channel to analyze (C... or G...)
Optional:
  POST_TO_CHANNEL_ID   Channel to post recaps to (defaults to CHANNEL_ID)
  TZ_NAME              IANA tz (default: America/Denver)

Scopes needed (public channel): channels:read, channels:history, chat:write
If the channel is private, also add: groups:read, groups:history
Make sure the bot is invited to the channel you are analyzing/posting to.
"""

import os
import math
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


# ---------- Config / Globals ----------

TZ_NAME = os.environ.get("TZ_NAME", "America/Denver")
TZ = ZoneInfo(TZ_NAME)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
# IMPORTANT: treat empty as unset; fall back to CHANNEL_ID
POST_TO_CHANNEL_ID = os.environ.get("POST_TO_CHANNEL_ID") or CHANNEL_ID

client = WebClient(token=SLACK_BOT_TOKEN)


# ---------- Helpers ----------

def _ts(dt: datetime) -> float:
    return dt.timestamp()


def _list_messages(channel_id: str, oldest: float, latest: float):
    """
    Iterate over messages in [oldest, latest) with pagination and basic 429 handling.
    """
    cursor = None
    while True:
        try:
            resp = client.conversations_history(
                channel=channel_id,
                oldest=str(oldest),
                latest=str(latest),
                inclusive=False,   # [oldest, latest)
                limit=200,
                cursor=cursor,
            )
        except SlackApiError as e:
            # Back off on rate limits
            if e.response and e.response.status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", "1"))
                time.sleep(retry_after + 1)
                continue
            raise
        for m in resp.get("messages", []):
            yield m
        if not resp.get("has_more"):
            break
        cursor = resp.get("response_metadata", {}).get("next_cursor")


def _is_typeform_message(msg: dict) -> bool:
    """
    Return True if this Slack message is from the Typeform app/bot.
    We intentionally *ignore human messages* and count *only* Typeform posts.
    """
    # Ignore Slack housekeeping subtypes (edits/deletes/joins/etc.)
    subtype = msg.get("subtype")
    if subtype in {
        "channel_join", "channel_leave", "channel_topic", "channel_purpose",
        "channel_name", "message_deleted", "message_changed"
    }:
        return False

    # Many app messages arrive as subtype='bot_message' with bot_profile.name
    bp = msg.get("bot_profile") or {}
    name_candidates = [
        (bp.get("name") or "").lower(),
        (bp.get("username") or "").lower(),
        (msg.get("username") or "").lower(),
    ]

    # Heuristic: treat anything that clearly says "typeform" as Typeform
    if any("typeform" in s for s in name_candidates):
        return True

    # If it's a human message, skip (we only want Typeform)
    if msg.get("user"):
        return False

    # Fallback: do not count
    return False


def _day_window_local(day: datetime):
    """
    For a local calendar day (day.tzinfo must be TZ), return (oldest_ts, latest_ts, start_dt, end_dt).
    """
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=TZ)
    end = start + timedelta(days=1)
    return _ts(start), _ts(end), start, end


def _today_window_until_now():
    """
    From local midnight to 'now' (local).
    """
    now = datetime.now(TZ)
    start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=TZ)
    return _ts(start), _ts(now), start, now


def _date_label(dt: datetime) -> str:
    # Example: "Sun Sep 14"
    try:
        return dt.strftime("%a %b %-d")
    except ValueError:
        # Windows/Python formatting fallback (no %-d)
        return dt.strftime("%a %b %d").replace(" 0", " ")


def _bar_chart(rows):
    """
    rows: list[tuple[str,int]] like [("Mon 9/8", 12), ...]
    Returns a fenced code block with a simple mono bar chart.
    """
    if not rows:
        return ""
    maxv = max(v for _, v in rows) or 1
    lines = []
    for label, v in rows:
        n = 0 if v == 0 else max(1, math.ceil((v / maxv) * 20))
        lines.append(f"{label:>12} | {'█' * n} {v}")
    return "```\n" + "\n".join(lines) + "\n```"


# ---------- Summaries (Typeform only) ----------

def summarize_typeform_today(channel_id: str):
    """
    Count Typeform messages today so far (local day start -> now).
    """
    oldest, latest, start_dt, _ = _today_window_until_now()
    count = 0
    for msg in _list_messages(channel_id, oldest, latest):
        if _is_typeform_message(msg):
            count += 1
    return {
        "date_label": _date_label(start_dt),
        "total": count,
    }


def summarize_typeform_for_day(channel_id: str, day: datetime):
    """
    Count Typeform messages for the full local calendar day.
    """
    oldest, latest, start_dt, _ = _day_window_local(day)
    count = 0
    for msg in _list_messages(channel_id, oldest, latest):
        if _is_typeform_message(msg):
            count += 1
    return {
        "date_label": _date_label(start_dt),
        "total": count,
    }


def summarize_typeform_week(channel_id: str, end_day_inclusive: datetime):
    """
    7 full local days ending with end_day_inclusive.
    Returns list of day dicts (oldest -> newest).
    """
    days = []
    for i in range(6, -1, -1):
        d = end_day_inclusive - timedelta(days=i)
        days.append(summarize_typeform_for_day(channel_id, d))
    return days


# ---------- Posting ----------

def post_daily_typeform_today(channel_id: str):
    s = summarize_typeform_today(channel_id)
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
         "text": f"Daily recap – {s['date_label']} (so far)"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Typeform messages today:* {s['total']}"}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"Channel: <#{channel_id}> • Counting only Typeform bot posts • Timezone: {TZ.key}"}
        ]},
    ]
    client.chat_postMessage(channel=POST_TO_CHANNEL_ID, text="Daily Typeform recap", blocks=blocks)


def post_weekly_typeform(channel_id: str, now_local: datetime):
    """
    Weekly at 5pm local (or whenever you run it): show the last 7 *full* days, ending yesterday.
    """
    yesterday = datetime(now_local.year, now_local.month, now_local.day, tzinfo=TZ) - timedelta(days=1)
    week = summarize_typeform_week(channel_id, yesterday)
    rows = [(d["date_label"], d["total"]) for d in week]
    total = sum(v for _, v in rows)
    avg = round(total / len(rows), 2)
    chart = _bar_chart(rows)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Weekly recap – last 7 days"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Total Typeform messages:* {total}\n*Daily average:* {avg}"}},
    ]
    if chart:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chart}})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"Channel: <#{channel_id}> • Counting only Typeform bot posts • Timezone: {TZ.key}"}
    ]})
    client.chat_postMessage(channel=POST_TO_CHANNEL_ID, text="Weekly Typeform recap", blocks=blocks)


# ---------- Main ----------

def main():
    mode = os.environ.get("MODE", "DAILY").upper()

    # Light sanity warnings (don't hard-fail CI unless truly missing)
    if not CHANNEL_ID or not (CHANNEL_ID.startswith("C") or CHANNEL_ID.startswith("G")):
        print("WARNING: CHANNEL_ID is missing or not a channel ID (should start with C or G)")

    now_local = datetime.now(TZ)

    if mode == "DAILY":
        post_daily_typeform_today(CHANNEL_ID)
    elif mode == "WEEKLY":
        post_weekly_typeform(CHANNEL_ID, now_local)
    elif mode == "DRYRUN":
        today = summarize_typeform_today(CHANNEL_ID)
        print({"today": today})
        # Include yesterday too for convenience
        yday = now_local - timedelta(days=1)
        ysum = summarize_typeform_for_day(CHANNEL_ID, yday)
        print({"yesterday": ysum})
    else:
        raise SystemExit(f"Unknown MODE: {mode}")


if __name__ == "__main__":
    main()
