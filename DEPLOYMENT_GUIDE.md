# Daily Scanner Deployment Guide

## How it works (the mechanics)

### What the daily scanner does
1. **After market close** (3:30 PM IST), it downloads 1 year of data for every NSE stock
2. **Scans ONLY the latest bar** — not the full history. This makes it fast (~15–20 min for all NSE)
3. **Runs 6 pattern detectors** on different window sizes for each stock
4. **Filters to BUY/WATCH signals only** — you don't see the noise
5. **Sends you alerts** (Telegram/email) with actionable signals
6. **Saves a CSV** per day so you can track what it found over time

### What you do with it
- Wake up at 9 AM, check your Telegram for yesterday's alerts
- Open the CSV and look at BUY signals
- Cross-check: does the chart actually look like the pattern? (always verify visually)
- Check if the breakout zone has been hit or is close
- Make your trade decision

### Why it scans after market close, not live
- Pattern detection needs a complete candle to confirm. Intraday data is noisy.
- The signals are valid for 1–5 days typically. No rush.
- This is a **swing/positional scanner**, not a scalping tool.
- If you want live alerts, you'd need a different architecture (WebSocket + streaming, which is 10x more complex).

---

## Deployment Option 1: GitHub Actions (FREE, recommended)

**Best for**: Set and forget. No server needed. Free forever.

### Setup steps

1. **Create a GitHub repo**
   ```
   mkdir nse-scanner && cd nse-scanner
   git init
   ```

2. **Copy files into it**
   ```
   cp daily_scanner.py .
   mkdir -p .github/workflows
   cp .github/workflows/daily_scan.yml .github/workflows/
   ```

3. **Set up Telegram bot** (for alerts)
   - Message @BotFather on Telegram → /newbot → follow steps → get TOKEN
   - Send any message to your bot, then visit:
     `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find your chat_id in the response

4. **Add secrets to GitHub**
   - Go to repo → Settings → Secrets and variables → Actions
   - Add: `TG_BOT_TOKEN` = your bot token
   - Add: `TG_CHAT_ID` = your chat id

5. **Push and enable**
   ```
   git add -A && git commit -m "scanner" && git push
   ```
   - Go to repo → Actions tab → enable workflows
   - It will run automatically at 4:15 PM IST every weekday

6. **Manual run**: Actions tab → "NSE Daily Pattern Scanner" → "Run workflow"

### Limitations
- GitHub Actions has 2000 free minutes/month. Scanner uses ~20 min/run × 22 days = 440 min. Well within limits.
- Results are saved as "Artifacts" — downloadable from the Actions tab for 90 days.

---

## Deployment Option 2: PythonAnywhere (FREE tier)

**Best for**: If you want a persistent server with a web dashboard later.

1. Sign up at pythonanywhere.com (free account)
2. Go to Files → upload `daily_scanner.py`
3. Go to Consoles → Bash → run:
   ```
   pip install --user yfinance pandas numpy scipy openpyxl requests
   python daily_scanner.py --telegram
   ```
4. Go to Tasks → add a scheduled task:
   - Time: 10:45 (UTC) = 4:15 PM IST
   - Command: `/home/yourusername/daily_scanner.py --telegram`

### Limitations
- Free tier: 1 scheduled task, limited CPU (scanner might be slow but will complete)
- No always-on web server on free tier

---

## Deployment Option 3: Local cron job (Linux/Mac)

**Best for**: If your computer is always on.

```bash
# Edit crontab
crontab -e

# Add this line (runs at 4:15 PM IST = 10:45 UTC)
45 10 * * 1-5 cd /path/to/scanner && /usr/bin/python3 daily_scanner.py --telegram >> /path/to/scanner/cron.log 2>&1
```

### Windows equivalent
Use Task Scheduler:
1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily at 4:15 PM
3. Action: Start a program → `python.exe`
4. Arguments: `C:\path\to\daily_scanner.py --telegram`

---

## Deployment Option 4: Railway / Render (FREE tier)

**Best for**: Cloud deployment with more control.

1. Push code to GitHub
2. Connect Railway/Render to your repo
3. Add a cron job service
4. Set environment variables (TG_BOT_TOKEN, TG_CHAT_ID)

---

## Setting up Telegram alerts (step by step)

This is the most useful alert method — instant push to your phone.

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`
3. Follow prompts: give it a name like "NSE Scanner Bot"
4. You'll get a token like: `1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`
5. Open your new bot in Telegram and send it any message (like "hi")
6. Visit this URL in browser (replace TOKEN):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
7. Find `"chat":{"id":123456789}` — that number is your CHAT_ID
8. Set environment variables:
   ```bash
   export TG_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ"
   export TG_CHAT_ID="123456789"
   ```
9. Test: `python daily_scanner.py --telegram --limit 10`

---

## Setting up email alerts

1. Use a Gmail account
2. Enable "App Passwords": Google Account → Security → App passwords → generate
3. Set environment variables:
   ```bash
   export EMAIL_FROM="your@gmail.com"
   export EMAIL_TO="your@gmail.com"
   export EMAIL_PASSWORD="xxxx xxxx xxxx xxxx"  # the app password, NOT your gmail password
   ```
4. Test: `python daily_scanner.py --email --limit 10`

---

## Architecture diagram

```
Market closes 3:30 PM IST
         |
    [4:15 PM] Cron/GitHub Action triggers
         |
    daily_scanner.py
         |
    ┌────┴─────────────────┐
    │  Download 1Y data    │ ← yfinance (NSE via Yahoo)
    │  for all ~2000 stocks│
    └────┬─────────────────┘
         │
    ┌────┴─────────────────┐
    │  Run 6 detectors on  │
    │  LATEST BAR ONLY     │ ← Cup, FlatBase, IHS, DB, AscTri, Flag
    │  × multiple windows  │
    └────┬─────────────────┘
         │
    ┌────┴─────────────────┐
    │  Score CANSLIM (7/7)  │
    │  Filter BUY/WATCH     │
    └────┬─────────────────┘
         │
    ┌────┴──────┬──────────┐
    │  Save CSV │ Telegram │ Email
    └───────────┴──────────┘
         │
    You check alerts next morning
    and decide what to trade
```

## FAQ

**Q: Can I add more patterns?**
A: Yes. Write a `det_yourpattern(c, v)` function that returns the same dict format. Add it to the `DETS` list and add its windows to `WINS`.

**Q: How do I backtest changes?**
A: Use the Kaggle 10Y backtest notebook with the same detector. Compare forward returns to see if your change improved alpha.

**Q: What if yfinance breaks?**
A: yfinance scrapes Yahoo Finance. If Yahoo changes their API (happens occasionally), update yfinance: `pip install --upgrade yfinance`. Alternatives: `tvDatafeed` (TradingView), `nsepython`, or the official NSE bhavcopy.

**Q: Should I blindly trade every BUY signal?**
A: No. The scanner narrows ~2000 stocks to ~5–20 actionable ones. You still need to:
1. Visually confirm the pattern on a chart
2. Check the broader sector trend
3. Look at recent news/events for the stock
4. Size your position appropriately (never more than 5% of capital on one trade)

**Q: How do I improve accuracy?**
A: Track the scanner's signals in a spreadsheet. After 30 days, compute hit rate. Tighten the detectors that have low hit rates.
