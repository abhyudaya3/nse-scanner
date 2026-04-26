#!/usr/bin/env python3
"""
NSE 6-Pattern Daily Scanner
============================
Run once per day after market close (3:30 PM IST / 4 PM safe).
Scans all NSE stocks for patterns forming RIGHT NOW on the latest bar.
Emails/Telegram alerts for BUY signals.

DEPLOYMENT OPTIONS (see bottom of file):
1. Local cron job (simplest)
2. GitHub Actions (free, no server)
3. PythonAnywhere (free tier)
4. Railway / Render (free tier)

Usage:
    pip install yfinance pandas numpy scipy openpyxl requests
    python daily_scanner.py                 # run once
    python daily_scanner.py --telegram      # run + send Telegram alert
    python daily_scanner.py --email         # run + send email alert
"""

import os, sys, json, time, argparse
from datetime import datetime, date
import yfinance as yf
import pandas as pd
import numpy as np
from scipy.signal import find_peaks
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

# ================================================================
# CONFIG — edit these
# ================================================================
SCAN_DATE = date.today()
OUTPUT_DIR = os.environ.get('SCANNER_OUTPUT', './scanner_output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Telegram config (optional)
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')

# Email config (optional)
EMAIL_FROM = os.environ.get('EMAIL_FROM', '')
EMAIL_TO = os.environ.get('EMAIL_TO', '')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD', '')

MAX_WORKERS = 10
NIFTY = '^NSEI'
PERIOD = '1y'     # enough history for pattern detection

CANSLIM_CFG = {
    'C_min': 0.25, 'A_min': 0.25, 'N_max_from_high': 0.15,
    'L_min_rs': 1.10, 'I_min_instl': 0.20,
    'buy_strong': 6, 'buy_moderate': 4,
}

# ================================================================
# HELPERS (same as full scanner, compacted)
# ================================================================
def load_universe():
    url = 'https://archives.nseindia.com/content/equities/EQUITY_L.csv'
    df = pd.read_csv(url).dropna(subset=['SYMBOL'])
    for col in [' SERIES', 'SERIES']:
        if col in df.columns:
            df = df[df[col].str.strip() == 'EQ']; break
    return [s.strip() + '.NS' for s in df['SYMBOL'].astype(str).tolist()]

def dl(symbol, interval='1d', period=PERIOD):
    try:
        df = yf.download(symbol, period=period, interval=interval,
                         auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        return df if len(df) > 30 else None
    except Exception: return None

def dl_fund(symbol):
    try:
        info = yf.Ticker(symbol).info or {}
        return {k: info.get(k) for k in
                ['marketCap','earningsQuarterlyGrowth','earningsGrowth',
                 'heldPercentInstitutions','sector','longName','shortName']}
    except: return {}

def cap_class(mc):
    if mc is None: return 'Unknown'
    cr = mc / 1e7
    if cr >= 20000: return 'Large'
    if cr >= 5000:  return 'Mid'
    if cr >= 500:   return 'Small'
    return 'Micro'

def _vs(v, n):
    if v is None or n < 20: return None
    a = np.mean(v[-20:])
    return round(v[-1] / a, 2) if a > 0 else None

# ================================================================
# DETECTORS (compact, same logic as full scanner)
# ================================================================
def det_cup(c, v):
    n = len(c)
    if n < 50: return None
    s = pd.Series(c).rolling(5, min_periods=1).mean().values
    ti = int(np.argmin(s))
    if ti < n*0.20 or ti > n*0.80: return None
    lm, rm = np.max(s[:ti+1]), np.max(s[ti:])
    pk, tr = max(lm, rm), s[ti]
    d = (pk-tr)/pk
    if not (0.08 <= d <= 0.55): return None
    sym = abs(lm-rm)/pk
    if sym > 0.22: return None
    rpi = ti + int(np.argmax(s[ti:]))
    if rpi >= n-2 or s[rpi] < pk*0.88: return None
    h = s[rpi:]
    if len(h) < 2: return None
    hd = (np.max(h)-np.min(h))/np.max(h)
    if hd > 0.20 or np.min(h) < (pk+tr)/2*0.92: return None
    r = (n-rpi)/(rpi+1)
    if not (0.10 <= r <= 0.45): return None
    try:
        cf = np.polyfit(np.arange(rpi+1), s[:rpi+1], 2)
        fit = np.polyval(cf, np.arange(rpi+1))
        ssr=np.sum((s[:rpi+1]-fit)**2); sst=np.sum((s[:rpi+1]-np.mean(s[:rpi+1]))**2)
        r2=1-ssr/sst if sst>0 else 0
        if cf[0]<=0 or r2<0.50: return None
    except: return None
    vs = _vs(v, n)
    bo = c[-1] >= pk*0.97 and (vs is not None and vs >= 1.2)
    return {'pattern':'CupHandle','bo':bo,'q':round(r2-sym,3),'bz':round(float(pk),2),
            'depth':round(d*100,2),'sym':round(sym*100,2),'r2':round(r2,3),'vs':vs,
            'pk':round(float(pk),2),'lo':round(float(tr),2)}

def det_fb(c, v):
    n = len(c)
    if n < 35: return None
    best = None
    for bl in range(15, min(75,n)+1):
        base = c[-bl:]
        bh, blo = np.max(base), np.min(base)
        br = (bh-blo)/bh if bh>0 else 1
        if br > 0.20: break
        bs = n-bl; tl = min(80, bs)
        if tl < 15: continue
        pre = c[bs-tl:bs]
        tg = (pre[-1]-np.min(pre))/np.min(pre) if np.min(pre)>0 else 0
        if tg < 0.10: continue
        best = {'bl':bl,'bh':bh,'blo':blo,'br':br,'tg':tg}
    if best is None: return None
    vs = _vs(v, n)
    bo = c[-1] >= best['bh']*0.99 and (vs is not None and vs >= 1.2)
    return {'pattern':'FlatBase','bo':bo,'q':round(best['tg']-best['br'],3),
            'bz':round(float(best['bh']),2),'range':round(best['br']*100,2),
            'trend':round(best['tg']*100,2),'bars':best['bl'],'vs':vs,
            'pk':round(float(best['bh']),2),'lo':round(float(best['blo']),2)}

def det_ihs(c, v):
    n = len(c)
    if n < 40: return None
    try: troughs,_ = find_peaks(-c, prominence=0.015*np.mean(c), distance=6)
    except: return None
    if len(troughs) < 3: return None
    hc = troughs[(troughs>n*0.20)&(troughs<n*0.80)]
    if len(hc)==0: return None
    hi = hc[np.argmin(c[hc])]
    hl = [t for t in troughs if t<hi and c[t]>c[hi]]
    hr = [t for t in troughs if t>hi and c[t]>c[hi]]
    if not hl or not hr: return None
    li, ri = hl[-1], hr[0]
    ls, hd, rs_ = c[li], c[hi], c[ri]
    sa = (ls+rs_)/2; asym = abs(ls-rs_)/sa
    if asym > 0.18: return None
    hb = (sa-hd)/sa
    if not (0.03 <= hb <= 0.50): return None
    nl = (np.max(c[li:hi+1]) + np.max(c[hi:ri+1]))/2
    if ri >= n-2: return None
    vs = _vs(v, n)
    bo = c[-1] >= nl*0.99 and (vs is not None and vs >= 1.2)
    return {'pattern':'InverseHS','bo':bo,'q':round(hb-asym,3),'bz':round(float(nl),2),
            'head_depth':round(hb*100,2),'asym':round(asym*100,2),'width':int(ri-li),
            'vs':vs,'pk':round(float(nl),2),'lo':round(float(hd),2)}

def det_db(c, v):
    n = len(c)
    if n < 30: return None
    try: troughs,_ = find_peaks(-c, prominence=0.02*np.mean(c), distance=5)
    except: return None
    if len(troughs) < 2: return None
    best = None
    for i in range(len(troughs)):
        for j in range(i+1, len(troughs)):
            sep = troughs[j]-troughs[i]
            if not (10<=sep<=150): continue
            p1,p2 = c[troughs[i]], c[troughs[j]]
            diff = abs(p1-p2)/min(p1,p2)
            if diff > 0.08: continue
            mid = np.max(c[troughs[i]:troughs[j]+1])
            mr = (mid-(p1+p2)/2)/((p1+p2)/2)
            if mr < 0.06 or troughs[j]>=n-2: continue
            sc = mr-diff
            if best is None or sc>best['sc']:
                best={'sc':sc,'i':troughs[i],'j':troughs[j],'p1':p1,'p2':p2,'mid':mid,'diff':diff,'mr':mr}
    if best is None: return None
    vs = _vs(v, n)
    bo = c[-1] >= best['mid']*0.99 and (vs is not None and vs >= 1.2)
    return {'pattern':'DoubleBottom','bo':bo,'q':round(best['sc'],3),'bz':round(float(best['mid']),2),
            'diff':round(best['diff']*100,2),'rise':round(best['mr']*100,2),
            'sep':int(best['j']-best['i']),'vs':vs,
            'pk':round(float(best['mid']),2),'lo':round(float(min(best['p1'],best['p2'])),2)}

def det_at(c, v):
    n = len(c)
    if not (15<=n<=200): return None
    try:
        pks,_ = find_peaks(c, prominence=0.01*np.mean(c), distance=3)
        trs,_ = find_peaks(-c, prominence=0.01*np.mean(c), distance=3)
    except: return None
    if len(pks)<2 or len(trs)<2: return None
    pp=c[pks]; res=np.median(pp); sp=(np.max(pp)-np.min(pp))/res if res>0 else 1
    if sp>0.04: return None
    tp=c[trs]; slope=np.polyfit(trs,tp,1)[0]
    rise=(tp[-1]-tp[0])/tp[0] if tp[0]>0 else 0
    if slope<=0 or rise<0.015 or trs[-1]<n*0.4: return None
    vs = _vs(v, n)
    bo = c[-1] >= res*0.99 and (vs is not None and vs >= 1.2)
    return {'pattern':'AscTriangle','bo':bo,'q':round(rise-sp,3),'bz':round(float(res),2),
            'spread':round(sp*100,2),'rise':round(rise*100,2),
            'top_touches':len(pks),'bot_touches':len(trs),'vs':vs,
            'pk':round(float(res),2),'lo':round(float(tp[0]),2)}

def det_fl(c, v):
    n = len(c)
    if n < 10: return None
    best = None
    for pl in range(4, min(25,n-3)+1):
        for fl in range(3, min(20,n-pl)+1):
            tot=pl+fl
            if tot>n: break
            pole=c[n-tot:n-fl]; flag=c[n-fl:]
            if pole[0]<=0: continue
            pg=(pole[-1]-pole[0])/pole[0]
            if not (0.08<=pg<=1.0): continue
            x=np.arange(pl)
            try:
                cf=np.polyfit(x,pole,1); fit=np.polyval(cf,x)
                ssr=np.sum((pole-fit)**2); sst=np.sum((pole-np.mean(pole))**2)
                r2=1-ssr/sst if sst>0 else 0
            except: continue
            if cf[0]<=0 or r2<0.55: continue
            up=np.sum(np.diff(pole)>0)/(pl-1) if pl>1 else 0
            if up<0.55: continue
            fhi,flo = np.max(flag), np.min(flag)
            fd=(pole[-1]-flo)/pole[-1] if pole[-1]>0 else 1
            if fd>0.25: continue
            ph=pole[-1]-pole[0]
            fr=(fhi-flo)/ph if ph>0 else 1
            if fr>0.70: continue
            q=pg*r2*up-fd-fr*0.5
            if best is None or q>best['q']:
                best={'q':q,'pl':pl,'fl':fl,'pg':pg,'r2':r2,'up':up,
                      'fhi':fhi,'flo':flo,'fd':fd,'ps':c[n-tot],'pt':pole[-1]}
    if best is None: return None
    if v is not None and len(v)==n and best['fl']>1:
        fv=np.mean(v[n-best['fl']:-1])
        vs=round(v[-1]/fv,2) if fv>0 else None
    else: vs=None
    bo = c[-1]>=best['fhi']*0.995 and (vs is not None and vs>=1.2)
    tgt = best['fhi']+(best['pt']-best['ps'])
    return {'pattern':'BullFlag','bo':bo,'q':round(best['q'],3),'bz':round(float(best['fhi']),2),
            'pole_gain':round(best['pg']*100,2),'pole_r2':round(best['r2'],3),
            'flag_depth':round(best['fd']*100,2),'pole_bars':best['pl'],'flag_bars':best['fl'],
            'target':round(float(tgt),2),'vs':vs,
            'pk':round(float(best['fhi']),2),'lo':round(float(best['flo']),2)}

DETS = [det_cup, det_fb, det_ihs, det_db, det_at, det_fl]
WINS = {
    'det_cup':[60,80,120,180,250], 'det_fb':[40,60,80,120,180],
    'det_ihs':[60,80,120,180,250], 'det_db':[40,60,100,150,200],
    'det_at':[30,50,80,120,180],   'det_fl':[15,20,30,40,50,60],
}

# ================================================================
# DAILY SCANNER — SCANS ONLY THE LATEST BAR
# ================================================================
def scan_latest(symbol, nifty_d, nifty_w):
    fund = dl_fund(symbol)
    rows = []
    for tf, intv, nifty in [('Daily','1d',nifty_d), ('Weekly','1wk',nifty_w)]:
        df = dl(symbol, intv)
        if df is None or len(df) < 30: continue
        close = df['Close'].values.astype(float)
        vol = df['Volume'].values.astype(float) if 'Volume' in df.columns else None
        dates = df.index
        nc = nifty.reindex(dates, method='ffill')['Close'].values
        n = len(close)

        # ONLY scan at the latest bar (end = n)
        end = n
        for det in DETS:
            for w in WINS[det.__name__]:
                if end < w: continue
                sc = close[end-w:end]
                sv = vol[end-w:end] if vol is not None else None
                res = det(sc, sv)
                if res is None: continue

                # quick CANSLIM
                rs_lb = 252 if tf == 'Daily' else 52
                score = 0
                # N
                lb = min(252 if tf=='Daily' else 52, n-1)
                hi = np.max(close[max(0,n-1-lb):n])
                if hi > 0 and (hi-close[-1])/hi <= 0.15: score += 1
                # L
                if n-1 >= rs_lb and len(nc) > n-1:
                    try:
                        sr = close[-1]/close[-1-rs_lb]-1
                        nr_ = nc[-1]/nc[-1-rs_lb]-1 if nc[-1-rs_lb]>0 else 0
                        if (1+sr)/(1+nr_) >= 1.10: score += 1
                    except: pass
                # M
                sma_p = 200 if tf=='Daily' else 40
                if len(nc) >= sma_p:
                    if nc[-1] > np.mean(nc[-sma_p:]): score += 1
                # S
                if vol is not None and n >= 20:
                    to = np.mean(vol[-20:])*np.mean(close[-20:])/1e7
                    if to >= 1.0: score += 1
                # C, A, I
                if fund.get('earningsQuarterlyGrowth') and fund['earningsQuarterlyGrowth'] >= 0.25: score += 1
                if fund.get('earningsGrowth') and fund['earningsGrowth'] >= 0.25: score += 1
                if fund.get('heldPercentInstitutions') and fund['heldPercentInstitutions'] >= 0.20: score += 1

                status = 'Breakout Ready' if res['bo'] else 'Pattern Formed'
                mkt_up = len(nc) >= (200 if tf=='Daily' else 40) and nc[-1] > np.mean(nc[-(200 if tf=='Daily' else 40):])

                # Recommendation
                if res['bo'] and score >= 6 and mkt_up: rec = 'BUY — strong'
                elif res['bo'] and score >= 4: rec = 'BUY — moderate'
                elif not res['bo'] and score >= 6: rec = 'WATCH — await breakout'
                elif score >= 4: rec = 'WATCH — mixed'
                else: rec = 'AVOID'

                if 'AVOID' in rec: continue

                rows.append({
                    'Stock': symbol.replace('.NS',''),
                    'Name': fund.get('longName') or fund.get('shortName'),
                    'Sector': fund.get('sector'),
                    'Cap': cap_class(fund.get('marketCap')),
                    'Pattern': res['pattern'],
                    'TF': tf,
                    'Date': dates[-1].date(),
                    'Status': status,
                    'Breakout_Zone': res['bz'],
                    'CMP': round(float(close[-1]),2),
                    'Upside_%': round((res['bz']-close[-1])/close[-1]*100, 2) if not res['bo'] else 0,
                    'Quality': res['q'],
                    'Vol_Surge': res.get('vs'),
                    'CANSLIM': score,
                    'Reco': rec,
                    'Window': w,
                })
                break  # best window per detector
    return rows

# ================================================================
# ALERTS
# ================================================================
def send_telegram(msg):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print('[TELEGRAM] No credentials set. Skipping.')
        return
    import requests
    url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
    requests.post(url, data={'chat_id': TG_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'})
    print('[TELEGRAM] Alert sent.')

def send_telegram_document(file_path, caption='📊 Scanner Results'):
    """Send Excel/CSV file to Telegram"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print('[TELEGRAM] No credentials set. Skipping file upload.')
        return
    
    import requests
    url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendDocument'
    
    try:
        with open(file_path, 'rb') as f:
            files = {'document': f}
            data = {'chat_id': TG_CHAT_ID, 'caption': caption}
            response = requests.post(url, files=files, data=data)
            if response.ok:
                print(f'[TELEGRAM] Document sent: {file_path}')
            else:
                print(f'[TELEGRAM] Error sending document: {response.text}')
    except Exception as e:
        print(f'[TELEGRAM] File upload error: {e}')

def send_email(subject, body):
    if not EMAIL_FROM or not EMAIL_TO or not EMAIL_PASSWORD:
        print('[EMAIL] No credentials set. Skipping.')
        return
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body, 'html')
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(EMAIL_FROM, EMAIL_PASSWORD)
        s.send_message(msg)
    print('[EMAIL] Alert sent.')

def format_alert(df):
    lines = [f"<b>📊 NSE Pattern Scanner — {SCAN_DATE}</b>\n"]
    lines.append(f"Found <b>{len(df)}</b> actionable signals:\n")
    for _, r in df.iterrows():
        emoji = '🟢' if 'strong' in r['Reco'] else '🟡'
        lines.append(
            f"{emoji} <b>{r['Stock']}</b> ({r['Cap']}) — {r['Pattern']} on {r['TF']}\n"
            f"   Status: {r['Status']} | CMP: ₹{r['CMP']} | Breakout: ₹{r['Breakout_Zone']}\n"
            f"   CANSLIM: {r['CANSLIM']}/7 | Quality: {r['Quality']} | {r['Reco']}\n"
        )
    return '\n'.join(lines)

# ================================================================
# MAIN
# ================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--telegram', action='store_true')
    parser.add_argument('--email', action='store_true')
    parser.add_argument('--limit', type=int, default=None, help='Limit stocks (for testing)')
    args = parser.parse_args()

    print(f'=== Daily Scanner — {SCAN_DATE} ===')
    t0 = time.time()

    stocks = load_universe()
    if args.limit: stocks = stocks[:args.limit]
    print(f'{len(stocks)} stocks')

    nifty_d = dl(NIFTY, '1d')
    nifty_w = dl(NIFTY, '1wk')
    assert nifty_d is not None and nifty_w is not None

    all_rows = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(scan_latest, s, nifty_d, nifty_w): s for s in stocks}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f'  {done}/{len(stocks)}...')
            try:
                r = fut.result()
                if r: all_rows.extend(r)
            except Exception as e:
                pass  # silent

    df = pd.DataFrame(all_rows)
    if len(df):
        df = df.drop_duplicates(subset=['Stock','Pattern','TF']).reset_index(drop=True)
        df = df.sort_values(['Reco','CANSLIM','Quality'], ascending=[True, False, False])

    elapsed = time.time() - t0
    print(f'\nDone in {elapsed/60:.1f} min')
    print(f'Signals: {len(df)}')

    if len(df):
        print('\n--- By Pattern ---')
        print(df['Pattern'].value_counts())
        print('\n--- By Recommendation ---')
        print(df['Reco'].value_counts())
        print('\n--- Top signals ---')
        print(df[['Stock','Cap','Pattern','TF','Status','CMP','Breakout_Zone',
                   'Quality','CANSLIM','Reco']].head(30).to_string(index=False))

        # Save CSV
        csv_path = os.path.join(OUTPUT_DIR, f'scan_{SCAN_DATE}.csv')
        df.to_csv(csv_path, index=False)
        print(f'\nSaved → {csv_path}')

        # Alerts
        if args.telegram:
            buys = df[df['Reco'].str.startswith('BUY')]
            if len(buys):
                send_telegram(format_alert(buys))
            else:
                send_telegram(f'📊 NSE Scanner {SCAN_DATE}: No BUY signals today.')
            
            # Send Excel file
            excel_file = os.path.join(OUTPUT_DIR, f'signals_{SCAN_DATE}.xlsx')
            try:
                with pd.ExcelWriter(excel_file, engine='openpyxl') as w:
                    buys.to_excel(w, sheet_name='BUY_signals', index=False) if len(buys) else None
                    df.to_excel(w, sheet_name='All_signals', index=False)
                send_telegram_document(excel_file, f'📊 Complete Scan - {SCAN_DATE}')
            except Exception as e:
                print(f'[ERROR] Failed to create/send Excel: {e}')

        if args.email:
            buys = df[df['Reco'].str.startswith('BUY')]
            send_email(
                f'NSE Scanner {SCAN_DATE} — {len(buys)} BUY signals',
                format_alert(buys if len(buys) else df)
            )
    else:
        print('No signals today.')

    return df

if __name__ == '__main__':
    main()
