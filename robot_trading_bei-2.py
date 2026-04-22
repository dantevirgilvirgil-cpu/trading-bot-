"""
╔══════════════════════════════════════════════════════════════════╗
║     ROBOT TRADING IDX v2 — T1MO x WISDOM QUANTITATIVE BOT      ║
║     Deploy: Railway | Data: yfinance 2m                         ║
║     Mode: AUTO ALERT + INTERACTIVE COMMANDS                     ║
╠══════════════════════════════════════════════════════════════════╣
║  COMMANDS:                                                       ║
║  /chart BBCA     → Tabel indikator + ASCII chart                ║
║  /signal BBCA    → Analisis sinyal lengkap                      ║
║  /screener       → Top signal semua watchlist                   ║
║  /zona BBCA      → Zona & trend detail                          ║
║  /trend BBCA     → Trend analysis                               ║
║  /volume BBCA    → Volume analysis                              ║
║  /watchlist      → Lihat daftar watchlist                       ║
║  /add TLKM       → Tambah ticker IDX ke watchlist               ║
║  /addus TSLA     → Tambah ticker US ke watchlist                ║
║  /remove TLKM    → Hapus ticker dari watchlist                  ║
║  /summary        → Summary harian semua ticker                  ║
║  /help           → Tampilkan semua command                      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, time, logging, threading
from datetime import datetime
import pytz, requests, yfinance as yf
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────────
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
WATCHLIST_IDX_RAW = os.environ.get("WATCHLIST_IDX", "BBCA,BBRI,BMRI,TLKM,ASII,MDKA,ANTM,NCKL,MBMA,PTBA,INCO,MEDC,ENRG,ELSA,BULL,TMAS")
WATCHLIST_US_RAW  = os.environ.get("WATCHLIST_US",  "PLTR,NVDA,TSLA,AAPL,AMD")

WATCHLIST_IDX = [f"{t.strip()}.JK" for t in WATCHLIST_IDX_RAW.split(",") if t.strip()]
WATCHLIST_US  = [t.strip() for t in WATCHLIST_US_RAW.split(",") if t.strip()]
dynamic_watchlist = {"IDX": list(WATCHLIST_IDX), "US": list(WATCHLIST_US)}

VOLUME_SPIKE_MULT = 2.0
VOL_AVG_PERIOD    = 20
EMA_FAST, EMA_SLOW = 8, 21
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
RSI_PERIOD = 14
STOCH_K, STOCH_D = 15, 3
BB_PERIOD, BB_STD = 20, 2
SCAN_INTERVAL     = 120
COOLDOWN_SEC      = 1800

WIB = pytz.timezone("Asia/Jakarta")
signal_cooldown = {}
last_update_id  = 0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def all_tickers():
    return dynamic_watchlist["IDX"] + dynamic_watchlist["US"]

def normalize_ticker(raw):
    t = raw.upper().strip()
    if t.endswith(".JK"):
        return t
    base = t.replace(".JK", "")
    if any(base == x.replace(".JK","") for x in dynamic_watchlist["IDX"]):
        return base + ".JK"
    return t

def is_idx(ticker):
    return ticker.endswith(".JK")

def fmt_price(price, ticker):
    return f"Rp {price:,.0f}" if is_idx(ticker) else f"${price:.2f}"

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(text, chat_id=None):
    if not TELEGRAM_TOKEN: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id or TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        )
    except Exception as e:
        log.error(f"Telegram: {e}")

def get_updates():
    global last_update_id
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 5}, timeout=10
        )
        data = r.json()
        if data.get("ok"):
            return data.get("result", [])
    except:
        pass
    return []

# ─────────────────────────────────────────────
#  INDIKATOR
# ─────────────────────────────────────────────
def fetch_data(ticker, interval="2m", period="5d"):
    try:
        df = yf.download(ticker, interval=interval, period=period,
                         progress=False, auto_adjust=True, timeout=15)
        if df is None or df.empty: raise ValueError("Empty")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        if len(df) < 30: raise ValueError("Too short")
        return df
    except:
        if interval == "2m":
            return fetch_data(ticker, "5m", "5d")
        return None

def calc_ema(s, n): return s.ewm(span=n, adjust=False).mean()
def calc_rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def calc_macd(s):
    ml = calc_ema(s, MACD_FAST) - calc_ema(s, MACD_SLOW)
    sl = calc_ema(ml, MACD_SIG)
    return ml, sl, ml - sl

def calc_stoch(h, l, c):
    lm = l.rolling(STOCH_K).min(); hm = h.rolling(STOCH_K).max()
    k = 100 * (c - lm) / (hm - lm).replace(0, np.nan)
    return k, k.rolling(STOCH_D).mean()

def calc_bb(s):
    m = s.rolling(BB_PERIOD).mean(); std = s.rolling(BB_PERIOD).std()
    return m + BB_STD * std, m, m - BB_STD * std

def get_zona(p, bl, bm, bu, ef, es):
    if p > bu and ef > es: return "ZONA 5 🔥", "STRONG BULL / OVERBOUGHT"
    if p > bm and ef > es: return "ZONA 4 ✅", "TREND UP"
    if p > bm:             return "ZONA 3 ⚡", "SIDEWAYS BULLISH"
    if p > bl:             return "ZONA 2 ⚠️", "SIDEWAYS LEMAH"
    return "ZONA 1 🔴", "DOWNTREND"

def get_trend(ef, es, mv, sv):
    if ef > es and mv > sv: return "TREND UP ↑", "🟢"
    if ef < es and mv < sv: return "TREND DOWN ↓", "🔴"
    return "SIDEWAYS ↔", "🟡"

def analyze(ticker):
    df = fetch_data(ticker)
    if df is None: return None
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ef_s = calc_ema(c, EMA_FAST); es_s = calc_ema(c, EMA_SLOW)
    ml, sl, mh = calc_macd(c)
    rsi_s = calc_rsi(c)
    sk, sd = calc_stoch(h, l, c)
    bu, bm, bl = calc_bb(c)
    va = v.rolling(VOL_AVG_PERIOD).mean()
    tp = (h + l + c) / 3
    vwap = (tp * v).rolling(20).sum() / v.rolling(20).sum()

    i = -1
    price = float(c.iloc[i]); prev = float(c.iloc[i-1])
    vol_n = float(v.iloc[i]); vol_a = float(va.iloc[i])
    ef = float(ef_s.iloc[i]); es_ = float(es_s.iloc[i])
    mv = float(ml.iloc[i]); sv_ = float(sl.iloc[i]); hv = float(mh.iloc[i])
    rsi = float(rsi_s.iloc[i])
    skv = float(sk.iloc[i]); sdv = float(sd.iloc[i])
    bu_ = float(bu.iloc[i]); bm_ = float(bm.iloc[i]); bl_ = float(bl.iloc[i])
    avg_p = float(vwap.iloc[i])
    chg = (price - prev) / prev * 100 if prev > 0 else 0
    vol_ratio = vol_n / vol_a if vol_a > 0 else 0
    vol_spike = vol_ratio >= VOLUME_SPIKE_MULT
    zona, zona_d = get_zona(price, bl_, bm_, bu_, ef, es_)
    trend, trend_e = get_trend(ef, es_, mv, sv_)
    closes = [float(x) for x in c.iloc[-20:].values]

    sig = sig_e = sig_l = None
    if vol_spike and chg > 0 and hv > 0 and ef > es_:
        sig, sig_e, sig_l = "AKUMULASI", "🟢▲", "AKUMULASI BESAR"
    elif vol_spike and chg < 0 and hv < 0:
        sig, sig_e, sig_l = "DISTRIBUSI", "🔴▼", "DISTRIBUSI / JUAL"
    elif price > bu_ and vol_spike and hv > 0:
        sig, sig_e, sig_l = "BREAKOUT", "⚡🚀", "BREAKOUT BULLISH"
    elif price < bl_ and vol_spike and hv < 0:
        sig, sig_e, sig_l = "BREAKDOWN", "📉💀", "BREAKDOWN / WASPADA"

    return dict(ticker=ticker, price=price, prev=prev, chg=chg,
                vol_n=vol_n, vol_a=vol_a, vol_ratio=vol_ratio, vol_spike=vol_spike,
                ef=ef, es=es_, mv=mv, sv=sv_, hv=hv, rsi=rsi,
                skv=skv, sdv=sdv, bu=bu_, bm=bm_, bl=bl_, avg_p=avg_p,
                zona=zona, zona_d=zona_d, trend=trend, trend_e=trend_e,
                sig=sig, sig_e=sig_e, sig_l=sig_l, closes=closes)

# ─────────────────────────────────────────────
#  ASCII CHART
# ─────────────────────────────────────────────
def ascii_chart(closes, width=20, height=7):
    if not closes or len(closes) < 2: return "  [no data]"
    mn, mx = min(closes), max(closes)
    rng = mx - mn if mx != mn else 1
    rows = []
    for row in range(height, 0, -1):
        threshold = mn + (row / height) * rng
        line = ""
        for i, val in enumerate(closes):
            prev_v = closes[i-1] if i > 0 else val
            if abs(val - threshold) < rng / height / 1.5:
                line += "●"
            elif val >= threshold:
                line += "─"
            else:
                line += " "
        if row == height:
            lbl = f"{mx:,.0f}" if mx > 1000 else f"{mx:.2f}"
        elif row == height // 2:
            mid = mn + 0.5 * rng
            lbl = f"{mid:,.0f}" if mid > 1000 else f"{mid:.2f}"
        elif row == 1:
            lbl = f"{mn:,.0f}" if mn > 1000 else f"{mn:.2f}"
        else:
            lbl = ""
        rows.append(f"  {line} {lbl}")
    rows.append("  " + "─" * width + "→")
    return "\n".join(rows)

# ─────────────────────────────────────────────
#  MESSAGE FORMATTERS
# ─────────────────────────────────────────────
def H(): return "─" * 28

def msg_chart(d):
    t = d["ticker"]; sym = t.replace(".JK","") if is_idx(t) else t
    exc = "🇮🇩 IDX" if is_idx(t) else "🇺🇸 US"
    now = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    chg_s = f"+{d['chg']:.2f}%" if d['chg'] >= 0 else f"{d['chg']:.2f}%"
    sw = " ⚠️OB" if d['skv'] > 80 else (" 💎OS" if d['skv'] < 20 else "")
    sig_line = f"\n🔔 <b>{d['sig_e']} {d['sig_l']}</b>" if d['sig'] else ""
    chart = ascii_chart(d['closes'])
    return (
        f"📊 <b>CHART — {sym}</b>  [{exc}]\n🕐 {now}\n{H()}\n"
        f"<pre>{chart}</pre>\n{H()}\n"
        f"<b>💰 HARGA</b>\n"
        f"  Close    : <b>{fmt_price(d['price'],t)}</b>  ({chg_s})\n"
        f"  Avg/VWAP : {fmt_price(d['avg_p'],t)}\n{H()}\n"
        f"<b>📈 INDIKATOR</b>\n"
        f"  EMA 8/21 : {fmt_price(d['ef'],t)} / {fmt_price(d['es'],t)}\n"
        f"  MACD     : {d['mv']:.4f}  Sig:{d['sv']:.4f}  H:{d['hv']:+.4f}\n"
        f"  RSI(14)  : {d['rsi']:.1f}\n"
        f"  Stoch K/D: {d['skv']:.1f}/{d['sdv']:.1f}{sw}\n"
        f"  BB       : {fmt_price(d['bu'],t)} / {fmt_price(d['bm'],t)} / {fmt_price(d['bl'],t)}\n{H()}\n"
        f"<b>🗺 ZONA</b>: <b>{d['zona']}</b>\n"
        f"<b>📈 TREND</b>: {d['trend_e']} {d['trend']}\n"
        f"<b>📦 VOL</b>  : {d['vol_ratio']:.1f}x {'🔥' if d['vol_spike'] else ''}\n"
        f"{sig_line}\n{H()}\n<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )

def msg_signal(d):
    t = d["ticker"]; sym = t.replace(".JK","") if is_idx(t) else t
    exc = "🇮🇩 IDX" if is_idx(t) else "🇺🇸 US"
    now = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    chg_s = f"+{d['chg']:.2f}%" if d['chg'] >= 0 else f"{d['chg']:.2f}%"
    score = 0; factors = []
    if d['vol_spike']:              score+=2; factors.append("Volume Spike 🔥")
    if d['hv']>0 and d['mv']>d['sv']: score+=2; factors.append("MACD Bullish ✅")
    if d['ef']>d['es']:             score+=1; factors.append("EMA Golden Cross ✅")
    if 40<d['rsi']<70:              score+=1; factors.append("RSI Netral 👍")
    if d['skv']<80:                 score+=1; factors.append("Stoch OK ✅")
    if d['price']>d['avg_p']:       score+=1; factors.append("Above VWAP 💪")
    strength = ("🔥🔥🔥 SANGAT KUAT" if score>=7 else "💪💪 KUAT" if score>=5
                else "⚡ CUKUP" if score>=3 else "⚠️ LEMAH")
    sig_txt = f"<b>{d['sig_e']} {d['sig_l']}</b>" if d['sig'] else "📊 Belum ada sinyal kuat"
    return (
        f"🎯 <b>SIGNAL ANALYSIS — {sym}</b>  [{exc}]\n🕐 {now}\n{H()}\n"
        f"Signal   : {sig_txt}\n"
        f"Kekuatan : <b>{strength}</b>  (skor {score}/8)\n{H()}\n"
        f"<b>✅ FAKTOR PENDUKUNG:</b>\n" +
        "\n".join(f"  • {f}" for f in factors) +
        f"\n{H()}\n"
        f"💰 Harga : <b>{fmt_price(d['price'],t)}</b>  ({chg_s})\n"
        f"📊 Trend : {d['trend_e']} {d['trend']}\n"
        f"🗺 Zona  : {d['zona']} — {d['zona_d']}\n"
        f"📦 Vol   : {d['vol_ratio']:.1f}x avg {'🔥' if d['vol_spike'] else ''}\n"
        f"RSI      : {d['rsi']:.1f}  |  Stoch: {d['skv']:.1f}/{d['sdv']:.1f}\n"
        f"{H()}\n<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )

def msg_zona(d):
    t = d["ticker"]; sym = t.replace(".JK","") if is_idx(t) else t
    now = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    checks = [
        f"{'🟢' if d['price']>d['ef'] else '🔴'} {'Di atas' if d['price']>d['ef'] else 'Di bawah'} EMA 8",
        f"{'🟢' if d['price']>d['es'] else '🔴'} {'Di atas' if d['price']>d['es'] else 'Di bawah'} EMA 21",
        f"{'🟢' if d['price']>d['bm'] else '🔴'} {'Di atas' if d['price']>d['bm'] else 'Di bawah'} BB Mid",
        f"{'🟢' if d['price']>d['avg_p'] else '🔴'} {'Di atas' if d['price']>d['avg_p'] else 'Di bawah'} VWAP",
    ]
    return (
        f"🗺 <b>ZONA ANALYSIS — {sym}</b>\n🕐 {now}\n{H()}\n"
        f"Zona  : <b>{d['zona']}</b>\n"
        f"Ket   : {d['zona_d']}\n"
        f"Trend : <b>{d['trend_e']} {d['trend']}</b>\n{H()}\n"
        f"Harga    : {fmt_price(d['price'],t)}\n"
        f"BB Top   : {fmt_price(d['bu'],t)}\n"
        f"BB Mid   : {fmt_price(d['bm'],t)}\n"
        f"BB Bot   : {fmt_price(d['bl'],t)}\n"
        f"EMA 8    : {fmt_price(d['ef'],t)}\n"
        f"EMA 21   : {fmt_price(d['es'],t)}\n"
        f"Avg/VWAP : {fmt_price(d['avg_p'],t)}\n{H()}\n"
        f"<b>Posisi Harga:</b>\n" + "\n".join(checks) +
        f"\n{H()}\n<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )

def msg_trend(d):
    t = d["ticker"]; sym = t.replace(".JK","") if is_idx(t) else t
    now = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    macd_s = "BULLISH ✅" if d['hv']>0 and d['mv']>d['sv'] else "BEARISH 🔴" if d['hv']<0 else "NETRAL ⚠️"
    ema_s  = "GOLDEN CROSS 🟢" if d['ef']>d['es'] else "DEATH CROSS 🔴"
    rsi_s  = "OVERBOUGHT ⚠️" if d['rsi']>70 else "OVERSOLD 💎" if d['rsi']<30 else "NETRAL ✅"
    return (
        f"📈 <b>TREND ANALYSIS — {sym}</b>\n🕐 {now}\n{H()}\n"
        f"Overall  : <b>{d['trend_e']} {d['trend']}</b>\n{H()}\n"
        f"EMA Cross: {ema_s}\n"
        f"  EMA 8  : {fmt_price(d['ef'],t)}\n"
        f"  EMA 21 : {fmt_price(d['es'],t)}\n{H()}\n"
        f"MACD     : {macd_s}\n"
        f"  Line   : {d['mv']:.4f}\n"
        f"  Signal : {d['sv']:.4f}\n"
        f"  Hist   : {d['hv']:+.4f}\n{H()}\n"
        f"RSI(14)  : {d['rsi']:.1f} — {rsi_s}\n"
        f"Stoch K/D: {d['skv']:.1f}/{d['sdv']:.1f}\n"
        f"{H()}\n<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )

def msg_volume(d):
    t = d["ticker"]; sym = t.replace(".JK","") if is_idx(t) else t
    now = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    vs = ("🔥🔥 SPIKE BESAR" if d['vol_ratio']>=3 else "🔥 SPIKE" if d['vol_ratio']>=2
          else "📊 NORMAL" if d['vol_ratio']>=0.7 else "📉 SEPI")
    interp = ("🟢 Volume besar + naik = AKUMULASI" if d['vol_spike'] and d['chg']>0 else
              "🔴 Volume besar + turun = DISTRIBUSI" if d['vol_spike'] and d['chg']<0 else
              "⚠️ Volume normal, tunggu konfirmasi")
    return (
        f"📦 <b>VOLUME ANALYSIS — {sym}</b>\n🕐 {now}\n{H()}\n"
        f"Status   : <b>{vs}</b>\n"
        f"Sekarang : <b>{d['vol_n']:,.0f}</b>\n"
        f"Rata-rata: {d['vol_a']:,.0f}\n"
        f"Rasio    : <b>{d['vol_ratio']:.2f}x</b>\n{H()}\n"
        f"Harga    : {fmt_price(d['price'],t)}\n"
        f"Arah     : {'📈 NAIK' if d['chg']>0 else '📉 TURUN'} ({d['chg']:+.2f}%)\n{H()}\n"
        f"<b>Interpretasi:</b>\n{interp}\n"
        f"{H()}\n<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )

def msg_screener(results):
    now = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    lines = [f"🔍 <b>SCREENER — T1MO × WISDOM</b>\n🕐 {now}\n{H()}"]
    has_signal = False
    for d in results:
        if d is None: continue
        t = d["ticker"]; sym = t.replace(".JK","") if is_idx(t) else t
        exc = "🇮🇩" if is_idx(t) else "🇺🇸"
        chg_s = f"+{d['chg']:.2f}%" if d['chg']>=0 else f"{d['chg']:.2f}%"
        sig_s = f" | {d['sig_e']} {d['sig_l']}" if d['sig'] else ""
        lines.append(f"{exc} <b>{sym}</b> {fmt_price(d['price'],t)} ({chg_s}) {d['vol_ratio']:.1f}x{'🔥' if d['vol_spike'] else ''}{sig_s}")
        lines.append(f"   {d['zona']} | {d['trend']}")
        if d['sig']: has_signal = True
    if not has_signal:
        lines.append("\n📊 Belum ada sinyal kuat saat ini")
    lines.append(f"{H()}\n<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>")
    return "\n".join(lines)

def msg_summary(results):
    now = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    valid = [d for d in results if d]
    bull = [d['ticker'].replace(".JK","") if is_idx(d['ticker']) else d['ticker'] for d in valid if "UP" in d['trend']]
    bear = [d['ticker'].replace(".JK","") if is_idx(d['ticker']) else d['ticker'] for d in valid if "DOWN" in d['trend']]
    side = [d['ticker'].replace(".JK","") if is_idx(d['ticker']) else d['ticker'] for d in valid if "SIDEWAYS" in d['trend']]
    top_vol = sorted(valid, key=lambda x: x['vol_ratio'], reverse=True)[:3]
    lines = [f"📋 <b>SUMMARY HARIAN</b>\n🕐 {now}\n{H()}",
             f"🟢 BULLISH ({len(bull)}): {', '.join(bull) or '-'}",
             f"🔴 BEARISH ({len(bear)}): {', '.join(bear) or '-'}",
             f"🟡 SIDEWAYS ({len(side)}): {', '.join(side) or '-'}",
             f"{H()}\n🔥 TOP VOLUME:"]
    for d in top_vol:
        sym = d['ticker'].replace(".JK","") if is_idx(d['ticker']) else d['ticker']
        lines.append(f"  {sym}: {d['vol_ratio']:.1f}x avg")
    lines.append(f"{H()}\n<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>")
    return "\n".join(lines)

def msg_help():
    return (
        "🤖 <b>T1MO × WISDOM SIGNAL BOT</b>\n"
        "<i>Command yang tersedia:</i>\n"
        "─────────────────────────────\n"
        "📊 <b>/chart BBCA</b> — Chart + tabel indikator\n"
        "🎯 <b>/signal BBCA</b> — Analisis sinyal lengkap\n"
        "🔍 <b>/screener</b> — Semua ticker + signal\n"
        "🗺 <b>/zona BBCA</b> — Zona &amp; posisi harga\n"
        "📈 <b>/trend BBCA</b> — Analisis trend\n"
        "📦 <b>/volume BBCA</b> — Analisis volume\n"
        "📋 <b>/summary</b> — Summary harian\n"
        "📋 <b>/watchlist</b> — Lihat daftar saham\n"
        "➕ <b>/add TLKM</b> — Tambah ticker IDX\n"
        "➕ <b>/addus TSLA</b> — Tambah ticker US\n"
        "➖ <b>/remove TLKM</b> — Hapus ticker\n"
        "❓ <b>/help</b> — Menu ini\n"
        "─────────────────────────────\n"
        "<i>Contoh: /chart PLTR atau /signal BBCA</i>\n"
        "<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )

def msg_watchlist():
    idx = [t.replace(".JK","") for t in dynamic_watchlist["IDX"]]
    us  = dynamic_watchlist["US"]
    return (
        f"📋 <b>WATCHLIST AKTIF</b>\n{H()}\n"
        f"🇮🇩 <b>IDX ({len(idx)})</b>:\n  {', '.join(idx)}\n\n"
        f"🇺🇸 <b>US ({len(us)})</b>:\n  {', '.join(us)}\n{H()}\n"
        f"➕ /add TICKER — tambah IDX\n"
        f"➕ /addus TICKER — tambah US\n"
        f"➖ /remove TICKER — hapus\n"
        f"<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )

def msg_alert(d):
    t = d["ticker"]; sym = t.replace(".JK","") if is_idx(t) else t
    exc = "🇮🇩 IDX" if is_idx(t) else "🇺🇸 US"
    now = datetime.now(WIB).strftime("%d %b %Y  %H:%M WIB")
    chg_s = f"+{d['chg']:.2f}%" if d['chg']>=0 else f"{d['chg']:.2f}%"
    sw = " ⚠️OB" if d['skv']>80 else (" 💎OS" if d['skv']<20 else "")
    return (
        f"{d['sig_e']} <b>{d['sig_l']}</b>\n{'═'*28}\n"
        f"<b>📌 {sym}</b>  [{exc}]\n🕐 {now}\n{H()}\n"
        f"💰 Close    : <b>{fmt_price(d['price'],t)}</b>  ({chg_s})\n"
        f"   AvgPrice : {fmt_price(d['avg_p'],t)}\n{H()}\n"
        f"📊 Trend : <b>{d['trend_e']} {d['trend']}</b>\n"
        f"🗺 Zona  : <b>{d['zona']}</b> — {d['zona_d']}\n{H()}\n"
        f"📈 EMA 8/21: {fmt_price(d['ef'],t)} / {fmt_price(d['es'],t)}\n"
        f"   MACD    : {d['mv']:.4f}  Sig:{d['sv']:.4f}\n"
        f"   Hist    : {d['hv']:+.4f}\n"
        f"   RSI(14) : {d['rsi']:.1f}\n"
        f"   Stoch   : {d['skv']:.1f}/{d['sdv']:.1f}{sw}\n{H()}\n"
        f"📦 Vol Now : {d['vol_n']:,.0f}\n"
        f"   Vol Avg : {d['vol_a']:,.0f}\n"
        f"   Rasio   : <b>{d['vol_ratio']:.1f}x</b> 🔥\n"
        f"{'═'*28}\n<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )

# ─────────────────────────────────────────────
#  COMMAND HANDLER
# ─────────────────────────────────────────────
def handle_command(text, chat_id):
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].upper() if len(parts) > 1 else ""

    if cmd == "/help":
        send_telegram(msg_help(), chat_id)

    elif cmd == "/watchlist":
        send_telegram(msg_watchlist(), chat_id)

    elif cmd in ["/chart", "/signal", "/zona", "/trend", "/volume"]:
        if not arg:
            send_telegram(f"⚠️ Format: {cmd} TICKER\nContoh: {cmd} BBCA", chat_id)
            return
        send_telegram(f"⏳ Fetching {arg}...", chat_id)
        ticker = normalize_ticker(arg)
        d = analyze(ticker)
        if d is None:
            send_telegram(f"❌ Data {arg} tidak tersedia.", chat_id)
            return
        if cmd == "/chart":    send_telegram(msg_chart(d), chat_id)
        elif cmd == "/signal": send_telegram(msg_signal(d), chat_id)
        elif cmd == "/zona":   send_telegram(msg_zona(d), chat_id)
        elif cmd == "/trend":  send_telegram(msg_trend(d), chat_id)
        elif cmd == "/volume": send_telegram(msg_volume(d), chat_id)

    elif cmd == "/screener":
        send_telegram("⏳ Scanning semua ticker...", chat_id)
        results = []
        for t in all_tickers():
            try: results.append(analyze(t))
            except: results.append(None)
            time.sleep(1)
        send_telegram(msg_screener(results), chat_id)

    elif cmd == "/summary":
        send_telegram("⏳ Generating summary...", chat_id)
        results = []
        for t in all_tickers():
            try: results.append(analyze(t))
            except: results.append(None)
            time.sleep(1)
        send_telegram(msg_summary(results), chat_id)

    elif cmd == "/add":
        if not arg:
            send_telegram("⚠️ Format: /add TICKER\nContoh: /add TLKM", chat_id)
            return
        ticker = arg + ".JK"
        if ticker not in dynamic_watchlist["IDX"]:
            dynamic_watchlist["IDX"].append(ticker)
            send_telegram(f"✅ {arg} ditambahkan ke IDX!\n\n" + msg_watchlist(), chat_id)
        else:
            send_telegram(f"⚠️ {arg} sudah ada di watchlist.", chat_id)

    elif cmd == "/addus":
        if not arg:
            send_telegram("⚠️ Format: /addus TICKER\nContoh: /addus TSLA", chat_id)
            return
        if arg not in dynamic_watchlist["US"]:
            dynamic_watchlist["US"].append(arg)
            send_telegram(f"✅ {arg} ditambahkan ke US!\n\n" + msg_watchlist(), chat_id)
        else:
            send_telegram(f"⚠️ {arg} sudah ada di watchlist.", chat_id)

    elif cmd == "/remove":
        if not arg:
            send_telegram("⚠️ Format: /remove TICKER\nContoh: /remove TLKM", chat_id)
            return
        removed = False
        ticker_jk = arg + ".JK"
        if ticker_jk in dynamic_watchlist["IDX"]:
            dynamic_watchlist["IDX"].remove(ticker_jk); removed = True
        elif arg in dynamic_watchlist["US"]:
            dynamic_watchlist["US"].remove(arg); removed = True
        if removed:
            send_telegram(f"✅ {arg} dihapus!\n\n" + msg_watchlist(), chat_id)
        else:
            send_telegram(f"⚠️ {arg} tidak ditemukan di watchlist.", chat_id)

    else:
        send_telegram("❓ Command tidak dikenal. Ketik /help", chat_id)

# ─────────────────────────────────────────────
#  POLLING THREAD
# ─────────────────────────────────────────────
def polling_loop():
    global last_update_id
    log.info("Polling thread started...")
    while True:
        try:
            updates = get_updates()
            for upd in updates:
                last_update_id = upd["update_id"]
                msg = upd.get("message") or upd.get("edited_message")
                if not msg: continue
                text = msg.get("text", "")
                chat_id = str(msg["chat"]["id"])
                if text.startswith("/"):
                    log.info(f"CMD: {text} from {chat_id}")
                    threading.Thread(target=handle_command, args=(text, chat_id), daemon=True).start()
        except Exception as e:
            log.error(f"Polling: {e}")
        time.sleep(2)

# ─────────────────────────────────────────────
#  AUTO SCAN
# ─────────────────────────────────────────────
def is_market_open():
    now = datetime.now(WIB); wd = now.weekday()
    if wd >= 5: return False
    t = now.hour * 60 + now.minute
    return (9*60 <= t <= 16*60+15) or (t >= 21*60+30) or (t <= 4*60)

def send_startup():
    idx_s = ", ".join(t.replace(".JK","") for t in dynamic_watchlist["IDX"])
    us_s  = ", ".join(dynamic_watchlist["US"])
    now   = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    send_telegram(
        f"🤖 <b>ROBOT TRADING AKTIF v2</b>\n{'═'*28}\n"
        f"🕐 {now}\n⚡ T1MO Quantitative × Wisdom Signal Bot\n{H()}\n"
        f"📋 <b>WATCHLIST</b>\n🇮🇩 IDX: {idx_s}\n🇺🇸 US : {us_s}\n{H()}\n"
        f"🔄 Scan: tiap {SCAN_INTERVAL}s | 📊 Data: 2m\n"
        f"🔔 Akumulasi 🟢 | Distribusi 🔴 | Breakout ⚡ | Breakdown 📉\n{H()}\n"
        f"💬 Ketik /help untuk semua command\n{'═'*28}"
    )

def main():
    log.info("=" * 50)
    log.info("  IDX SCREENER v2 — T1MO x WISDOM QUANTITATIVE BOT")
    log.info("=" * 50)
    send_startup()
    threading.Thread(target=polling_loop, daemon=True).start()

    scan_count = 0
    while True:
        try:
            scan_count += 1
            log.info(f"[SCAN #{scan_count}] {datetime.now(WIB).strftime('%H:%M:%S WIB')}")
            if not is_market_open():
                log.info("Market tutup. Standby 5 menit...")
                time.sleep(300)
                continue
            signals_found = 0
            for ticker in all_tickers():
                try:
                    if time.time() - signal_cooldown.get(ticker, 0) < COOLDOWN_SEC:
                        continue
                    d = analyze(ticker)
                    if d and d["sig"]:
                        signals_found += 1
                        log.info(f"[{ticker}] {d['sig_l']} @ {d['price']:.2f}")
                        send_telegram(msg_alert(d))
                        signal_cooldown[ticker] = time.time()
                    time.sleep(1.5)
                except Exception as e:
                    log.error(f"[{ticker}] {e}")
            log.info(f"[SCAN #{scan_count}] Sinyal: {signals_found}")
            if scan_count % 30 == 0:
                now_s = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
                send_telegram(f"📡 <b>HEARTBEAT</b>\n🕐 {now_s}\n🔄 Scan ke-{scan_count}\n<i>⚡ T1MO × Wisdom Bot</i>")
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"Main: {e}")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
