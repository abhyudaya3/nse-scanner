"""
=============================================================
  STOCKBEE-STYLE MOMENTUM BURST + EPISODIC PIVOT SCANNER
  Indian Market (NSE/BSE) | Author: Claude
  Patterns: Momentum Burst (3-5 day 8%+ moves) + Episodic Pivots (catalyst-driven)
=============================================================
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import time
import datetime
import requests
from typing import List, Dict, Optional
import warnings
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
#  NIFTY 500 STOCK LIST (subset for demo; full list loaded from CSV if available)
# ─────────────────────────────────────────────
NIFTY500_SYMBOLS = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","SBIN","BHARTIARTL",
    "KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI","SUNPHARMA","TITAN","WIPRO","ULTRACEMCO",
    "NESTLEIND","TATAMOTORS","BAJFINANCE","HCLTECH","POWERGRID","TECHM","NTPC","ONGC","JSWSTEEL",
    "TATASTEEL","HINDALCO","ADANIENT","ADANIPORTS","BAJAJFINSV","DRREDDY","CIPLA","DIVISLAB",
    "GRASIM","EICHERMOT","BAJAJ-AUTO","HEROMOTOCO","BPCL","IOC","COALINDIA","BRITANNIA",
    "APOLLOHOSP","TATACONSUM","UPL","PIDILITIND","DABUR","BERGEPAINT","GODREJCP",
    "HAVELLS","VOLTAS","WHIRLPOOL","DIXON","AMBER","APLAPOLLO","POLYCAB",
    "ASTRAL","SUPREMEIND","CAMS","CDSL","MCX","BSE","ANGELONE","MOTILALOFS",
    "MUTHOOTFIN","BAJAJHLDNG","CHOLAFIN","M&MFIN","SHRIRAMFIN","SUNDARMFIN",
    "PERSISTENT","LTIM","COFORGE","MPHASIS","KPITTECH","TATAELXSI","ZOMATO","NYKAA",
    "DELHIVERY","PAYTM","POLICYBZR","IRCTC","RVNL","IRFC","PFC","REC","NHPC",
    "TORNTPHARM","ALKEM","AUROPHARMA","LUPIN","IPCALAB","NATCOPHARM","LALPATHLAB",
    "METROPOLIS","FORTIS","MAXHEALTH","RAINBOW","ASTER","YATHARTH",
    "INDIGO","SPICEJET","BLUEDART","CONCOR",
    "ZEEL","SUNTV","PVR","INOXLEISURE",
    "VEDL","HINDZINC","NATIONALUM","SAIL","NMDC",
    "TRENT","DMART","SHOPERSTOP","VMART","METRO",
    "OBEROIRLTY","PHOENIXLTD","BRIGADE","PRESTIGE","GODREJPROP","DLF","SOBHA",
    "MARICO","EMAMILTD","JYOTHYLAB","CHOLAHLDNG",
    "ESCORTS","BHEL","HAL","BEL","BEML","TIINDIA",
    "BANKBARODA","PNB","CANBK","UNIONBANK","IDFCFIRSTB","FEDERALBNK","RBLBANK","BANDHANBNK",
]

def nse_symbol(sym: str) -> str:
    """Convert symbol to Yahoo Finance NSE format"""
    return f"{sym}.NS"

# ─────────────────────────────────────────────
#  DATA FETCHER
# ─────────────────────────────────────────────
def fetch_ohlcv(symbol: str, period: str = "60d", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Fetch OHLCV data for a symbol"""
    try:
        ticker = yf.Ticker(nse_symbol(symbol))
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df is None or len(df) < 10:
            return None
        df.index = pd.to_datetime(df.index)
        df.columns = [c.lower() for c in df.columns]
        df = df[["open","high","low","close","volume"]].dropna()
        return df
    except Exception:
        return None

# ─────────────────────────────────────────────
#  PATTERN 1: MOMENTUM BURST
#  - Stock must make 8-40% move in 3-5 days
#  - Starts with a RANGE EXPANSION day (today's range > 1.5x avg range)
#  - Volume surge (>1.5x 20-day avg volume)
#  - Trend filter: 50-day MA slope positive OR stock near 52-week high
#  - Entry: Day 1 of range expansion (buy breakout)
#  - Target: 8-15% in 3-10 days
#  - Stop: Low of range expansion day
# ─────────────────────────────────────────────
def detect_momentum_burst(symbol: str, df: pd.DataFrame) -> Optional[Dict]:
    if df is None or len(df) < 25:
        return None

    df = df.copy()
    df["range"] = df["high"] - df["low"]
    df["avg_range"] = df["range"].rolling(14).mean()
    df["avg_vol"]   = df["volume"].rolling(20).mean()
    df["pct_chg"]   = df["close"].pct_change() * 100
    df["ma50"]      = df["close"].rolling(50).mean() if len(df) >= 50 else df["close"].rolling(len(df)).mean()
    df["ma20"]      = df["close"].rolling(20).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Range expansion: today's range > 1.5x 14-day avg range
    range_expansion = last["range"] > 1.5 * last["avg_range"]

    # Volume surge
    vol_surge = last["volume"] > 1.5 * last["avg_vol"]

    # Price up on range expansion day
    price_up = last["close"] > prev["close"]

    # Trend filter: price above 20 MA
    above_ma20 = last["close"] > last["ma20"]

    # Price change today must be positive & meaningful (not already extended)
    pct_today = float(last["pct_chg"])
    valid_move = 1.5 <= pct_today <= 25

    if not (range_expansion and vol_surge and price_up and valid_move):
        return None

    # Check if momentum burst is FRESH (within last 2 bars)
    recent_5d_return = ((last["close"] - df.iloc[-6]["close"]) / df.iloc[-6]["close"] * 100) if len(df) >= 6 else 0

    # Tightness check: was stock consolidating before? (low vol 5 days before)
    pre_vol_avg = df["volume"].iloc[-6:-2].mean()
    consolidation = last["volume"] > 1.8 * pre_vol_avg

    # Entry / Stop / Target
    entry_price  = round(float(last["close"]), 2)
    stop_loss    = round(float(last["low"]) * 0.99, 2)      # 1% below day's low
    risk_pct     = round((entry_price - stop_loss) / entry_price * 100, 2)
    target_1     = round(entry_price * 1.08, 2)              # 8% target
    target_2     = round(entry_price * 1.15, 2)              # 15% target

    # Burst quality score (0-100)
    score = 0
    score += 30 if consolidation else 15
    score += 20 if above_ma20 else 0
    score += 20 if vol_surge and last["volume"] > 2 * last["avg_vol"] else 10
    score += 15 if valid_move else 0
    score += 15 if range_expansion and last["range"] > 2 * last["avg_range"] else 8

    return {
        "symbol":        symbol,
        "pattern":       "Momentum Burst 🚀",
        "pattern_type":  "momentum_burst",
        "signal":        "BUY",
        "entry":         entry_price,
        "stop_loss":     stop_loss,
        "target_1":      target_1,
        "target_2":      target_2,
        "risk_pct":      risk_pct,
        "hold_days":     "3-5 days",
        "vol_surge_x":   round(last["volume"] / last["avg_vol"], 1),
        "range_exp_x":   round(last["range"] / last["avg_range"], 1),
        "pct_today":     round(pct_today, 2),
        "score":         min(score, 100),
        "above_ma20":    bool(above_ma20),
        "consolidation": bool(consolidation),
        "description":   (
            f"Range expansion {round(last['range']/last['avg_range'],1)}x avg. "
            f"Volume {round(last['volume']/last['avg_vol'],1)}x avg. "
            f"Up {round(pct_today,2)}% today. "
            f"Target: +8-15% in 3-5 days. "
            f"Stop: ₹{stop_loss}"
        ),
        "timeframe":     "Daily",
        "timestamp":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ─────────────────────────────────────────────
#  PATTERN 2: EPISODIC PIVOT (EP)
#  - Gap up 5%+ on MASSIVE volume (earnings / news / results)
#  - Stock was "ignored/sleepy" before — base formation
#  - Volume on gap day must be 3x+ average
#  - Close near HIGH of the day (strong close)
#  - Entry: Buy the gap (or pullback to VWAP / open)
#  - Target: 20-50% multi-week
#  - Stop: Below gap day low
# ─────────────────────────────────────────────
def detect_episodic_pivot(symbol: str, df: pd.DataFrame) -> Optional[Dict]:
    if df is None or len(df) < 25:
        return None

    df = df.copy()
    df["avg_vol"] = df["volume"].rolling(20).mean()
    df["avg_range"] = (df["high"] - df["low"]).rolling(14).mean()

    last  = df.iloc[-1]
    prev  = df.iloc[-2]

    # GAP UP: open much higher than previous close
    gap_pct = (float(last["open"]) - float(prev["close"])) / float(prev["close"]) * 100

    # Must gap up at least 5%
    if gap_pct < 5:
        return None

    # Massive volume: 3x average
    if last["volume"] < 3 * last["avg_vol"]:
        return None

    # Closed strong: close in top 60% of day's range
    day_range = float(last["high"]) - float(last["low"])
    if day_range == 0:
        return None
    close_position = (float(last["close"]) - float(last["low"])) / day_range
    if close_position < 0.4:
        return None

    # Stock was sleeping before: check 20-day avg daily move was small
    pre_moves = df["close"].pct_change().abs().iloc[-22:-2].mean() * 100
    was_sleeping = pre_moves < 2.0  # avg daily move < 2%

    # Entry, Stop, Targets
    entry_price  = round(float(last["close"]), 2)
    stop_loss    = round(float(last["low"]) * 0.98, 2)     # 2% below gap day low
    risk_pct     = round((entry_price - stop_loss) / entry_price * 100, 2)
    target_1     = round(entry_price * 1.15, 2)             # 15%
    target_2     = round(entry_price * 1.30, 2)             # 30%

    # EP Score
    score = 0
    score += 30 if gap_pct >= 10 else (20 if gap_pct >= 7 else 12)
    score += 25 if last["volume"] >= 5 * last["avg_vol"] else (15 if last["volume"] >= 3 * last["avg_vol"] else 8)
    score += 20 if close_position >= 0.7 else 10
    score += 15 if was_sleeping else 5
    score += 10 if gap_pct >= 5 else 0

    return {
        "symbol":        symbol,
        "pattern":       "Episodic Pivot ⚡",
        "pattern_type":  "episodic_pivot",
        "signal":        "BUY",
        "entry":         entry_price,
        "stop_loss":     stop_loss,
        "target_1":      target_1,
        "target_2":      target_2,
        "risk_pct":      risk_pct,
        "hold_days":     "5-15 days",
        "vol_surge_x":   round(last["volume"] / last["avg_vol"], 1),
        "gap_pct":       round(gap_pct, 2),
        "close_pos_pct": round(close_position * 100, 1),
        "was_sleeping":  bool(was_sleeping),
        "score":         min(score, 100),
        "description":   (
            f"Gapped up {round(gap_pct,1)}% on {round(last['volume']/last['avg_vol'],1)}x volume. "
            f"Closed in top {round(close_position*100,0):.0f}% of day range. "
            f"{'Stock was in base/sleeping phase — HIGH QUALITY EP. ' if was_sleeping else ''}"
            f"Target: +15-30%. Stop: ₹{stop_loss}"
        ),
        "timeframe":     "Daily",
        "timestamp":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ─────────────────────────────────────────────
#  PATTERN 3: TIGHT CONSOLIDATION BREAKOUT
#  (TTT - Tight Tight Tight, Stockbee anticipation setup)
#  - Price range in last 5-7 days < 5% total range
#  - Volume drying up (avg vol 50% below 20-day avg)
#  - Stock near 52-week high or recent resistance
#  - Today breaks out of the tight range on volume
# ─────────────────────────────────────────────
def detect_tight_consolidation_breakout(symbol: str, df: pd.DataFrame) -> Optional[Dict]:
    if df is None or len(df) < 30:
        return None

    df = df.copy()
    df["avg_vol"]  = df["volume"].rolling(20).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Last 7 days tight range
    window = df.iloc[-8:-1]
    hi7  = window["high"].max()
    lo7  = window["low"].min()
    tight_pct = (hi7 - lo7) / lo7 * 100

    if tight_pct > 6:
        return None

    # Volume dried up during consolidation
    consol_vol_avg = window["volume"].mean()
    vol_dry = consol_vol_avg < 0.7 * float(df["avg_vol"].iloc[-9])

    # Today breakout above the tight range with volume
    breakout = float(last["close"]) > hi7
    vol_breakout = last["volume"] > 1.5 * last["avg_vol"]

    if not (breakout and vol_breakout):
        return None

    entry_price = round(float(last["close"]), 2)
    stop_loss   = round(lo7 * 0.99, 2)
    target_1    = round(entry_price * 1.08, 2)
    target_2    = round(entry_price * 1.15, 2)
    risk_pct    = round((entry_price - stop_loss) / entry_price * 100, 2)

    score = 60
    score += 20 if vol_dry else 0
    score += 20 if tight_pct < 4 else 10

    return {
        "symbol":        symbol,
        "pattern":       "TTT Breakout 📦",
        "pattern_type":  "tight_consolidation",
        "signal":        "BUY",
        "entry":         entry_price,
        "stop_loss":     stop_loss,
        "target_1":      target_1,
        "target_2":      target_2,
        "risk_pct":      risk_pct,
        "hold_days":     "3-7 days",
        "vol_surge_x":   round(last["volume"] / last["avg_vol"], 1),
        "tight_range_pct": round(tight_pct, 2),
        "score":         min(score, 100),
        "description":   (
            f"Tight consolidation {round(tight_pct,1)}% range over 7 days. "
            f"Breakout today on {round(last['volume']/last['avg_vol'],1)}x volume. "
            f"{'Volume dried up during base. ' if vol_dry else ''}"
            f"Target: +8-15%. Stop: ₹{stop_loss}"
        ),
        "timeframe":     "Daily",
        "timestamp":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ─────────────────────────────────────────────
#  NEWS FETCHER (Moneycontrol RSS - free)
# ─────────────────────────────────────────────
def fetch_news(symbol: str) -> List[Dict]:
    """Fetch recent news from free RSS feeds"""
    news_items = []
    try:
        # Economic Times RSS
        url = f"https://economictimes.indiatimes.com/rssfeeds/-1233454870.cms"
        resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "")
                link  = item.findtext("link", "")
                date  = item.findtext("pubDate", "")
                if symbol.upper() in title.upper():
                    news_items.append({"title": title, "link": link, "date": date, "source": "Economic Times"})
    except Exception:
        pass
    return news_items


# ─────────────────────────────────────────────
#  QUARTER RESULTS EVENT DETECTOR
# ─────────────────────────────────────────────
def check_recent_results(symbol: str, df: pd.DataFrame) -> Dict:
    """
    Heuristic: if stock gapped up 5%+ in last 5 days on huge volume,
    it likely had quarterly results or major news.
    """
    if df is None or len(df) < 10:
        return {"has_event": False}

    recent = df.iloc[-5:]
    for i in range(1, len(recent)):
        row  = recent.iloc[i]
        prev = recent.iloc[i-1]
        gap  = (float(row["open"]) - float(prev["close"])) / float(prev["close"]) * 100
        vol_avg = df["volume"].rolling(20).mean().iloc[-(5-i)]
        vol_ratio = float(row["volume"]) / float(vol_avg) if vol_avg > 0 else 0
        if abs(gap) >= 5 and vol_ratio >= 2.5:
            return {
                "has_event":  True,
                "event_type": "Quarterly Results / Major News" if gap > 0 else "Negative Event / Results Miss",
                "gap_pct":    round(gap, 2),
                "vol_ratio":  round(vol_ratio, 2),
                "direction":  "POSITIVE" if gap > 0 else "NEGATIVE",
                "days_ago":   5 - i,
            }
    return {"has_event": False}


# ─────────────────────────────────────────────
#  MAIN SCANNER RUNNER
# ─────────────────────────────────────────────
def run_scanner(symbols: List[str] = None, max_symbols: int = 150) -> List[Dict]:
    """Run full scanner across all symbols"""
    if symbols is None:
        symbols = NIFTY500_SYMBOLS[:max_symbols]

    results = []
    print(f"\n🔍 Scanning {len(symbols)} NSE stocks...")

    for i, sym in enumerate(symbols):
        try:
            df = fetch_ohlcv(sym, period="90d", interval="1d")

            # Run all pattern detectors
            mb = detect_momentum_burst(sym, df)
            ep = detect_episodic_pivot(sym, df)
            tb = detect_tight_consolidation_breakout(sym, df)

            # Event check
            event = check_recent_results(sym, df)

            for pattern in [mb, ep, tb]:
                if pattern:
                    pattern["event"] = event
                    # Boost score if there's a matching event
                    if event.get("has_event") and event.get("direction") == "POSITIVE":
                        pattern["score"] = min(pattern["score"] + 15, 100)
                        pattern["has_catalyst"] = True
                    else:
                        pattern["has_catalyst"] = False
                    results.append(pattern)

            if i % 20 == 0:
                print(f"  → Scanned {i+1}/{len(symbols)}... Found {len(results)} signals so far")
            time.sleep(0.1)  # Be polite to Yahoo Finance

        except Exception as e:
            continue

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n✅ Scan complete. Found {len(results)} trade signals.\n")
    return results


# ─────────────────────────────────────────────
#  SAVE RESULTS
# ─────────────────────────────────────────────
def save_results(results: List[Dict], path: str = "scan_results.json"):
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"💾 Results saved to {path}")


# ─────────────────────────────────────────────
#  LIVE SCHEDULER (runs during market hours IST)
# ─────────────────────────────────────────────
def is_market_hours() -> bool:
    """Check if NSE market is open (9:15 AM - 3:30 PM IST Monday-Friday)"""
    import pytz
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.datetime.now(ist)
    if now.weekday() >= 5:  # Saturday, Sunday
        return False
    market_open  = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def run_live_scheduler(scan_interval_minutes: int = 30):
    """
    Continuously runs scanner during market hours.
    scan_interval_minutes: how often to rescan (default 30 min)
    """
    print("=" * 60)
    print("  STOCKBEE MOMENTUM SCANNER — LIVE MODE")
    print("  NSE Market Hours: 9:15 AM - 3:30 PM IST")
    print(f"  Scan interval: Every {scan_interval_minutes} minutes")
    print("=" * 60)

    while True:
        if is_market_hours():
            print(f"\n⏰ {datetime.datetime.now().strftime('%H:%M:%S')} — Market is OPEN. Running scan...")
            results = run_scanner()
            save_results(results, "scan_results.json")

            # Print top 10
            print("\n📊 TOP 10 SIGNALS:")
            print("-" * 80)
            for r in results[:10]:
                evt = "🎯 CATALYST" if r.get("has_catalyst") else ""
                print(f"  {r['symbol']:15s} | {r['pattern']:25s} | Score:{r['score']:3d} | "
                      f"Entry:₹{r['entry']:.2f} | T1:₹{r['target_1']:.2f} | SL:₹{r['stop_loss']:.2f} {evt}")
            print("-" * 80)

            print(f"\n⏳ Next scan in {scan_interval_minutes} minutes...")
            time.sleep(scan_interval_minutes * 60)
        else:
            import pytz
            ist = pytz.timezone("Asia/Kolkata")
            now = datetime.datetime.now(ist)
            print(f"  💤 Market CLOSED ({now.strftime('%H:%M IST')}). Waiting 5 min...")
            time.sleep(300)


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "live":
        run_live_scheduler(scan_interval_minutes=30)
    else:
        results = run_scanner(max_symbols=100)
        save_results(results)
        print("\n📊 TOP SIGNALS:")
        for r in results[:15]:
            print(f"  {r['symbol']:15s} | {r['pattern']:25s} | Score:{r['score']} | Entry:₹{r['entry']} → T1:₹{r['target_1']} | SL:₹{r['stop_loss']}")
