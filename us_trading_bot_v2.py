#!/usr/bin/env python3
"""
US STOCK TRADING BOT v2 — T1MO Style
Free Data (Yahoo Finance) + Smart Money Detection + Telegram Alerts
Mode: Screener + Alert (manual entry)
Signals: Hawk1, Break Top, Green Bull, Buy Magenta, Smart Money, Institutional
"""

import os yfinance as yf
import os pandas as pd
import os numpy as np
import os requests
import os time
import os json
from datetime import os datetime, timedelta
import os pytz

# ============================================================
# ⚙️ CONFIG — EDIT DI SINI
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")
     

# Scan interval
SCAN_INTERVAL_SECONDS = 300   # 5 menit

# Signal cooldown (cegah spam signal yg sama)
SIGNAL_COOLDOWN_MINUTES = 60  # 1 jam per signal per ticker

# Hanya kirim signal dengan strength >= threshold
MIN_SIGNAL_STRENGTH = 4       # 1-7, makin tinggi makin selektif

# Timeframes yg di-scan setiap run
SCAN_TIMEFRAMES = ["5m", "15m", "30m", "1h", "1d"]

# ============================================================
# 📋 WATCHLIST US STOCKS
# ============================================================
WATCHLIST = [
    # AI / Mega Cap
    "NVDA", "MSFT", "AAPL", "META", "GOOGL", "AMZN", "TSLA",
    # Defense AI
    "PLTR", "RCAT", "AXON", "LHX", "RTX", "NOC",
    # Finance
    "JPM", "GS", "BAC", "V", "MA", "PYPL",
    # Energy
    "XOM", "CVX", "SLB", "OXY",
    # Semis
    "AMD", "INTC", "AVGO", "QCOM", "MU", "AMAT", "ASML",
    # Leveraged ETF (volatil, hati2)
    "TQQQ", "SOXL", "LABU",
    # Crypto-adjacent
    "MSTR", "COIN", "HOOD",
    # Hot movers
    "SMCI", "IONQ", "RGTI", "OKLO",
]

# ============================================================
# 🕐 MARKET HOURS CHECK
# ============================================================
ET = pytz.timezone("America/New_York")

def get_market_session() -> str:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return "CLOSED"
    t = now.hour * 60 + now.minute
    if 240 <= t < 570:   return "PRE"      # 04:00–09:30
    if 570 <= t < 960:   return "OPEN"     # 09:30–16:00
    if 960 <= t < 1200:  return "AFTER"    # 16:00–20:00
    return "CLOSED"

def should_scan() -> bool:
    s = get_market_session()
    return s in ("OPEN", "PRE", "AFTER")

# ============================================================
# 📡 TELEGRAM
# ============================================================
_sent_cache: dict = {}

def telegram(text: str, silent=False) -> bool:
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print(f"[TG-MOCK] {text[:100]}")
        return True
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
            "disable_web_page_preview": True,
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TG ERR] {e}")
        return False

def send_signal(ticker, signal_type, timeframe, price, change, strength, reason, entry_hint=""):
    key = f"{ticker}|{signal_type}|{timeframe}"
    now = time.time()
    if key in _sent_cache and (now - _sent_cache[key]) < SIGNAL_COOLDOWN_MINUTES * 60:
        return
    _sent_cache[key] = now

    icons = {
        "Hawk1":       "🦅",
        "Break Top":   "🚀",
        "Green Bull":  "🟢",
        "Buy Magenta": "💜",
        "Smart Money": "🏦",
        "Institutional":"🏛️",
        "Spec Sell":   "🔴",
    }
    icon = icons.get(signal_type, "📊")
    stars = "⭐" * min(strength, 5)
    session = get_market_session()
    session_tag = {"OPEN":"🟢 MARKET OPEN","PRE":"🌅 PRE-MARKET","AFTER":"🌙 AFTER HOURS","CLOSED":"⛔ CLOSED"}.get(session,"")

    msg = (
        f"{icon} <b>{signal_type}</b>  {stars}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{ticker}</b>  |  ⏱ <b>{timeframe.upper()}</b>  |  {session_tag}\n"
        f"💵 Price : <b>${price:.2f}</b>  ({change:+.2f}%)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📝 {reason}\n"
    )
    if entry_hint:
        msg += f"🎯 {entry_hint}\n"
    msg += (
        f"━━━━━━━━━━━━━━━━\n"
        f"🔗 <a href='https://finance.yahoo.com/quote/{ticker}'>Yahoo</a>  "
        f"<a href='https://www.tradingview.com/chart/?symbol={ticker}'>TradingView</a>\n"
        f"🕐 {datetime.now(ET).strftime('%H:%M ET')}"
    )
    ok = telegram(msg)
    tag = "✅" if ok else "🖨️"
    print(f"  {tag} [{signal_type}] {ticker} | {timeframe} | ${price:.2f} {change:+.2f}% | str={strength}")

# ============================================================
# 📊 DATA FETCH
# ============================================================
def fetch(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty or len(df) < 20:
            return None
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        df.rename(columns={"adj close":"close"}, inplace=True)
        df.dropna(subset=["close","high","low","open","volume"], inplace=True)
        return df
    except Exception as e:
        print(f"  [FETCH ERR] {ticker}/{interval}: {e}")
        return None

TF_CONFIG = {
    "5m":  ("5d",  "5m"),
    "15m": ("5d",  "15m"),
    "30m": ("30d", "30m"),
    "1h":  ("60d", "1h"),
    "1d":  ("1y",  "1d"),
}

# ============================================================
# 🧮 INDICATORS
# ============================================================
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))

def macd(s, f=12, sl=26, sg=9):
    m = ema(s, f) - ema(s, sl)
    return m, ema(m, sg)

def atr(df, n=14):
    h, l, c = df.high, df.low, df.close
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def obv_series(df):
    return (np.sign(df.close.diff().fillna(0)) * df.volume).cumsum()

def vwap_daily(df):
    tp = (df.high + df.low + df.close) / 3
    return (tp * df.volume).cumsum() / (df.volume.cumsum() + 1e-9)

def bbands(s, n=20, k=2.0):
    m = s.rolling(n).mean()
    std = s.rolling(n).std()
    return m - k*std, m, m + k*std

def stoch_rsi(s, n=14, k=3, d=3):
    r = rsi(s, n)
    lo = r.rolling(n).min()
    hi = r.rolling(n).max()
    k_raw = 100 * (r - lo) / (hi - lo + 1e-9)
    return k_raw.rolling(k).mean(), k_raw.rolling(k).mean().rolling(d).mean()

# ============================================================
# 🏦 SMART MONEY DETECTOR
# ============================================================
def smart_money_score(df: pd.DataFrame) -> tuple[int, list]:
    score = 0
    reasons = []
    if len(df) < 30:
        return 0, []

    c = df.close
    last = df.iloc[-1]
    vol_ma20 = df.volume.rolling(20).mean().iloc[-1]
    vol_ratio = last.volume / (vol_ma20 + 1e-9)
    atr_val = atr(df).iloc[-1]
    rng = last.high - last.low + 1e-9
    body = abs(last.close - last.open)
    obv = obv_series(df)
    vwap = vwap_daily(df).iloc[-1]

    # 1. Volume spike ≥ 2.5x avg
    if vol_ratio >= 2.5:
        score += 2
        reasons.append(f"Vol spike {vol_ratio:.1f}× avg")

    # 2. OBV tren naik 5 bar (akumulasi diam-diam)
    obv_slope = (obv.iloc[-1] - obv.iloc[-6]) / (abs(obv.iloc[-6]) + 1e-9)
    if obv_slope > 0.01 and obv.diff().iloc[-3:].mean() > 0:
        score += 1
        reasons.append("OBV accumulation ↑")

    # 3. Candle bull besar + high volume (institusi masuk agresif)
    if last.close > last.open and body/rng > 0.65 and vol_ratio > 1.8:
        score += 2
        reasons.append(f"Large bull candle body={body/rng*100:.0f}%")

    # 4. Buying absorption (candle merah tapi close dekat high)
    if last.close < last.open:
        lower_wick = max(last.open, last.close) - last.low
        if lower_wick/rng > 0.65 and vol_ratio > 1.5:
            score += 2
            reasons.append("Absorption candle (big lower wick)")

    # 5. VWAP bounce + volume
    if last.low <= vwap * 1.003 and last.close > vwap and vol_ratio > 1.3:
        score += 2
        reasons.append(f"VWAP bounce ${vwap:.2f}")

    # 6. Institutional block: volume >> normal tapi harga tidak jatuh
    if vol_ratio >= 3.5 and last.close >= df.close.iloc[-2] * 0.998:
        score += 2
        reasons.append(f"Institutional block {vol_ratio:.1f}× vol held price")

    # 7. Price near 52-week high dengan volume
    hi52 = df.high.rolling(252, min_periods=50).max().iloc[-1]
    if last.close >= hi52 * 0.98 and vol_ratio > 1.5:
        score += 1
        reasons.append("Near 52W high w/ volume")

    return score, reasons

# ============================================================
# 📡 SIGNAL ENGINE
# ============================================================
def detect_signals(df: pd.DataFrame) -> list:
    sigs = []
    if len(df) < 50:
        return sigs

    c = df.close
    h = df.high
    l = df.low
    v = df.volume
    last = df.iloc[-1]
    prev = df.iloc[-2]

    e9   = ema(c, 9)
    e21  = ema(c, 21)
    e50  = ema(c, 50)
    e200 = ema(c, 200) if len(df) >= 200 else ema(c, len(df)//2)
    r14  = rsi(c)
    ml, ms = macd(c)
    atr14 = atr(df)
    bbl, bbm, bbu = bbands(c)
    vol_ma = v.rolling(20).mean()
    vol_ratio = v.iloc[-1] / (vol_ma.iloc[-1] + 1e-9)
    sm_score, sm_reasons = smart_money_score(df)

    price  = c.iloc[-1]
    price1 = c.iloc[-2]
    chg    = (price - price1) / (price1 + 1e-9) * 100

    def entry_suggestion(sig_type):
        atr_val = atr14.iloc[-1]
        stop = round(price - 1.5 * atr_val, 2)
        tp1  = round(price + 2.0 * atr_val, 2)
        tp2  = round(price + 3.5 * atr_val, 2)
        if sig_type == "Spec Sell":
            stop = round(price + 1.5 * atr_val, 2)
            tp1  = round(price - 2.0 * atr_val, 2)
            tp2  = round(price - 3.5 * atr_val, 2)
            return f"Short entry: ~${price:.2f} | SL: ${stop} | TP1: ${tp1} | TP2: ${tp2}"
        return f"Entry: ~${price:.2f} | SL: ${stop} | TP1: ${tp1} | TP2: ${tp2}"

    # ── HAWK1 DET ─────────────────────────────────────────────
    # EMA stack bullish + MACD cross + RSI mid zone + volume
    hc = [
        e9.iloc[-1]  > e21.iloc[-1],
        e21.iloc[-1] > e50.iloc[-1],
        price > e9.iloc[-1],
        ml.iloc[-1]  > ms.iloc[-1],
        ml.iloc[-1]  > ml.iloc[-2],
        ml.iloc[-2]  <= ms.iloc[-2],         # fresh MACD cross
        55 < r14.iloc[-1] < 80,
        vol_ratio > 1.3,
    ]
    s = sum(hc)
    if s >= 6:
        sigs.append({
            "type":    "Hawk1",
            "strength": s,
            "reason":  f"EMA9>21>50 ✓ | MACD cross | RSI {r14.iloc[-1]:.0f} | Vol {vol_ratio:.1f}×",
            "entry":   entry_suggestion("Hawk1"),
        })

    # ── BREAK TOP ─────────────────────────────────────────────
    # Breakout level resistance 20-bar
    resist = h.rolling(20).max().iloc[-2]
    if price > resist * 1.001 and vol_ratio >= 1.5:
        sigs.append({
            "type":    "Break Top",
            "strength": 4 + (1 if vol_ratio > 2 else 0) + (1 if chg > 3 else 0),
            "reason":  f"Break 20-bar high ${resist:.2f} | Vol {vol_ratio:.1f}× | +{chg:.1f}%",
            "entry":   entry_suggestion("Break Top"),
        })

    # ── GREEN BULL ────────────────────────────────────────────
    # Trend bullish solid, RSI sehat
    gc = [
        price > e21.iloc[-1],
        price > e50.iloc[-1],
        e21.iloc[-1] > e50.iloc[-1],
        r14.iloc[-1] > 50,
        ml.iloc[-1] > 0,
        ml.iloc[-1] > ml.iloc[-3],
        chg > 0,
    ]
    s = sum(gc)
    if s >= 5:
        sigs.append({
            "type":    "Green Bull",
            "strength": s,
            "reason":  f"Trend bull | RSI {r14.iloc[-1]:.0f} | EMA stack | MACD>{ml.iloc[-1]:.3f}",
            "entry":   entry_suggestion("Green Bull"),
        })

    # ── BUY MAGENTA ───────────────────────────────────────────
    # Oversold reversal dari lower Bollinger
    mc = [
        price1 < bbl.iloc[-2] * 1.005,
        price  > bbl.iloc[-1],
        r14.iloc[-1] > r14.iloc[-2],
        r14.iloc[-1] < 48,
        ml.iloc[-1] > ml.iloc[-2],
        vol_ratio > 1.0,
        c.iloc[-1] > c.iloc[-2],
    ]
    s = sum(mc)
    if s >= 5:
        sigs.append({
            "type":    "Buy Magenta",
            "strength": s,
            "reason":  f"BB lower bounce | RSI {r14.iloc[-2]:.0f}→{r14.iloc[-1]:.0f} | MACD hook",
            "entry":   entry_suggestion("Buy Magenta"),
        })

    # ── SMART MONEY ───────────────────────────────────────────
    if sm_score >= 4:
        sigs.append({
            "type":    "Smart Money",
            "strength": sm_score,
            "reason":  " | ".join(sm_reasons),
            "entry":   entry_suggestion("Smart Money"),
        })

    # ── INSTITUTIONAL BUY ─────────────────────────────────────
    # Massive volume, harga hold/naik = institusi akumulasi
    if vol_ratio >= 3.5 and price >= price1 and chg >= -0.5:
        sigs.append({
            "type":    "Institutional",
            "strength": 5 + (1 if vol_ratio > 5 else 0),
            "reason":  f"Institutional flow {vol_ratio:.1f}× vol | Price held/rose {chg:+.2f}%",
            "entry":   entry_suggestion("Institutional"),
        })

    # ── SPEC SELL ─────────────────────────────────────────────
    sc = [
        e9.iloc[-1]  < e21.iloc[-1],
        price < e21.iloc[-1],
        price < e50.iloc[-1],
        r14.iloc[-1] < 45,
        ml.iloc[-1]  < ms.iloc[-1],
        chg < -1.5,
    ]
    s = sum(sc)
    if s >= 4:
        sigs.append({
            "type":    "Spec Sell",
            "strength": s,
            "reason":  f"Bearish EMA | RSI {r14.iloc[-1]:.0f} | MACD bearish | {chg:.1f}%",
            "entry":   entry_suggestion("Spec Sell"),
        })

    return sigs

# ============================================================
# 🔍 SCAN ONE TICKER × ONE TIMEFRAME
# ============================================================
def scan(ticker: str, tf: str) -> dict | None:
    period, interval = TF_CONFIG[tf]
    df = fetch(ticker, period, interval)
    if df is None or len(df) < 2:
        return None

    price  = df.close.iloc[-1]
    price1 = df.close.iloc[-2]
    chg    = (price - price1) / (price1 + 1e-9) * 100
    vol    = df.volume.iloc[-1]
    vol_ma = df.volume.rolling(20).mean().iloc[-1]

    sigs = detect_signals(df)
    # filter by min strength
    sigs = [s for s in sigs if s["strength"] >= MIN_SIGNAL_STRENGTH]
    if not sigs:
        return None

    top = max(sigs, key=lambda x: x["strength"])
    return {
        "ticker":    ticker,
        "tf":        tf,
        "price":     price,
        "change":    chg,
        "volume":    vol,
        "vol_ratio": vol / (vol_ma + 1e-9),
        "top":       top,
        "all_sigs":  [s["type"] for s in sigs],
    }

# ============================================================
# 🔄 FULL SCREENER RUN
# ============================================================
def run_screener(tickers=None, timeframes=None, notify=True) -> list:
    if tickers is None:   tickers    = WATCHLIST
    if timeframes is None: timeframes = SCAN_TIMEFRAMES

    session = get_market_session()
    ts      = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    print(f"\n{'═'*55}")
    print(f" 🔍 US QUANT SCAN  |  {ts}  |  {session}")
    print(f" Tickers: {len(tickers)} × TFs: {timeframes}")
    print(f"{'═'*55}")

    results = []
    for tf in timeframes:
        print(f"\n  [{tf.upper()}]")
        for ticker in tickers:
            res = scan(ticker, tf)
            if res:
                results.append(res)
                if notify:
                    send_signal(
                        res["ticker"],
                        res["top"]["type"],
                        res["tf"],
                        res["price"],
                        res["change"],
                        res["top"]["strength"],
                        res["top"]["reason"],
                        res["top"].get("entry",""),
                    )
            time.sleep(0.3)

    results.sort(key=lambda x: x["top"]["strength"], reverse=True)
    print(f"\n{'═'*55}")
    print(f" ✅ {len(results)} signals found (strength ≥ {MIN_SIGNAL_STRENGTH})")
    print(f"{'═'*55}\n")
    return results

# ============================================================
# 📨 SUMMARY TELEGRAM
# ============================================================
def send_summary(results: list):
    if not results:
        telegram("📊 <b>US QUANT SCAN</b> — No strong signals found this round.", silent=True)
        return

    session = get_market_session()
    ts = datetime.now(ET).strftime("%H:%M ET")

    lines = [f"📊 <b>US QUANT — SCAN SUMMARY</b>\n"
             f"🕐 {ts}  |  Session: {session}\n"
             f"Found <b>{len(results)}</b> signals\n"]

    icons = {"Hawk1":"🦅","Break Top":"🚀","Green Bull":"🟢",
             "Buy Magenta":"💜","Smart Money":"🏦","Institutional":"🏛️","Spec Sell":"🔴"}

    # Top 8 saja
    for r in results[:8]:
        t = r["top"]
        icon = icons.get(t["type"], "📌")
        lines.append(
            f"{icon} <b>{r['ticker']}</b> [{r['tf']}]  "
            f"${r['price']:.2f}  {r['change']:+.1f}%  "
            f"str:{t['strength']}  → {t['type']}"
        )

    telegram("\n".join(lines))

# ============================================================
# 💾 SAVE JSON (untuk dashboard)
# ============================================================
def save_json(results: list, path="us_signals.json"):
    out = [{
        "ticker":    r["ticker"],
        "tf":        r["tf"],
        "price":     round(r["price"], 2),
        "change":    round(r["change"], 2),
        "vol_ratio": round(r["vol_ratio"], 2),
        "signal":    r["top"]["type"],
        "strength":  r["top"]["strength"],
        "reason":    r["top"]["reason"],
        "entry":     r["top"].get("entry",""),
        "all":       r["all_sigs"],
    } for r in results]
    with open(path, "w") as f:
        json.dump({"ts": datetime.now().isoformat(), "data": out}, f, indent=2)
    print(f"💾 {len(out)} signals saved → {path}")

# ============================================================
# ▶️ MAIN LOOP
# ============================================================
def main():
    print("╔══════════════════════════════════════╗")
    print("║   US QUANT TRADING BOT v2 STARTED   ║")
    print("╚══════════════════════════════════════╝")
    print(f"  Tickers     : {len(WATCHLIST)}")
    print(f"  Timeframes  : {SCAN_TIMEFRAMES}")
    print(f"  Interval    : {SCAN_INTERVAL_SECONDS}s")
    print(f"  Min Strength: {MIN_SIGNAL_STRENGTH}")
    print(f"  TG Token    : {'SET ✅' if TELEGRAM_BOT_TOKEN != 'YOUR_BOT_TOKEN_HERE' else 'NOT SET ⚠️  (edit config)'}\n")

    telegram(
        f"🤖 <b>US Quant Bot STARTED</b>\n"
        f"Watching {len(WATCHLIST)} stocks × {len(SCAN_TIMEFRAMES)} timeframes\n"
        f"Scan every {SCAN_INTERVAL_SECONDS//60} min | Min strength: {MIN_SIGNAL_STRENGTH}"
    )

    while True:
        try:
            results = run_screener()
            save_json(results)
            send_summary(results)
        except KeyboardInterrupt:
            print("\n⛔ Bot stopped.")
            telegram("⛔ US Quant Bot stopped by user.")
            break
        except Exception as e:
            print(f"[ERR] {e}")
            time.sleep(30)
            continue

        print(f"⏳ Next scan in {SCAN_INTERVAL_SECONDS}s  ({SCAN_INTERVAL_SECONDS//60} min)...\n")
        time.sleep(SCAN_INTERVAL_SECONDS)

# ============================================================
# QUICK SCAN (non-loop, sekali jalan)
# ============================================================
def quick(tickers=None, tf="1d"):
    results = run_screener(tickers, [tf])
    save_json(results)
    send_summary(results)
    return results

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        quick(tf=sys.argv[2] if len(sys.argv) > 2 else "1d")
    else:
        main()
