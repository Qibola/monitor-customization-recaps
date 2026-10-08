# Customization Recap Bot

Counts customization requests in **Intercom** (ticket type "Customization Request") and posts daily, weekly, and monthly recaps in Slack. Requests come from Help → AI Customization Request in Jump, and tickets created by hand are counted too.

Full write-up: the "Customization Recap Bot" page under Automation Bots in Notion.

## When posts happen (America/Denver)

| Type | When | What it shows |
| --- | --- | --- |
| Daily | 9:00 AM every weekday | Yesterday's count (Mondays: Sat + Sun) |
| Weekly | 2:00 PM every Friday | Last 7 full days with a bar chart |
| Monthly | 2:00 PM on the last day of the month | Month-to-date total |

The bot runs 30 minutes before each post time and schedules the Slack message with `chat.scheduleMessage`, so delivery lands on time even when GitHub starts the job late.

## Configuration (GitHub secrets)

Required: `SLACK_BOT_TOKEN`, `INTERCOM_ACCESS_TOKEN` (read access to tickets), `CHANNEL_ID` (daily channel and fallback).

Optional: `WEEKLY_POST_TO_CHANNEL_ID`, `MONTHLY_POST_TO_CHANNEL_ID`, `POST_TO_CHANNEL_ID` (testing override), `INTERCOM_TICKET_TYPE_NAME`, `INTERCOM_TICKET_TYPE_ID`, `INTERCOM_API_BASE`, `TZ_NAME`, `SCHEDULE_AT_LOCAL`.

Slack scopes: `chat:write` (plus `chat:write.public` if the bot isn't in the channel).

## Running manually

```bash
MODE=DRYRUN python recap_bot.py   # prints counts, posts nothing
MODE=DAILY python recap_bot.py
MODE=WEEKLY python recap_bot.py
MODE=MONTHLY python recap_bot.py
```

In GitHub: Actions → Slack Recaps → Run workflow (start with `DRYRUN`).

## Notes

- Counts use each ticket's created date in Denver time and include every ticket state.
- GitHub disables scheduled workflows after 60 days without repo activity. The workflow re-enables itself on each scheduled run; if it was already disabled, enable it once under Actions.
- Monthly counts are taken when the bot runs (about 1:30 PM), roughly 30 minutes before the post.

## Files

- `recap_bot.py` - the bot
- `.github/workflows/slack-recaps.yml` - schedule and run logic
- `requirements.txt` - `slack_sdk`, `requests`

Version: intercom v1 (replaces typeform-only v5).
