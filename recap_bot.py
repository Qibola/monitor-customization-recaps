#!/usr/bin/env python3
"""
Slack Typeform Recap Bot

- DAILY   : Posts *yesterday's full day* Typeform message count (local 00:00–24:00)
- WEEKLY  : Posts a 7-day bar chart of Typeform counts (last 7 full local days, ending yesterday)
- MONTHLY : Posts a month-to-date count for the current calendar month (runs last day 14:00 local)
- DRYRUN  : Prints summaries to stdout (no Slack post)

Notes
- MONTHLY here is month-to-date as of the run (last day @ 14:00). If you later prefer a fully complete
  prior month, run at 09:00 on the 1st and summarize the *previous* month.

Required env:
  SLACK_BOT_TOKEN        xoxb-...
  CHANNEL_ID             Monitor channel to analyze (C... or G...)  (daily posts here)

Optional env:
  WEEKLY_POST_TO_CHANNEL_ID   Channel ID for weekly posts (e.g., cx-3-customization)
  MONTHLY_POST_TO_CHANNEL_ID  Channel ID for monthly posts (defaults to weekly, else monitor)
  TZ_NAME                IANA tz (default: America/Denver)
  TYPEFORM_APP_ID        If set, only count messages where bot_profile.app_id == this
  SCHEDULE_AT_LOCAL      If set (e.g., "09:00" or "14:00"), schedule the Slack message for *today*
                         at that local time using chat.scheduleMessage; otherwise post immediately.

Scopes needed (public channel): channels:read, channels:history, chat:write
If the channel is private, also add: groups:read, groups:history
"""

import os
import math
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

BOT_VERSION = "typeform-only v5 (daily/weekly/monthly; per-channel posting; headers; scheduled delivery)"

# ---------- Config / Globals ----------

TZ_NAME = os.environ.get("TZ_NAME", "America/Denver")
TZ = ZoneInfo(TZ_NAME)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
MONITOR_CHANNEL_ID = os.environ["CHANNEL_ID"]  # analyzed channel + where daily posts

WEEKLY_POST_TO_CHANNEL_ID = os.environ.get("WEEKLY_POST_TO_CHANNEL_ID")
MONTHLY_POST_TO_CHANNEL_ID = os.environ.get("MONTHLY_POST_TO_CHANNEL_ID") \
    or WEEKLY_POST_TO_CHANNEL_ID or MONITOR_CHANNEL_ID

# Optional hard match to Typeform app id (recommended if you know it)
TYPEFORM_APP_ID = os.environ.get("TYPEFORM_APP_ID")

# If provided (e.g., "09:00" or "14:00"), we'll *schedule* the message for today at that local time
SCHEDULE_AT_LOCAL = os.environ.get("SCHEDULE_AT_LOCAL")

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
    True if this message is from the Typeform app/bot.
    We intentionally ignore human messages and count only Typeform posts.
    """
    # Ignore Slack housekeeping subtypes (edits/deletes/joins/etc.)
    subtype = msg.get("subtype")
    if subtype in {
        "channel_join", "channel_leave", "channel_topic", "channel_purpose",
        "channel_name", "message_deleted", "message_changed"
    }:
        return False

    # If the app_id is known, prefer that (rock-solid)
    bp = msg.get("bot_profile") or {}
    if TYPEFORM_APP_ID and bp.get("app_id") == TYPEFORM_APP_ID:
        return True

    # Otherwise, check bot names/usernames for 'typeform'
    name_candidates = [
        (bp.get("name") or "").lower(),
        (bp.get("username") or "").lower(),
        (msg.get("username") or "").lower(),
    ]
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


def _month_window_local_for(dt_any: datetime):
    """
    For any local datetime, return (start_ts, end_ts, start_dt) covering the *current* calendar month.
    """
    start = datetime(dt_any.year, dt_any.month, 1, 0, 0, 0, tzinfo=TZ)
    if dt_any.month == 12:
        next_month = datetime(dt_any.year + 1, 1, 1, 0, 0, 0, tzinfo=TZ)
    else:
        next_month = datetime(dt_any.year, dt_any.month + 1, 1, 0, 0, 0, tzinfo=TZ)
    return _ts(start), _ts(next_month), start


def _date_label(dt: datetime) -> str:
    # Example: "Sun Sep 14"
    try:
        return dt.strftime("%a %b %-d")
    except ValueError:
        return dt.strftime("%a %b %d").replace(" 0", " ")


def _month_label(dt: datetime) -> str:
    try:
        return dt.strftime("%B")  # "September"
    except ValueError:
        return ["January","February","March","April","May","June","July","August",
                "September","October","November","December"][dt.month-1]


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


def _local_dt_today_at(hhmm: str) -> datetime:
    """Return today's local DateTime at HH:MM."""
    h, m = map(int, hhmm.split(":"))
    now = datetime.now(TZ)
    return datetime(now.year, now.month, now.day, h, m, 0, tzinfo=TZ)


def _post_or_schedule(channel_to_post: str, text: str, blocks: list, schedule_at_local: str | None):
    """
    If schedule_at_local (HH:MM) is provided and still in the future today, schedule; else post now.
    """
    if schedule_at_local:
        target = _local_dt_today_at(schedule_at_local)
        if target > datetime.now(TZ) + timedelta(seconds=15):
            client.chat_scheduleMessage(
                channel=channel_to_post,
                text=text,
                blocks=blocks,
                post_at=int(target.timestamp()),
            )
            return
    client.chat_postMessage(channel=channel_to_post, text=text, blocks=blocks)


# ---------- Summaries (Typeform only) ----------

def summarize_typeform_for_day(channel_id: str, day: datetime):
    """
    Count Typeform messages for the full local calendar day.
    """
    oldest, latest, start_dt, _ = _day_window_local(day)
    count = 0
    for msg in _list_messages(channel_id, oldest, latest):
        if _is_typeform_message(msg):
            count += 1
    return {"date_label": _date_label(start_dt), "dow": start_dt.strftime("%A"), "total": count}


def summarize_typeform_yesterday(channel_id: str):
    now_local = datetime.now(TZ)
    y = datetime(now_local.year, now_local.month, now_local.day, tzinfo=TZ) - timedelta(days=1)
    return summarize_typeform_for_day(channel_id, y)


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


def summarize_typeform_month_to_now(channel_id: str, now_local: datetime):
    """
    Count Typeform messages for the current month up to 'now_local' (end exclusive).
    """
    oldest, _month_end_ts, month_start_dt = _month_window_local_for(now_local)
    latest = _ts(now_local)
    count = 0
    for msg in _list_messages(channel_id, oldest, latest):
        if _is_typeform_message(msg):
            count += 1
    return {"month": _month_label(month_start_dt), "total": count}


# ---------- Posting ----------

def post_daily_typeform_yesterday():
    s = summarize_typeform_yesterday(MONITOR_CHANNEL_ID)
    header = f"Previous Day Recap ({s['dow']})"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Typeform messages:* {s['total']}"}},
    ]
    _post_or_schedule(MONITOR_CHANNEL_ID, "Daily Typeform recap", blocks, SCHEDULE_AT_LOCAL)


def post_weekly_typeform(now_local: datetime):
    """
    Weekly: show the last 7 *full* days, ending yesterday.
    """
    yesterday = datetime(now_local.year, now_local.month, now_local.day, tzinfo=TZ) - timedelta(days=1)
    week = summarize_typeform_week(MONITOR_CHANNEL_ID, yesterday)
    rows = [(d["date_label"], d["total"]) for d in week]
    total = sum(v for _, v in rows)
    avg = round(total / len(rows), 2)
    chart = _bar_chart(rows)

    header = "Weekly Recap as of 2pm Friday"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Total Typeform messages:* {total}\n*Daily average:* {avg}"}},
    ]
    if chart:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chart}})

    post_channel = WEEKLY_POST_TO_CHANNEL_ID or MONITOR_CHANNEL_ID
    _post_or_schedule(post_channel, "Weekly Typeform recap", blocks, SCHEDULE_AT_LOCAL)


def post_monthly_typeform(now_local: datetime):
    """
    Monthly: month-to-date (as of run time).
    """
    m = summarize_typeform_month_to_now(MONITOR_CHANNEL_ID, now_local)
    header = f"{m['month']} Monthly Recap"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Typeform messages this month:* {m['total']}"}},
    ]
    post_channel = MONTHLY_POST_TO_CHANNEL_ID
    _post_or_schedule(post_channel, "Monthly Typeform recap", blocks, SCHEDULE_AT_LOCAL)


# ---------- Main ----------

def main():
    print(f"[recap_bot] starting {BOT_VERSION}")
    mode = os.environ.get("MODE", "DAILY").upper()

    if not MONITOR_CHANNEL_ID or not (MONITOR_CHANNEL_ID.startswith("C") or MONITOR_CHANNEL_ID.startswith("G")):
        print("WARNING: CHANNEL_ID is missing or not a channel ID (should start with C or G)")

    now_local = datetime.now(TZ)

    if mode == "DAILY":
        post_daily_typeform_yesterday()
    elif mode == "WEEKLY":
        post_weekly_typeform(now_local)
    elif mode == "MONTHLY":
        post_monthly_typeform(now_local)
    elif mode == "DRYRUN":
        ysum = summarize_typeform_yesterday(MONITOR_CHANNEL_ID)
        print({"yesterday_full": ysum})
        week = summarize_typeform_week(MONITOR_CHANNEL_ID, datetime(now_local.year, now_local.month, now_local.day, tzinfo=TZ) - timedelta(days=1))
        print({"last_7_days": [(d['date_label'], d['total']) for d in week]})
        msum = summarize_typeform_month_to_now(MONITOR_CHANNEL_ID, now_local)
        print({"month_to_now": msum})
    else:
        raise SystemExit(f"Unknown MODE: {mode}")


if __name__ == "__main__":
    main()
