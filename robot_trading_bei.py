"""
╔══════════════════════════════════════════════════════════════╗
║         ROBOT TRADING IDX — T1MO x WISDOM QUANTITATIVE      ║
║         Deploy: Railway | Data: yfinance 2m interval         ║
║         Signals: Akumulasi 🟢 | Distribusi 🔴 | Breakout ⚡  ║
╚══════════════════════════════════════════════════════════════╝

SETUP Railway Environment Variables:
  TELEGRAM_TOKEN  = token bot telegram kamu
  TELEGRAM_CHAT_ID = chat id / group id kamu
  WATCHLIST_IDX   = BBCA,BBRI,BMRI,TLKM,ASII   (pisah koma)
  WATCHLIST_US    = PLTR,NVDA,TSLA              (pisah koma)
"""

import os
import time
import logging
from datetime import datetime, timezone
import pytz

import yfinance as yf
import pandas as pd
import numpy as np
import requests

# ─────────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Ganti ticker di sini atau set lewat env Railway
WATCHLIST_IDX_RAW = os.environ.get(
    "WATCHLIST_IDX",
    "BBCA,BBRI,BMRI,TLKM,ASII,MDKA,ANTM,NCKL,MBMA,PTBA,INCO,MEDC,ENRG,ELSA,BULL,TMAS"
)
WATCHLIST_US_RAW = os.environ.get(
    "WATCHLIST_US",
    "PLTR,NVDA,TSLA,AAPL,AMD"
)

# Tambah .JK untuk IDX
WATCHLIST_IDX = [f"{t.strip()}.JK" for t in WATCHLIST_IDX_RAW.split(",") if t.strip()]
WATCHLIST_US  = [t.strip() for t in WATCHLIST_US_RAW.split(",") if t.strip()]
ALL_TICKERS   = WATCHLIST_IDX + WATCHLIST_US

# Parameter signal
VOLUME_SPIKE_MULTIPLIER = 2.0   # volume > 2x rata-rata = spike
VOLUME_AVG_PERIOD       = 20    # periode rata-rata volume
EMA_FAST                = 8
EMA_SLOW                = 21
MACD_FAST               = 12
MACD_SLOW               = 26
MACD_SIGNAL             = 9
RSI_PERIOD              = 14
STOCH_K                 = 15
STOCH_D                 = 3
BB_PERIOD               = 20
BB_STD                  = 2

SCAN_INTERVAL_SECONDS   = 120   # scan tiap 2 menit
WIB = pytz.timezone("Asia/Jakarta")

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram token/chat_id belum diset!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log.error(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

# ─────────────────────────────────────────────
#  INDIKATOR TEKNIKAL
# ─────────────────────────────────────────────
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast   = calc_ema(series, fast)
    ema_slow   = calc_ema(series, slow)
    macd_line  = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_stochastic(high, low, close, k=15, d=3):
    low_min  = low.rolling(k).min()
    high_max = high.rolling(k).max()
    stoch_k  = 100 * (close - low_min) / (high_max - low_min).replace(0, np.nan)
    stoch_d  = stoch_k.rolling(d).mean()
    return stoch_k, stoch_d

def calc_bollinger(series, period=20, std_dev=2):
    ma     = series.rolling(period).mean()
    std    = series.rolling(period).std()
    upper  = ma + std_dev * std
    lower  = ma - std_dev * std
    return upper, ma, lower

def determine_zona(price, bb_lower, bb_mid, bb_upper, ema_fast, ema_slow):
    """Zona 1–5 mirip sistem Wisdom & Invest"""
    if price > bb_upper and ema_fast > ema_slow:
        return "ZONA 5 🔥", "OVERBOUGHT / STRONG BULL"
    elif price > bb_mid and ema_fast > ema_slow:
        return "ZONA 4 ✅", "TREND UP"
    elif price > bb_mid:
        return "ZONA 3 ⚡", "SIDEWAYS BULLISH"
    elif price > bb_lower:
        return "ZONA 2 ⚠️", "SIDEWAYS / LEMAH"
    else:
        return "ZONA 1 🔴", "DOWNTREND"

def get_trend(ema_fast, ema_slow, macd_val, macd_sig):
    if ema_fast > ema_slow and macd_val > macd_sig:
        return "TREND UP ↑"
    elif ema_fast < ema_slow and macd_val < macd_sig:
        return "TREND DOWN ↓"
    else:
        return "SIDEWAYS ↔"

# ─────────────────────────────────────────────
#  FETCH DATA
# ─────────────────────────────────────────────
def fetch_data(ticker: str, interval="2m", period="5d") -> pd.DataFrame | None:
    """
    Fetch OHLCV. Fallback ke 5m jika 2m gagal.
    """
    try:
        df = yf.download(
            ticker,
            interval=interval,
            period=period,
            progress=False,
            auto_adjust=True,
            timeout=15
        )
        if df is None or df.empty:
            raise ValueError("Empty data")
        # Flatten multi-index kolom jika ada
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        if len(df) < 30:
            raise ValueError(f"Data terlalu sedikit: {len(df)} baris")
        return df
    except Exception as e:
        log.warning(f"[{ticker}] Gagal fetch {interval}: {e}")
        # Fallback ke 5m
        if interval == "2m":
            return fetch_data(ticker, interval="5m", period="5d")
        return None

# ─────────────────────────────────────────────
#  SINYAL UTAMA
# ─────────────────────────────────────────────
def detect_signals(ticker: str, df: pd.DataFrame) -> dict | None:
    """
    Deteksi sinyal akumulasi, distribusi, breakout.
    Return dict atau None jika tidak ada sinyal.
    """
    try:
        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        # ── Indikator ──
        ema_fast_s = calc_ema(close, EMA_FAST)
        ema_slow_s = calc_ema(close, EMA_SLOW)
        macd_line, macd_sig, macd_hist = calc_macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        rsi_s      = calc_rsi(close, RSI_PERIOD)
        stoch_k_s, stoch_d_s = calc_stochastic(high, low, close, STOCH_K, STOCH_D)
        bb_upper_s, bb_mid_s, bb_lower_s = calc_bollinger(close, BB_PERIOD, BB_STD)
        vol_avg    = volume.rolling(VOLUME_AVG_PERIOD).mean()

        # ── Nilai terkini ──
        idx     = -1
        price   = float(close.iloc[idx])
        prev    = float(close.iloc[idx - 1])
        vol_now = float(volume.iloc[idx])
        vol_avg_now = float(vol_avg.iloc[idx])
        ema_f   = float(ema_fast_s.iloc[idx])
        ema_s   = float(ema_slow_s.iloc[idx])
        macd_v  = float(macd_line.iloc[idx])
        macd_sv = float(macd_sig.iloc[idx])
        macd_hv = float(macd_hist.iloc[idx])
        rsi_v   = float(rsi_s.iloc[idx])
        stoch_kv = float(stoch_k_s.iloc[idx])
        stoch_dv = float(stoch_d_s.iloc[idx])
        bb_top  = float(bb_upper_s.iloc[idx])
        bb_mid  = float(bb_mid_s.iloc[idx])
        bb_bot  = float(bb_lower_s.iloc[idx])

        # ── Volume spike? ──
        vol_spike = vol_now >= (vol_avg_now * VOLUME_SPIKE_MULTIPLIER)
        vol_ratio = vol_now / vol_avg_now if vol_avg_now > 0 else 0

        # ── Perubahan harga ──
        price_change = price - prev
        price_change_pct = (price_change / prev * 100) if prev > 0 else 0

        # ── Zona & Trend ──
        zona, zona_desc = determine_zona(price, bb_bot, bb_mid, bb_top, ema_f, ema_s)
        trend = get_trend(ema_f, ema_s, macd_v, macd_sv)

        # ── Avg Price (VWAP simple) ──
        typical = (high + low + close) / 3
        vwap    = (typical * volume).rolling(20).sum() / volume.rolling(20).sum()
        avg_price = float(vwap.iloc[idx])

        # ── Deteksi sinyal ──
        signal_type = None
        signal_emoji = ""
        signal_label = ""

        # 🟢 AKUMULASI BESAR
        if (vol_spike and price_change > 0 and
                macd_hv > 0 and ema_f > ema_s and rsi_v < 75):
            signal_type  = "AKUMULASI"
            signal_emoji = "🟢▲"
            signal_label = "AKUMULASI BESAR"

        # 🔴 DISTRIBUSI
        elif (vol_spike and price_change < 0 and
              macd_hv < 0 and rsi_v > 30):
            signal_type  = "DISTRIBUSI"
            signal_emoji = "🔴▼"
            signal_label = "DISTRIBUSI / JUAL"

        # ⚡ BREAKOUT
        elif (price > bb_top and vol_spike and
              macd_hv > 0 and ema_f > ema_s):
            signal_type  = "BREAKOUT"
            signal_emoji = "⚡🚀"
            signal_label = "BREAKOUT BULLISH"

        # 📉 BREAKDOWN
        elif (price < bb_bot and vol_spike and macd_hv < 0):
            signal_type  = "BREAKDOWN"
            signal_emoji = "📉💀"
            signal_label = "BREAKDOWN / WASPADA"

        if signal_type is None:
            return None

        return {
            "ticker"       : ticker,
            "signal_type"  : signal_type,
            "signal_emoji" : signal_emoji,
            "signal_label" : signal_label,
            "price"        : price,
            "price_change_pct": price_change_pct,
            "vol_now"      : vol_now,
            "vol_avg"      : vol_avg_now,
            "vol_ratio"    : vol_ratio,
            "ema_fast"     : ema_f,
            "ema_slow"     : ema_s,
            "macd"         : macd_v,
            "macd_signal"  : macd_sv,
            "macd_hist"    : macd_hv,
            "rsi"          : rsi_v,
            "stoch_k"      : stoch_kv,
            "stoch_d"      : stoch_dv,
            "bb_top"       : bb_top,
            "bb_mid"       : bb_mid,
            "bb_bot"       : bb_bot,
            "avg_price"    : avg_price,
            "trend"        : trend,
            "zona"         : zona,
            "zona_desc"    : zona_desc,
        }
    except Exception as e:
        log.error(f"[{ticker}] Error detect signals: {e}")
        return None

# ─────────────────────────────────────────────
#  FORMAT PESAN TELEGRAM (T1MO x WISDOM STYLE)
# ─────────────────────────────────────────────
def format_message(s: dict) -> str:
    now_wib  = datetime.now(WIB).strftime("%d %b %Y  %H:%M WIB")
    is_idx   = s["ticker"].endswith(".JK")
    exchange = "IDX 🇮🇩" if is_idx else "US 🇺🇸"
    ticker_display = s["ticker"].replace(".JK", "") if is_idx else s["ticker"]

    # Format angka harga (IDX bisa ratusan, US bisa desimal)
    if is_idx:
        price_fmt = f"Rp {s['price']:,.0f}"
        avg_fmt   = f"Rp {s['avg_price']:,.0f}"
        ema_f_fmt = f"Rp {s['ema_fast']:,.0f}"
        ema_s_fmt = f"Rp {s['ema_slow']:,.0f}"
        bb_fmt    = (f"Rp {s['bb_top']:,.0f} / {s['bb_mid']:,.0f} / {s['bb_bot']:,.0f}")
    else:
        price_fmt = f"${s['price']:.2f}"
        avg_fmt   = f"${s['avg_price']:.2f}"
        ema_f_fmt = f"${s['ema_fast']:.2f}"
        ema_s_fmt = f"${s['ema_slow']:.2f}"
        bb_fmt    = f"${s['bb_top']:.2f} / {s['bb_mid']:.2f} / {s['bb_bot']:.2f}"

    vol_ratio_str = f"{s['vol_ratio']:.1f}x avg"
    chg_sign  = "+" if s['price_change_pct'] >= 0 else ""
    chg_str   = f"{chg_sign}{s['price_change_pct']:.2f}%"

    stoch_warn = ""
    if s['stoch_k'] > 80:
        stoch_warn = " ⚠️ Overbought"
    elif s['stoch_k'] < 20:
        stoch_warn = " 💎 Oversold"

    msg = (
        f"{s['signal_emoji']} <b>{s['signal_label']}</b>\n"
        f"{'═'*30}\n"
        f"<b>📌 {ticker_display}</b>  [{exchange}]\n"
        f"🕐 {now_wib}\n"
        f"{'─'*30}\n"
        f"<b>💰 HARGA</b>\n"
        f"   Close     : <b>{price_fmt}</b>  ({chg_str})\n"
        f"   Avg Price  : {avg_fmt}\n"
        f"{'─'*30}\n"
        f"<b>📊 TREND & ZONA</b>\n"
        f"   Trend      : <b>{s['trend']}</b>\n"
        f"   Sektor Zona: <b>{s['zona']}</b>\n"
        f"   Keterangan : {s['zona_desc']}\n"
        f"{'─'*30}\n"
        f"<b>📈 INDIKATOR</b>\n"
        f"   EMA(8/21)  : {ema_f_fmt} / {ema_s_fmt}\n"
        f"   MACD(12,26): {s['macd']:.4f}  Signal: {s['macd_signal']:.4f}\n"
        f"   Histogram  : {'+' if s['macd_hist']>0 else ''}{s['macd_hist']:.4f}\n"
        f"   RSI(14)    : {s['rsi']:.1f}\n"
        f"   Stoch K/D  : {s['stoch_k']:.1f} / {s['stoch_d']:.1f}{stoch_warn}\n"
        f"   BB         : {bb_fmt}\n"
        f"{'─'*30}\n"
        f"<b>📦 VOLUME</b>\n"
        f"   Vol Now    : {s['vol_now']:,.0f}\n"
        f"   Vol Avg    : {s['vol_avg']:,.0f}\n"
        f"   Rasio      : <b>{vol_ratio_str}</b> 🔥\n"
        f"{'═'*30}\n"
        f"<i>⚡ T1MO Quantitative × Wisdom Signal Bot</i>"
    )
    return msg

# ─────────────────────────────────────────────
#  STARTUP MESSAGE
# ─────────────────────────────────────────────
def send_startup():
    now_wib = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    idx_list = ", ".join([t.replace(".JK","") for t in WATCHLIST_IDX])
    us_list  = ", ".join(WATCHLIST_US)
    msg = (
        f"🤖 <b>ROBOT TRADING AKTIF</b>\n"
        f"{'═'*30}\n"
        f"🕐 {now_wib}\n"
        f"⚡ T1MO Quantitative × Wisdom Signal Bot\n"
        f"{'─'*30}\n"
        f"<b>📋 WATCHLIST</b>\n"
        f"🇮🇩 IDX : {idx_list}\n"
        f"🇺🇸 US  : {us_list}\n"
        f"{'─'*30}\n"
        f"🔄 Scan interval : setiap {SCAN_INTERVAL_SECONDS}s\n"
        f"📊 Data interval : 2m (fallback 5m)\n"
        f"🔔 Signal aktif  : Akumulasi 🟢 | Distribusi 🔴 | Breakout ⚡ | Breakdown 📉\n"
        f"{'═'*30}"
    )
    send_telegram(msg)

# ─────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────
def is_market_open() -> bool:
    """Cek jam trading IDX dan US (WIB)"""
    now = datetime.now(WIB)
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()  # 0=Senin, 6=Minggu

    if weekday >= 5:  # Weekend
        return False

    # IDX: 09:00–16:15 WIB
    # US:  21:30–04:00 WIB (overnight)
    time_now = hour * 60 + minute

    idx_open  = (9 * 60 <= time_now <= 16 * 60 + 15)
    us_open   = (time_now >= 21 * 60 + 30) or (time_now <= 4 * 60)

    return idx_open or us_open

def main():
    log.info("═" * 50)
    log.info("  IDX SCREENER — T1MO x WISDOM QUANTITATIVE BOT")
    log.info("═" * 50)
    log.info(f"  IDX Watchlist : {', '.join(WATCHLIST_IDX)}")
    log.info(f"  US Watchlist  : {', '.join(WATCHLIST_US)}")
    log.info(f"  Scan interval : {SCAN_INTERVAL_SECONDS}s")
    log.info("═" * 50)

    send_startup()

    # Track sinyal yang sudah dikirim (cooldown 30 menit per ticker)
    signal_cooldown = {}  # ticker -> last signal timestamp

    scan_count = 0

    while True:
        try:
            now = datetime.now(WIB)
            scan_count += 1
            log.info(f"[SCAN #{scan_count}] {now.strftime('%H:%M:%S WIB')} — scanning {len(ALL_TICKERS)} tickers...")

            if not is_market_open():
                log.info("⏸  Market tutup. Menunggu 5 menit...")
                time.sleep(300)
                continue

            signals_found = 0

            for ticker in ALL_TICKERS:
                try:
                    # Cooldown check: jangan spam sinyal sama dalam 30 menit
                    last_sent = signal_cooldown.get(ticker, 0)
                    if time.time() - last_sent < 1800:
                        continue

                    df = fetch_data(ticker, interval="2m", period="5d")
                    if df is None:
                        log.warning(f"[{ticker}] Skip — data tidak tersedia")
                        continue

                    result = detect_signals(ticker, df)

                    if result:
                        signals_found += 1
                        log.info(f"[{ticker}] *** {result['signal_label']} *** @ {result['price']:.2f}")
                        msg = format_message(result)
                        send_telegram(msg)
                        signal_cooldown[ticker] = time.time()

                    # Jeda antar ticker untuk hindari rate limit
                    time.sleep(1.5)

                except Exception as e:
                    log.error(f"[{ticker}] Error: {e}")
                    time.sleep(2)
                    continue

            log.info(f"[SCAN #{scan_count}] Selesai. Sinyal ditemukan: {signals_found}")

            # Summary tiap 30 scan (1 jam)
            if scan_count % 30 == 0:
                now_str = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
                send_telegram(
                    f"📡 <b>HEARTBEAT</b> — Robot masih aktif\n"
                    f"🕐 {now_str}\n"
                    f"🔄 Total scan: {scan_count}\n"
                    f"⚡ T1MO Quantitative × Wisdom Signal Bot"
                )

        except KeyboardInterrupt:
            log.info("Robot dihentikan manual.")
            break
        except Exception as e:
            log.error(f"Main loop error: {e}")

        time.sleep(SCAN_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
