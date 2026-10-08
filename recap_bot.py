#!/usr/bin/env python3
"""
Customization Recap Bot (Intercom)

Counts Intercom tickets of type "Customization Request" (in-product form submissions
and manually created tickets alike) and posts recaps to Slack.

- DAILY   : previous full local day (Mondays cover Sat + Sun together)
- WEEKLY  : 7-day bar chart, last 7 full local days ending yesterday
- MONTHLY : month-to-date count for the current calendar month (last day of month)
- DRYRUN  : prints all summaries to stdout (no Slack post)

Counts use each ticket's created_at timestamp, windowed in local time (default America/Denver).

Required env:
  SLACK_BOT_TOKEN          xoxb-...  (scopes: chat:write, plus chat:write.public if the bot is not in the channel)
  INTERCOM_ACCESS_TOKEN    Intercom access token with read access to tickets
  CHANNEL_ID               Channel for daily recaps (also the fallback for weekly/monthly)

Optional env:
  WEEKLY_POST_TO_CHANNEL_ID    Channel ID for weekly recaps
  MONTHLY_POST_TO_CHANNEL_ID   Channel ID for monthly recaps (defaults to weekly, else CHANNEL_ID)
  POST_TO_CHANNEL_ID           Manual/testing override for where any recap posts
  INTERCOM_TICKET_TYPE_NAME    Ticket type to count (default: "Customization Request")
  INTERCOM_TICKET_TYPE_ID      Skip the name lookup and use this ticket type ID directly
  INTERCOM_API_BASE            Default https://api.intercom.io (use https://api.eu.intercom.io for EU workspaces)
  TZ_NAME                      IANA timezone (default: America/Denver)
  SCHEDULE_AT_LOCAL            e.g. "09:00" or "14:00". Schedules the Slack message for today at that
                               local time via chat.scheduleMessage; otherwise posts immediately.
"""

import math
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

BOT_VERSION = "intercom v1 (daily/weekly/monthly; weekday dailies; per-channel posting; scheduled delivery)"

# ---------- Config / Globals ----------

TZ_NAME = os.environ.get("TZ_NAME", "America/Denver")
TZ = ZoneInfo(TZ_NAME)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
INTERCOM_ACCESS_TOKEN = os.environ.get("INTERCOM_ACCESS_TOKEN")

DAILY_POST_TO_CHANNEL_ID = os.environ.get("CHANNEL_ID")
WEEKLY_POST_TO_CHANNEL_ID = os.environ.get("WEEKLY_POST_TO_CHANNEL_ID") or DAILY_POST_TO_CHANNEL_ID
MONTHLY_POST_TO_CHANNEL_ID = os.environ.get("MONTHLY_POST_TO_CHANNEL_ID") or WEEKLY_POST_TO_CHANNEL_ID

# Manual/testing override for where recaps post.
POST_TO_CHANNEL_ID = os.environ.get("POST_TO_CHANNEL_ID")

TICKET_TYPE_NAME = os.environ.get("INTERCOM_TICKET_TYPE_NAME", "Customization Request")
TICKET_TYPE_ID = os.environ.get("INTERCOM_TICKET_TYPE_ID")
INTERCOM_API_BASE = os.environ.get("INTERCOM_API_BASE", "https://api.intercom.io").rstrip("/")

# If provided (e.g., "09:00" or "14:00"), schedule the message for today at that local time.
SCHEDULE_AT_LOCAL = os.environ.get("SCHEDULE_AT_LOCAL")

_slack = None  # created lazily so DRYRUN works without a Slack token


def _slack_client() -> WebClient:
    global _slack
    if _slack is None:
        if not SLACK_BOT_TOKEN:
            raise SystemExit("SLACK_BOT_TOKEN is required to post to Slack")
        _slack = WebClient(token=SLACK_BOT_TOKEN)
    return _slack


# ---------- Intercom ----------

def _intercom(method: str, path: str, **kwargs) -> dict:
    """Call the Intercom API with basic retry on 429 / 5xx."""
    if not INTERCOM_ACCESS_TOKEN:
        raise SystemExit("INTERCOM_ACCESS_TOKEN is required")
    headers = {
        "Authorization": f"Bearer {INTERCOM_ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Intercom-Version": "2.11",
    }
    resp = None
    for attempt in range(5):
        resp = requests.request(method, f"{INTERCOM_API_BASE}{path}", headers=headers, timeout=30, **kwargs)
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = int(resp.headers.get("Retry-After", 2 ** attempt))
            print(f"[recap_bot] Intercom {resp.status_code}; retrying in {wait}s")
            time.sleep(min(wait, 30))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


_ticket_type_id_cache = None


def _ticket_type_id() -> str:
    """Resolve the ticket type ID (explicit env wins; otherwise look up by name)."""
    global _ticket_type_id_cache
    if TICKET_TYPE_ID:
        return TICKET_TYPE_ID
    if _ticket_type_id_cache:
        return _ticket_type_id_cache
    data = _intercom("GET", "/ticket_types")
    wanted = TICKET_TYPE_NAME.strip().lower()
    for t in data.get("data", []):
        if (t.get("name") or "").strip().lower() == wanted:
            _ticket_type_id_cache = str(t["id"])
            return _ticket_type_id_cache
    names = ", ".join(sorted(t.get("name", "?") for t in data.get("data", [])))
    raise SystemExit(f'Intercom ticket type "{TICKET_TYPE_NAME}" not found. Available: {names}')


def _count_tickets(start: datetime, end: datetime) -> int:
    """Number of customization tickets created in [start, end)."""
    body = {
        "query": {
            "operator": "AND",
            "value": [
                {"field": "ticket_type_id", "operator": "=", "value": _ticket_type_id()},
                # Intercom's search only supports strict > and <, so widen the lower bound by 1s.
                {"field": "created_at", "operator": ">", "value": str(int(start.timestamp()) - 1)},
                {"field": "created_at", "operator": "<", "value": str(int(end.timestamp()))},
            ],
        },
        "pagination": {"per_page": 1},
    }
    return int(_intercom("POST", "/tickets/search", json=body).get("total_count", 0))


# ---------- Date helpers ----------

def _midnight(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=TZ)


def _date_label(dt: datetime) -> str:
    # Example: "Sun Sep 14"
    return f"{dt.strftime('%a %b')} {dt.day}"


def _month_label(dt: datetime) -> str:
    return ["January", "February", "March", "April", "May", "June", "July", "August",
            "September", "October", "November", "December"][dt.month - 1]


def _bar_chart(rows):
    """rows: list[tuple[str,int]]. Returns a fenced code block with a simple mono bar chart."""
    if not rows:
        return ""
    maxv = max(v for _, v in rows) or 1
    lines = []
    for label, v in rows:
        n = 0 if v == 0 else max(1, math.ceil((v / maxv) * 20))
        lines.append(f"{label:>12} | {'█' * n} {v}")
    return "```\n" + "\n".join(lines) + "\n```"


# ---------- Summaries ----------

def summarize_daily(now_local: datetime) -> dict:
    """Previous full local day. On Mondays, Sat + Sun combined so weekends are never skipped."""
    today = _midnight(now_local)
    if today.weekday() == 0:
        start = today - timedelta(days=2)
        header = "Weekend Recap (Sat–Sun)"
    else:
        start = today - timedelta(days=1)
        header = f"Previous Day Recap ({start.strftime('%A')})"
    return {"header": header, "total": _count_tickets(start, today)}


def summarize_week(now_local: datetime) -> list:
    """Last 7 full local days ending yesterday, oldest -> newest."""
    today = _midnight(now_local)
    days = []
    for i in range(7, 0, -1):
        start = today - timedelta(days=i)
        end = today - timedelta(days=i - 1)
        days.append({"label": _date_label(start), "total": _count_tickets(start, end)})
    return days


def summarize_month_to_now(now_local: datetime) -> dict:
    """Current calendar month up to the moment the job runs."""
    start = datetime(now_local.year, now_local.month, 1, tzinfo=TZ)
    return {"month": _month_label(start), "total": _count_tickets(start, now_local)}


# ---------- Slack posting ----------

def _local_dt_today_at(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    now = datetime.now(TZ)
    return datetime(now.year, now.month, now.day, h, m, 0, tzinfo=TZ)


def _post_or_schedule(channel: str, text: str, blocks: list, schedule_at_local):
    if not channel:
        raise SystemExit("No Slack channel configured (set CHANNEL_ID or POST_TO_CHANNEL_ID)")
    client = _slack_client()
    try:
        if schedule_at_local:
            target = _local_dt_today_at(schedule_at_local)
            if target > datetime.now(TZ) + timedelta(seconds=15):
                resp = client.chat_scheduleMessage(
                    channel=channel, text=text, blocks=blocks, post_at=int(target.timestamp())
                )
                print(f"[recap_bot] Scheduled for {target.isoformat()} → id={resp.get('scheduled_message_id')}")
                return
            late = datetime.now(TZ) - target
            print(f"[recap_bot] Target {target.isoformat()} already passed ({late} ago); posting now")
        resp = client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        print(f"[recap_bot] Posted → ts={resp.get('ts')}, channel={resp.get('channel')}")
    except SlackApiError as e:
        print(f"[recap_bot] SlackApiError: {e.response['error']} — channel={channel}")
        raise


def _blocks(header: str, body: str, extra: str = ""):
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
    ]
    if extra:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": extra}})
    return blocks


def post_daily(now_local: datetime):
    s = summarize_daily(now_local)
    blocks = _blocks(s["header"], f"*Customization requests:* {s['total']}")
    _post_or_schedule(POST_TO_CHANNEL_ID or DAILY_POST_TO_CHANNEL_ID, "Daily customization recap",
                      blocks, SCHEDULE_AT_LOCAL)


def post_weekly(now_local: datetime):
    week = summarize_week(now_local)
    rows = [(d["label"], d["total"]) for d in week]
    total = sum(v for _, v in rows)
    avg = round(total / len(rows), 2)
    blocks = _blocks(
        "Weekly Recap as of 2pm Friday",
        f"*Customization requests this week:* {total}\n*Daily average:* {avg}",
        _bar_chart(rows),
    )
    _post_or_schedule(POST_TO_CHANNEL_ID or WEEKLY_POST_TO_CHANNEL_ID, "Weekly customization recap",
                      blocks, SCHEDULE_AT_LOCAL)


def post_monthly(now_local: datetime):
    m = summarize_month_to_now(now_local)
    blocks = _blocks(f"{m['month']} Monthly Recap", f"*Customization requests this month:* {m['total']}")
    _post_or_schedule(POST_TO_CHANNEL_ID or MONTHLY_POST_TO_CHANNEL_ID, "Monthly customization recap",
                      blocks, SCHEDULE_AT_LOCAL)


# ---------- Main ----------

def main():
    print(f"[recap_bot] starting {BOT_VERSION}")
    mode = os.environ.get("MODE", "DAILY").upper()
    now_local = datetime.now(TZ)

    if POST_TO_CHANNEL_ID:
        print(f"[recap_bot] POST_TO_CHANNEL_ID override active: {POST_TO_CHANNEL_ID}")

    if mode == "DAILY":
        post_daily(now_local)
    elif mode == "WEEKLY":
        post_weekly(now_local)
    elif mode == "MONTHLY":
        post_monthly(now_local)
    elif mode == "DRYRUN":
        print({"ticket_type_id": _ticket_type_id()})
        print({"daily": summarize_daily(now_local)})
        print({"last_7_days": [(d["label"], d["total"]) for d in summarize_week(now_local)]})
        print({"month_to_now": summarize_month_to_now(now_local)})
    else:
        raise SystemExit(f"Unknown MODE: {mode}")


if __name__ == "__main__":
    main()
