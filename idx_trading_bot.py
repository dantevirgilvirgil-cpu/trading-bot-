"""
IDX STOCK TRADING BOT - Signal Detection + Chart + Telegram
Inspired by T1MO & WIN BOT Quantitative Screener
Signals: Green Bull, Break Top, Hawk1, Buy Magenta, Buy Lautan
Chart: Candlestick + EMA + Volume + MACD + RSI dikirim ke Telegram

Requirements:
pip install yfinance pandas ta requests schedule python-dotenv matplotlib mplfinance

Setup .env:
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
"""

import yfinance as yf
import pandas as pd
import numpy as np
import ta
import requests
import schedule
import time
import os
import io
from datetime import datetime
import pytz
from dotenv import load_dotenv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch
import warnings
warnings.filterwarnings('ignore')

load_dotenv()

# ─── CONFIG ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WIB = pytz.timezone("Asia/Jakarta")

# IDX Watchlist - Saham pilihan
WATCHLIST = [
    # Big Cap / Blue Chip
    "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "TLKM.JK",
    "ASII.JK", "UNVR.JK", "ICBP.JK", "INDF.JK", "KLBF.JK",
    # Komoditas / Mining
    "ANTM.JK", "PTBA.JK", "ITMG.JK", "ADRO.JK", "BSSR.JK",
    "INCO.JK", "NCKL.JK", "MBMA.JK", "MDKA.JK", "BRMS.JK",
    # Gold
    "EMAS.JK", "MERDEKA.JK",
    # Energy
    "MEDC.JK", "ENRG.JK", "ELSA.JK",
    # Property
    "BSDE.JK", "CTRA.JK", "SMRA.JK",
    # Shipping
    "BULL.JK", "TMAS.JK", "SMDR.JK",
    # Tech / Consumer
    "BUKA.JK", "GOTO.JK", "EMTK.JK",
    # CPO
    "AALI.JK", "TAPG.JK", "DSNG.JK",
]

TIMEFRAMES = {
    "Daily":  {"period": "6mo",  "interval": "1d"},
    "Hourly": {"period": "1mo",  "interval": "1h"},
    "15Min":  {"period": "5d",   "interval": "15m"},
    "5Min":   {"period": "2d",   "interval": "5m"},
}

# ─── TELEGRAM ────────────────────────────────────────────────────────────────
def send_telegram_text(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token belum diset")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"[TELEGRAM ERROR] {r.text}")
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")

def send_telegram_photo(image_bytes: bytes, caption: str = ""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        files = {"photo": ("chart.png", image_bytes, "image/png")}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        r = requests.post(url, files=files, data=data, timeout=30)
        if r.status_code != 200:
            print(f"[TELEGRAM PHOTO ERROR] {r.text}")
    except Exception as e:
        print(f"[TELEGRAM PHOTO EXCEPTION] {e}")

# ─── DATA FETCH ──────────────────────────────────────────────────────────────
def fetch_data(ticker: str, period: str, interval: str) -> pd.DataFrame:
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         auto_adjust=True, progress=False)
        if df.empty or len(df) < 20:
            return pd.DataFrame()
        df.dropna(inplace=True)
        return df
    except Exception as e:
        print(f"[FETCH ERROR] {ticker}: {e}")
        return pd.DataFrame()

def get_company_name(ticker: str) -> str:
    """Ambil nama perusahaan dari ticker"""
    names = {
        "BBCA.JK": "Bank BCA", "BBRI.JK": "Bank BRI", "BMRI.JK": "Bank Mandiri",
        "BBNI.JK": "Bank BNI", "TLKM.JK": "Telkom", "ASII.JK": "Astra International",
        "ANTM.JK": "Antam", "PTBA.JK": "Bukit Asam", "ITMG.JK": "Indo Tambangraya",
        "ADRO.JK": "Adaro Energy", "INCO.JK": "Vale Indonesia", "NCKL.JK": "Trimegah Nickel",
        "MBMA.JK": "Merdeka Battery", "MDKA.JK": "Merdeka Copper", "BRMS.JK": "Bumi Resources Minerals",
        "EMAS.JK": "Merdeka Gold", "MEDC.JK": "Medco Energy", "ENRG.JK": "Energi Mega",
        "BULL.JK": "Buana Lintas Lautan", "BUKA.JK": "Bukalapak", "GOTO.JK": "GoTo",
        "AALI.JK": "Astra Agro", "TAPG.JK": "Trimitra Propertindo",
    }
    return names.get(ticker, ticker.replace(".JK", ""))

# ─── INDICATORS ──────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    vol   = df["Volume"].squeeze()

    df["EMA9"]   = ta.trend.ema_indicator(close, window=9)
    df["EMA21"]  = ta.trend.ema_indicator(close, window=21)
    df["EMA50"]  = ta.trend.ema_indicator(close, window=50)
    df["EMA200"] = ta.trend.ema_indicator(close, window=200)

    macd = ta.trend.MACD(close)
    df["MACD"]        = macd.macd()
    df["MACD_Signal"] = macd.macd_signal()
    df["MACD_Hist"]   = macd.macd_diff()

    df["RSI"] = ta.momentum.rsi(close, window=14)

    bb = ta.volatility.BollingerBands(close)
    df["BB_Upper"] = bb.bollinger_hband()
    df["BB_Lower"] = bb.bollinger_lband()

    stoch = ta.momentum.StochasticOscillator(high, low, close)
    df["STOCH_K"] = stoch.stoch()
    df["STOCH_D"] = stoch.stoch_signal()

    df["Vol_MA20"] = vol.rolling(20).mean()
    df["ATR"]      = ta.volatility.average_true_range(high, low, close)

    adx = ta.trend.ADXIndicator(high, low, close)
    df["ADX"] = adx.adx()

    df.dropna(inplace=True)
    return df

# ─── SIGNAL DETECTION ────────────────────────────────────────────────────────
def signal_green_bull(df):
    if len(df) < 3: return False
    last, prev = df.iloc[-1], df.iloc[-2]
    try:
        return all([
            float(last["EMA9"]) > float(last["EMA21"]),
            float(last["EMA21"]) > float(last["EMA50"]),
            float(last["Close"]) > float(last["EMA9"]),
            float(last["MACD_Hist"]) > 0,
            float(last["MACD_Hist"]) > float(prev["MACD_Hist"]),
            55 <= float(last["RSI"]) <= 75,
            float(last["Volume"]) > float(last["Vol_MA20"]) * 1.5,
            float(last["ADX"]) > 20,
        ])
    except: return False

def signal_break_top(df):
    if len(df) < 12: return False
    last = df.iloc[-1]
    prev10_high = df["High"].iloc[-12:-2].max()
    try:
        return all([
            float(last["Close"]) > float(prev10_high),
            float(last["Close"]) > float(last["Open"]),
            float(last["Volume"]) > float(last["Vol_MA20"]) * 2.0,
            float(last["MACD_Hist"]) > 0,
            float(last["RSI"]) > 50,
        ])
    except: return False

def signal_hawk1(df):
    if len(df) < 5: return False
    last, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    try:
        ema_cross  = float(last["EMA9"]) > float(last["EMA21"]) and float(prev2["EMA9"]) <= float(prev2["EMA21"])
        macd_cross = float(last["MACD"]) > float(last["MACD_Signal"]) and float(prev["MACD"]) <= float(prev["MACD_Signal"])
        rsi_cross  = float(last["RSI"]) > 50 and float(prev["RSI"]) <= 50
        return sum([ema_cross, macd_cross, rsi_cross]) >= 2 and float(last["Close"]) > float(last["EMA50"])
    except: return False

def signal_buy_magenta(df):
    if len(df) < 5: return False
    last, prev = df.iloc[-1], df.iloc[-2]
    try:
        rsi_rev   = float(last["RSI"]) > float(prev["RSI"]) and 28 <= float(prev["RSI"]) <= 45
        stoch_cross = float(last["STOCH_K"]) > float(last["STOCH_D"]) and float(prev["STOCH_K"]) <= float(prev["STOCH_D"])
        return all([
            rsi_rev or stoch_cross,
            float(last["Close"]) > float(last["BB_Lower"]),
            float(last["MACD_Hist"]) > float(prev["MACD_Hist"]),
            float(last["Volume"]) > float(last["Vol_MA20"]) * 1.2,
        ])
    except: return False

def signal_buy_lautan(df):
    if len(df) < 20: return False
    last, prev = df.iloc[-1], df.iloc[-2]
    try:
        avg_atr = float(df["ATR"].iloc[-20:-1].mean())
        return all([
            float(last["ATR"]) < avg_atr * 0.9,
            float(last["Volume"]) > float(last["Vol_MA20"]) * 0.8,
            40 <= float(last["RSI"]) <= 58,
            float(last["RSI"]) > float(prev["RSI"]),
            float(last["Close"]) > float(last["EMA200"]),
        ])
    except: return False

def get_signal(df):
    signals = {
        "🟢 Green Bull":     (signal_green_bull(df),   5),
        "🔴 Break Top":      (signal_break_top(df),    4),
        "🦅 Hawk1 Detected": (signal_hawk1(df),        4),
        "🟣 Buy Magenta":    (signal_buy_magenta(df),  3),
        "🌊 Buy Lautan":     (signal_buy_lautan(df),   2),
    }
    active = [(label, score) for label, (active, score) in signals.items() if active]
    if not active: return "No Signal", 0
    best = max(active, key=lambda x: x[1])
    total = sum(s for _, s in active)
    return best[0], total

# ─── CHART GENERATOR ─────────────────────────────────────────────────────────
def generate_chart(ticker: str, df: pd.DataFrame, signal: str, timeframe: str = "Daily") -> bytes:
    """Generate candlestick chart dengan EMA, Volume, MACD, RSI — dark theme"""
    try:
        # Ambil 60 candle terakhir
        df_plot = df.tail(60).copy()
        n = len(df_plot)
        x = np.arange(n)

        fig = plt.figure(figsize=(12, 9), facecolor='#0d1117')
        gs = gridspec.GridSpec(4, 1, height_ratios=[4, 1.2, 1.2, 1.2],
                               hspace=0.08, top=0.93, bottom=0.06,
                               left=0.06, right=0.96)

        ax1 = fig.add_subplot(gs[0])  # Candlestick + EMA
        ax2 = fig.add_subplot(gs[1])  # Volume
        ax3 = fig.add_subplot(gs[2])  # MACD
        ax4 = fig.add_subplot(gs[3])  # RSI

        for ax in [ax1, ax2, ax3, ax4]:
            ax.set_facecolor('#0d1117')
            ax.tick_params(colors='#8b949e', labelsize=7)
            ax.spines['bottom'].set_color('#30363d')
            ax.spines['top'].set_color('#30363d')
            ax.spines['left'].set_color('#30363d')
            ax.spines['right'].set_color('#30363d')
            ax.grid(True, color='#21262d', linewidth=0.5, alpha=0.7)

        # ── Candlestick ──
        opens  = df_plot["Open"].values.flatten()
        closes = df_plot["Close"].values.flatten()
        highs  = df_plot["High"].values.flatten()
        lows   = df_plot["Low"].values.flatten()

        for i in range(n):
            color = '#26a641' if closes[i] >= opens[i] else '#f85149'
            ax1.plot([x[i], x[i]], [lows[i], highs[i]], color=color, linewidth=0.8)
            body_h = abs(closes[i] - opens[i])
            body_y = min(opens[i], closes[i])
            if body_h == 0: body_h = highs[i] * 0.001
            rect = plt.Rectangle((x[i]-0.35, body_y), 0.7, body_h, color=color)
            ax1.add_patch(rect)

        # ── EMA Lines ──
        ax1.plot(x, df_plot["EMA9"].values.flatten(),  color='#f0b429', linewidth=1.2, label='EMA9')
        ax1.plot(x, df_plot["EMA21"].values.flatten(), color='#58a6ff', linewidth=1.2, label='EMA21')
        ax1.plot(x, df_plot["EMA50"].values.flatten(), color='#bc8cff', linewidth=0.9, label='EMA50', alpha=0.8)

        # Signal badge
        signal_colors = {
            "🟢 Green Bull": '#26a641', "🔴 Break Top": '#f85149',
            "🦅 Hawk1 Detected": '#e3b341', "🟣 Buy Magenta": '#bc8cff',
            "🌊 Buy Lautan": '#58a6ff'
        }
        sc = signal_colors.get(signal, '#58a6ff')

        company = get_company_name(ticker)
        last_close = float(df_plot["Close"].iloc[-1])
        last_open  = float(df_plot["Open"].iloc[-2]) if len(df_plot) > 1 else last_close
        pct = ((last_close - float(df_plot["Close"].iloc[-2])) / float(df_plot["Close"].iloc[-2])) * 100 if len(df_plot) > 1 else 0
        rsi_val = float(df_plot["RSI"].iloc[-1])

        ticker_clean = ticker.replace(".JK", "")
        pct_str = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"
        pct_color = '#26a641' if pct >= 0 else '#f85149'

        ax1.set_title(
            f"  {ticker_clean} — {company}   |   {timeframe}   |   Rp {last_close:,.0f}  {pct_str}   |   RSI: {rsi_val:.1f}",
            color='#e6edf3', fontsize=10, fontweight='bold', loc='left', pad=8
        )

        # Signal text
        ax1.text(0.99, 0.97, signal, transform=ax1.transAxes,
                 color=sc, fontsize=9, fontweight='bold',
                 ha='right', va='top',
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='#161b22', edgecolor=sc, linewidth=1.5))

        ax1.legend(loc='upper left', fontsize=7, facecolor='#161b22',
                   edgecolor='#30363d', labelcolor='#8b949e')
        ax1.set_xlim(-1, n)
        ax1.yaxis.set_label_position('right')
        ax1.yaxis.tick_right()

        # ── Volume ──
        vol_vals = df_plot["Volume"].values.flatten()
        vol_ma   = df_plot["Vol_MA20"].values.flatten()
        vol_colors = ['#26a641' if closes[i] >= opens[i] else '#f85149' for i in range(n)]
        ax2.bar(x, vol_vals, color=vol_colors, alpha=0.8, width=0.7)
        ax2.plot(x, vol_ma, color='#f0b429', linewidth=1, alpha=0.8)
        ax2.set_ylabel('Vol', color='#8b949e', fontsize=7)
        ax2.yaxis.set_label_position('right')
        ax2.yaxis.tick_right()
        ax2.set_xlim(-1, n)

        # ── MACD ──
        macd_vals = df_plot["MACD"].values.flatten()
        macd_sig  = df_plot["MACD_Signal"].values.flatten()
        macd_hist = df_plot["MACD_Hist"].values.flatten()
        hist_colors = ['#26a641' if v >= 0 else '#f85149' for v in macd_hist]
        ax3.bar(x, macd_hist, color=hist_colors, alpha=0.8, width=0.7)
        ax3.plot(x, macd_vals, color='#58a6ff', linewidth=1)
        ax3.plot(x, macd_sig,  color='#f85149', linewidth=1)
        ax3.axhline(0, color='#30363d', linewidth=0.5)
        ax3.set_ylabel('MACD', color='#8b949e', fontsize=7)
        ax3.yaxis.set_label_position('right')
        ax3.yaxis.tick_right()
        ax3.set_xlim(-1, n)

        # ── RSI ──
        rsi_vals = df_plot["RSI"].values.flatten()
        ax4.plot(x, rsi_vals, color='#bc8cff', linewidth=1.2)
        ax4.axhline(70, color='#f85149', linewidth=0.7, linestyle='--', alpha=0.6)
        ax4.axhline(30, color='#26a641', linewidth=0.7, linestyle='--', alpha=0.6)
        ax4.axhline(50, color='#30363d', linewidth=0.5)
        ax4.fill_between(x, rsi_vals, 50,
                         where=(np.array(rsi_vals) >= 50), alpha=0.1, color='#26a641')
        ax4.fill_between(x, rsi_vals, 50,
                         where=(np.array(rsi_vals) < 50), alpha=0.1, color='#f85149')
        ax4.set_ylim(0, 100)
        ax4.set_ylabel('RSI', color='#8b949e', fontsize=7)
        ax4.yaxis.set_label_position('right')
        ax4.yaxis.tick_right()
        ax4.set_xlim(-1, n)

        # X axis labels (tanggal)
        step = max(1, n // 8)
        xticks = x[::step]
        try:
            xlabels = [str(df_plot.index[i])[:10] for i in xticks]
        except:
            xlabels = [str(i) for i in xticks]
        ax4.set_xticks(xticks)
        ax4.set_xticklabels(xlabels, rotation=30, ha='right', fontsize=6, color='#8b949e')
        for ax in [ax1, ax2, ax3]:
            ax.set_xticks([])

        # Watermark
        fig.text(0.5, 0.01, '📊 IDX Trading Bot — Dantebadai_bot',
                 ha='center', color='#484f58', fontsize=7)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight',
                    facecolor='#0d1117', edgecolor='none')
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"[CHART ERROR] {ticker}: {e}")
        plt.close('all')
        return b""

# ─── SCREENER ────────────────────────────────────────────────────────────────
def run_screener(timeframe_name: str = "Daily", send_chart: bool = True) -> list:
    tf = TIMEFRAMES.get(timeframe_name, TIMEFRAMES["Daily"])
    results = []
    now_wib = datetime.now(WIB).strftime("%H:%M WIB")

    print(f"\n{'='*55}")
    print(f"  IDX SCREENER — {timeframe_name} — {now_wib}")
    print(f"{'='*55}")

    for ticker in WATCHLIST:
        df = fetch_data(ticker, tf["period"], tf["interval"])
        if df.empty: continue
        df = add_indicators(df)
        if df.empty or len(df) < 5: continue

        signal, score = get_signal(df)
        if score == 0: continue

        last   = df.iloc[-1]
        prev   = df.iloc[-2]
        close  = float(last["Close"])
        change = ((close - float(prev["Close"])) / float(prev["Close"])) * 100
        rsi    = float(last["RSI"])
        vol    = float(last["Volume"])
        vol_ma = float(last["Vol_MA20"])
        vol_ratio = vol / vol_ma if vol_ma > 0 else 1

        result = {
            "ticker": ticker, "signal": signal, "score": score,
            "close": close, "change_pct": change, "rsi": rsi,
            "vol_ratio": vol_ratio, "df": df,
        }
        results.append(result)

        ticker_clean = ticker.replace(".JK", "")
        pct_str = f"+{change:.1f}%" if change >= 0 else f"{change:.1f}%"
        print(f"  {ticker_clean:8s} | {signal:22s} | Rp{close:>8,.0f} | {pct_str:>7s} | RSI:{rsi:.0f} | Vol:{vol_ratio:.1f}x | ⭐{score}")

    results.sort(key=lambda x: x["score"], reverse=True)

    if results:
        _send_summary(results, timeframe_name, now_wib)
        if send_chart:
            # Kirim chart untuk top 3 signal terkuat
            for r in results[:3]:
                if r["score"] >= 3:
                    chart_bytes = generate_chart(r["ticker"], r["df"], r["signal"], timeframe_name)
                    if chart_bytes:
                        ticker_clean = r["ticker"].replace(".JK", "")
                        pct_str = f"+{r['change_pct']:.2f}%" if r['change_pct'] >= 0 else f"{r['change_pct']:.2f}%"
                        caption = (
                            f"<b>{r['signal']}</b>\n"
                            f"📌 <b>{ticker_clean}</b> — {get_company_name(r['ticker'])}\n"
                            f"💰 Rp {r['close']:,.0f}  {pct_str}\n"
                            f"📊 RSI: {r['rsi']:.1f}  |  Vol: {r['vol_ratio']:.1f}x avg\n"
                            f"⭐ Score: {r['score']}  |  {timeframe_name}"
                        )
                        send_telegram_photo(chart_bytes, caption)
                        time.sleep(2)

    return results

def _send_summary(results: list, timeframe: str, now_wib: str):
    lines = [
        f"<b>🇮🇩 IDX STOCK SCREENER</b>",
        f"<b>Timeframe:</b> {timeframe}  |  <b>Time:</b> {now_wib}",
        f"<b>Signals:</b> {len(results)} saham",
        "─────────────────────",
    ]
    for r in results[:8]:
        ticker_clean = r["ticker"].replace(".JK", "")
        pct_str = f"+{r['change_pct']:.1f}%" if r['change_pct'] >= 0 else f"{r['change_pct']:.1f}%"
        lines.append(
            f"{r['signal']} <b>{ticker_clean}</b>\n"
            f"   💰 Rp {r['close']:,.0f} | {pct_str} | RSI:{r['rsi']:.0f} | ⭐{r['score']}"
        )
    lines.append("─────────────────────")
    lines.append("<i>⚠️ Bukan rekomendasi investasi. DYOR.</i>")
    send_telegram_text("\n".join(lines))

# ─── OPENING SCAN (15 menit pertama) ─────────────────────────────────────────
def opening_scan():
    """Scan khusus jam 09:05 WIB — 15 menit pertama opening IDX"""
    now_wib = datetime.now(WIB).strftime("%H:%M WIB")
    print(f"\n🚀 OPENING SCAN — {now_wib}")

    send_telegram_text(
        f"🚀 <b>OPENING SCAN IDX</b>\n"
        f"⏰ {now_wib} — Memulai scan 15 menit pertama...\n"
        f"Mencari volume spike + momentum awal sesi!"
    )

    results = []
    for ticker in WATCHLIST:
        df = fetch_data(ticker, "2d", "5m")
        if df.empty or len(df) < 10: continue
        df = add_indicators(df)
        if df.empty: continue

        last   = df.iloc[-1]
        prev   = df.iloc[-2]
        close  = float(last["Close"])
        change = ((close - float(prev["Close"])) / float(prev["Close"])) * 100
        vol    = float(last["Volume"])
        vol_ma = float(last["Vol_MA20"])
        vol_ratio = vol / vol_ma if vol_ma > 0 else 1

        # Kriteria opening: volume spike + gerak positif
        if vol_ratio >= 2.0 and change >= 1.0:
            signal, score = get_signal(df)
            results.append({
                "ticker": ticker, "signal": signal if score > 0 else "⚡ Volume Spike",
                "score": score, "close": close, "change_pct": change,
                "rsi": float(last["RSI"]), "vol_ratio": vol_ratio, "df": df,
            })

    results.sort(key=lambda x: x["vol_ratio"], reverse=True)

    if not results:
        send_telegram_text("❌ Tidak ada volume spike signifikan di 15 menit pertama.")
        return

    lines = [
        "⚡ <b>OPENING ALERT — Volume Spike!</b>",
        f"⏰ {now_wib}",
        "─────────────────────",
    ]
    for r in results[:5]:
        ticker_clean = r["ticker"].replace(".JK", "")
        pct_str = f"+{r['change_pct']:.1f}%"
        lines.append(
            f"🔥 <b>{ticker_clean}</b> — {r['signal']}\n"
            f"   💰 Rp {r['close']:,.0f} | {pct_str} | Vol: {r['vol_ratio']:.1f}x | RSI:{r['rsi']:.0f}"
        )
    lines.append("─────────────────────")
    lines.append("⚡ <i>Cek chart di Stockbit sekarang!</i>")
    send_telegram_text("\n".join(lines))

    # Kirim chart top 2
    for r in results[:2]:
        chart_bytes = generate_chart(r["ticker"], r["df"], r["signal"], "5Min")
        if chart_bytes:
            ticker_clean = r["ticker"].replace(".JK", "")
            caption = (
                f"⚡ <b>OPENING SPIKE — {ticker_clean}</b>\n"
                f"💰 Rp {r['close']:,.0f} | +{r['change_pct']:.1f}%\n"
                f"📊 Volume: {r['vol_ratio']:.1f}x avg | RSI: {r['rsi']:.1f}\n"
                f"🕐 {now_wib}"
            )
            send_telegram_photo(chart_bytes, caption)
            time.sleep(2)

# ─── SCHEDULER ───────────────────────────────────────────────────────────────
def is_market_open() -> bool:
    now = datetime.now(WIB)
    if now.weekday() >= 5: return False  # Weekend
    h, m = now.hour, now.minute
    return (9 <= h < 12) or (13 <= h < 16)

def job_opening():
    opening_scan()

def job_daily():
    run_screener("Daily", send_chart=True)

def job_15min():
    if is_market_open():
        results = run_screener("15Min", send_chart=False)
        # Kirim chart hanya untuk signal kuat
        strong = [r for r in results if r["score"] >= 4]
        for r in strong[:2]:
            chart_bytes = generate_chart(r["ticker"], r["df"], r["signal"], "15Min")
            if chart_bytes:
                ticker_clean = r["ticker"].replace(".JK", "")
                pct_str = f"+{r['change_pct']:.2f}%" if r['change_pct'] >= 0 else f"{r['change_pct']:.2f}%"
                caption = (
                    f"<b>{r['signal']}</b>\n"
                    f"📌 <b>{ticker_clean}</b>\n"
                    f"💰 Rp {r['close']:,.0f}  {pct_str}\n"
                    f"📊 RSI: {r['rsi']:.1f} | Vol: {r['vol_ratio']:.1f}x\n"
                    f"⭐ Score: {r['score']} | 15Min"
                )
                send_telegram_photo(chart_bytes, caption)
                time.sleep(2)

# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 IDX Trading Bot Starting...")
    print(f"   Watchlist: {len(WATCHLIST)} saham IDX")
    print(f"   Telegram: {'✅ Configured' if TELEGRAM_TOKEN else '❌ Not configured'}")

    send_telegram_text(
        "🤖 <b>IDX Trading Bot AKTIF!</b>\n"
        f"📋 Watchlist: {len(WATCHLIST)} saham\n"
        "⏰ Jadwal:\n"
        "  • 09:05 WIB — Opening Scan\n"
        "  • Tiap 15 menit (jam bursa) — Intraday\n"
        "  • 16:10 WIB — Daily Screener\n"
        "Sinyal: 🟢 Green Bull | 🔴 Break Top | 🦅 Hawk1 | 🟣 Buy Magenta | 🌊 Buy Lautan"
    )

    # Run daily scan sekali saat start
    job_daily()

    # Schedule
    schedule.every().day.at("09:05").do(job_opening)   # Opening 15 menit pertama
    schedule.every().day.at("10:00").do(job_15min)
    schedule.every().day.at("10:30").do(job_15min)
    schedule.every().day.at("11:00").do(job_15min)
    schedule.every().day.at("11:30").do(job_15min)
    schedule.every().day.at("13:00").do(job_15min)
    schedule.every().day.at("13:30").do(job_15min)
    schedule.every().day.at("14:00").do(job_15min)
    schedule.every().day.at("14:30").do(job_15min)
    schedule.every().day.at("15:00").do(job_15min)
    schedule.every().day.at("15:30").do(job_15min)
    schedule.every().day.at("16:10").do(job_daily)     # After market close

    print("\n⏰ Scheduler running... Ctrl+C to stop")
    while True:
        schedule.run_pending()
        time.sleep(60)
