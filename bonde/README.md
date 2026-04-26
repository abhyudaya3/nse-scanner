# 🚀 STOCKBEE MOMENTUM SCANNER — NSE/BSE
## Setup, Run & Deploy Guide

---

## 📦 WHAT'S INCLUDED

| File | Purpose |
|------|---------|
| `scanner_engine.py` | Core pattern detection engine (Momentum Burst, Episodic Pivot, TTT) |
| `api_server.py` | Flask REST API + auto-scheduler (runs every 30 min during market hours) |
| `dashboard.html` | Live trading dashboard (dark terminal UI) |
| `requirements.txt` | Python dependencies |

---

## 🛠️ INSTALLATION

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Run a one-time scan (saves scan_results.json)
python scanner_engine.py

# 3. Start the live server (scans every 30 min during market hours)
python api_server.py

# 4. Open dashboard
# Visit: http://localhost:5000
```

---

## ⚡ PATTERNS DETECTED

### 1. MOMENTUM BURST 🚀
- **What it is:** Stock makes 8-40% move in 3-5 days starting with a Range Expansion day
- **Entry trigger:** Day 1 of range expansion (buy the breakout candle)
- **Volume:** Must be 1.5x+ 20-day average
- **Target:** +8-15% in 3-5 days
- **Stop:** 1% below the range expansion day's LOW
- **Best in:** Stocks above 20-day MA, fresh trends, post-consolidation

### 2. EPISODIC PIVOT ⚡
- **What it is:** Stock was "sleeping" then GAPS UP 5%+ on 3x volume (earnings/news/results)
- **Entry trigger:** Buy the close of gap day, or pullback to VWAP next day
- **Volume:** Must be 3x+ 20-day average
- **Target:** +15-30% over 5-15 days
- **Stop:** 2% below gap day's LOW
- **Best in:** Small/mid caps with earnings surprise, major order wins

### 3. TTT BREAKOUT 📦 (Tight-Tight-Tight)
- **What it is:** Tight 7-day consolidation (< 6% range) breaks out on volume
- **Entry trigger:** Close above the tight range HIGH
- **Volume:** 1.5x+ on breakout day
- **Target:** +8-15% in 3-7 days
- **Stop:** Below tight range LOW

---

## 📊 SIGNAL SCORING (0-100)

| Score | Meaning |
|-------|---------|
| 80-100 | ⭐ High Quality — Strong setup, act on it |
| 60-79  | 👀 Watch — Good setup, wait for confirmation |
| <60    | ⚠️ Low Quality — Skip or very small size |

**Score is boosted by:**
- +15 if a quarterly result / major news event is detected
- Volume 3x+ (not just 1.5x)
- Stock was in tight base before breakout
- Closes near day's HIGH

---

## 📰 NEWS/CATALYST INTEGRATION

The scanner automatically detects **Episodic catalysts** by checking:
1. Gap % on heavy volume in last 5 sessions
2. Cross-references with Economic Times RSS feed
3. Flags the card with 🎯 CATALYST tag

**For manual news:**
- Filter by 🎯 CATALYST in the dashboard
- Right-click any card → opens NSE quote page
- Check Moneycontrol/ET for the specific event

**Free news sources used:**
- Economic Times RSS: `https://economictimes.indiatimes.com/rssfeeds/-1233454870.cms`
- NSE announcements: `https://www.nseindia.com/companies-listing/corporate-filings-announcements`

---

## 🕐 MARKET HOURS & SCHEDULING

The scanner runs automatically during:
- **Hours:** 9:15 AM – 3:30 PM IST
- **Days:** Monday – Friday
- **Frequency:** Every 30 minutes
- **Outside hours:** Uses last saved results (no new scans)

---

## 🌐 DEPLOYING 24/7 (Free)

### Option A: Railway.app (Recommended - Free)
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and deploy
railway login
railway init
railway up

# Set start command: python api_server.py
```

### Option B: Render.com (Free)
1. Push code to GitHub
2. Create new Web Service on render.com
3. Build command: `pip install -r requirements.txt`
4. Start command: `python api_server.py`
5. Free tier spins down after inactivity — use UptimeRobot to ping it

### Option C: Local always-on (Raspberry Pi / old PC)
```bash
# Run with nohup so it stays alive
nohup python api_server.py &

# Or use screen
screen -S scanner
python api_server.py
# Ctrl+A, D to detach
```

### Option D: Google Colab (Free, needs refresh)
```python
!pip install yfinance flask flask-cors pytz pyngrok
from pyngrok import ngrok
# Run api_server.py and expose with ngrok
```

---

## 📱 USING THE DASHBOARD

1. **Open** `http://localhost:5000` in browser
2. **Filter** by pattern type (MB / EP / TTT) using top buttons
3. **🎯 CATALYST** filter shows only event-driven setups
4. **Search** by symbol name
5. **Click any card** → opens NSE quote page
6. **RUN SCAN** button → triggers fresh scan manually

**Card Color Coding:**
- 🟢 Green top bar = Momentum Burst
- 🟡 Amber top bar = Episodic Pivot
- 🔵 Blue top bar = TTT Breakout

---

## ⚠️ RISK DISCLAIMER

This is a scanning tool, NOT financial advice.
- Always use a STOP LOSS on every trade
- Risk max 1-2% of capital per trade
- Never chase a stock already up 10%+ on the day
- EP setups can fail if market turns — check Nifty trend
- Verify news before entering an Episodic Pivot

---

## 🔧 CUSTOMIZATION

In `scanner_engine.py`, you can adjust:
```python
# Momentum Burst thresholds
range_expansion = last["range"] > 1.5 * last["avg_range"]  # Change 1.5 to 2.0 for stricter
vol_surge = last["volume"] > 1.5 * last["avg_vol"]          # Change to 2.0 for higher bar
valid_move = 1.5 <= pct_today <= 25                         # Min/max % move today

# Episodic Pivot thresholds
if gap_pct < 5:   # Change to 7 for higher quality EPs only
    return None
if last["volume"] < 3 * last["avg_vol"]:  # Change to 5 for huge volume only
    return None

# Scan interval (in api_server.py)
time.sleep(1800)  # Change to 900 for 15-min scans
```
