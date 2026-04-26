#!/usr/bin/env python3
"""
NSE Complete Pattern Scanner — All 12 Patterns
================================================
Patterns: Cup&Handle, VCP, FlatBase, InvHS, DoubleBottom,
          AscTriangle, BullFlag, FallingWedge, TripleBottom,
          MomentumBurst, EpisodicPivot, PocketPivot

Run daily after market close (4:15 PM IST).
Outputs: SQLite DB + daily CSV + Telegram alert.

Usage:
    python scanner.py              # full scan, no alerts
    python scanner.py --telegram   # scan + Telegram push
    python scanner.py --test       # 5 stocks only, fast check
    python scanner.py --weekly     # weekly TF scan (Saturdays)
"""

import os, sys, json, time, sqlite3, argparse, logging
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
import pandas as pd
import numpy as np
from scipy.signal import find_peaks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ================================================================
# CONFIGURATION
# ================================================================

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "signals.db")
OUTPUT_DIR  = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

NIFTY_SYM   = "^NSEI"
DATA_PERIOD = "1y"
MAX_WORKERS = 10

TG_TOKEN    = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT     = os.environ.get("TG_CHAT_ID", "")

# CANSLIM thresholds
CS = {
    "C_min": 0.25, "A_min": 0.25,
    "N_max_from_high": 0.15,
    "L_min_rs": 1.10,
    "I_min_instl": 0.20,
    "buy_strong": 6,
    "buy_moderate": 4,
}

# Pattern detector windows
WINS = {
    "CupHandle":    [60, 80, 120, 180, 250],
    "VCP":          [60, 80, 120, 180, 250],
    "FlatBase":     [40, 60, 80, 120, 180],
    "InvHS":        [60, 80, 120, 180, 250],
    "DoubleBottom": [40, 60, 100, 150, 200],
    "TripleBottom": [60, 100, 150, 200],
    "AscTriangle":  [30, 50, 80, 120, 180],
    "BullFlag":     [15, 20, 30, 40, 50, 60],
    "FallingWedge": [30, 50, 80, 120],
    "MomBurst":     [5, 7, 10],        # short lookback — it's a recent event
    "EpisodicPivot":[1],               # single-day check
    "PocketPivot":  [11],              # 1 up-day + 10 prior
}


# ================================================================
# DATABASE SETUP
# ================================================================

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date   TEXT,
            stock       TEXT,
            name        TEXT,
            sector      TEXT,
            cap_class   TEXT,
            cap_cr      REAL,
            pattern     TEXT,
            timeframe   TEXT,
            status      TEXT,
            breakout_zone REAL,
            cmp         REAL,
            quality     REAL,
            vol_surge   REAL,
            canslim_score INTEGER,
            recommendation TEXT,
            -- pattern metrics (generic slots)
            m1          REAL, m2 REAL, m3 REAL, m4 REAL, m5 REAL,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_date ON signals(scan_date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_stock ON signals(stock)")
    con.commit()
    return con


def save_signals(con, rows):
    if not rows:
        return
    con.executemany("""
        INSERT INTO signals
        (scan_date,stock,name,sector,cap_class,cap_cr,pattern,timeframe,
         status,breakout_zone,cmp,quality,vol_surge,canslim_score,recommendation,
         m1,m2,m3,m4,m5)
        VALUES
        (:scan_date,:stock,:name,:sector,:cap_class,:cap_cr,:pattern,:timeframe,
         :status,:breakout_zone,:cmp,:quality,:vol_surge,:canslim_score,:recommendation,
         :m1,:m2,:m3,:m4,:m5)""", rows)
    con.commit()


# ================================================================
# DATA HELPERS
# ================================================================

def load_universe():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    df  = pd.read_csv(url).dropna(subset=["SYMBOL"])
    for col in [" SERIES", "SERIES"]:
        if col in df.columns:
            df = df[df[col].str.strip() == "EQ"]
            break
    return [s.strip() + ".NS" for s in df["SYMBOL"].astype(str).tolist()]


def dl(sym, interval="1d", period=DATA_PERIOD):
    try:
        df = yf.download(sym, period=period, interval=interval,
                         auto_adjust=True, progress=False, timeout=15)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        return df if len(df) > 30 else None
    except Exception:
        return None


def dl_fund(sym):
    try:
        info = yf.Ticker(sym).info or {}
        return {
            "marketCap":                   info.get("marketCap"),
            "earningsQuarterlyGrowth":     info.get("earningsQuarterlyGrowth"),
            "earningsGrowth":              info.get("earningsGrowth"),
            "heldPercentInstitutions":     info.get("heldPercentInstitutions"),
            "sector":                      info.get("sector"),
            "longName":                    info.get("longName") or info.get("shortName"),
        }
    except Exception:
        return {}


def cap_class(mc):
    if not mc or pd.isna(mc):
        return "Unknown", None
    cr = mc / 1e7
    if cr >= 20000: return "Large", round(cr)
    if cr >= 5000:  return "Mid",   round(cr)
    if cr >= 500:   return "Small", round(cr)
    return "Micro", round(cr)


# ================================================================
# CANSLIM SCORING (as-of latest bar)
# ================================================================

def canslim_score(close, vol, fund, tf, nifty_close, nifty_ret):
    n   = len(close)
    idx = n - 1
    sc  = 0

    # N — near 52-week high
    lb = min(252 if tf == "Daily" else 52, idx)
    hi = np.max(close[max(0, idx-lb): idx+1])
    if hi > 0 and (hi - close[idx]) / hi <= CS["N_max_from_high"]:
        sc += 1

    # L — relative strength vs Nifty (1-year return ratio)
    rs_lb = 252 if tf == "Daily" else 52
    if idx >= rs_lb and idx < len(nifty_ret) and not np.isnan(nifty_ret[idx]):
        sr = close[idx] / close[idx - rs_lb] - 1
        nr = nifty_ret[idx]
        if (1 + nr) > 0 and (1 + sr) / (1 + nr) >= CS["L_min_rs"]:
            sc += 1

    # M — Nifty above 200-DMA
    sma_p = 200 if tf == "Daily" else 40
    if len(nifty_close) >= sma_p:
        if nifty_close[min(idx, len(nifty_close)-1)] > np.mean(nifty_close[-sma_p:]):
            sc += 1

    # S — daily turnover > ₹1 Cr
    if vol is not None and idx >= 20:
        to = np.mean(vol[idx-19:idx+1]) * np.mean(close[idx-19:idx+1]) / 1e7
        if to >= 1.0:
            sc += 1

    # C, A, I — current snapshot
    if fund.get("earningsQuarterlyGrowth") and fund["earningsQuarterlyGrowth"] >= CS["C_min"]:
        sc += 1
    if fund.get("earningsGrowth") and fund["earningsGrowth"] >= CS["A_min"]:
        sc += 1
    if fund.get("heldPercentInstitutions") and fund["heldPercentInstitutions"] >= CS["I_min_instl"]:
        sc += 1

    return sc


def recommend(status, score, nifty_up):
    bo = "Breakout" in status
    if bo and score >= CS["buy_strong"] and nifty_up:
        return "BUY — strong"
    if bo and score >= CS["buy_moderate"]:
        return "BUY — moderate"
    if not bo and score >= CS["buy_strong"]:
        return "WATCH — await breakout"
    if score >= CS["buy_moderate"]:
        return "WATCH — mixed"
    return "AVOID"


# ================================================================
# UTILITY — vol surge relative to recent average
# ================================================================

def vsurge(vol, n, lookback=20):
    if vol is None or n < lookback:
        return None
    avg = np.mean(vol[-lookback:])
    return round(vol[-1] / avg, 2) if avg > 0 else None


# ================================================================
# PATTERN DETECTORS
# Each returns None (no match) or a dict:
#   pattern, status, quality, breakout_zone, m1..m5, last
# ================================================================

def det_cup(c, v):
    n = len(c)
    if n < 50: return None
    s   = pd.Series(c).rolling(5, min_periods=1).mean().values
    ti  = int(np.argmin(s))
    if not (n * 0.20 <= ti <= n * 0.80): return None
    lm, rm = np.max(s[:ti+1]), np.max(s[ti:])
    pk, tr = max(lm, rm), s[ti]
    d   = (pk - tr) / pk
    if not (0.08 <= d <= 0.55): return None
    sym = abs(lm - rm) / pk
    if sym > 0.22: return None
    rpi = ti + int(np.argmax(s[ti:]))
    if rpi >= n - 2 or s[rpi] < pk * 0.88: return None
    h   = s[rpi:]
    hd  = (np.max(h) - np.min(h)) / np.max(h)
    if hd > 0.20 or np.min(h) < (pk + tr) / 2 * 0.92: return None
    r   = (n - rpi) / (rpi + 1)
    if not (0.10 <= r <= 0.45): return None
    cx  = np.arange(rpi + 1)
    try:
        cf  = np.polyfit(cx, s[:rpi+1], 2)
        fit = np.polyval(cf, cx)
        ssr = np.sum((s[:rpi+1] - fit) ** 2)
        sst = np.sum((s[:rpi+1] - np.mean(s[:rpi+1])) ** 2)
        r2  = 1 - ssr / sst if sst > 0 else 0
        if cf[0] <= 0 or r2 < 0.50: return None
    except Exception:
        return None
    vs = vsurge(v, n)
    bo = c[-1] >= pk * 0.97 and (vs is not None and vs >= 1.2)
    return dict(pattern="CupHandle", status="Breakout Ready" if bo else "Pattern Formed",
                quality=round(r2 - sym, 3), breakout_zone=round(float(pk), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(d*100, 2), m2=round(sym*100, 2),
                m3=round(hd*100, 2), m4=round(r, 2), m5=round(r2, 3))


def det_vcp(c, v):
    """Volatility Contraction Pattern — Minervishi."""
    n = len(c)
    if n < 40: return None
    # find swing highs and lows using peak detection
    prom = 0.02 * np.mean(c)
    try:
        highs, _ = find_peaks(c,  prominence=prom, distance=5)
        lows,  _ = find_peaks(-c, prominence=prom, distance=5)
    except Exception:
        return None
    if len(highs) < 2 or len(lows) < 2: return None

    # pair each high with the next low to form a contraction
    # also include the final swing high → its nearest subsequent low (or end of window)
    contractions = []
    high_list = list(highs) + [n]   # sentinel so last high gets processed
    for i, hi in enumerate(high_list[:-1]):
        next_high = high_list[i+1]
        next_lows = lows[(lows > hi) & (lows < next_high)]
        if len(next_lows) == 0:
            # no local low between highs — use the minimum of that stretch
            stretch = c[hi:next_high]
            if len(stretch) < 3: continue
            lo = hi + int(np.argmin(stretch))
        else:
            lo = next_lows[0]
        if lo >= n: continue
        depth = (c[hi] - c[lo]) / c[hi]
        if depth < 0.03: continue   # skip noise
        contractions.append((hi, lo, depth))

    if len(contractions) < 3: return None

    # check each contraction is tighter than previous
    depths = [ct[2] for ct in contractions]
    contracting = all(depths[i] <= depths[i-1] * 0.75 for i in range(1, len(depths)))
    if not contracting: return None

    # tightest contraction must be recent
    last_ct = contractions[-1]
    if last_ct[1] < n * 0.6: return None  # must be in latter 40%

    # volume must contract too
    vol_ok = True
    if v is not None:
        vol_in_contractions = [np.mean(v[ct[0]:ct[1]+1]) for ct in contractions]
        vol_ok = all(vol_in_contractions[i] <= vol_in_contractions[i-1] * 1.1
                     for i in range(1, len(vol_in_contractions)))

    pivot = c[highs[-1]]   # breakout level = highest point
    vs = vsurge(v, n)
    bo = c[-1] >= pivot * 0.98 and (vs is not None and vs >= 1.5)
    tightest = round(depths[-1] * 100, 2)
    first    = round(depths[0]  * 100, 2)
    contraction_ratio = round(depths[-1] / depths[0], 2) if depths[0] > 0 else None
    return dict(pattern="VCP", status="Breakout Ready" if bo else "Pattern Formed",
                quality=round(1 - depths[-1], 3), breakout_zone=round(float(pivot), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=first, m2=tightest, m3=contraction_ratio,
                m4=len(contractions), m5=1.0 if vol_ok else 0.0)


def det_flatbase(c, v):
    n = len(c)
    if n < 35: return None
    best = None
    for bl in range(15, min(75, n) + 1):
        base = c[-bl:]
        bh, blo = np.max(base), np.min(base)
        br = (bh - blo) / bh if bh > 0 else 1
        if br > 0.20: break
        bs  = n - bl
        tl  = min(80, bs)
        if tl < 15: continue
        pre = c[bs-tl:bs]
        tg  = (pre[-1] - np.min(pre)) / np.min(pre) if np.min(pre) > 0 else 0
        if tg < 0.10: continue
        if best is None or br < best["br"]:
            best = dict(bl=bl, bh=bh, blo=blo, br=br, tg=tg)
    if best is None: return None
    vs = vsurge(v, n)
    bo = c[-1] >= best["bh"] * 0.99 and (vs is not None and vs >= 1.2)
    return dict(pattern="FlatBase", status="Breakout Ready" if bo else "Pattern Formed",
                quality=round(best["tg"] - best["br"], 3),
                breakout_zone=round(float(best["bh"]), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(best["br"]*100, 2), m2=round(best["tg"]*100, 2),
                m3=best["bl"], m4=None, m5=None)


def det_ihs(c, v):
    n   = len(c)
    if n < 40: return None
    prom = 0.015 * np.mean(c)
    try:
        troughs, _ = find_peaks(-c, prominence=prom, distance=6)
    except Exception:
        return None
    if len(troughs) < 3: return None
    hc  = troughs[(troughs > n*0.20) & (troughs < n*0.80)]
    if len(hc) == 0: return None
    hi  = hc[np.argmin(c[hc])]
    hl  = [t for t in troughs if t < hi and c[t] > c[hi]]
    hr  = [t for t in troughs if t > hi and c[t] > c[hi]]
    if not hl or not hr: return None
    li, ri = hl[-1], hr[0]
    ls, hd, rs_ = c[li], c[hi], c[ri]
    sa   = (ls + rs_) / 2
    asym = abs(ls - rs_) / sa
    if asym > 0.18: return None
    hb   = (sa - hd) / sa
    if not (0.03 <= hb <= 0.50): return None
    nl   = (np.max(c[li:hi+1]) + np.max(c[hi:ri+1])) / 2
    if ri >= n - 2: return None
    vs  = vsurge(v, n)
    bo  = c[-1] >= nl * 0.99 and (vs is not None and vs >= 1.2)
    return dict(pattern="InvHS", status="Breakout Ready" if bo else "Pattern Formed",
                quality=round(hb - asym, 3), breakout_zone=round(float(nl), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(hb*100, 2), m2=round(asym*100, 2),
                m3=int(ri - li), m4=None, m5=None)


def det_dbot(c, v):
    n   = len(c)
    if n < 30: return None
    prom = 0.02 * np.mean(c)
    try:
        troughs, _ = find_peaks(-c, prominence=prom, distance=5)
    except Exception:
        return None
    if len(troughs) < 2: return None
    best = None
    for i in range(len(troughs)):
        for j in range(i+1, len(troughs)):
            sep = troughs[j] - troughs[i]
            if not (10 <= sep <= 150): continue
            p1, p2 = c[troughs[i]], c[troughs[j]]
            diff   = abs(p1 - p2) / min(p1, p2)
            if diff > 0.08: continue
            mid = np.max(c[troughs[i]:troughs[j]+1])
            mr  = (mid - (p1+p2)/2) / ((p1+p2)/2)
            if mr < 0.06 or troughs[j] >= n - 2: continue
            if best is None or mr - diff > best["sc"]:
                best = dict(sc=mr-diff, i=troughs[i], j=troughs[j],
                            p1=p1, p2=p2, mid=mid, diff=diff, mr=mr)
    if best is None: return None
    vs = vsurge(v, n)
    bo = c[-1] >= best["mid"] * 0.99 and (vs is not None and vs >= 1.2)
    return dict(pattern="DoubleBottom", status="Breakout Ready" if bo else "Pattern Formed",
                quality=round(best["sc"], 3), breakout_zone=round(float(best["mid"]), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(best["diff"]*100, 2), m2=round(best["mr"]*100, 2),
                m3=int(best["j"]-best["i"]), m4=None, m5=None)


def det_tbot(c, v):
    """Triple bottom — extends double bottom logic to 3 troughs."""
    n   = len(c)
    if n < 40: return None
    prom = 0.02 * np.mean(c)
    try:
        troughs, _ = find_peaks(-c, prominence=prom, distance=8)
    except Exception:
        return None
    if len(troughs) < 3: return None
    best = None
    for i in range(len(troughs)-2):
        for j in range(i+1, len(troughs)-1):
            for k in range(j+1, len(troughs)):
                p1,p2,p3 = c[troughs[i]], c[troughs[j]], c[troughs[k]]
                avg  = (p1+p2+p3)/3
                spread = (max(p1,p2,p3) - min(p1,p2,p3)) / avg
                if spread > 0.10: continue
                sep_ij = troughs[j]-troughs[i]; sep_jk = troughs[k]-troughs[j]
                if not (8<=sep_ij<=120 and 8<=sep_jk<=120): continue
                neckline = max(np.max(c[troughs[i]:troughs[j]+1]),
                               np.max(c[troughs[j]:troughs[k]+1]))
                if troughs[k] >= n-2: continue
                sc = (neckline/avg - 1) - spread
                if best is None or sc > best["sc"]:
                    best = dict(sc=sc, p1=p1, p2=p2, p3=p3,
                                neckline=neckline, spread=spread)
    if best is None: return None
    vs = vsurge(v, n)
    bo = c[-1] >= best["neckline"] * 0.99 and (vs is not None and vs >= 1.2)
    return dict(pattern="TripleBottom", status="Breakout Ready" if bo else "Pattern Formed",
                quality=round(best["sc"], 3),
                breakout_zone=round(float(best["neckline"]), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(best["spread"]*100, 2), m2=None, m3=None, m4=None, m5=None)


def det_asctri(c, v):
    n = len(c)
    if not (15 <= n <= 200): return None
    try:
        pks, _ = find_peaks( c, prominence=0.01*np.mean(c), distance=3)
        trs, _ = find_peaks(-c, prominence=0.01*np.mean(c), distance=3)
    except Exception:
        return None
    if len(pks) < 2 or len(trs) < 2: return None
    pp  = c[pks]; res = np.median(pp)
    sp  = (np.max(pp) - np.min(pp)) / res if res > 0 else 1
    if sp > 0.04: return None
    tp    = c[trs]
    slope = np.polyfit(trs, tp, 1)[0]
    rise  = (tp[-1] - tp[0]) / tp[0] if tp[0] > 0 else 0
    if slope <= 0 or rise < 0.015 or trs[-1] < n*0.4: return None
    vs  = vsurge(v, n)
    bo  = c[-1] >= res * 0.99 and (vs is not None and vs >= 1.2)
    return dict(pattern="AscTriangle", status="Breakout Ready" if bo else "Pattern Formed",
                quality=round(rise - sp, 3), breakout_zone=round(float(res), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(sp*100, 2), m2=round(rise*100, 2),
                m3=len(pks), m4=len(trs), m5=None)


def det_flag(c, v):
    n = len(c)
    if n < 10: return None
    best = None
    for pl in range(4, min(25, n-3)+1):
        for fl in range(3, min(20, n-pl)+1):
            tot  = pl + fl
            if tot > n: break
            pole = c[n-tot:n-fl]; flag = c[n-fl:]
            if pole[0] <= 0: continue
            pg = (pole[-1] - pole[0]) / pole[0]
            if not (0.08 <= pg <= 1.0): continue
            x = np.arange(pl)
            try:
                cf  = np.polyfit(x, pole, 1); fit = np.polyval(cf, x)
                ssr = np.sum((pole-fit)**2); sst = np.sum((pole-np.mean(pole))**2)
                r2  = 1 - ssr/sst if sst > 0 else 0
            except Exception:
                continue
            if cf[0] <= 0 or r2 < 0.55: continue
            up = np.sum(np.diff(pole) > 0) / (pl-1) if pl > 1 else 0
            if up < 0.55: continue
            fhi, flo = np.max(flag), np.min(flag)
            fd = (pole[-1] - flo) / pole[-1] if pole[-1] > 0 else 1
            if fd > 0.25: continue
            ph = pole[-1] - pole[0]
            fr = (fhi - flo) / ph if ph > 0 else 1
            if fr > 0.70: continue
            q  = pg * r2 * up - fd - fr*0.5
            if best is None or q > best["q"]:
                best = dict(q=q, pl=pl, fl=fl, pg=pg, r2=r2, up=up,
                            fhi=fhi, flo=flo, fd=fd, ps=c[n-tot], pt=pole[-1])
    if best is None: return None
    if v is not None and len(v) == n and best["fl"] > 1:
        fv  = np.mean(v[n-best["fl"]:-1])
        vs  = round(v[-1]/fv, 2) if fv > 0 else None
    else:
        vs = None
    bo  = c[-1] >= best["fhi"]*0.995 and (vs is not None and vs >= 1.2)
    tgt = best["fhi"] + (best["pt"] - best["ps"])
    return dict(pattern="BullFlag", status="Breakout Ready" if bo else "Flag Forming",
                quality=round(best["q"], 3), breakout_zone=round(float(best["fhi"]), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(best["pg"]*100, 2), m2=round(best["r2"], 3),
                m3=round(best["fd"]*100, 2), m4=best["pl"], m5=best["fl"])


def det_fwedge(c, v):
    """Falling wedge — two converging declining trendlines."""
    n = len(c)
    if n < 25: return None
    prom = 0.015 * np.mean(c)
    try:
        highs, _ = find_peaks( c, prominence=prom, distance=4)
        lows,  _ = find_peaks(-c, prominence=prom, distance=4)
    except Exception:
        return None
    if len(highs) < 2 or len(lows) < 2: return None
    # fit trendlines
    hx, hy = highs.astype(float), c[highs]
    lx, ly = lows.astype(float),  c[lows]
    try:
        h_slope = np.polyfit(hx, hy, 1)[0]
        l_slope = np.polyfit(lx, ly, 1)[0]
    except Exception:
        return None
    # both must be negative and lower trendline falling faster
    if h_slope >= 0 or l_slope >= h_slope: return None
    # convergence — upper falls slower than lower (they converge toward a point)
    if abs(l_slope) <= abs(h_slope): return None
    # width must be narrowing
    upper_at_end = np.polyval(np.polyfit(hx, hy, 1), n-1)
    lower_at_end = np.polyval(np.polyfit(lx, ly, 1), n-1)
    width_end    = upper_at_end - lower_at_end
    if width_end <= 0: return None
    vs = vsurge(v, n)
    bo = c[-1] >= upper_at_end * 0.99 and (vs is not None and vs >= 1.2)
    return dict(pattern="FallingWedge",
                status="Breakout Ready" if bo else "Pattern Formed",
                quality=round(abs(h_slope), 4),
                breakout_zone=round(float(upper_at_end), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(h_slope, 4), m2=round(l_slope, 4),
                m3=round(width_end, 2), m4=len(highs), m5=len(lows))


def det_momburst(c, v):
    """
    Pradeep Bonde Momentum Burst.
    5-day return >= 8% after a quiet period.
    """
    n = len(c)
    if n < 30: return None
    # burst = gain over last 5 days
    for lookback in [5, 7, 10]:
        if n < lookback + 10: continue
        ret = (c[-1] - c[-lookback]) / c[-lookback]
        if ret < 0.08: continue
        # quiet period — ATR in the 15 days before the burst was below its own 20d avg
        pre_start = max(0, n - lookback - 20)
        pre_end   = n - lookback
        if pre_end - pre_start < 10: continue
        pre_c = c[pre_start:pre_end]
        pre_atr = np.mean(np.abs(np.diff(pre_c)) / pre_c[:-1])
        all_atr = np.mean(np.abs(np.diff(c[:pre_end])) / c[:pre_end][:-1]) if pre_end > 1 else pre_atr
        quiet   = pre_atr <= all_atr * 1.1   # ATR was average or below
        # must be in uptrend
        ma50    = np.mean(c[-min(50,n):]) if n >= 10 else c[-1]
        uptrend = c[-1] > ma50
        if not quiet or not uptrend: continue
        # volume confirmation
        vs = vsurge(v, n)
        vo = vs is not None and vs >= 1.2
        return dict(pattern="MomBurst",
                    status="Burst Active" if vo else "Burst (low vol)",
                    quality=round(ret, 3),
                    breakout_zone=round(float(c[-1]), 2),
                    last=round(float(c[-1]), 2), vol_surge=vs,
                    m1=round(ret*100, 2),   # burst return %
                    m2=round(pre_atr*100, 4),  # quiet ATR
                    m3=lookback,
                    m4=1.0 if quiet else 0.0,
                    m5=1.0 if uptrend else 0.0)
    return None


def det_epivot(c, v):
    """
    Episodic Pivot (Kullamägi) — gap-up > 5% on 3x+ volume.
    Checks the *latest bar* as a single-day event.
    """
    n = len(c)
    if n < 22: return None
    # gap-up = today's open vs yesterday's close
    # since we only have close (daily bars), approximate: close today vs close yesterday
    gap = (c[-1] - c[-2]) / c[-2]
    if gap < 0.05: return None   # need >= 5% gap
    vs  = vsurge(v, n)
    if vs is None or vs < 3.0: return None   # need 3x+ volume
    ma200 = np.mean(c[-min(200,n):])
    uptrend = c[-1] > ma200
    if not uptrend: return None
    return dict(pattern="EpisodicPivot",
                status="Breakout Ready",
                quality=round(gap, 3),
                breakout_zone=round(float(c[-1]), 2),
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round(gap*100, 2),  # gap %
                m2=vs,
                m3=None, m4=None, m5=None)


def det_ppivot(c, v):
    """
    Pocket Pivot (Morales & Kacher).
    Up-day volume > highest down-day volume in prior 10 sessions.
    """
    n = len(c)
    if n < 12 or v is None: return None
    # today must be an up day
    if c[-1] <= c[-2]: return None
    today_vol = v[-1]
    # find highest down-day volume in last 10 sessions (days -11 to -2)
    window = min(10, n-2)
    max_down_vol = 0.0
    for i in range(2, window + 2):
        if c[-i] < c[-i-1]:   # down day
            max_down_vol = max(max_down_vol, v[-i])
    if max_down_vol == 0 or today_vol <= max_down_vol: return None
    # must be above 50-DMA
    ma50 = np.mean(c[-min(50,n):])
    if c[-1] < ma50: return None
    vs = round(today_vol / max_down_vol, 2)
    return dict(pattern="PocketPivot",
                status="Pocket Pivot",
                quality=round(vs, 2),
                breakout_zone=round(float(c[-2]), 2),  # prior day's close
                last=round(float(c[-1]), 2), vol_surge=vs,
                m1=round((c[-1]-c[-2])/c[-2]*100, 2),   # day gain %
                m2=vs,   # vol ratio vs max down-day
                m3=None, m4=None, m5=None)


# ================================================================
# DETECTOR REGISTRY
# ================================================================

DETECTORS = {
    "CupHandle":    det_cup,
    "VCP":          det_vcp,
    "FlatBase":     det_flatbase,
    "InvHS":        det_ihs,
    "DoubleBottom": det_dbot,
    "TripleBottom": det_tbot,
    "AscTriangle":  det_asctri,
    "BullFlag":     det_flag,
    "FallingWedge": det_fwedge,
    "MomBurst":     det_momburst,
    "EpisodicPivot":det_epivot,
    "PocketPivot":  det_ppivot,
}


# ================================================================
# SCAN ONE STOCK
# ================================================================

def scan_stock(sym, nifty_d, nifty_w, timeframes):
    fund   = dl_fund(sym)
    cc, cr = cap_class(fund.get("marketCap"))
    rows   = []

    for tf, interval in timeframes:
        df = dl(sym, interval)
        if df is None or len(df) < 30: continue

        close  = df["Close"].values.astype(float)
        vol    = df["Volume"].values.astype(float) if "Volume" in df.columns else None
        nifty  = nifty_d if tf == "Daily" else nifty_w
        nc     = nifty.reindex(df.index, method="ffill")["Close"].values

        # Nifty 1-year return at each bar
        rs_lb  = 252 if tf == "Daily" else 52
        nr     = np.full(len(nc), np.nan)
        for i in range(rs_lb, len(nc)):
            if nc[i - rs_lb] > 0:
                nr[i] = nc[i] / nc[i - rs_lb] - 1

        # Market direction
        sma_p  = 200 if tf == "Daily" else 40
        nifty_up = len(nc) >= sma_p and nc[-1] > np.mean(nc[-sma_p:])

        # CANSLIM score at latest bar
        cs = canslim_score(close, vol, fund, tf, nc, nr)

        for pat_name, detector in DETECTORS.items():
            windows = WINS.get(pat_name, [60])
            best = None
            for w in windows:
                if len(close) < w: continue
                seg_c = close[-w:]
                seg_v = vol[-w:] if vol is not None else None
                try:
                    res = detector(seg_c, seg_v)
                except Exception:
                    continue
                if res is None: continue
                if best is None or res["quality"] > best["quality"]:
                    best = {**res, "_w": w}

            if best is None: continue

            rec = recommend(best["status"], cs, nifty_up)
            if rec == "AVOID": continue   # filter out noise

            rows.append(dict(
                scan_date   = str(date.today()),
                stock       = sym.replace(".NS", ""),
                name        = fund.get("longName"),
                sector      = fund.get("sector"),
                cap_class   = cc,
                cap_cr      = cr,
                pattern     = best["pattern"],
                timeframe   = tf,
                status      = best["status"],
                breakout_zone = best["breakout_zone"],
                cmp         = best["last"],
                quality     = best["quality"],
                vol_surge   = best.get("vol_surge"),
                canslim_score = cs,
                recommendation = rec,
                m1 = best.get("m1"), m2 = best.get("m2"),
                m3 = best.get("m3"), m4 = best.get("m4"),
                m5 = best.get("m5"),
            ))
    return rows


# ================================================================
# TELEGRAM ALERT
# ================================================================

def send_telegram(rows_df):
    if not TG_TOKEN or not TG_CHAT:
        log.info("Telegram not configured — skipping alert")
        return
    try:
        import requests
    except ImportError:
        log.warning("requests not installed — no Telegram")
        return

    buys   = rows_df[rows_df["recommendation"].str.startswith("BUY", na=False)]
    watch  = rows_df[rows_df["recommendation"].str.startswith("WATCH", na=False)]

    lines = [
        f"<b>NSE Scanner — {date.today()}</b>",
        f"BUY: {len(buys)} | WATCH: {len(watch)}",
        "",
    ]
    for _, r in buys.head(20).iterrows():
        em = "🟢" if "strong" in r["recommendation"] else "🟡"
        lines.append(
            f"{em} <b>{r['stock']}</b> ({r.get('cap_class','?')}) "
            f"— {r['pattern']} {r['timeframe']}\n"
            f"   CMP ₹{r['cmp']} | BZ ₹{r['breakout_zone']} "
            f"| CANSLIM {r['canslim_score']}/7"
        )
    if len(watch) > 0:
        lines.append(f"\n<i>+ {len(watch)} WATCH signals in CSV</i>")

    msg = "\n".join(lines)
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TG_CHAT, "text": msg,
                                 "parse_mode": "HTML"}, timeout=10)
        log.info("Telegram alert sent")
    except Exception as e:
        log.error(f"Telegram failed: {e}")


# ================================================================
# MAIN
# ================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--test",     action="store_true", help="5 stocks only")
    ap.add_argument("--weekly",   action="store_true", help="add weekly TF")
    ap.add_argument("--monthly",  action="store_true", help="add monthly TF")
    args = ap.parse_args()

    # Timeframes
    timeframes = [("Daily", "1d")]
    if args.weekly:  timeframes.append(("Weekly",  "1wk"))
    if args.monthly: timeframes.append(("Monthly", "1mo"))

    log.info(f"=== NSE Scanner {date.today()} | TFs: {[t for t,_ in timeframes]} ===")
    t0 = time.time()

    # Universe
    log.info("Loading universe...")
    stocks = load_universe()
    if args.test:
        stocks = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ADANIENT.NS"]
        log.info(f"TEST MODE: {stocks}")
    else:
        log.info(f"{len(stocks)} stocks")

    # Benchmark
    log.info("Fetching Nifty...")
    nifty_d = dl(NIFTY_SYM, "1d")
    nifty_w = dl(NIFTY_SYM, "1wk")
    assert nifty_d is not None, "Nifty daily fetch failed"
    assert nifty_w is not None, "Nifty weekly fetch failed"

    # DB
    con = init_db()

    # Scan
    all_rows = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(scan_stock, s, nifty_d, nifty_w, timeframes): s
                for s in stocks}
        done = 0
        for fut in as_completed(futs):
            done += 1
            if done % 100 == 0:
                log.info(f"  {done}/{len(stocks)} scanned...")
            try:
                rows = fut.result()
                if rows:
                    all_rows.extend(rows)
            except Exception as e:
                log.warning(f"Error {futs[fut]}: {e}")

    elapsed = time.time() - t0
    log.info(f"Scan done in {elapsed/60:.1f} min — {len(all_rows)} signals")

    if not all_rows:
        log.info("No signals today.")
        return

    df = (pd.DataFrame(all_rows)
          .drop_duplicates(subset=["stock", "pattern", "timeframe"])
          .sort_values(["recommendation", "canslim_score", "quality"],
                       ascending=[True, False, False])
          .reset_index(drop=True))

    # Save to DB
    save_signals(con, df.to_dict("records"))
    con.close()

    # Save CSV
    csv_path = os.path.join(OUTPUT_DIR, f"scan_{date.today()}.csv")
    df.to_csv(csv_path, index=False)
    log.info(f"Saved → {csv_path}")

    # Summary
    log.info("\n--- By recommendation ---")
    log.info(df["recommendation"].value_counts().to_string())
    log.info("\n--- By pattern ---")
    log.info(df["pattern"].value_counts().to_string())
    log.info("\n--- Top BUYs ---")
    buys = df[df["recommendation"].str.startswith("BUY", na=False)]
    if len(buys):
        print(buys[["stock","cap_class","pattern","timeframe","status",
                     "cmp","breakout_zone","canslim_score","recommendation"]]
              .head(30).to_string(index=False))

    # Telegram
    if args.telegram:
        send_telegram(df)


if __name__ == "__main__":
    main()
