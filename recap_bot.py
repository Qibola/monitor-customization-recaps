#!/usr/bin/env python3
import os
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

TZ = ZoneInfo("America/Denver")

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]  # e.g. C0123ABCDEF
POST_TO_CHANNEL_ID = os.environ.get("POST_TO_CHANNEL_ID") or CHANNEL_ID


client = WebClient(token=SLACK_BOT_TOKEN)


def _ts(dt: datetime) -> float:
    # Slack expects float seconds as string; WebClient accepts float.
    return dt.timestamp()


def _list_messages(channel_id: str, oldest: float, latest: float):
    """Paginate conversations.history within [oldest, latest)."""
    cursor = None
    while True:
        try:
            resp = client.conversations_history(
                channel=channel_id,
                oldest=str(oldest),
                latest=str(latest),
                inclusive=False,
                limit=200,
                cursor=cursor,
            )
        except SlackApiError as e:
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


def _is_countable(msg: dict) -> bool:
    # Exclude obvious system/bot messages
    subtype = msg.get("subtype")
    if subtype:  # channel_join, bot_message, etc.
        return False
    if msg.get("bot_id") or msg.get("bot_profile"):
        return False
    if "user" not in msg:  # no human user id -> skip
        return False
    return True


def _human_name(uid: str) -> str:
    try:
        u = client.users_info(user=uid)["user"]
        profile = u.get("profile", {})
        return profile.get("display_name") or profile.get("real_name") or uid
    except SlackApiError:
        return uid


def get_day_window_local(day: datetime):
    """Return (start_ts, end_ts) for the *local* calendar day in America/Denver."""
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=TZ)
    end = start + timedelta(days=1)
    return _ts(start), _ts(end), start, end


def summarize_channel_for_day(channel_id: str, day: datetime):
    oldest, latest, start_dt, _ = get_day_window_local(day)
    totals = 0
    threads_started = 0
    replies = 0
    by_user = Counter()

    for msg in _list_messages(channel_id, oldest, latest):
        if not _is_countable(msg):
            continue
        totals += 1
        if msg.get("thread_ts") and msg["thread_ts"] != msg["ts"]:
            replies += 1
        else:
            threads_started += 1
        by_user[msg["user"]] += 1

    top5 = [(uid, cnt) for uid, cnt in by_user.most_common(5)]
    top5_named = [( _human_name(uid), cnt) for uid, cnt in top5]

    return {
        "date_label": start_dt.strftime("%a %b %-d"),
        "total": totals,
        "threads_started": threads_started,
        "replies": replies,
        "top5": top5_named,
    }


def summarize_channel_for_week(channel_id: str, end_day: datetime):
    # Week = last 7 full local days ending with end_day (inclusive)
    day_summaries = []
    for i in range(6, -1, -1):
        d = end_day - timedelta(days=i)
        day_summaries.append(summarize_channel_for_day(channel_id, d))
    return day_summaries


def _bar_chart(rows):
    """
    Make a tiny mono bar chart from [('Mon 9/8', 12), ...]
    Uses '█' scaled to max. Looks nice in a code block.
    """
    if not rows:
        return ""
    maxv = max(v for _, v in rows) or 1
    lines = []
    for label, v in rows:
        n = 0 if v == 0 else max(1, math.ceil((v / maxv) * 20))
        lines.append(f"{label:>10} | {'█'*n} {v}")
    return "```\n" + "\n".join(lines) + "\n```"


def post_daily_summary(channel_id: str, day: datetime):
    s = summarize_channel_for_day(channel_id, day)
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Daily recap – {s['date_label']}"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Total messages:* {s['total']}\n• Threads started: {s['threads_started']}\n• Replies: {s['replies']}"}},
    ]
    if s["top5"]:
        top_lines = "\n".join([f"• {name}: {cnt}" for name, cnt in s["top5"]])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Top contributors:*\n{top_lines}"}})

    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"Channel: <#{channel_id}> • Generated in America/Denver timezone"}
    ]})

    client.chat_postMessage(channel=POST_TO_CHANNEL_ID, text="Daily recap", blocks=blocks)


def post_weekly_summary(channel_id: str, end_day: datetime):
    week = summarize_channel_for_week(channel_id, end_day)
    rows = [(d["date_label"], d["total"]) for d in week]
    chart = _bar_chart(rows)
    total = sum(v for _, v in rows)
    avg = round(total / len(rows), 2)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Weekly recap – last 7 days"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Total messages:* {total}\n*Daily avg:* {avg}"}},
    ]
    if chart:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chart}})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"Channel: <#{channel_id}> • Generated in America/Denver timezone"}
    ]})
    client.chat_postMessage(channel=POST_TO_CHANNEL_ID, text="Weekly recap", blocks=blocks)


def main():
    """
    Modes:
      DAILY   -> recap of *yesterday* local day
      WEEKLY  -> recap covering the 7 days ending yesterday
      DRYRUN  -> print yesterday’s summary to stdout (no post)
    """
    mode = os.environ.get("MODE", "DAILY").upper()
    today_local = datetime.now(TZ).date()
    yesterday = datetime(today_local.year, today_local.month, today_local.day, tzinfo=TZ) - timedelta(days=1)
    if mode == "DAILY":
        post_daily_summary(CHANNEL_ID, yesterday)
    elif mode == "WEEKLY":
        post_weekly_summary(CHANNEL_ID, yesterday)
    elif mode == "DRYRUN":
        s = summarize_channel_for_day(CHANNEL_ID, yesterday)
        print(s)
    else:
        raise SystemExit(f"Unknown MODE: {mode}")

if __name__ == "__main__":
    main()
