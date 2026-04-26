# NSE Complete Pattern Scanner

12 patterns, fully automated, runs daily after market close.

## Patterns covered

| Pattern | Type | Avg rise | What it detects |
|---|---|---|---|
| CupHandle | Base | ~34% | Rounded base + handle |
| VCP | Base | ~40% | Volatility contraction — Minervishi |
| FlatBase | Base | ~39% | Tight sideways consolidation |
| InvHS | Reversal | ~45% | Inverse head & shoulders |
| DoubleBottom | Reversal | ~38% | W-shape, two equal lows |
| TripleBottom | Reversal | ~37% | Three tests of support |
| AscTriangle | Continuation | ~35% | Flat resistance + rising lows |
| BullFlag | Continuation | ~23% | Pole + tight flag |
| FallingWedge | Reversal | ~32% | Converging downward trendlines |
| MomBurst | Momentum | ~8-12%/week | Pradeep Bonde's 3–5 day burst |
| EpisodicPivot | Momentum | ~10-15% | Gap-up on 3x+ volume |
| PocketPivot | Volume signal | Confirmation | Stealth institutional buying |

## Setup (15 minutes)

### 1. Install
```bash
pip install yfinance pandas numpy scipy requests
```

### 2. Test it works
```bash
python scanner.py --test
```
This scans 5 stocks only. Should finish in 2–3 minutes.

### 3. Full scan
```bash
python scanner.py
```
Takes 15–25 minutes. Results saved to `output/scan_YYYY-MM-DD.csv`
and `signals.db` (SQLite).

### 4. Set up Telegram alerts

**Get bot token:**
1. Message @BotFather on Telegram → `/newbot`
2. Follow prompts → copy the token

**Get your chat ID:**
1. Message your new bot anything
2. Visit: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Find `"chat":{"id":12345}` — that number is your chat ID

**Set environment variables:**
```bash
export TG_BOT_TOKEN="your_token_here"
export TG_CHAT_ID="your_chat_id_here"

python scanner.py --telegram
```

### 5. Automate with GitHub Actions (FREE, recommended)

1. Create a GitHub repo and push everything
2. Go to repo → Settings → Secrets → Actions
3. Add: `TG_BOT_TOKEN` and `TG_CHAT_ID`
4. Go to Actions tab → enable workflows
5. Done — it runs automatically Mon–Fri at 4:15 PM IST

To trigger manually: Actions → "NSE Pattern Scanner" → "Run workflow"

## Timeframes

```bash
python scanner.py                          # Daily only (default, 15-25 min)
python scanner.py --weekly                 # Daily + Weekly (~35 min)
python scanner.py --weekly --monthly       # All three (~50 min)
```

Run daily on weekdays. Run with `--weekly` on Saturdays for longer-term setups.

## Output files

- `output/scan_YYYY-MM-DD.csv` — today's signals (BUY and WATCH only)
- `signals.db` — all historical signals in SQLite

## Query the database

```python
import sqlite3, pandas as pd

con = sqlite3.connect("signals.db")

# All BUY signals this week
df = pd.read_sql("""
    SELECT stock, pattern, timeframe, status, cmp, breakout_zone,
           canslim_score, recommendation
    FROM signals
    WHERE scan_date >= date('now', '-7 days')
    AND recommendation LIKE 'BUY%'
    ORDER BY canslim_score DESC, quality DESC
""", con)
print(df)

# Which pattern works best? (forward return analysis — if you track manually)
# Add your own tracking table and join here
```

## Metric legend (m1–m5 columns per pattern)

| Pattern | m1 | m2 | m3 | m4 | m5 |
|---|---|---|---|---|---|
| CupHandle | cup_depth_% | symmetry_% | handle_drop_% | handle/cup_ratio | roundedness_R2 |
| VCP | first_contraction_% | tightest_contraction_% | contraction_ratio | num_contractions | vol_contracting |
| FlatBase | base_range_% | prior_trend_% | base_bars | - | - |
| InvHS | head_depth_% | shoulder_asym_% | pattern_width | - | - |
| DoubleBottom | bottom_diff_% | middle_rise_% | separation_bars | - | - |
| TripleBottom | spread_% | - | - | - | - |
| AscTriangle | resistance_spread_% | support_rise_% | top_touches | bot_touches | - |
| BullFlag | pole_gain_% | pole_R2 | flag_depth_% | pole_bars | flag_bars |
| FallingWedge | upper_slope | lower_slope | width_end | highs_count | lows_count |
| MomBurst | burst_return_% | quiet_ATR | lookback_days | was_quiet | is_uptrend |
| EpisodicPivot | gap_% | vol_surge_x | - | - | - |
| PocketPivot | day_gain_% | vol_vs_downday | - | - | - |

## Recommended trading workflow

1. Scanner runs at 4:15 PM IST → Telegram alert arrives
2. Next morning, check Telegram — see which stocks flagged
3. Open chart (TradingView/Zerodha) and visually confirm the pattern
4. Check: is the stock still near the breakout zone?
5. Check: is the broader market healthy? (Nifty above 200-DMA?)
6. Position size: never more than 5% of portfolio on a single trade
7. Set stop loss at: low of the pattern for base patterns;
   low of the burst day for MomBurst/EpisodicPivot
