# Customization Recap Bot

## What is this?

This is a friendly robot that watches a Slack channel and counts how many customization requests come through a Typeform. Every day, it posts a summary message in Slack so everyone can see how many requests came in.

---

## How it works (Simple Version)

### The Daily Recap
Every morning at **9:00 AM** (Denver time), the bot posts a message that says:
> "Yesterday, we received X customization requests!"

### The Weekly Recap
Every **Friday at 2:00 PM** (Denver time), the bot posts a bigger report showing:
- How many requests came in all week
- A little bar chart showing each day
- The average per day

### The Monthly Recap
On the **last day of each month at 2:00 PM** (Denver time), the bot posts:
> "This month, we received X customization requests total!"

---

## What the Bot Does Behind the Scenes

1. **Wakes up** - GitHub Actions runs the bot automatically on a schedule
2. **Looks at messages** - It reads through all the messages in the channel
3. **Finds Typeform messages** - It only counts messages from the Typeform bot (not regular people chatting)
4. **Counts them up** - It adds up how many Typeform messages there were
5. **Posts to Slack** - It sends a nice formatted message with the count

---

## When Posts Happen

| Type | When | What It Shows |
|------|------|---------------|
| **Daily** | 9:00 AM every weekday | Yesterday's count |
| **Weekly** | 2:00 PM every Friday | Last 7 days with a chart |
| **Monthly** | 2:00 PM on the last day of month | Month-to-date total |

**Note:** The bot actually runs 30 minutes before the post time to schedule the message, so it appears at exactly the right time!

---

## Technical Details

### What You Need to Run This

1. **Python 3.11** - The programming language
2. **Slack Bot Token** - Permission to read messages and post
3. **Channel ID** - Which Slack channel to watch
4. **GitHub Actions** - To run it automatically on a schedule

### Required Environment Variables

- `SLACK_BOT_TOKEN` - Your Slack bot's authentication token
- `CHANNEL_ID` - The channel to analyze (starts with `C` or `G`)

### Optional Environment Variables

- `WEEKLY_POST_TO_CHANNEL_ID` - Different channel for weekly posts
- `MONTHLY_POST_TO_CHANNEL_ID` - Different channel for monthly posts
- `TZ_NAME` - Timezone (default: `America/Denver`)
- `TYPEFORM_APP_ID` - If you know the exact Typeform app ID, use this for more accurate filtering
- `SCHEDULE_AT_LOCAL` - Schedule messages for a specific time (e.g., `"09:00"`)

### Slack Permissions Needed

- `channels:read` - Read channel info
- `channels:history` - Read message history
- `chat:write` - Post messages
- `chat:write.public` - Post to public channels (if needed)
- `groups:read` & `groups:history` - If using a private channel

---

## How to Use

### Automatic Mode (Recommended)
The bot runs automatically via GitHub Actions. Just set up your secrets in GitHub and it will post on schedule!

### Manual Mode
You can also run it manually:

```bash
# Daily recap
MODE=DAILY python recap_bot.py

# Weekly recap
MODE=WEEKLY python recap_bot.py

# Monthly recap
MODE=MONTHLY python recap_bot.py

# Test mode (just prints, doesn't post)
MODE=DRYRUN python recap_bot.py
```

---

## Example Output

### Daily Recap
```
Previous Day Recap (Monday)
Customization requests: 12
```

### Weekly Recap
```
Weekly Recap as of 2pm Friday
Customization requests this week: 87
Daily average: 12.43

Mon Sep 9  | ████████████ 15
Tue Sep 10 | ████████████████ 18
Wed Sep 11 | ████████ 10
Thu Sep 12 | ████████████ 14
Fri Sep 13 | ████████████████ 18
Sat Sep 14 | ████ 5
Sun Sep 15 | ███████ 7
```

### Monthly Recap
```
September Monthly Recap
Customization requests via form this month: 245
```

---

## Files in This Project

- **`recap_bot.py`** - The main bot code that does all the work
- **`.github/workflows/slack-recaps.yml`** - The schedule that tells GitHub when to run the bot
- **`requirements.txt`** - List of Python packages needed
- **`README.md`** - This file!

---

## Troubleshooting

**Q: The bot isn't posting!**
- Check that your Slack bot token is valid
- Make sure the channel ID is correct
- Check GitHub Actions logs to see if there were any errors

**Q: It's counting the wrong messages!**
- Set `TYPEFORM_APP_ID` to the exact Typeform app ID for more precise filtering
- The bot looks for "typeform" in bot names as a fallback

**Q: Posts are at the wrong time!**
- Check your timezone setting (`TZ_NAME` environment variable)
- The bot uses America/Denver by default

---

## Version

Current version: **typeform-only v5** (daily/weekly/monthly; per-channel posting; headers; scheduled delivery)

---

## Questions?

If you need help or have questions, check the code comments in `recap_bot.py` or review the GitHub Actions workflow file.
