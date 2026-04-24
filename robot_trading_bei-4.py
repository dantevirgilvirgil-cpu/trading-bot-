"""
ROBOT TRADING IDX v3 — T1MO x WISDOM QUANTITATIVE BOT
Pakai python-telegram-bot v20 (ApplicationBuilder)
Deploy: Railway | Data: yfinance 2m
"""

import os, time, logging, asyncio
from datetime import datetime
import pytz, yfinance as yf
import pandas as pd
import numpy as np
import requests

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

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

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def all_tickers():
    return dynamic_watchlist["IDX"] + dynamic_watchlist["US"]

def is_idx(ticker): return ticker.endswith(".JK")

def fmt_price(p, t):
    return f"Rp {p:,.0f}" if is_idx(t) else f"${p:.2f}"

def normalize_ticker(raw):
    t = raw.upper().strip()
    if t.endswith(".JK"): return t
    if any(t == x.replace(".JK","") for x in dynamic_watchlist["IDX"]):
        return t + ".JK"
    return t

def H(): return "─" * 28

# ─────────────────────────────────────────────
#  SEND ALERT (sync, untuk auto scan)
# ─────────────────────────────────────────────
def send_alert_sync(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        )
    except Exception as e:
        log.error(f"Alert error: {e}")

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
    d = s.diff(); g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))
def calc_macd(s):
    ml = calc_ema(s, MACD_FAST) - calc_ema(s, MACD_SLOW)
    sl = calc_ema(ml, MACD_SIG); return ml, sl, ml - sl
def calc_stoch(h, l, c):
    lm = l.rolling(STOCH_K).min(); hm = h.rolling(STOCH_K).max()
    k = 100*(c-lm)/(hm-lm).replace(0,np.nan); return k, k.rolling(STOCH_D).mean()
def calc_bb(s):
    m = s.rolling(BB_PERIOD).mean(); std = s.rolling(BB_PERIOD).std()
    return m+BB_STD*std, m, m-BB_STD*std

def get_zona(p, bl, bm, bu, ef, es):
    if p>bu and ef>es: return "ZONA 5 🔥","STRONG BULL"
    if p>bm and ef>es: return "ZONA 4 ✅","TREND UP"
    if p>bm: return "ZONA 3 ⚡","SIDEWAYS BULLISH"
    if p>bl: return "ZONA 2 ⚠️","SIDEWAYS LEMAH"
    return "ZONA 1 🔴","DOWNTREND"

def get_trend(ef, es, mv, sv):
    if ef>es and mv>sv: return "TREND UP ↑","🟢"
    if ef<es and mv<sv: return "TREND DOWN ↓","🔴"
    return "SIDEWAYS ↔","🟡"

def analyze(ticker):
    df = fetch_data(ticker)
    if df is None: return None
    c,h,l,v = df["Close"],df["High"],df["Low"],df["Volume"]
    ef_s=calc_ema(c,EMA_FAST); es_s=calc_ema(c,EMA_SLOW)
    ml,sl,mh=calc_macd(c); rsi_s=calc_rsi(c)
    sk,sd=calc_stoch(h,l,c); bu,bm,bl=calc_bb(c)
    va=v.rolling(VOL_AVG_PERIOD).mean()
    vwap=(((h+l+c)/3)*v).rolling(20).sum()/v.rolling(20).sum()

    i=-1
    price=float(c.iloc[i]); prev=float(c.iloc[i-1])
    vol_n=float(v.iloc[i]); vol_a=float(va.iloc[i])
    ef=float(ef_s.iloc[i]); es_=float(es_s.iloc[i])
    mv=float(ml.iloc[i]); sv_=float(sl.iloc[i]); hv=float(mh.iloc[i])
    rsi=float(rsi_s.iloc[i]); skv=float(sk.iloc[i]); sdv=float(sd.iloc[i])
    bu_=float(bu.iloc[i]); bm_=float(bm.iloc[i]); bl_=float(bl.iloc[i])
    avg_p=float(vwap.iloc[i])
    chg=(price-prev)/prev*100 if prev>0 else 0
    vol_ratio=vol_n/vol_a if vol_a>0 else 0
    vol_spike=vol_ratio>=VOLUME_SPIKE_MULT
    zona,zona_d=get_zona(price,bl_,bm_,bu_,ef,es_)
    trend,trend_e=get_trend(ef,es_,mv,sv_)
    closes=[float(x) for x in c.iloc[-20:].values]

    sig=sig_e=sig_l=None
    if vol_spike and chg>0 and hv>0 and ef>es_: sig,sig_e,sig_l="AKUMULASI","🟢▲","AKUMULASI BESAR"
    elif vol_spike and chg<0 and hv<0: sig,sig_e,sig_l="DISTRIBUSI","🔴▼","DISTRIBUSI / JUAL"
    elif price>bu_ and vol_spike and hv>0: sig,sig_e,sig_l="BREAKOUT","⚡🚀","BREAKOUT BULLISH"
    elif price<bl_ and vol_spike and hv<0: sig,sig_e,sig_l="BREAKDOWN","📉💀","BREAKDOWN"

    return dict(ticker=ticker,price=price,prev=prev,chg=chg,
                vol_n=vol_n,vol_a=vol_a,vol_ratio=vol_ratio,vol_spike=vol_spike,
                ef=ef,es=es_,mv=mv,sv=sv_,hv=hv,rsi=rsi,
                skv=skv,sdv=sdv,bu=bu_,bm=bm_,bl=bl_,avg_p=avg_p,
                zona=zona,zona_d=zona_d,trend=trend,trend_e=trend_e,
                sig=sig,sig_e=sig_e,sig_l=sig_l,closes=closes)

# ─────────────────────────────────────────────
#  ASCII CHART
# ─────────────────────────────────────────────
def ascii_chart(closes, height=7):
    if not closes or len(closes)<2: return "[no data]"
    mn,mx=min(closes),max(closes); rng=mx-mn if mx!=mn else 1
    rows=[]
    for row in range(height,0,-1):
        threshold=mn+(row/height)*rng; line=""
        for i,val in enumerate(closes):
            if abs(val-threshold)<rng/height/1.5: line+="●"
            elif val>=threshold: line+="─"
            else: line+=" "
        lbl=(f"{mx:,.0f}" if row==height else
             f"{mn+(mx-mn)*0.5:,.0f}" if row==height//2 else
             f"{mn:,.0f}" if row==1 else "")
        rows.append(f"{line} {lbl}")
    rows.append("─"*20+"→")
    return "\n".join(rows)

# ─────────────────────────────────────────────
#  MESSAGE FORMATTERS
# ─────────────────────────────────────────────
def msg_chart(d):
    t=d["ticker"]; sym=t.replace(".JK","") if is_idx(t) else t
    exc="🇮🇩 IDX" if is_idx(t) else "🇺🇸 US"
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    chg_s=f"+{d['chg']:.2f}%" if d['chg']>=0 else f"{d['chg']:.2f}%"
    sw=" ⚠️OB" if d['skv']>80 else (" 💎OS" if d['skv']<20 else "")
    sig_line=f"\n🔔 <b>{d['sig_e']} {d['sig_l']}</b>" if d['sig'] else ""
    chart=ascii_chart(d['closes'])
    return (f"📊 <b>CHART — {sym}</b> [{exc}]\n🕐 {now}\n{H()}\n"
            f"<pre>{chart}</pre>\n{H()}\n"
            f"💰 Close: <b>{fmt_price(d['price'],t)}</b> ({chg_s})\n"
            f"   VWAP : {fmt_price(d['avg_p'],t)}\n{H()}\n"
            f"EMA 8/21: {fmt_price(d['ef'],t)} / {fmt_price(d['es'],t)}\n"
            f"MACD    : {d['mv']:.4f} | Hist: {d['hv']:+.4f}\n"
            f"RSI(14) : {d['rsi']:.1f}\n"
            f"Stoch   : {d['skv']:.1f}/{d['sdv']:.1f}{sw}\n"
            f"BB      : {fmt_price(d['bu'],t)}/{fmt_price(d['bm'],t)}/{fmt_price(d['bl'],t)}\n{H()}\n"
            f"🗺 {d['zona']} | {d['trend_e']} {d['trend']}\n"
            f"📦 Vol: {d['vol_ratio']:.1f}x {'🔥' if d['vol_spike'] else ''}"
            f"{sig_line}\n{H()}\n<i>⚡ T1MO × Wisdom Bot</i>")

def msg_signal(d):
    t=d["ticker"]; sym=t.replace(".JK","") if is_idx(t) else t
    exc="🇮🇩 IDX" if is_idx(t) else "🇺🇸 US"
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    chg_s=f"+{d['chg']:.2f}%" if d['chg']>=0 else f"{d['chg']:.2f}%"
    score=0; factors=[]
    if d['vol_spike']: score+=2; factors.append("Volume Spike 🔥")
    if d['hv']>0 and d['mv']>d['sv']: score+=2; factors.append("MACD Bullish ✅")
    if d['ef']>d['es']: score+=1; factors.append("EMA Golden Cross ✅")
    if 40<d['rsi']<70: score+=1; factors.append("RSI Netral 👍")
    if d['skv']<80: score+=1; factors.append("Stoch OK ✅")
    if d['price']>d['avg_p']: score+=1; factors.append("Above VWAP 💪")
    strength=("🔥🔥🔥 SANGAT KUAT" if score>=7 else "💪💪 KUAT" if score>=5
              else "⚡ CUKUP" if score>=3 else "⚠️ LEMAH")
    sig_txt=f"<b>{d['sig_e']} {d['sig_l']}</b>" if d['sig'] else "📊 Belum ada sinyal kuat"
    return (f"🎯 <b>SIGNAL — {sym}</b> [{exc}]\n🕐 {now}\n{H()}\n"
            f"Signal  : {sig_txt}\n"
            f"Kekuatan: <b>{strength}</b> (skor {score}/8)\n{H()}\n"
            f"<b>Faktor:</b>\n" + "\n".join(f"  • {f}" for f in factors) +
            f"\n{H()}\n"
            f"💰 {fmt_price(d['price'],t)} ({chg_s})\n"
            f"📊 {d['trend_e']} {d['trend']}\n"
            f"🗺 {d['zona']} — {d['zona_d']}\n"
            f"📦 Vol: {d['vol_ratio']:.1f}x | RSI: {d['rsi']:.1f}\n"
            f"{H()}\n<i>⚡ T1MO × Wisdom Bot</i>")

def msg_zona(d):
    t=d["ticker"]; sym=t.replace(".JK","") if is_idx(t) else t
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    checks=[
        f"{'🟢' if d['price']>d['ef'] else '🔴'} {'Di atas' if d['price']>d['ef'] else 'Di bawah'} EMA 8",
        f"{'🟢' if d['price']>d['es'] else '🔴'} {'Di atas' if d['price']>d['es'] else 'Di bawah'} EMA 21",
        f"{'🟢' if d['price']>d['bm'] else '🔴'} {'Di atas' if d['price']>d['bm'] else 'Di bawah'} BB Mid",
        f"{'🟢' if d['price']>d['avg_p'] else '🔴'} {'Di atas' if d['price']>d['avg_p'] else 'Di bawah'} VWAP",
    ]
    return (f"🗺 <b>ZONA — {sym}</b>\n🕐 {now}\n{H()}\n"
            f"Zona : <b>{d['zona']}</b>\n"
            f"Ket  : {d['zona_d']}\n"
            f"Trend: <b>{d['trend_e']} {d['trend']}</b>\n{H()}\n"
            f"Harga  : {fmt_price(d['price'],t)}\n"
            f"BB Top : {fmt_price(d['bu'],t)}\n"
            f"BB Mid : {fmt_price(d['bm'],t)}\n"
            f"BB Bot : {fmt_price(d['bl'],t)}\n"
            f"EMA 8  : {fmt_price(d['ef'],t)}\n"
            f"EMA 21 : {fmt_price(d['es'],t)}\n"
            f"VWAP   : {fmt_price(d['avg_p'],t)}\n{H()}\n"
            + "\n".join(checks) +
            f"\n{H()}\n<i>⚡ T1MO × Wisdom Bot</i>")

def msg_trend(d):
    t=d["ticker"]; sym=t.replace(".JK","") if is_idx(t) else t
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    macd_s="BULLISH ✅" if d['hv']>0 and d['mv']>d['sv'] else "BEARISH 🔴" if d['hv']<0 else "NETRAL ⚠️"
    ema_s="GOLDEN CROSS 🟢" if d['ef']>d['es'] else "DEATH CROSS 🔴"
    rsi_s="OVERBOUGHT ⚠️" if d['rsi']>70 else "OVERSOLD 💎" if d['rsi']<30 else "NETRAL ✅"
    return (f"📈 <b>TREND — {sym}</b>\n🕐 {now}\n{H()}\n"
            f"Overall : <b>{d['trend_e']} {d['trend']}</b>\n{H()}\n"
            f"EMA     : {ema_s}\n"
            f"  EMA 8 : {fmt_price(d['ef'],t)}\n"
            f"  EMA 21: {fmt_price(d['es'],t)}\n{H()}\n"
            f"MACD    : {macd_s}\n"
            f"  Line  : {d['mv']:.4f}\n"
            f"  Signal: {d['sv']:.4f}\n"
            f"  Hist  : {d['hv']:+.4f}\n{H()}\n"
            f"RSI(14) : {d['rsi']:.1f} — {rsi_s}\n"
            f"Stoch   : {d['skv']:.1f}/{d['sdv']:.1f}\n"
            f"{H()}\n<i>⚡ T1MO × Wisdom Bot</i>")

def msg_volume(d):
    t=d["ticker"]; sym=t.replace(".JK","") if is_idx(t) else t
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    vs=("🔥🔥 SPIKE BESAR" if d['vol_ratio']>=3 else "🔥 SPIKE" if d['vol_ratio']>=2
        else "📊 NORMAL" if d['vol_ratio']>=0.7 else "📉 SEPI")
    interp=("🟢 Volume besar + naik = AKUMULASI" if d['vol_spike'] and d['chg']>0 else
            "🔴 Volume besar + turun = DISTRIBUSI" if d['vol_spike'] and d['chg']<0 else
            "⚠️ Volume normal")
    return (f"📦 <b>VOLUME — {sym}</b>\n🕐 {now}\n{H()}\n"
            f"Status  : <b>{vs}</b>\n"
            f"Sekarang: <b>{d['vol_n']:,.0f}</b>\n"
            f"Rata2   : {d['vol_a']:,.0f}\n"
            f"Rasio   : <b>{d['vol_ratio']:.2f}x</b>\n{H()}\n"
            f"Harga : {fmt_price(d['price'],t)}\n"
            f"Arah  : {'📈 NAIK' if d['chg']>0 else '📉 TURUN'} ({d['chg']:+.2f}%)\n{H()}\n"
            f"{interp}\n{H()}\n<i>⚡ T1MO × Wisdom Bot</i>")

def msg_screener(results):
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    lines=[f"🔍 <b>SCREENER</b>\n🕐 {now}\n{H()}"]
    has_sig=False
    for d in results:
        if d is None: continue
        t=d["ticker"]; sym=t.replace(".JK","") if is_idx(t) else t
        exc="🇮🇩" if is_idx(t) else "🇺🇸"
        chg_s=f"+{d['chg']:.2f}%" if d['chg']>=0 else f"{d['chg']:.2f}%"
        sig_s=f" | {d['sig_e']} {d['sig_l']}" if d['sig'] else ""
        lines.append(f"{exc} <b>{sym}</b> {fmt_price(d['price'],t)} ({chg_s}) {d['vol_ratio']:.1f}x{'🔥' if d['vol_spike'] else ''}{sig_s}")
        lines.append(f"   {d['zona']} | {d['trend']}")
        if d['sig']: has_sig=True
    if not has_sig: lines.append("\n📊 Belum ada sinyal kuat")
    lines.append(f"{H()}\n<i>⚡ T1MO × Wisdom Bot</i>")
    return "\n".join(lines)

def msg_summary(results):
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    valid=[d for d in results if d]
    bull=[d['ticker'].replace(".JK","") if is_idx(d['ticker']) else d['ticker'] for d in valid if "UP" in d['trend']]
    bear=[d['ticker'].replace(".JK","") if is_idx(d['ticker']) else d['ticker'] for d in valid if "DOWN" in d['trend']]
    side=[d['ticker'].replace(".JK","") if is_idx(d['ticker']) else d['ticker'] for d in valid if "SIDEWAYS" in d['trend']]
    top_vol=sorted(valid,key=lambda x:x['vol_ratio'],reverse=True)[:3]
    lines=[f"📋 <b>SUMMARY HARIAN</b>\n🕐 {now}\n{H()}",
           f"🟢 BULLISH ({len(bull)}): {', '.join(bull) or '-'}",
           f"🔴 BEARISH ({len(bear)}): {', '.join(bear) or '-'}",
           f"🟡 SIDEWAYS ({len(side)}): {', '.join(side) or '-'}",
           f"{H()}\n🔥 TOP VOLUME:"]
    for d in top_vol:
        sym=d['ticker'].replace(".JK","") if is_idx(d['ticker']) else d['ticker']
        lines.append(f"  {sym}: {d['vol_ratio']:.1f}x")
    lines.append(f"{H()}\n<i>⚡ T1MO × Wisdom Bot</i>")
    return "\n".join(lines)

def msg_alert(d):
    t=d["ticker"]; sym=t.replace(".JK","") if is_idx(t) else t
    exc="🇮🇩 IDX" if is_idx(t) else "🇺🇸 US"
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    chg_s=f"+{d['chg']:.2f}%" if d['chg']>=0 else f"{d['chg']:.2f}%"
    sw=" ⚠️OB" if d['skv']>80 else (" 💎OS" if d['skv']<20 else "")
    return (f"{d['sig_e']} <b>{d['sig_l']}</b>\n{'═'*28}\n"
            f"<b>📌 {sym}</b> [{exc}]\n🕐 {now}\n{H()}\n"
            f"💰 Close  : <b>{fmt_price(d['price'],t)}</b> ({chg_s})\n"
            f"   VWAP   : {fmt_price(d['avg_p'],t)}\n{H()}\n"
            f"📊 Trend  : <b>{d['trend_e']} {d['trend']}</b>\n"
            f"🗺 Zona   : <b>{d['zona']}</b>\n{H()}\n"
            f"EMA 8/21 : {fmt_price(d['ef'],t)} / {fmt_price(d['es'],t)}\n"
            f"MACD Hist: {d['hv']:+.4f}\n"
            f"RSI(14)  : {d['rsi']:.1f}\n"
            f"Stoch    : {d['skv']:.1f}/{d['sdv']:.1f}{sw}\n{H()}\n"
            f"📦 Vol   : {d['vol_n']:,.0f} ({d['vol_ratio']:.1f}x) 🔥\n"
            f"{'═'*28}\n<i>⚡ T1MO × Wisdom Bot</i>")

# ─────────────────────────────────────────────
#  TELEGRAM COMMAND HANDLERS
# ─────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>T1MO × WISDOM SIGNAL BOT</b>\n"
        "─────────────────────────────\n"
        "📊 /chart BBCA — Chart + indikator\n"
        "🎯 /signal BBCA — Analisis sinyal\n"
        "🔍 /screener — Scan semua ticker\n"
        "🗺 /zona BBCA — Zona &amp; posisi\n"
        "📈 /trend BBCA — Analisis trend\n"
        "📦 /volume BBCA — Analisis volume\n"
        "📋 /summary — Summary harian\n"
        "📋 /watchlist — Daftar saham\n"
        "➕ /add TLKM — Tambah IDX\n"
        "➕ /addus TSLA — Tambah US\n"
        "➖ /remove TLKM — Hapus ticker\n"
        "─────────────────────────────\n"
        "<i>Contoh: /chart PLTR atau /signal BBCA</i>\n"
        "<i>⚡ T1MO Quantitative × Wisdom Bot</i>",
        parse_mode="HTML"
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_help(update, ctx)

async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    idx=[t.replace(".JK","") for t in dynamic_watchlist["IDX"]]
    us=dynamic_watchlist["US"]
    await update.message.reply_text(
        f"📋 <b>WATCHLIST AKTIF</b>\n{H()}\n"
        f"🇮🇩 IDX ({len(idx)}): {', '.join(idx)}\n\n"
        f"🇺🇸 US ({len(us)}): {', '.join(us)}\n{H()}\n"
        f"➕ /add TICKER | ➖ /remove TICKER\n"
        f"<i>⚡ T1MO × Wisdom Bot</i>",
        parse_mode="HTML"
    )

async def _analyze_and_reply(update, ticker, fmt_func):
    await update.message.reply_text(f"⏳ Fetching {ticker.replace('.JK','')}...")
    d = analyze(ticker)
    if d is None:
        await update.message.reply_text(f"❌ Data {ticker} tidak tersedia.")
        return
    await update.message.reply_text(fmt_func(d), parse_mode="HTML")

async def cmd_chart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Format: /chart BBCA"); return
    await _analyze_and_reply(update, normalize_ticker(ctx.args[0]), msg_chart)

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Format: /signal BBCA"); return
    await _analyze_and_reply(update, normalize_ticker(ctx.args[0]), msg_signal)

async def cmd_zona(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Format: /zona BBCA"); return
    await _analyze_and_reply(update, normalize_ticker(ctx.args[0]), msg_zona)

async def cmd_trend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Format: /trend BBCA"); return
    await _analyze_and_reply(update, normalize_ticker(ctx.args[0]), msg_trend)

async def cmd_volume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Format: /volume BBCA"); return
    await _analyze_and_reply(update, normalize_ticker(ctx.args[0]), msg_volume)

async def cmd_screener(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Scanning semua ticker...")
    results=[]
    for t in all_tickers():
        try: results.append(analyze(t))
        except: results.append(None)
        await asyncio.sleep(1)
    await update.message.reply_text(msg_screener(results), parse_mode="HTML")

async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Generating summary...")
    results=[]
    for t in all_tickers():
        try: results.append(analyze(t))
        except: results.append(None)
        await asyncio.sleep(1)
    await update.message.reply_text(msg_summary(results), parse_mode="HTML")

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Format: /add TLKM"); return
    ticker=ctx.args[0].upper()+".JK"
    if ticker not in dynamic_watchlist["IDX"]:
        dynamic_watchlist["IDX"].append(ticker)
        await update.message.reply_text(f"✅ {ctx.args[0].upper()} ditambahkan ke IDX!")
    else:
        await update.message.reply_text(f"⚠️ {ctx.args[0].upper()} sudah ada.")

async def cmd_addus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Format: /addus TSLA"); return
    ticker=ctx.args[0].upper()
    if ticker not in dynamic_watchlist["US"]:
        dynamic_watchlist["US"].append(ticker)
        await update.message.reply_text(f"✅ {ticker} ditambahkan ke US!")
    else:
        await update.message.reply_text(f"⚠️ {ticker} sudah ada.")

async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Format: /remove TLKM"); return
    arg=ctx.args[0].upper()
    removed=False
    if arg+".JK" in dynamic_watchlist["IDX"]:
        dynamic_watchlist["IDX"].remove(arg+".JK"); removed=True
    elif arg in dynamic_watchlist["US"]:
        dynamic_watchlist["US"].remove(arg); removed=True
    if removed: await update.message.reply_text(f"✅ {arg} dihapus!")
    else: await update.message.reply_text(f"⚠️ {arg} tidak ditemukan.")

# ─────────────────────────────────────────────
#  AUTO SCAN (background job)
# ─────────────────────────────────────────────
def is_market_open():
    now=datetime.now(WIB); wd=now.weekday()
    if wd>=5: return False
    t=now.hour*60+now.minute
    return (9*60<=t<=16*60+15) or (t>=21*60+30) or (t<=4*60)

async def auto_scan(ctx: ContextTypes.DEFAULT_TYPE):
    if not is_market_open():
        log.info("Market tutup, skip scan")
        return
    log.info(f"Auto scan {datetime.now(WIB).strftime('%H:%M WIB')}")
    for ticker in all_tickers():
        try:
            if time.time()-signal_cooldown.get(ticker,0)<COOLDOWN_SEC:
                continue
            d=analyze(ticker)
            if d and d["sig"]:
                log.info(f"[{ticker}] {d['sig_l']}")
                send_alert_sync(msg_alert(d))
                signal_cooldown[ticker]=time.time()
            await asyncio.sleep(1.5)
        except Exception as e:
            log.error(f"[{ticker}] {e}")

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    log.info("="*50)
    log.info("  IDX SCREENER v3 — T1MO x WISDOM BOT")
    log.info("="*50)

    # Kirim startup message
    idx_s=", ".join(t.replace(".JK","") for t in dynamic_watchlist["IDX"])
    us_s=", ".join(dynamic_watchlist["US"])
    now=datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    send_alert_sync(
        f"🤖 <b>ROBOT TRADING AKTIF v3</b>\n{'═'*28}\n"
        f"🕐 {now}\n⚡ T1MO Quantitative × Wisdom Bot\n{H()}\n"
        f"🇮🇩 IDX: {idx_s}\n🇺🇸 US : {us_s}\n{H()}\n"
        f"🔄 Scan: tiap {SCAN_INTERVAL}s\n"
        f"💬 Ketik /help untuk command\n{'═'*28}"
    )

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("chart",     cmd_chart))
    app.add_handler(CommandHandler("signal",    cmd_signal))
    app.add_handler(CommandHandler("zona",      cmd_zona))
    app.add_handler(CommandHandler("trend",     cmd_trend))
    app.add_handler(CommandHandler("volume",    cmd_volume))
    app.add_handler(CommandHandler("screener",  cmd_screener))
    app.add_handler(CommandHandler("summary",   cmd_summary))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add",       cmd_add))
    app.add_handler(CommandHandler("addus",     cmd_addus))
    app.add_handler(CommandHandler("remove",    cmd_remove))

    # Auto scan job every 2 minutes
    app.job_queue.run_repeating(auto_scan, interval=SCAN_INTERVAL, first=30)

    log.info("Bot polling started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
