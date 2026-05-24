import os,threading,logging,io,json
import yfinance as yf
import pandas as pd
import numpy as np
try:
    from scipy import stats as sp_stats
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from datetime import datetime,time as dtime
import pytz
from flask import Flask,send_file,jsonify
from telegram import Update,Bot
from telegram.ext import Application,CommandHandler,ContextTypes,JobQueue
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s",level=logging.INFO)
log=logging.getLogger(__name__)
TOKEN=os.environ.get("TELEGRAM_TOKEN","")
PORT=int(os.environ.get("PORT",8080))
WIB=pytz.timezone("Asia/Jakarta")

# ══ TF MAP ══
TF_MAP={"5M":("5m","5d"),"15M":("15m","5d"),"30M":("30m","10d"),
        "1H":("60m","60d"),"4H":("60m","60d"),"D":("1d","1y"),
        "W":("1wk","5y"),"M":("1mo","10y")}

# ══ STOCK LISTS ══
IDX_STOCKS=["ADMR","ENRG","ANTM","NCKL","MBMA","PTBA","MEDC","BULL","TMAS","INCO",
            "MDKA","ITMG","AALI","TAPG","ELSA","SMDR","ADRO","INDY","BSSR","RAJA",
            "DEWA","DSNG","GOTO","TLKM","BBRI","BBCA","BMRI","PGAS","BYAN","HRUM",
            "FIRE","TINS","ZINC","KIJA","LSIP","SSMS","SLIS","NFCX","CUAN","NICK",
            "PTRO","BSBK","PACK","TPIA","EMTK","FILM","ACES","MAPA","MTEL","ISAT"]

# ✅ FIX: Semua 50 US stocks discan (sebelumnya [:30])
US_STOCKS=["PLTR","MU","NVDA","AAPL","TSLA","AMD","META","GOOGL","MSFT","AMZN",
           "INTC","TSM","ASML","BABA","JD","NIO","SMCI","ARM","AVGO","QCOM",
           "SPY","QQQ","MARA","CLSK","RIOT","MELI","SHOP","SQ","PYPL","SNAP",
           "UBER","LYFT","ABNB","NET","DDOG","SNOW","ZM","CRWD","PANW","OKTA",
           "APP","MSTR","COIN","SOFI","HOOD","RKLB","IONQ","QUBT","RGTI","JOBY",
           "SNDK","MU","AMAT","LRCX","KLAC","MRVL","NXPI","ON","STM","TXN"]

# ══ MARKET HOURS ══
def is_idx_market_open():
    now=datetime.now(WIB)
    if now.weekday()>=5: return False
    t=now.time()
    return dtime(9,0)<=t<=dtime(15,15)

def is_us_market_open():
    now=datetime.now(WIB)
    if now.weekday()>=5: return False
    t=now.time()
    return t>=dtime(21,30) or t<=dtime(4,0)

def is_weekday():
    return datetime.now(WIB).weekday()<5

# ══ LOW LIQUIDITY FILTER ══
IDX_MIN_AVG_VOLUME = 500_000
IDX_MIN_PRICE = 100

def is_liquid_stock(avg_vol, price):
    return avg_vol >= IDX_MIN_AVG_VOLUME and price >= IDX_MIN_PRICE

# ══ PERSISTENT STORAGE ══
ALERT_FILE="/tmp/alerts.json"
WL_FILE="/tmp/watchlist.json"
AUTO_FILE="/tmp/auto_users.json"

def load_json(f):
    try:
        if os.path.exists(f):
            with open(f) as fp: return json.load(fp)
    except: pass
    return {}

def save_json(f,data):
    try:
        with open(f,"w") as fp: json.dump(data,fp)
    except: pass

alerts_db=load_json(ALERT_FILE)
watchlist_db=load_json(WL_FILE)
auto_users=load_json(AUTO_FILE)

# == FLIP ALERT STATE ==
FLIP_FILE="/tmp/flip_state.json"
flip_state_db=load_json(FLIP_FILE)

def get_trend_state(code, tf="D"):
    try:
        r=get_signal(code, tf)
        if "error" in r: return None
        c=float(r["df"]["Close"].squeeze().iloc[-1])
        e9v=float(r["ema9"].iloc[-1]); e20v=float(r["ema20"].iloc[-1]); e50v=float(r["ema50"].iloc[-1])
        if c>e9v and e9v>e20v and e20v>e50v: return "bull"
        elif c<e9v and e9v<e20v and e20v<e50v: return "bear"
        else: return "neutral"
    except: return None

# ══ INDICATORS ══
def ema(s,p): return s.ewm(span=p,adjust=False).mean()
def rsi(s,p=14):
    d=s.diff(); g=d.clip(lower=0).rolling(p).mean(); l=(-d.clip(upper=0)).rolling(p).mean()
    rs=g/l; return 100-(100/(1+rs))
def macd(s):
    m=ema(s,12)-ema(s,26); sg=ema(m,9); return m,sg,m-sg
def stoch(h,l,c,k=15,d=3):
    lo=l.rolling(k).min(); hi=h.rolling(k).max()
    K=100*(c-lo)/(hi-lo); return K,K.rolling(d).mean()

def get_ticker(code):
    code=code.upper().replace(".JK","").replace("-","")
    if code in US_STOCKS: return code
    return f"{code}.JK"

# ✅ FIX: Cache data agar tidak fetch ulang dalam 5 menit
_data_cache = {}
_cache_ttl = 300  # 5 menit

def get_cached_data(ticker, interval, period):
    """Return cached yfinance data kalau masih fresh"""
    key = f"{ticker}_{interval}_{period}"
    now = datetime.now().timestamp()
    if key in _data_cache:
        ts, df = _data_cache[key]
        if now - ts < _cache_ttl:
            return df
    try:
        df = yf.download(ticker, period=period, interval=interval,
                        progress=False, auto_adjust=True)
        _data_cache[key] = (now, df)
        return df
    except:
        return pd.DataFrame()

def get_signal(code,tf="D"):
    iv,per=TF_MAP.get(tf.upper(),("1d","1y"))
    ticker=get_ticker(code)
    try:
        # ✅ FIX: Pakai cache
        df = get_cached_data(ticker, iv, per)
        if (df.empty or len(df)<26) and ticker.endswith(".JK"):
            ticker=code.upper()
            df = get_cached_data(ticker, iv, per)
        if df.empty or len(df)<26: return{"error":"Data kurang"}
        c=df["Close"].squeeze(); h=df["High"].squeeze()
        l=df["Low"].squeeze(); v=df["Volume"].squeeze()
        e9=ema(c,9); e20=ema(c,20); e50=ema(c,50)
        r=rsi(c); ml,sg,hs=macd(c); sk,sd=stoch(h,l,c)
        lc=float(c.iloc[-1]); pc=float(c.iloc[-2])
        le9=float(e9.iloc[-1]); le20=float(e20.iloc[-1]); le50=float(e50.iloc[-1])
        lr=float(r.iloc[-1]); lm=float(ml.iloc[-1]); ls=float(sg.iloc[-1])
        lh=float(hs.iloc[-1]); ph=float(hs.iloc[-2]); lsk=float(sk.iloc[-1])
        lv=float(v.iloc[-1]); av=float(v.tail(20).mean()); vr=lv/av if av>0 else 1
        chg=(lc-pc)/pc*100; sigs=[]; sc=0
        if lc>le9>le20>le50: sigs.append("🦅 HAWK1 - EMA Stack Bullish"); sc+=3
        elif lc>le20>le50: sigs.append("🟢 GREEN BULL - Di atas MA20&50"); sc+=2
        elif lc>le9 and le9>le20: sigs.append("⬆ BREAK TOP - EMA9 cross MA20"); sc+=2
        if lm>ls and ph<0 and lh>0: sigs.append("🔵 MACD Golden Cross"); sc+=2
        elif lm>ls and lh>0: sigs.append("🔵 MACD Positif"); sc+=1
        if 50<lr<70: sigs.append(f"💪 RSI Kuat ({lr:.1f})"); sc+=1
        elif lr<30: sigs.append(f"🔄 RSI Oversold ({lr:.1f})"); sc+=1
        elif lr>70: sigs.append(f"⚠️ RSI Overbought ({lr:.1f})"); sc-=1
        if vr>2: sigs.append(f"🌊 BUY LAUTAN - Volume {vr:.1f}x"); sc+=2
        elif vr>1.5: sigs.append(f"📈 Volume {vr:.1f}x avg"); sc+=1
        if lsk<20: sigs.append(f"🟣 BUY MAGENTA - Stoch ({lsk:.1f})"); sc+=1
        elif lsk>80: sigs.append(f"⚠️ Stoch OB ({lsk:.1f})")
        trend="UPTREND ⬆" if lc>le50 else "DOWNTREND ⬇" if lc<le50 else "SIDEWAYS ↔"
        is_idx = ticker.endswith(".JK")
        liquid = is_liquid_stock(av, lc) if is_idx else True
        liquidity_tag = "" if liquid else "⚠️ LOW LIQUIDITY"
        return{"code":code.upper(),"ticker":ticker,"tf":tf.upper(),"price":lc,"chg":chg,
               "e9":le9,"e20":le20,"e50":le50,"rsi":lr,"macd":lm,"msig":ls,"stoch":lsk,
               "vr":vr,"vol":lv,"avg_vol":av,"sigs":sigs,"score":sc,"trend":trend,
               "liquid":liquid,"liquidity_tag":liquidity_tag,
               "df":df,"ema9":e9,"ema20":e20,"ema50":e50,"rsi_s":r,
               "macd_l":ml,"macd_sg":sg,"macd_h":hs,"stoch_k":sk,"stoch_d":sd}
    except Exception as e: return{"error":str(e)}

# ══ VOLUME SPIKE DETECTION ══
def detect_volume_spike(code, tf="5M", threshold=2.0):
    r=get_signal(code, tf)
    if "error" in r: return None
    if r["vr"]>=threshold:
        direction="BUY" if r["chg"]>=0 else "SELL"
        return{"code":code,"price":r["price"],"chg":r["chg"],"vr":r["vr"],
               "direction":direction,"liquid":r.get("liquid",True),"r":r}
    return None

# ✅ FIX: Parallel scan pakai ThreadPoolExecutor
def parallel_scan(stock_list, tf="5M", threshold=2.5, max_workers=10):
    """Scan semua saham secara paralel — jauh lebih cepat dari sequential"""
    spikes = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(detect_volume_spike, code, tf, threshold): code
                   for code in stock_list}
        for future in as_completed(futures):
            try:
                result = future.result(timeout=15)
                if result:
                    spikes.append(result)
            except Exception as e:
                log.warning(f"Parallel scan error {futures[future]}: {e}")
    return spikes

def parallel_signal_scan(stock_list, tf="D", min_score=3, max_workers=10):
    """Scan signal semua saham secara paralel"""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(get_signal, code, tf): code
                   for code in stock_list}
        for future in as_completed(futures):
            try:
                r = future.result(timeout=15)
                if "error" not in r and r["score"] >= min_score:
                    results.append(r)
            except Exception as e:
                log.warning(f"Signal scan error: {e}")
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

# ══ DOJI BULLISH REVERSAL DETECTOR ══
def detect_doji(code, tf="1H"):
    """
    Deteksi Doji Bullish Reversal pada candle terakhir.
    Returns dict dengan detail doji, atau None kalau tidak ada doji.
    Tipe doji: Standard, Dragonfly, Gravestone, Long-legged, Spinning Top
    """
    r = get_signal(code, tf)
    if "error" in r: return None
    df = r["df"]
    if len(df) < 10: return None

    opens  = df["Open"].squeeze().values
    closes = df["Close"].squeeze().values
    highs  = df["High"].squeeze().values
    lows   = df["Low"].squeeze().values

    # Pakai 3 candle terakhir: [-3] prev-prev, [-2] prev, [-1] latest
    i = -1
    o, c_, h_, l_ = opens[i], closes[i], highs[i], lows[i]
    total_range = h_ - l_
    if total_range == 0: return None

    body = abs(c_ - o)
    upper_wick = h_ - max(o, c_)
    lower_wick = min(o, c_) - l_

    body_ratio  = body / total_range
    upper_ratio = upper_wick / total_range
    lower_ratio = lower_wick / total_range

    # ── Klasifikasi tipe doji ──
    doji_type = None
    doji_emoji = ""

    # Dragonfly Doji: body kecil di atas, lower wick panjang (bullish reversal kuat)
    if body_ratio < 0.12 and lower_ratio >= 0.55 and upper_ratio < 0.20:
        doji_type  = "Dragonfly Doji"
        doji_emoji = "🐉"

    # Gravestone Doji: body kecil di bawah, upper wick panjang (bearish, skip)
    elif body_ratio < 0.12 and upper_ratio >= 0.55 and lower_ratio < 0.20:
        return None  # bearish reversal, skip

    # Long-legged Doji: wick panjang di kedua sisi
    elif body_ratio < 0.15 and lower_ratio >= 0.30 and upper_ratio >= 0.25:
        doji_type  = "Long-legged Doji"
        doji_emoji = "🦵"

    # Standard Doji: body sangat kecil
    elif body_ratio < 0.10:
        doji_type  = "Standard Doji"
        doji_emoji = "⊕"

    # Spinning Top: body sedikit lebih besar tapi wick dominan
    elif body_ratio < 0.25 and lower_ratio >= 0.35:
        doji_type  = "Spinning Top"
        doji_emoji = "🌀"

    if doji_type is None: return None

    # ── Deteksi Hammer (bukan doji tapi reversal kuat) ──
    if doji_type is None:
        if body > 0 and lower_wick >= 2*body and upper_wick <= 0.5*body and c_ >= o:
            doji_type  = "Hammer 🔨"
            doji_emoji = "🔨"

    if doji_type is None: return None

    rsi_val   = r["rsi"]
    stoch_val = r["stoch"]
    price     = r["price"]
    e20       = r["e20"]
    e50       = r["e50"]

    bullish_score = 0
    bullish_factors = []

    # Bullish Engulfing check
    prev_o = opens[-2]; prev_c = closes[-2]
    if (c_ > o) and (o <= prev_c) and (c_ >= prev_o) and (prev_c < prev_o):
        bullish_score += 2
        bullish_factors.append("Bullish Engulfing ✨")

    # Hammer bonus
    if "Hammer" in doji_type:
        bullish_score += 1
        bullish_factors.append("Hammer = reversal kuat")

    # RSI oversold
    if rsi_val < 35:
        bullish_score += 2
        bullish_factors.append(f"RSI Oversold ({rsi_val:.0f})")
    elif rsi_val < 45:
        bullish_score += 1
        bullish_factors.append(f"RSI Lemah ({rsi_val:.0f})")

    # Stoch oversold
    if stoch_val < 20:
        bullish_score += 2
        bullish_factors.append(f"Stoch OS ({stoch_val:.0f})")
    elif stoch_val < 35:
        bullish_score += 1
        bullish_factors.append(f"Stoch Lemah ({stoch_val:.0f})")

    # Harga dekat / di bawah MA20 (potential support bounce)
    if price <= e20 * 1.01:
        bullish_score += 1
        bullish_factors.append("Dekat/Di bawah MA20")

    # Harga dekat / di bawah MA50
    if price <= e50 * 1.01:
        bullish_score += 1
        bullish_factors.append("Dekat/Di bawah MA50")

    # Dragonfly bonus
    if doji_type == "Dragonfly Doji":
        bullish_score += 1
        bullish_factors.append("Dragonfly = reversal kuat")

    # Sebelumnya downtrend (2 candle sebelum merah)
    prev_bearish = closes[-2] < opens[-2] and closes[-3] < opens[-3]
    if prev_bearish:
        bullish_score += 1
        bullish_factors.append("Sebelumnya downtrend")

    if bullish_score < 2: return None  # filter: harus ada minimal 2 faktor

    return {
        "code":          code,
        "tf":            tf,
        "doji_type":     doji_type,
        "doji_emoji":    doji_emoji,
        "price":         price,
        "chg":           r["chg"],
        "rsi":           rsi_val,
        "stoch":         stoch_val,
        "e20":           e20,
        "e50":           e50,
        "bull_score":    bullish_score,
        "bull_factors":  bullish_factors,
        "body_ratio":    body_ratio,
        "lower_wick_r":  lower_ratio,
        "upper_wick_r":  upper_ratio,
        "liquid":        r.get("liquid", True),
        "ticker":        r["ticker"],
    }

def doji_screener_tf(stock_list, tf, max_workers=10):
    """Scan doji bullish reversal untuk satu TF secara paralel"""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(detect_doji, code, tf): code for code in stock_list}
        for future in as_completed(futures):
            try:
                res = future.result(timeout=20)
                if res: results.append(res)
            except Exception as e:
                log.warning(f"Doji scan error: {e}")
    results.sort(key=lambda x: x["bull_score"], reverse=True)
    return results

def doji_scan_all_tf(stock_list):
    """Scan doji bullish reversal di 3 TF sekaligus: 1H, 4H, D"""
    all_results = {}
    for tf in ["1H", "4H", "D"]:
        all_results[tf] = doji_screener_tf(stock_list, tf)
    return all_results

def fmt_doji_msg(results_by_tf, market_name="IDX"):
    """Format pesan Telegram untuk doji screener hasil"""
    now_str = datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
    lines = [f"🕯 *DOJI BULLISH REVERSAL SCAN — {market_name}*", f"🕐 {now_str}",
             "━━━━━━━━━━━━━━━━━━━━"]
    total_found = 0
    for tf in ["1H", "4H", "D"]:
        hits = results_by_tf.get(tf, [])
        if not hits: continue
        tf_label = {"1H": "1 JAM", "4H": "4 JAM", "D": "HARIAN"}[tf]
        lines.append(f"\n⏱ *TF {tf_label}:* ({len(hits)} saham)")
        for h in hits[:6]:
            is_idr = h["ticker"].endswith(".JK")
            px = f"Rp {h['price']:,.0f}" if is_idr else f"${h['price']:,.2f}"
            liq = " ⚠️" if not h["liquid"] else ""
            facs = " | ".join(h["bull_factors"][:2])
            lines.append(
                f"  {h['doji_emoji']} *{h['code']}* `{px}` {h['chg']:+.2f}%\n"
                f"    ↳ {h['doji_type']} | BullScore:`{h['bull_score']}` | {facs}{liq}"
            )
        total_found += len(hits)
    if total_found == 0:
        lines.append("✅ Tidak ada doji bullish reversal terdeteksi saat ini.")
    lines += ["━━━━━━━━━━━━━━━━━━━━",
              "💡 Doji = candle ketidakpastian → potensi reversal naik",
              "⚠️ Selalu konfirmasi dengan candle berikutnya!",
              f"⏱ {now_str}"]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
# PATTERN DETECTION: Trendline, Triangle, Cup&Handle, Double Bottom
# ══════════════════════════════════════════════════════════════

def detect_trendlines(highs, lows, n=30):
    """
    Deteksi upper & lower trendline dari pivot high/low.
    Return dict dengan slope, intercept, dan titik-titik garis.
    """
    from scipy import stats as sp_stats

    # Ambil n candle terakhir
    h = highs[-n:]
    l = lows[-n:]
    x = np.arange(len(h))

    # Pivot High: local maxima (window 3)
    ph_idx = [i for i in range(1, len(h)-1) if h[i] >= h[i-1] and h[i] >= h[i+1]]
    # Pivot Low: local minima (window 3)
    pl_idx = [i for i in range(1, len(l)-1) if l[i] <= l[i-1] and l[i] <= l[i+1]]

    result = {}

    # Upper trendline (pivot highs)
    if len(ph_idx) >= 2:
        px = np.array(ph_idx); py = np.array([h[i] for i in ph_idx])
        slope, intercept, _, _, _ = sp_stats.linregress(px, py)
        result["upper"] = {"slope": slope, "intercept": intercept,
                           "x0": 0, "x1": len(h)-1,
                           "y0": intercept, "y1": slope*(len(h)-1)+intercept,
                           "pivot_x": ph_idx, "pivot_y": [h[i] for i in ph_idx]}

    # Lower trendline (pivot lows)
    if len(pl_idx) >= 2:
        px = np.array(pl_idx); py = np.array([l[i] for i in pl_idx])
        slope, intercept, _, _, _ = sp_stats.linregress(px, py)
        result["lower"] = {"slope": slope, "intercept": intercept,
                           "x0": 0, "x1": len(l)-1,
                           "y0": intercept, "y1": slope*(len(l)-1)+intercept,
                           "pivot_x": pl_idx, "pivot_y": [l[i] for i in pl_idx]}

    return result

def detect_triangle(highs, lows, closes, n=40):
    """
    Deteksi pola triangle: Ascending, Descending, Symmetrical.
    Return: dict {type, quality, upper_line, lower_line, apex_x} atau None
    """
    try:
        from scipy import stats as sp_stats
        h = highs[-n:]; l = lows[-n:]; c = closes[-n:]
        x = np.arange(len(h))

        ph_idx = [i for i in range(1, len(h)-1) if h[i] >= h[i-1] and h[i] >= h[i+1]]
        pl_idx = [i for i in range(1, len(l)-1) if l[i] <= l[i-1] and l[i] <= l[i+1]]

        if len(ph_idx) < 2 or len(pl_idx) < 2: return None

        # Fit trendlines
        ux = np.array(ph_idx); uy = np.array([h[i] for i in ph_idx])
        lx = np.array(pl_idx); ly = np.array([l[i] for i in pl_idx])

        us, ui, _, _, _ = sp_stats.linregress(ux, uy)
        ls, li, _, _, _ = sp_stats.linregress(lx, ly)

        # Apex: titik pertemuan dua garis
        if abs(us - ls) < 1e-8: return None
        apex_x = (li - ui) / (us - ls)

        # Pattern classification
        asc_threshold = 0.0005 * float(np.mean(h))
        if abs(us) <= asc_threshold and ls > asc_threshold:
            pat_type = "Ascending Triangle 📐"
            quality = "BULLISH BREAKOUT"
        elif us < -asc_threshold and abs(ls) <= asc_threshold:
            pat_type = "Descending Triangle 📐"
            quality = "BEARISH BREAKDOWN"
        elif us < -asc_threshold and ls > asc_threshold:
            pat_type = "Symmetrical Triangle 🔺"
            quality = "BREAKOUT PENDING"
        else:
            return None

        # Cek apakah apex masih di depan (belum expired)
        if apex_x < len(h) * 0.7: return None  # terlalu jauh ke belakang

        return {
            "type": pat_type,
            "quality": quality,
            "upper_slope": us, "upper_intercept": ui,
            "lower_slope": ls, "lower_intercept": li,
            "apex_x": apex_x,
            "n_used": n,
            "ph_idx": ph_idx, "ph_y": [h[i] for i in ph_idx],
            "pl_idx": pl_idx, "pl_y": [l[i] for i in pl_idx],
        }
    except:
        return None

def detect_double_bottom(lows, closes, n=60):
    """
    Deteksi Double Bottom (W pattern).
    Return: dict {bottom1_x, bottom2_x, neckline, depth_pct, confirmed} atau None
    """
    try:
        l = lows[-n:]; c = closes[-n:]

        # Cari local minima yang dalam
        pl_idx = []
        for i in range(2, len(l)-2):
            if (l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]):
                pl_idx.append(i)

        if len(pl_idx) < 2: return None

        # Ambil 2 bottom terdalam dari semua pivot low
        sorted_pl = sorted(pl_idx, key=lambda i: l[i])[:4]
        sorted_pl.sort()  # sorted by position

        best = None
        for i in range(len(sorted_pl)-1):
            b1 = sorted_pl[i]; b2 = sorted_pl[i+1]
            if b2 - b1 < 5: continue  # terlalu berdekatan

            y1 = l[b1]; y2 = l[b2]
            # Bottom harus hampir sama tingginya (max 3% beda)
            if abs(y1-y2)/max(y1,y2) > 0.03: continue

            # Cari neckline: max harga antara 2 bottom
            neckline = float(max(c[b1:b2+1]))
            depth_pct = (neckline - min(y1,y2)) / neckline * 100

            if depth_pct < 3: continue  # terlalu dangkal

            # Cek apakah harga sekarang sudah break neckline (konfirmasi)
            current_price = float(c[-1])
            confirmed = current_price >= neckline * 0.99

            if best is None or depth_pct > best["depth_pct"]:
                best = {
                    "bottom1_x": b1, "bottom2_x": b2,
                    "bottom1_y": y1, "bottom2_y": y2,
                    "neckline": neckline, "depth_pct": depth_pct,
                    "confirmed": confirmed, "n_used": n
                }

        return best
    except:
        return None

def detect_cup_and_handle(closes, highs, lows, n=60):
    """
    Deteksi Cup & Handle pattern.
    Ciri: rounding bottom (cup) diikuti konsolidasi kecil (handle),
    lalu potensi breakout.
    Return: dict {cup_left, cup_bottom, cup_right, handle_low, breakout_level, quality} atau None
    """
    try:
        c = closes[-n:]; h = highs[-n:]; l = lows[-n:]
        x = np.arange(len(c))

        if len(c) < 20: return None

        # Cari high tertinggi di sepertiga awal (cup left rim)
        left_third = len(c) // 3
        cup_left_x = int(np.argmax(c[:left_third]))
        cup_left_y = float(c[cup_left_x])

        # Cari bottom cup (terendah di tengah)
        mid_start = left_third; mid_end = 2 * left_third
        cup_bottom_x = int(np.argmin(c[mid_start:mid_end])) + mid_start
        cup_bottom_y = float(c[cup_bottom_x])

        # Cari right rim (high mirip cup_left di sepertiga akhir)
        right_third_start = 2 * left_third
        cup_right_x = int(np.argmax(c[right_third_start:])) + right_third_start
        cup_right_y = float(c[cup_right_x])

        # Validasi: right rim harus mendekati left rim (max 5% beda)
        if abs(cup_left_y - cup_right_y) / cup_left_y > 0.05: return None

        # Validasi: depth cup harus cukup dalam (min 5%)
        cup_depth = (cup_left_y - cup_bottom_y) / cup_left_y * 100
        if cup_depth < 5: return None

        # Handle: konsolidasi setelah cup_right (10-20% dari sisa data)
        handle_start = cup_right_x
        if handle_start >= len(c) - 3: return None
        handle_segment = c[handle_start:]
        handle_low_x = int(np.argmin(handle_segment)) + handle_start
        handle_low_y = float(c[handle_low_x])

        # Handle tidak boleh turun lebih dari 50% depth cup
        handle_pullback = (cup_right_y - handle_low_y) / cup_right_y * 100
        if handle_pullback > cup_depth * 0.5: return None

        # Breakout level = cup right rim
        breakout_level = cup_right_y
        current_price = float(c[-1])
        confirmed = current_price >= breakout_level * 0.99

        return {
            "cup_left_x": cup_left_x, "cup_left_y": cup_left_y,
            "cup_bottom_x": cup_bottom_x, "cup_bottom_y": cup_bottom_y,
            "cup_right_x": cup_right_x, "cup_right_y": cup_right_y,
            "handle_low_x": handle_low_x, "handle_low_y": handle_low_y,
            "breakout_level": breakout_level,
            "cup_depth_pct": cup_depth,
            "handle_pullback_pct": handle_pullback,
            "confirmed": confirmed,
            "n_used": n
        }
    except:
        return None

def detect_head_and_shoulders(highs, closes, n=60):
    """
    Deteksi Head & Shoulders (bearish reversal) dan
    Inverse Head & Shoulders (bullish reversal).
    Return: dict {type, left_x, head_x, right_x, neckline, confirmed} atau None
    """
    try:
        h = highs[-n:]; c = closes[-n:]

        # Cari pivot high (H&S) dan pivot low (IH&S)
        ph_idx = [i for i in range(2, len(h)-2)
                  if h[i] >= h[i-1] and h[i] >= h[i-2] and h[i] >= h[i+1] and h[i] >= h[i+2]]
        pl_idx = [i for i in range(2, len(c)-2)
                  if c[i] <= c[i-1] and c[i] <= c[i-2] and c[i] <= c[i+1] and c[i] <= c[i+2]]

        # ── HEAD & SHOULDERS (bearish) ──
        best_hs = None
        if len(ph_idx) >= 3:
            for i in range(len(ph_idx)-2):
                ls_x = ph_idx[i]; hd_x = ph_idx[i+1]; rs_x = ph_idx[i+2]
                ls_y = h[ls_x]; hd_y = h[hd_x]; rs_y = h[rs_x]

                # Head harus lebih tinggi dari kedua shoulder
                if not (hd_y > ls_y and hd_y > rs_y): continue
                # Kedua shoulder harus hampir sama (max 5% beda)
                if abs(ls_y - rs_y) / max(ls_y, rs_y) > 0.05: continue
                # Head harus setidaknya 3% lebih tinggi dari shoulder
                if (hd_y - max(ls_y, rs_y)) / hd_y < 0.03: continue

                # Neckline: rata-rata lembah antara L-H dan H-R
                valley1 = float(min(c[ls_x:hd_x+1]))
                valley2 = float(min(c[hd_x:rs_x+1]))
                neckline = (valley1 + valley2) / 2

                current = float(c[-1])
                confirmed = current <= neckline * 1.01  # break below neckline

                if best_hs is None or hd_y > best_hs["head_y"]:
                    best_hs = {
                        "type": "Head & Shoulders 🔻",
                        "signal": "BEARISH REVERSAL",
                        "left_x": ls_x, "head_x": hd_x, "right_x": rs_x,
                        "left_y": ls_y, "head_y": hd_y, "right_y": rs_y,
                        "neckline": neckline, "confirmed": confirmed,
                        "is_inverse": False, "n_used": n
                    }

        # ── INVERSE HEAD & SHOULDERS (bullish) ──
        best_ihs = None
        if len(pl_idx) >= 3:
            for i in range(len(pl_idx)-2):
                ls_x = pl_idx[i]; hd_x = pl_idx[i+1]; rs_x = pl_idx[i+2]
                ls_y = c[ls_x]; hd_y = c[hd_x]; rs_y = c[rs_x]

                # Head harus lebih rendah dari kedua shoulder
                if not (hd_y < ls_y and hd_y < rs_y): continue
                # Kedua shoulder hampir sama (max 5%)
                if abs(ls_y - rs_y) / max(ls_y, rs_y) > 0.05: continue
                # Head harus setidaknya 3% lebih rendah
                if (min(ls_y, rs_y) - hd_y) / min(ls_y, rs_y) < 0.03: continue

                peak1 = float(max(h[ls_x:hd_x+1]))
                peak2 = float(max(h[hd_x:rs_x+1]))
                neckline = (peak1 + peak2) / 2

                current = float(c[-1])
                confirmed = current >= neckline * 0.99  # break above neckline

                if best_ihs is None or hd_y < best_ihs["head_y"]:
                    best_ihs = {
                        "type": "Inv. Head & Shoulders 🔺",
                        "signal": "BULLISH REVERSAL",
                        "left_x": ls_x, "head_x": hd_x, "right_x": rs_x,
                        "left_y": ls_y, "head_y": hd_y, "right_y": rs_y,
                        "neckline": neckline, "confirmed": confirmed,
                        "is_inverse": True, "n_used": n
                    }

        # Return yang paling baru (right shoulder paling kanan)
        candidates = [x for x in [best_hs, best_ihs] if x is not None]
        if not candidates: return None
        return max(candidates, key=lambda x: x["right_x"])
    except:
        return None

def detect_all_patterns(df, n=60):
    """
    Jalankan semua pattern detector sekaligus.
    Return dict: {trendlines, triangle, double_bottom, cup_handle, hs}
    """
    close = df["Close"].squeeze().values[-n:]
    high  = df["High"].squeeze().values[-n:]
    low   = df["Low"].squeeze().values[-n:]

    result = {}
    try: result["trendlines"]    = detect_trendlines(high, low, min(n, 30))
    except: result["trendlines"] = {}
    try: result["triangle"]      = detect_triangle(high, low, close, min(n, 40))
    except: result["triangle"]   = None
    try: result["double_bottom"] = detect_double_bottom(low, close, n)
    except: result["double_bottom"] = None
    try: result["cup_handle"]    = detect_cup_and_handle(close, high, low, n)
    except: result["cup_handle"] = None
    try: result["hs"]            = detect_head_and_shoulders(high, close, n)
    except: result["hs"]         = None

    return result

def fmt_patterns_text(patterns, price_fmt):
    """Format ringkasan pola untuk caption Telegram"""
    lines = []
    tri = patterns.get("triangle")
    if tri:
        lines.append(f"📐 *{tri['type']}* — {tri['quality']}")

    db = patterns.get("double_bottom")
    if db:
        status = "✅ CONFIRMED" if db["confirmed"] else "⏳ FORMING"
        lines.append(f"〰️ *Double Bottom* {status} | Neck:`{price_fmt(db['neckline'])}` Depth:`{db['depth_pct']:.1f}%`")

    ch = patterns.get("cup_handle")
    if ch:
        status = "✅ BREAKOUT" if ch["confirmed"] else "⏳ FORMING"
        lines.append(f"☕ *Cup & Handle* {status} | BO:`{price_fmt(ch['breakout_level'])}` Depth:`{ch['cup_depth_pct']:.1f}%`")

    hs = patterns.get("hs")
    if hs:
        status = "✅ CONFIRMED" if hs["confirmed"] else "⏳ FORMING"
        lines.append(f"{'🔻' if not hs['is_inverse'] else '🔺'} *{hs['type']}* {status} | Neck:`{price_fmt(hs['neckline'])}`")

    return "\n".join(lines) if lines else ""

def pattern_scan_one(code, tf="D"):
    """Scan satu saham untuk semua pattern. Return dict atau None."""
    try:
        r = get_signal(code, tf)
        if "error" in r: return None
        pats = detect_all_patterns(r["df"])
        found = []
        if pats.get("triangle"):      found.append(("triangle",    pats["triangle"]))
        if pats.get("double_bottom"): found.append(("double_bottom", pats["double_bottom"]))
        if pats.get("cup_handle"):    found.append(("cup_handle",  pats["cup_handle"]))
        if pats.get("hs"):            found.append(("hs",          pats["hs"]))
        if not found: return None
        is_idr = r["ticker"].endswith(".JK")
        return {
            "code": code, "ticker": r["ticker"], "price": r["price"],
            "chg": r["chg"], "trend": r["trend"], "score": r["score"],
            "patterns": found, "is_idr": is_idr,
            "liquid": r.get("liquid", True)
        }
    except:
        return None

def parallel_pattern_scan(stock_list, tf="D", max_workers=10):
    """Scan semua saham secara paralel untuk pattern."""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(pattern_scan_one, code, tf): code for code in stock_list}
        for future in as_completed(futures):
            try:
                res = future.result(timeout=15)
                if res: results.append(res)
            except Exception as e:
                log.warning(f"Pattern scan error {futures[future]}: {e}")
    # Sort: confirmed dulu, lalu score tertinggi
    def sort_key(x):
        confirmed = any(
            (p[1].get("confirmed", False) if isinstance(p[1], dict) else False)
            for p in x["patterns"]
        )
        return (0 if confirmed else 1, -x["score"])
    results.sort(key=sort_key)
    return results

# ── BREAKOUT ALERT STATE ──
BREAKOUT_FILE = "/tmp/breakout_state.json"
breakout_state_db = load_json(BREAKOUT_FILE)

def check_pattern_breakout(code, tf="D"):
    """
    Cek apakah ada pattern yang baru saja confirmed (breakout).
    Return list of alert dicts, atau [].
    """
    alerts = []
    try:
        r = get_signal(code, tf)
        if "error" in r: return []
        pats = detect_all_patterns(r["df"])
        is_idr = r["ticker"].endswith(".JK")
        price_fmt = lambda p: f"Rp {p:,.0f}" if is_idr else f"${p:,.2f}"

        db = pats.get("double_bottom")
        if db and db["confirmed"]:
            key = f"{code}_DB"
            if breakout_state_db.get(key) != "confirmed":
                breakout_state_db[key] = "confirmed"
                save_json(BREAKOUT_FILE, breakout_state_db)
                alerts.append({
                    "code": code, "price": r["price"], "is_idr": is_idr,
                    "msg": (f"🚨 *BREAKOUT ALERT!*\n"
                            f"〰️ *Double Bottom CONFIRMED*\n"
                            f"*{r['ticker']}* | `{price_fmt(r['price'])}` `{r['chg']:+.2f}%`\n"
                            f"Neckline: `{price_fmt(db['neckline'])}` | Depth: `{db['depth_pct']:.1f}%`\n"
                            f"Trend: {r['trend']} | Score: `{r['score']}/8`\n"
                            f"⏱ {fmt_now()}")
                })
        elif db and not db["confirmed"]:
            key = f"{code}_DB"
            if key in breakout_state_db: del breakout_state_db[key]; save_json(BREAKOUT_FILE, breakout_state_db)

        ch = pats.get("cup_handle")
        if ch and ch["confirmed"]:
            key = f"{code}_CH"
            if breakout_state_db.get(key) != "confirmed":
                breakout_state_db[key] = "confirmed"
                save_json(BREAKOUT_FILE, breakout_state_db)
                alerts.append({
                    "code": code, "price": r["price"], "is_idr": is_idr,
                    "msg": (f"🚨 *BREAKOUT ALERT!*\n"
                            f"☕ *Cup & Handle BREAKOUT*\n"
                            f"*{r['ticker']}* | `{price_fmt(r['price'])}` `{r['chg']:+.2f}%`\n"
                            f"Breakout Level: `{price_fmt(ch['breakout_level'])}` | Depth: `{ch['cup_depth_pct']:.1f}%`\n"
                            f"Trend: {r['trend']} | Score: `{r['score']}/8`\n"
                            f"⏱ {fmt_now()}")
                })
        elif ch and not ch["confirmed"]:
            key = f"{code}_CH"
            if key in breakout_state_db: del breakout_state_db[key]; save_json(BREAKOUT_FILE, breakout_state_db)

        hs = pats.get("hs")
        if hs and hs["confirmed"]:
            key = f"{code}_HS"
            if breakout_state_db.get(key) != "confirmed":
                breakout_state_db[key] = "confirmed"
                save_json(BREAKOUT_FILE, breakout_state_db)
                emoji = "🔺" if hs["is_inverse"] else "🔻"
                alerts.append({
                    "code": code, "price": r["price"], "is_idr": is_idr,
                    "msg": (f"🚨 *BREAKOUT ALERT!*\n"
                            f"{emoji} *{hs['type']} CONFIRMED*\n"
                            f"*{r['ticker']}* | `{price_fmt(r['price'])}` `{r['chg']:+.2f}%`\n"
                            f"Neckline: `{price_fmt(hs['neckline'])}`\n"
                            f"Signal: {hs['signal']} | Score: `{r['score']}/8`\n"
                            f"⏱ {fmt_now()}")
                })
        elif hs and not hs["confirmed"]:
            key = f"{code}_HS"
            if key in breakout_state_db: del breakout_state_db[key]; save_json(BREAKOUT_FILE, breakout_state_db)

    except Exception as e:
        log.warning(f"Breakout check error {code}: {e}")
    return alerts


# ══ CHART GENERATOR ══
def generate_chart(code, tf="D", volume_spikes=None):
    r=get_signal(code,tf)
    if "error" in r: return None,r["error"]
    df=r["df"]; close=df["Close"].squeeze(); high=df["High"].squeeze()
    low=df["Low"].squeeze(); vol=df["Volume"].squeeze()
    n=min(len(df),80); df=df.iloc[-n:]; close=close.iloc[-n:]
    high=high.iloc[-n:]; low=low.iloc[-n:]; vol=vol.iloc[-n:]
    e9=r["ema9"].iloc[-n:]; e20=r["ema20"].iloc[-n:]; e50=r["ema50"].iloc[-n:]
    rsi_s=r["rsi_s"].iloc[-n:]; macd_l=r["macd_l"].iloc[-n:]
    macd_sg=r["macd_sg"].iloc[-n:]; macd_h=r["macd_h"].iloc[-n:]
    sk=r["stoch_k"].iloc[-n:]; sd=r["stoch_d"].iloc[-n:]
    idx=range(n)

    # ══ WHITE TRADINGVIEW THEME ══
    BG="#ffffff"        # pure white background
    BG2="#f8f9fa"       # panel background (very light gray)
    GRID="#e0e3eb"      # grid lines (light gray)
    GREEN="#089981"     # TradingView green
    RED="#f23645"       # TradingView red
    ORANGE="#ef6c00"    # MA20 orange
    BLUE="#1976d2"      # MA50 blue
    PINK="#9c27b0"      # MA9 purple
    TEXT="#131722"      # dark text
    TEXT2="#555f6d"     # secondary text
    MID_GREEN="#1b5e20" # vol spike buy
    MID_RED="#b71c1c"   # vol spike sell
    GRAY="#cfd8dc"      # neutral pixel

    opens=df["Open"].squeeze().values; closes=close.values
    highs=high.values; lows=low.values; vols=vol.values
    e9v=e9.values; e20v=e20.values; e50v=e50.values
    rsi_v=rsi_s.values; macd_v=macd_l.values; macd_sig_v=macd_sg.values
    avg_v=np.mean(vols)

    # ══ 3-ROW PIXEL DATA ══
    # Row 1: Trend (EMA stack)
    trend_vals=[]
    for i in range(n):
        c_=closes[i]; e9_=e9v[i]; e20_=e20v[i]; e50_=e50v[i]
        if c_>e9_ and e9_>e20_ and e20_>e50_: trend_vals.append(3)
        elif c_>e20_ and e20_>e50_:           trend_vals.append(2)
        elif c_>e50_:                          trend_vals.append(1)
        elif c_<e9_ and e9_<e20_ and e20_<e50_:trend_vals.append(-3)
        elif c_<e20_ and e20_<e50_:            trend_vals.append(-2)
        elif c_<e50_:                          trend_vals.append(-1)
        else:                                  trend_vals.append(0)

    flip_up=[]; flip_dn=[]
    for i in range(1,n):
        if trend_vals[i-1]<=0 and trend_vals[i]>0: flip_up.append(i)
        elif trend_vals[i-1]>=0 and trend_vals[i]<0: flip_dn.append(i)

    # Row 2: Momentum (MACD + RSI)
    momentum_vals=[]
    for i in range(n):
        m_=macd_v[i]; ms_=macd_sig_v[i]; rsi_=rsi_v[i]
        score=0
        if m_>ms_: score+=1
        if m_>0:   score+=1
        if 50<rsi_<70: score+=1
        elif rsi_>=70: score-=1
        elif rsi_<30:  score+=1
        if m_<ms_: score-=1
        momentum_vals.append(max(-3,min(3,score)))

    # Row 3: Volume spike
    vol_vals=[]
    for i in range(n):
        vr_=vols[i]/avg_v if avg_v>0 else 1
        is_buy=closes[i]>=opens[i]
        if vr_>=2.5:   vol_vals.append(3 if is_buy else -3)
        elif vr_>=2.0: vol_vals.append(2 if is_buy else -2)
        elif vr_>=1.5: vol_vals.append(1 if is_buy else -1)
        else:          vol_vals.append(0)

    # ══ LAYOUT ══
    fig=plt.figure(figsize=(14,11),facecolor=BG)
    gs=GridSpec(7,1,figure=fig,height_ratios=[5,1.0,1.0,1.0,0.28,0.28,0.28],hspace=0.04)
    ax1=fig.add_subplot(gs[0]); ax2=fig.add_subplot(gs[1])
    ax3=fig.add_subplot(gs[2]); ax4=fig.add_subplot(gs[3])
    ax_p1=fig.add_subplot(gs[4]); ax_p2=fig.add_subplot(gs[5]); ax_p3=fig.add_subplot(gs[6])

    for ax in [ax1,ax2,ax3,ax4]:
        ax.set_facecolor(BG2)
        ax.tick_params(colors=TEXT2,labelsize=7)
        for s in ax.spines.values(): s.set_color(GRID)
        ax.grid(True,color=GRID,linewidth=0.5,alpha=0.8)

    for ax in [ax_p1,ax_p2,ax_p3]:
        ax.set_facecolor("#f0f2f5")
        for s in ax.spines.values(): s.set_color(GRID)
        ax.set_yticks([]); ax.grid(False)

    # ══ CANDLESTICKS ══
    for i in idx:
        o,c_,h_,l_=opens[i],closes[i],highs[i],lows[i]
        color=GREEN if c_>=o else RED
        ax1.plot([i,i],[l_,h_],color=color,linewidth=0.8,zorder=2)
        rect_color=GREEN if c_>=o else RED
        edge_color=GREEN if c_>=o else RED
        ax1.bar(i,abs(c_-o),bottom=min(o,c_),color=rect_color,
                edgecolor=edge_color,linewidth=0.3,width=0.7,zorder=3)

    # ══ VOLUME SPIKE ARROWS ══
    for i in idx:
        vr_i=vols[i]/avg_v if avg_v>0 else 1
        if vr_i>=2.0:
            is_buy=closes[i]>=opens[i]
            arr_color=MID_GREEN if is_buy else MID_RED
            y_pos=lows[i]*0.998 if is_buy else highs[i]*1.002
            offset=-abs(highs[i]-lows[i])*2 if is_buy else abs(highs[i]-lows[i])*2
            ax1.annotate("",xy=(i,y_pos),xytext=(i,y_pos+offset),
                arrowprops=dict(arrowstyle="->",color=arr_color,lw=2.5),zorder=10)
            ax1.text(i,y_pos+offset*1.3,f"{vr_i:.1f}x",
                    color=arr_color,fontsize=6,ha='center',fontweight='bold')

    # ══ EMAs ══
    ax1.plot(idx,e50v,color=BLUE,linewidth=1.4,label=f"MA50:{r['e50']:,.0f}",zorder=4)
    ax1.plot(idx,e20v,color=ORANGE,linewidth=1.6,label=f"MA20:{r['e20']:,.0f}",zorder=5)
    ax1.plot(idx,e9v,color=PINK,linewidth=1.1,linestyle='--',label=f"MA9:{r['e9']:,.0f}",zorder=6)

    # ══ BOLLINGER BANDS ══
    bb_m=close.rolling(20).mean(); bb_s=close.rolling(20).std()
    bb_u=(bb_m+2*bb_s).iloc[-n:]; bb_l=(bb_m-2*bb_s).iloc[-n:]
    ax1.fill_between(idx,bb_u.values,bb_l.values,alpha=0.05,color=BLUE)
    ax1.plot(idx,bb_u.values,color=BLUE,linewidth=0.5,linestyle=':',alpha=0.4)
    ax1.plot(idx,bb_l.values,color=BLUE,linewidth=0.5,linestyle=':',alpha=0.4)

    # ══ FIBONACCI ══
    swing_high=float(max(highs)); swing_low=float(min(lows)); fib_range=swing_high-swing_low
    is_idr=r["ticker"].endswith(".JK")
    price_fmt=lambda p: f"Rp {p:,.0f}" if is_idr else f"${p:,.2f}"
    fib_levels={"0.0":(swing_high,"#424242","0.0%"),"23.6":(swing_high-0.236*fib_range,"#827717","23.6%"),
                "38.2":(swing_high-0.382*fib_range,"#e65100","38.2%"),"50.0":(swing_high-0.500*fib_range,"#880e4f","50.0%"),
                "61.8":(swing_high-0.618*fib_range,"#1b5e20","61.8% ★"),"78.6":(swing_high-0.786*fib_range,"#0d47a1","78.6%"),
                "100.0":(swing_low,"#b71c1c","100%")}
    fib_styles={"0.0":(0.5,"--"),"23.6":(0.6,"--"),"38.2":(0.8,"-."),"50.0":(0.8,"-."),"61.8":(1.2,"-"),"78.6":(0.8,"-."),"100.0":(0.5,"--")}
    for key,(fval,fcol,flabel) in fib_levels.items():
        lw,ls=fib_styles[key]
        ax1.axhline(fval,color=fcol,linewidth=lw,linestyle=ls,alpha=0.6,zorder=3)
        ax1.text(0.5,fval,f" {flabel}  {price_fmt(fval)}",color=fcol,fontsize=6.5,va='center',alpha=0.9,
                bbox=dict(boxstyle='round,pad=0.15',facecolor=BG,edgecolor=fcol,alpha=0.6,linewidth=0.4))

    lp=closes[-1]; pc_=GREEN if lp>=closes[-2] else RED
    ax1.axhline(lp,color=pc_,linewidth=0.8,linestyle='--',alpha=0.8)
    ax1.text(n-0.5,lp,f" {price_fmt(lp)}",color="white",fontsize=8,fontweight='bold',va='center',
             bbox=dict(boxstyle='round,pad=0.25',facecolor=pc_,edgecolor=pc_,linewidth=0))

    # ══ PATTERN DETECTION & DRAWING ══
    try:
        # Offset x supaya pattern align dengan candle chart
        p_offset = n - 60 if n >= 60 else 0

        # ── TRENDLINES ──
        tl = detect_trendlines(highs, lows, min(n, 30))
        tl_offset = n - min(n, 30)
        if "upper" in tl:
            u = tl["upper"]
            x0 = tl_offset + u["x0"]; x1 = tl_offset + u["x1"]
            ax1.plot([x0, x1],[u["y0"], u["y1"]],
                     color="#f39c12", linewidth=1.4, linestyle='--', alpha=0.85, zorder=7,
                     label="Upper TL")
            # Pivot dots
            for px2, py2 in zip([tl_offset+px3 for px3 in u["pivot_x"]], u["pivot_y"]):
                ax1.scatter(px2, py2, color="#f39c12", s=18, zorder=8, alpha=0.8)

        if "lower" in tl:
            lo2 = tl["lower"]
            x0 = tl_offset + lo2["x0"]; x1 = tl_offset + lo2["x1"]
            ax1.plot([x0, x1],[lo2["y0"], lo2["y1"]],
                     color="#27ae60", linewidth=1.4, linestyle='--', alpha=0.85, zorder=7,
                     label="Lower TL")
            for px2, py2 in zip([tl_offset+px3 for px3 in lo2["pivot_x"]], lo2["pivot_y"]):
                ax1.scatter(px2, py2, color="#27ae60", s=18, zorder=8, alpha=0.8)

        # ── TRIANGLE ──
        tri = detect_triangle(highs, lows, closes, min(n, 40))
        tri_offset = n - min(n, 40)
        if tri:
            xs = np.arange(min(n, 40))
            upper_line = tri["upper_slope"] * xs + tri["upper_intercept"]
            lower_line = tri["lower_slope"] * xs + tri["lower_intercept"]
            xs_plot = xs + tri_offset
            tri_color = "#e74c3c" if "Descending" in tri["type"] else "#2ecc71" if "Ascending" in tri["type"] else "#9b59b6"
            ax1.plot(xs_plot, upper_line, color=tri_color, linewidth=1.8, linestyle='-', alpha=0.75, zorder=7)
            ax1.plot(xs_plot, lower_line, color=tri_color, linewidth=1.8, linestyle='-', alpha=0.75, zorder=7)
            # Shaded area
            ax1.fill_between(xs_plot, lower_line, upper_line, alpha=0.04, color=tri_color, zorder=2)
            # Label
            mid_y = (upper_line[-1] + lower_line[-1]) / 2
            ax1.text(xs_plot[-1]+0.5, mid_y, f" {tri['type'].split()[0]}\n {tri['quality']}",
                     color=tri_color, fontsize=6, va='center', fontweight='bold',
                     bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor=tri_color, alpha=0.7, linewidth=0.7))

        # ── DOUBLE BOTTOM ──
        db = detect_double_bottom(lows, closes, min(n, 60))
        db_offset = n - min(n, 60)
        if db:
            b1x = db_offset + db["bottom1_x"]; b2x = db_offset + db["bottom2_x"]
            # Mark bottoms
            ax1.scatter([b1x, b2x], [db["bottom1_y"], db["bottom2_y"]],
                        color="#3498db", s=50, zorder=9, marker='v', alpha=0.9)
            # Neckline
            ax1.axhline(db["neckline"], color="#3498db", linewidth=1.3,
                        linestyle='-.', alpha=0.8, xmin=b1x/n, zorder=7)
            db_status = "✓ CONFIRMED" if db["confirmed"] else "FORMING"
            ax1.text(b2x+1, db["neckline"]*1.002,
                     f" 〰️ Double Bottom {db_status} | {db['depth_pct']:.1f}%",
                     color="#3498db", fontsize=6.5, va='bottom', fontweight='bold',
                     bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor="#3498db", alpha=0.75, linewidth=0.7))

        # ── CUP & HANDLE ──
        ch = detect_cup_and_handle(closes, highs, lows, min(n, 60))
        ch_offset = n - min(n, 60)
        if ch:
            # Draw cup arc (simplified as polyline through key points)
            cx = [ch_offset + ch["cup_left_x"], ch_offset + ch["cup_bottom_x"], ch_offset + ch["cup_right_x"]]
            cy = [ch["cup_left_y"], ch["cup_bottom_y"], ch["cup_right_y"]]
            ax1.plot(cx, cy, color="#e67e22", linewidth=2.0, linestyle='-', alpha=0.7, zorder=7)
            # Handle
            hx = [ch_offset + ch["cup_right_x"], ch_offset + ch["handle_low_x"]]
            hy = [ch["cup_right_y"], ch["handle_low_y"]]
            ax1.plot(hx, hy, color="#e67e22", linewidth=1.5, linestyle='--', alpha=0.7, zorder=7)
            # Breakout level
            ax1.axhline(ch["breakout_level"], color="#e67e22", linewidth=1.2,
                        linestyle='-.', alpha=0.8, zorder=7)
            ch_status = "☕ BREAKOUT" if ch["confirmed"] else "☕ C&H FORMING"
            ax1.text(ch_offset + ch["cup_right_x"]+1, ch["breakout_level"]*1.002,
                     f" {ch_status} | Depth:{ch['cup_depth_pct']:.1f}%",
                     color="#e67e22", fontsize=6.5, va='bottom', fontweight='bold',
                     bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor="#e67e22", alpha=0.75, linewidth=0.7))
            # Key points markers
            ax1.scatter([ch_offset+ch["cup_left_x"], ch_offset+ch["cup_right_x"]],
                        [ch["cup_left_y"], ch["cup_right_y"]],
                        color="#e67e22", s=30, zorder=9, marker='o', alpha=0.9)
            ax1.scatter([ch_offset+ch["cup_bottom_x"]], [ch["cup_bottom_y"]],
                        color="#e67e22", s=40, zorder=9, marker='v', alpha=0.9)

        # ── HEAD & SHOULDERS ──
        hs = detect_head_and_shoulders(highs, closes, min(n, 60))
        hs_offset = n - min(n, 60)
        if hs:
            hs_color = "#c0392b" if not hs["is_inverse"] else "#27ae60"
            lx = hs_offset + hs["left_x"]
            hx2 = hs_offset + hs["head_x"]
            rx = hs_offset + hs["right_x"]
            # Draw shoulder-head-shoulder lines
            ax1.plot([lx, hx2, rx],
                     [hs["left_y"], hs["head_y"], hs["right_y"]],
                     color=hs_color, linewidth=2.0, linestyle='-', alpha=0.75, zorder=7)
            # Neckline
            ax1.axhline(hs["neckline"], color=hs_color, linewidth=1.3,
                        linestyle='-.', alpha=0.8, zorder=7)
            # Markers: L, H, R
            ax1.scatter([lx, rx], [hs["left_y"], hs["right_y"]],
                        color=hs_color, s=35, zorder=9, marker='o', alpha=0.9)
            ax1.scatter([hx2], [hs["head_y"]],
                        color=hs_color, s=55, zorder=9, marker='^' if hs["is_inverse"] else 'v', alpha=0.9)
            # Label
            hs_status = "CONFIRMED" if hs["confirmed"] else "FORMING"
            label_y = hs["neckline"] * (1.003 if hs["is_inverse"] else 0.997)
            ax1.text(rx + 1, label_y,
                     f" {'🔺' if hs['is_inverse'] else '🔻'} {hs['type'].split()[0]} {hs_status}",
                     color=hs_color, fontsize=6.5, va='center', fontweight='bold',
                     bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                               edgecolor=hs_color, alpha=0.75, linewidth=0.7))
    except Exception as pat_err:
        log.warning(f"Pattern draw error: {pat_err}")

    if not r.get("liquid",True):
        ax1.text(n/2,(swing_high+swing_low)/2,"⚠️ LOW LIQUIDITY",
                color="#c62828",fontsize=22,alpha=0.18,ha='center',va='center',
                fontweight='bold',rotation=30,zorder=15)

    sig_txt=r['sigs'][0].split('-')[0].strip() if r['sigs'] else 'No Signal'
    chg_s=f"+{r['chg']:.2f}%" if r['chg']>=0 else f"{r['chg']:.2f}%"
    chg_color=GREEN if r['chg']>=0 else RED
    liq_tag=" | ⚠️LOW LIQ" if not r.get("liquid",True) else ""
    ax1.set_title(f"  {r['ticker']}  |  TF:{r['tf']}  |  {price_fmt(lp)}  {chg_s}  |  {r['trend']}  |  Score:{r['score']}/8  |  {sig_txt}{liq_tag}",
                  color=TEXT,fontsize=9,fontweight='bold',loc='left',pad=6,
                  bbox=dict(boxstyle='round,pad=0.3',facecolor='#e8eaf6',edgecolor=GRID))
    ax1.legend(loc='upper left',fontsize=7,facecolor=BG,edgecolor=GRID,labelcolor=TEXT2)
    ax1.set_xlim(-0.5,n-0.5); ax1.tick_params(labelbottom=False)

    # ══ VOLUME BAR ══
    vol_colors=[GREEN if closes[i]>=opens[i] else RED for i in idx]
    ax2.bar(idx,vols,color=vol_colors,alpha=0.7,width=0.7)
    ax2.axhline(avg_v,color=TEXT2,linewidth=0.7,linestyle='--',alpha=0.6)
    for i in idx:
        vr_i=vols[i]/avg_v if avg_v>0 else 1
        if vr_i>=2.0:
            is_buy=closes[i]>=opens[i]
            ax2.bar(i,vols[i],color=MID_GREEN if is_buy else MID_RED,alpha=0.9,width=0.7)
    ax2.set_ylabel("VOL",color=TEXT2,fontsize=7)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x,_: f"{x/1e9:.1f}B" if x>=1e9 else f"{x/1e6:.0f}M" if x>=1e6 else f"{x/1e3:.0f}K"))
    ax2.tick_params(labelbottom=False); ax2.set_xlim(-0.5,n-0.5)

    # ══ MACD ══
    hist_colors=[GREEN if v>=0 else RED for v in macd_h.values]
    ax3.bar(idx,macd_h.values,color=hist_colors,alpha=0.7,width=0.7)
    ax3.plot(idx,macd_l.values,color=BLUE,linewidth=1.1,label=f"MACD:{r['macd']:.1f}")
    ax3.plot(idx,macd_sg.values,color=RED,linewidth=0.9,label=f"Sig:{r['msig']:.1f}")
    ax3.axhline(0,color=TEXT2,linewidth=0.5)
    ax3.set_ylabel("MACD",color=TEXT2,fontsize=7)
    ax3.legend(loc='upper left',fontsize=6,facecolor=BG,edgecolor=GRID,labelcolor=TEXT2)
    ax3.tick_params(labelbottom=False); ax3.set_xlim(-0.5,n-0.5)

    # ══ STOCH + RSI ══
    ax4.plot(idx,sk.values,color=BLUE,linewidth=1.1,label=f"K:{r['stoch']:.1f}")
    ax4.plot(idx,sd.values,color=PINK,linewidth=0.9,label="D")
    ax4.plot(idx,rsi_v,color=ORANGE,linewidth=0.9,linestyle='--',label=f"RSI:{r['rsi']:.1f}")
    ax4.axhline(80,color=RED,linewidth=0.5,linestyle='--',alpha=0.5)
    ax4.axhline(20,color=GREEN,linewidth=0.5,linestyle='--',alpha=0.5)
    ax4.axhline(50,color=TEXT2,linewidth=0.4,alpha=0.3)
    ax4.fill_between(idx,80,100,alpha=0.05,color=RED)
    ax4.fill_between(idx,0,20,alpha=0.05,color=GREEN)
    ax4.set_ylim(0,100); ax4.set_ylabel("STOCH",color=TEXT2,fontsize=7)
    ax4.legend(loc='upper left',fontsize=6,facecolor=BG,edgecolor=GRID,labelcolor=TEXT2)
    ax4.set_xlim(-0.5,n-0.5)
    step=max(1,n//10); ticks=list(range(0,n,step))
    fmt_t="%d/%m" if tf in ["D","W","M"] else "%H:%M"
    labels=[df.index[i].strftime(fmt_t) for i in ticks]
    ax4.set_xticks(ticks); ax4.set_xticklabels(labels,fontsize=7,color=TEXT2)

    # ══════════════════════════════════
    # T1MO PIXEL HEATMAP — 3 ROWS (WHITE)
    # ══════════════════════════════════
    PH=1.0

    def px_trend(v):
        if v==3:    return "#00897b"   # teal green full bull
        elif v==2:  return "#4db6ac"   # teal mild bull
        elif v==1:  return "#b2dfdb"   # light teal weak bull
        elif v==-3: return "#e53935"   # red full bear
        elif v==-2: return "#ef9a9a"   # light red mild bear
        elif v==-1: return "#ffcdd2"   # very light red weak bear
        else:       return "#e0e0e0"

    def px_momentum(v):
        if v>=2:    return "#1565c0"   # strong blue bull
        elif v==1:  return "#90caf9"   # light blue mild
        elif v==-1: return "#ce93d8"   # light purple mild bear
        elif v<=-2: return "#7b1fa2"   # purple strong bear
        else:       return "#e0e0e0"

    def px_volume(v):
        if v==3:    return "#1b5e20"   # dark green big buy
        elif v==2:  return "#43a047"   # green medium buy
        elif v==1:  return "#a5d6a7"   # light green mild buy
        elif v==-3: return "#b71c1c"   # dark red big sell
        elif v==-2: return "#e53935"   # red medium sell
        elif v==-1: return "#ffcdd2"   # light red mild sell
        else:       return "#f5f5f5"

    # PIXEL ROW 1: TREND
    for i in idx:
        v=trend_vals[i]; col=px_trend(v)
        if v>0:    ax_p1.bar(i,PH,bottom=0,color=col,width=0.92,zorder=3)
        elif v<0:  ax_p1.bar(i,-PH,bottom=0,color=col,width=0.92,zorder=3)
        else:      ax_p1.bar(i,0.1,bottom=-0.05,color=GRAY,width=0.92,zorder=2)
    for fi in flip_up:
        ax_p1.axvline(fi,color="#00695c",linewidth=2.0,alpha=0.9,zorder=5)
    for fi in flip_dn:
        ax_p1.axvline(fi,color="#c62828",linewidth=2.0,alpha=0.9,zorder=5)
    ax_p1.axhline(0,color=TEXT2,linewidth=0.5,alpha=0.4,zorder=4)
    ax_p1.set_ylim(-1.4,1.4); ax_p1.set_xlim(-0.5,n-0.5)
    ax_p1.set_yticks([]); ax_p1.set_xticks([])
    ax_p1.text(-0.5,0,"TREND",color=TEXT2,fontsize=5.5,va='center',ha='right',fontweight='bold')

    # PIXEL ROW 2: MOMENTUM
    for i in idx:
        v=momentum_vals[i]; col=px_momentum(v)
        if v>0:    ax_p2.bar(i,PH,bottom=0,color=col,width=0.92,zorder=3)
        elif v<0:  ax_p2.bar(i,-PH,bottom=0,color=col,width=0.92,zorder=3)
        else:      ax_p2.bar(i,0.1,bottom=-0.05,color=GRAY,width=0.92,zorder=2)
    ax_p2.axhline(0,color=TEXT2,linewidth=0.5,alpha=0.4,zorder=4)
    ax_p2.set_ylim(-1.4,1.4); ax_p2.set_xlim(-0.5,n-0.5)
    ax_p2.set_yticks([]); ax_p2.set_xticks([])
    ax_p2.text(-0.5,0,"MOMT",color=TEXT2,fontsize=5.5,va='center',ha='right',fontweight='bold')

    # PIXEL ROW 3: VOLUME
    for i in idx:
        v=vol_vals[i]; col=px_volume(v)
        if v>0:
            ax_p3.bar(i,PH,bottom=0,color=col,width=0.92,zorder=3)
            if v==3: ax_p3.text(i,0.5,"▲",color="white",fontsize=5,ha='center',va='center',fontweight='bold',zorder=6)
        elif v<0:
            ax_p3.bar(i,-PH,bottom=0,color=col,width=0.92,zorder=3)
            if v==-3: ax_p3.text(i,-0.5,"▼",color="white",fontsize=5,ha='center',va='center',fontweight='bold',zorder=6)
        else:
            ax_p3.bar(i,PH,bottom=0,color=col,width=0.92,zorder=2)
    ax_p3.axhline(0,color=TEXT2,linewidth=0.5,alpha=0.4,zorder=4)
    ax_p3.set_ylim(-1.4,1.4); ax_p3.set_xlim(-0.5,n-0.5)
    ax_p3.set_yticks([]); ax_p3.set_xticks([])
    ax_p3.text(-0.5,0,"VOL",color=TEXT2,fontsize=5.5,va='center',ha='right',fontweight='bold')

    # ══ WATERMARK ══
    fig.text(0.5,0.5,"IDX QUANT\nT1MO Style",color='#131722',alpha=0.03,
             fontsize=48,ha='center',va='center',rotation=30,fontweight='bold')

    plt.tight_layout(pad=0.5)
    buf=io.BytesIO()
    plt.savefig(buf,format='png',dpi=130,bbox_inches='tight',facecolor=BG)
    buf.seek(0); plt.close(fig)
    return buf,None

def fmt_now(): return datetime.now(WIB).strftime("%d-%b-%Y %H:%M")+" WIB"

# ══════════════════════════════════════════
# TP / SL CALCULATOR
# ══════════════════════════════════════════
def calculate_tp_sl(r):
    """
    Hitung TP1/TP2/TP3 dan SL1/SL2/SL3 otomatis dari data sinyal.
    Basis: EMA levels, ATR-approx, support/resistance dinamis.
    Returns dict dengan tp1..tp3, sl1..sl3, rr (risk/reward).
    """
    price  = r["price"]
    e9     = r["e9"]
    e20    = r["e20"]
    e50    = r["e50"]
    is_idr = r["ticker"].endswith(".JK")

    # ── ATR approx: pakai range harga 20 candle terakhir dari df
    try:
        df  = r["df"]
        hi  = df["High"].squeeze().tail(20)
        lo  = df["Low"].squeeze().tail(20)
        atr = float((hi - lo).mean())
    except:
        atr = price * 0.03   # fallback 3% kalau df tidak tersedia

    # ── Target Price (TP) ──
    # TP1: EMA9 + 1 ATR  (target cepat)
    # TP2: EMA9 + 2 ATR  (swing normal)
    # TP3: EMA9 + 3.5 ATR (extended target)
    tp1 = price + (1.0 * atr)
    tp2 = price + (2.0 * atr)
    tp3 = price + (3.5 * atr)

    # Kalau harga sudah di atas TP1 (misal sudah naik duluan),
    # shift TP berbasis % relatif supaya tetap masuk akal
    if tp1 <= price * 1.005:
        tp1 = price * 1.04
        tp2 = price * 1.09
        tp3 = price * 1.16

    # ── Stop Loss (SL) ──
    # SL1: Di bawah EMA9 tipis — exit awal (3%)
    # SL2: Di bawah MA20 — trend melemah (6–7%)
    # SL3: Di bawah MA50 — cut loss (10–12%)
    sl1 = min(e9  * 0.97,  price * 0.97)
    sl2 = min(e20 * 0.96,  price * 0.94)
    sl3 = min(e50 * 0.95,  price * 0.89)

    # Pastikan SL tidak terbalik
    sl1 = min(sl1, price * 0.97)
    sl2 = min(sl2, sl1  * 0.97)
    sl3 = min(sl3, sl2  * 0.97)

    # ── Risk/Reward Ratio (vs SL1) ──
    risk   = price - sl1
    reward = tp1   - price
    rr     = round(reward / risk, 2) if risk > 0 else 0

    def fmt_p(v):
        return f"Rp {v:,.0f}" if is_idr else f"${v:,.2f}"

    return {
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "sl1": sl1, "sl2": sl2, "sl3": sl3,
        "rr":  rr,
        "atr": atr,
        "tp1_str": fmt_p(tp1), "tp2_str": fmt_p(tp2), "tp3_str": fmt_p(tp3),
        "sl1_str": fmt_p(sl1), "sl2_str": fmt_p(sl2), "sl3_str": fmt_p(sl3),
        "tp1_pct": (tp1-price)/price*100,
        "tp2_pct": (tp2-price)/price*100,
        "tp3_pct": (tp3-price)/price*100,
        "sl1_pct": (sl1-price)/price*100,
        "sl2_pct": (sl2-price)/price*100,
        "sl3_pct": (sl3-price)/price*100,
    }

def fmt_tp_sl_block(ts, is_idr=True):
    """Format blok TP/SL untuk pesan Telegram"""
    rr_emoji = "🔥" if ts["rr"] >= 2 else "✅" if ts["rr"] >= 1.5 else "⚠️"
    return (
        f"\n🎯 *TARGET PRICE:*\n"
        f"  TP1: `{ts['tp1_str']}` ({ts['tp1_pct']:+.1f}%)\n"
        f"  TP2: `{ts['tp2_str']}` ({ts['tp2_pct']:+.1f}%)\n"
        f"  TP3: `{ts['tp3_str']}` ({ts['tp3_pct']:+.1f}%)\n"
        f"\n🛡 *STOP LOSS:*\n"
        f"  SL1: `{ts['sl1_str']}` ({ts['sl1_pct']:+.1f}%) — cut EMA9\n"
        f"  SL2: `{ts['sl2_str']}` ({ts['sl2_pct']:+.1f}%) — cut MA20\n"
        f"  SL3: `{ts['sl3_str']}` ({ts['sl3_pct']:+.1f}%) — cut MA50\n"
        f"\n{rr_emoji} R/R Ratio: `{ts['rr']}x` (vs SL1)"
    )

# ══════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════
async def start(u,c):
    await u.message.reply_text(
        "⚡ *IDX QUANT Bot v4 — T1MO × Wisdom*\n\n"
        "📊 *Chart & Signal:*\n"
        "`/signal BBCA` — Signal + indikator\n"
        "`/signal PLTR D` — Saham US juga bisa!\n"
        "`/chart ENRG 1H` — Chart candlestick\n"
        "`/tp ENRG` — TP1/TP2/TP3 + SL1/SL2/SL3 otomatis\n\n"
        "🔍 *Screener:*\n"
        "`/screener` — Top picks IDX\n"
        "`/screener us` — Top picks US stocks\n"
        "`/doji` — Doji Bullish Reversal scan 1H+4H+1D IDX\n"
        "`/doji us` — Doji scan US stocks\n\n"
        "🌊 *Volume Momentum (BARU):*\n"
        "`/volmom` — IDX: volume naik terus 5M→15M→30M→1H\n"
        "`/volmom us` — US stocks volume momentum\n\n"
        "🤖 *Auto Scan:*\n"
        "`/auto on` — Aktifkan auto scan\n"
        "`/auto off` — Matikan auto scan\n\n"
        "📈 *Market:*\n"
        "`/volume` — Top volume IDX\n"
        "`/trend` — Market overview\n"
        "`/help` — Bantuan lengkap\n\n"
        "⚡ *v4: TP/SL otomatis + Evening Summary + VolMom Multi-TF*",
        parse_mode="Markdown")

async def flipstatus_cmd(u,c):
    bull_list=[k for k,v in flip_state_db.items() if v=="bull"]
    bear_list=[k for k,v in flip_state_db.items() if v=="bear"]
    now_str=datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
    lines=[f"📊 *PIXEL FLIP STATUS*",f"🕐 {now_str}","━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"🟢 *BULLISH* ({len(bull_list)} saham):")
    lines.append(" | ".join(bull_list[:15]) if bull_list else "— belum ada —")
    lines.append(f"\n🔴 *BEARISH* ({len(bear_list)} saham):")
    lines.append(" | ".join(bear_list[:15]) if bear_list else "— belum ada —")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"Total tracked: {len(flip_state_db)} saham","🔄 Scan otomatis setiap 30 menit"]
    await u.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def help_cmd(u,c):
    await u.message.reply_text(
        "📖 *IDX QUANT v4 — Command List*\n\n"
        "*Signal & Chart:*\n"
        "`/signal KODE [TF]` — TF: 5M 15M 30M 1H 4H D W M\n"
        "`/chart KODE [TF]` — Gambar chart candlestick\n"
        "`/tp KODE [TF]` — TP1/TP2/TP3 + SL1/SL2/SL3 + R/R Ratio\n\n"
        "*Screener:*\n"
        "`/screener [idx/min_score]` — IDX screener (parallel)\n"
        "`/screener us` atau `/screener_us` — US stock screener\n\n"
        "🕯 *Doji Bullish Reversal:*\n"
        "`/doji` — Scan doji IDX di TF 1H + 4H + 1D\n"
        "`/doji us` — Scan doji US stocks\n"
        "🤖 Auto alert doji tiap 1 jam saat IDX buka\n\n"
        "🌊 *Volume Momentum (BARU):*\n"
        "`/volmom` — Scan IDX volume naik konsisten 5M→15M→30M→1H\n"
        "`/volmom us` — Scan US stocks volume momentum\n"
        "🤖 Auto alert volmom tiap 30 menit saat IDX buka\n\n"
        "*Auto Scan:*\n"
        "`/auto on` — Aktifkan (IDX 09:00-15:15 + US 21:30-04:00)\n"
        "`/auto off` — Matikan\n"
        "`/summary` — Summary IDX manual 📋\n"
        "`/summary us` — Summary 🇺🇸 US Stocks manual\n\n"
        "*Market:*\n"
        "`/volume` — Top volume IDX\n"
        "`/trend` — Trend market + IHSG\n\n"
        "*Flip Alert:*\n"
        "`/flipstatus` — Status flip pixel semua saham\n"
        "🔔 Auto alert flip tiap 30 menit (aktifkan /auto on)\n\n"
        "Score: 1-3 Lemah | 4-5 OK | 6+ 🔥\n"
        "⚠️ LOW LIQUIDITY = saham illiquid/gorengan\n"
        "⚡ v4: TP/SL otomatis | Evening Summary 16:05 | Volmom Multi-TF",
        parse_mode="Markdown")

async def signal_cmd(u,c):
    args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/signal BBCA` atau `/signal PLTR D`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK",""); tf=args[1].upper() if len(args)>1 else "D"
    m=await u.message.reply_text(f"🔍 Analisis *{code}* TF:{tf}...",parse_mode="Markdown")
    r=get_signal(code,tf)
    if "error" in r: await m.edit_text(f"❌ {r['error']}"); return
    em="🟢" if r["chg"]>=0 else "🔴"; bar="█"*min(r["score"],8)+"░"*max(0,8-r["score"])
    sx="\n".join([f"  • {s}" for s in r["sigs"]]) or "  • Tidak ada signal kuat"
    sc="🔥" if r["score"]>=6 else "💪" if r["score"]>=4 else "📊"
    vspike="🌊 VOLUME SPIKE!" if r["vr"]>=2 else ""
    liq_warn=f"\n⚠️ *LOW LIQUIDITY* — avg vol {r['avg_vol']/1e6:.1f}M, hati-hati gorengan!" if not r.get("liquid",True) else ""
    is_idr=r["ticker"].endswith(".JK")
    price_str=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
    ts = calculate_tp_sl(r)
    tp_sl_block = fmt_tp_sl_block(ts, is_idr=is_idr)
    await m.edit_text(
        f"⚡ *{r['ticker']}* | TF:`{r['tf']}`\n━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Harga: *{price_str}*\n{em} Change: `{r['chg']:+.2f}%`\n"
        f"📊 Trend: *{r['trend']}* {vspike}{liq_warn}\n\n"
        f"📐 *Indikator:*\n  EMA9:  `{r['e9']:,.2f}`\n  EMA20: `{r['e20']:,.2f}`\n"
        f"  EMA50: `{r['e50']:,.2f}`\n  RSI:   `{r['rsi']:.1f}`\n"
        f"  MACD:  `{r['macd']:.2f}` Sig:`{r['msig']:.2f}`\n"
        f"  STOCH: `{r['stoch']:.1f}`\n  Vol:   `{r['vr']:.1f}x` avg\n\n"
        f"🎯 *Signals:*\n{sx}\n\n"
        f"{sc} Score:`[{bar}]` {r['score']}/8"
        f"{tp_sl_block}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n⏱ {fmt_now()}",
        parse_mode="Markdown")

async def tp_cmd(u,c):
    """Command /tp KODE [TF] — tampilkan TP/SL detail"""
    args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/tp BBCA` atau `/tp KETR D`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK",""); tf=args[1].upper() if len(args)>1 else "D"
    m=await u.message.reply_text(f"⚖️ Hitung TP/SL *{code}* TF:{tf}...",parse_mode="Markdown")
    r=get_signal(code,tf)
    if "error" in r: await m.edit_text(f"❌ {r['error']}"); return
    ts=calculate_tp_sl(r)
    is_idr=r["ticker"].endswith(".JK")
    price_str=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
    rr_emoji = "🔥" if ts["rr"]>=2 else "✅" if ts["rr"]>=1.5 else "⚠️"
    await m.edit_text(
        f"⚖️ *{r['ticker']}* | TF:`{tf}` | Entry: *{price_str}*\n"
        f"📊 Trend: {r['trend']} | Score:`{r['score']}/8`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *TARGET PRICE:*\n"
        f"  TP1: `{ts['tp1_str']}` `({ts['tp1_pct']:+.1f}%)`\n"
        f"  TP2: `{ts['tp2_str']}` `({ts['tp2_pct']:+.1f}%)`\n"
        f"  TP3: `{ts['tp3_str']}` `({ts['tp3_pct']:+.1f}%)`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡 *STOP LOSS:*\n"
        f"  SL1: `{ts['sl1_str']}` `({ts['sl1_pct']:+.1f}%)` — cut EMA9\n"
        f"  SL2: `{ts['sl2_str']}` `({ts['sl2_pct']:+.1f}%)` — cut MA20\n"
        f"  SL3: `{ts['sl3_str']}` `({ts['sl3_pct']:+.1f}%)` — cut MA50\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{rr_emoji} *Risk/Reward Ratio:* `{ts['rr']}x`\n"
        f"📏 ATR: `{ts['atr']:,.0f}` (basis kalkulasi)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 SL1=exit cepat | SL2=trend gagal | SL3=cut loss\n"
        f"⏱ {fmt_now()}",
        parse_mode="Markdown")

async def chart_cmd(u,c):
    args=c.args
    if not args: await u.message.reply_text("Format: `/chart BBCA` atau `/chart PLTR D`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK",""); tf=args[1].upper() if len(args)>1 else "D"
    m=await u.message.reply_text(f"Membuat chart *{code}* TF:{tf}...",parse_mode="Markdown")
    buf,err=generate_chart(code,tf)
    if err: await m.edit_text(f"Error: {err}"); return
    await m.delete()
    r=get_signal(code,tf)
    sig_txt=r['sigs'][0].split('-')[0].strip() if r.get('sigs') else 'No Signal'
    vspike="VOL SPIKE!" if r.get('vr',0)>=2 else ""
    liq_tag=" | LOW LIQ" if not r.get("liquid",True) else ""
    is_idr=r.get("ticker","").endswith(".JK")
    price_str=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
    price_fmt=lambda p: f"Rp {p:,.0f}" if is_idr else f"${p:,.2f}"
    ts = calculate_tp_sl(r)
    pat_text = ""
    try:
        if "df" in r:
            pats = detect_all_patterns(r["df"])
            pat_text = fmt_patterns_text(pats, price_fmt)
            if pat_text: pat_text = f"\n{pat_text}\n"
    except: pass
    caption=(f"*{r['ticker']}* | TF:`{tf}` | `{price_str}` `{r['chg']:+.2f}%`\n"
             f"{r['trend']} | Score:`{r['score']}/8` | {sig_txt} {vspike}{liq_tag}\n"
             f"EMA9:`{r['e9']:,.2f}` MA20:`{r['e20']:,.2f}` MA50:`{r['e50']:,.2f}`\n"
             f"RSI:`{r['rsi']:.1f}` MACD:`{r['macd']:.1f}` STOCH:`{r['stoch']:.1f}`"
             f"{pat_text}\n"
             f"TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) "
             f"TP2:`{ts['tp2_str']}`({ts['tp2_pct']:+.1f}%) "
             f"TP3:`{ts['tp3_str']}`({ts['tp3_pct']:+.1f}%)\n"
             f"SL1:`{ts['sl1_str']}`({ts['sl1_pct']:+.1f}%) "
             f"SL2:`{ts['sl2_str']}`({ts['sl2_pct']:+.1f}%) "
             f"SL3:`{ts['sl3_str']}`({ts['sl3_pct']:+.1f}%)\n"
             f"R/R:`{ts['rr']}x` | {fmt_now()}")
    await u.message.reply_photo(photo=buf,caption=caption,parse_mode="Markdown")

# ══ SCREENER ══ (pakai parallel scan)
async def screener_cmd(u,c):
    args=c.args
    market="idx"; ms=3
    for a in args:
        if a.lower()=="us": market="us"
        elif a.lower() in ("idx","indo"): market="idx"
        elif a.isdigit(): ms=int(a)
    if market=="us":
        await screener_us_exec(u,c,ms); return
    m=await u.message.reply_text(f"🔍 Screener IDX min score {ms}... (parallel ⚡)")
    # ✅ FIX: Parallel scan
    res = await asyncio.get_event_loop().run_in_executor(
        None, parallel_signal_scan, IDX_STOCKS, "D", ms)
    if not res: await m.edit_text("❌ Tidak ada hasil."); return
    lines=[f"🇮🇩 *IDX SCREENER* | Min Score:{ms}","━━━━━━━━━━━━━━━━━━━━"]
    for r in res[:15]:
        em="🟢" if r["chg"]>=0 else "🔴"
        top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
        vs="🌊" if r["vr"]>=2 else ""
        liq="⚠️" if not r.get("liquid",True) else ""
        lines.append(f"{em} *{r['code']}* `{r['price']:,.0f}` {r['chg']:+.2f}% Score:`{r['score']}/8` {top}{vs}{liq}")
    lines+=["━━━━━━━━━━━━━━━━━━━━","⚠️ = LOW LIQUIDITY (hati-hati gorengan)",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

async def screener_us_exec(u,c,ms=2):
    m=await u.message.reply_text(f"🇺🇸 Screener US Stocks min score {ms}... (parallel ⚡)")
    market_status=""
    if not is_us_market_open():
        market_status="\n⚠️ *US MARKET CLOSED* — Data bukan realtime\n"
    # ✅ FIX: Parallel scan semua US stocks
    res = await asyncio.get_event_loop().run_in_executor(
        None, parallel_signal_scan, US_STOCKS, "D", ms)
    if not res: await m.edit_text("❌ Tidak ada hasil."); return
    lines=[f"🇺🇸 *US STOCK SCREENER* | Min Score:{ms}{market_status}","━━━━━━━━━━━━━━━━━━━━"]
    for r in res[:12]:
        em="🟢" if r["chg"]>=0 else "🔴"
        top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
        vs="🌊" if r["vr"]>=2 else ""
        lines.append(f"{em} *{r['code']}* `${r['price']:,.2f}` {r['chg']:+.2f}% Score:`{r['score']}/8` {top}{vs}")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

async def screener_us_cmd(u,c):
    args=c.args; ms=int(args[0]) if args and args[0].isdigit() else 2
    await screener_us_exec(u,c,ms)

# ══ DOJI SCREENER COMMAND ══
async def doji_cmd(u,c):
    args=c.args
    market="idx"
    for a in args:
        if a.lower()=="us": market="us"
        elif a.lower() in ("idx","indo"): market="idx"
    stock_list = US_STOCKS if market=="us" else IDX_STOCKS
    market_name = "US" if market=="us" else "IDX"
    m = await u.message.reply_text(
        f"🕯 Scanning *Doji Bullish Reversal* {'🇺🇸 '+market_name if market=='us' else '🇮🇩 '+market_name} di TF 1H, 4H, 1D...\n"
        f"⏳ Mohon tunggu (parallel scan ⚡)", parse_mode="Markdown")
    try:
        results = await asyncio.get_event_loop().run_in_executor(
            None, doji_scan_all_tf, stock_list)
        msg = fmt_doji_msg(results, market_name)
        await m.edit_text(msg, parse_mode="Markdown")
        # Kirim chart saham doji terbaik (prioritas 4H lalu D lalu 1H)
        best = None; best_tf = "D"
        for tf in ["4H","D","1H"]:
            hits = results.get(tf,[])
            liquid_hits = [h for h in hits if h["liquid"]]
            if liquid_hits: best=liquid_hits[0]; best_tf=tf; break
            elif hits: best=hits[0]; best_tf=tf; break
        if best:
            buf, _ = generate_chart(best["code"], best_tf)
            if buf:
                is_idr = best["ticker"].endswith(".JK")
                px = f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
                await u.message.reply_photo(
                    photo=buf,
                    caption=(f"🕯 *{best['code']}* | TF:{best_tf} | `{px}`\n"
                             f"{best['doji_emoji']} {best['doji_type']} | BullScore:`{best['bull_score']}`\n"
                             f"RSI:`{best['rsi']:.0f}` STOCH:`{best['stoch']:.0f}`\n"
                             f"💡 {' | '.join(best['bull_factors'][:3])}"),
                    parse_mode="Markdown")
    except Exception as e:
        await m.edit_text(f"❌ Error doji scan: {e}")



# ══ VOLUME MOMENTUM SCREENER ══
# Deteksi saham dengan volume naik KONSISTEN di multi-TF: 5M → 15M → 30M → 1H
# Logika: volume rata-rata TF kecil harus lebih tinggi dari TF besar = momentum accumulation

def get_vol_ratio(ticker, interval, period):
    """Ambil volume ratio (last vol / avg vol) untuk satu TF"""
    try:
        df = get_cached_data(ticker, interval, period)
        if df.empty or len(df) < 5: return None
        v = df["Volume"].squeeze()
        lv = float(v.iloc[-1])
        av = float(v.iloc[-6:-1].mean()) if len(v) >= 6 else float(v.mean())
        if av <= 0: return None
        price = float(df["Close"].squeeze().iloc[-1])
        chg = float((df["Close"].squeeze().iloc[-1] - df["Close"].squeeze().iloc[-2])
                    / df["Close"].squeeze().iloc[-2] * 100)
        return {"vr": lv/av, "vol": lv, "avg_vol": av, "price": price, "chg": chg}
    except:
        return None

def detect_volume_momentum(code):
    """
    Cek apakah volume saham naik terus dari TF pendek ke panjang.
    Kriteria LULUS: minimal 3 dari 4 TF harus VR >= 1.5, dan trend VR naik.
    Return dict dengan detail atau None kalau tidak memenuhi.
    """
    ticker = get_ticker(code)
    tfs = [
        ("5M",  "5m",  "5d"),
        ("15M", "15m", "5d"),
        ("30M", "30m", "10d"),
        ("1H",  "60m", "60d"),
    ]
    vr_data = {}
    for tf_name, interval, period in tfs:
        res = get_vol_ratio(ticker, interval, period)
        if res: vr_data[tf_name] = res

    if len(vr_data) < 3: return None  # data kurang, skip

    vr_vals = [vr_data[tf]["vr"] for tf in ["5M","15M","30M","1H"] if tf in vr_data]
    if not vr_vals: return None

    # Hitung skor momentum
    # Syarat 1: minimal 3 TF dengan VR >= 1.5
    strong_tfs = sum(1 for v in vr_vals if v >= 1.5)
    if strong_tfs < 3: return None

    # Syarat 2: volume makin tinggi di TF lebih kecil (akumulasi intraday)
    # VR 5M harus >= VR 1H → artinya volume sekarang lebih "hot" dari rata-rata
    vr_5m  = vr_data.get("5M",  {}).get("vr", 0)
    vr_15m = vr_data.get("15M", {}).get("vr", 0)
    vr_30m = vr_data.get("30M", {}).get("vr", 0)
    vr_1h  = vr_data.get("1H",  {}).get("vr", 0)

    # Momentum score: lebih tinggi = lebih kuat
    momentum_score = 0
    if vr_5m >= 2.0:  momentum_score += 3
    elif vr_5m >= 1.5: momentum_score += 2
    if vr_15m >= 2.0: momentum_score += 2
    elif vr_15m >= 1.5: momentum_score += 1
    if vr_30m >= 1.5: momentum_score += 1
    if vr_1h >= 1.5:  momentum_score += 1
    # Bonus: VR 5M > VR 1H (fresh surge)
    if vr_5m > vr_1h: momentum_score += 1

    if momentum_score < 4: return None

    # Ambil data harga dari TF 5M atau 15M
    ref = vr_data.get("5M") or vr_data.get("15M") or {}
    price = ref.get("price", 0)
    chg   = ref.get("chg", 0)

    # Cek likuiditas (IDX only)
    is_idx = ticker.endswith(".JK")
    avg_vol_daily = vr_data.get("1H", {}).get("avg_vol", 0)
    liquid = is_liquid_stock(avg_vol_daily * 6, price) if is_idx else True  # estimasi daily vol

    return {
        "code":    code,
        "ticker":  ticker,
        "price":   price,
        "chg":     chg,
        "vr_5m":   vr_5m,
        "vr_15m":  vr_15m,
        "vr_30m":  vr_30m,
        "vr_1h":   vr_1h,
        "mom_score": momentum_score,
        "liquid":  liquid,
        "strong_tfs": strong_tfs,
    }

def volmom_screener(stock_list, max_workers=10):
    """Scan volume momentum seluruh daftar saham secara paralel"""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(detect_volume_momentum, code): code for code in stock_list}
        for future in as_completed(futures):
            try:
                res = future.result(timeout=20)
                if res: results.append(res)
            except Exception as e:
                log.warning(f"VolMom scan error: {e}")
    results.sort(key=lambda x: x["mom_score"], reverse=True)
    return results

def fmt_volmom_msg(results, market_name="IDX"):
    """Format pesan Telegram untuk volume momentum scan"""
    now_str = datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
    lines = [
        f"🌊 *VOLUME MOMENTUM SCAN — {market_name}*",
        f"🕐 {now_str}",
        f"📌 Volume naik konsisten: 5M → 15M → 30M → 1H",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not results:
        lines.append("— Tidak ada saham dengan volume momentum kuat —")
    else:
        for r in results[:10]:
            em     = "🟢" if r["chg"] >= 0 else "🔴"
            liq    = "" if r["liquid"] else " ⚠️ILL"
            is_idr = r["ticker"].endswith(".JK")
            px     = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
            # Bar visual untuk setiap TF
            def bar(v):
                if v >= 3.0: return "🔴🔴🔴"
                if v >= 2.0: return "🟠🟠"
                if v >= 1.5: return "🟡"
                return "⬜"
            lines.append(
                f"{em} *{r['code']}* `{px}` {r['chg']:+.2f}%{liq}\n"
                f"  5M:{bar(r['vr_5m'])}`{r['vr_5m']:.1f}x` "
                f"15M:{bar(r['vr_15m'])}`{r['vr_15m']:.1f}x` "
                f"30M:{bar(r['vr_30m'])}`{r['vr_30m']:.1f}x` "
                f"1H:{bar(r['vr_1h'])}`{r['vr_1h']:.1f}x` "
                f"| Score:`{r['mom_score']}`"
            )
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "💡 VR=Volume ratio vs rata² 5 candle sebelumnya",
        "🟡≥1.5x 🟠≥2x 🔴≥3x — makin merah makin kuat",
        f"⏱ {now_str}"
    ]
    return "\n".join(lines)

async def volmom_cmd(u, c):
    """Command /volmom [idx/us] — Volume Momentum Screener multi-TF"""
    args = c.args
    market = "us" if args and args[0].lower() in ("us","usa") else "idx"
    stocks = US_STOCKS if market == "us" else IDX_STOCKS
    flag   = "🇺🇸 US" if market == "us" else "🇮🇩 IDX"
    m = await u.message.reply_text(
        f"🌊 Scanning *{flag}* volume momentum di 4 TF...\n"
        f"_(5M, 15M, 30M, 1H — harap tunggu ~30 detik)_",
        parse_mode="Markdown")
    results = await asyncio.get_event_loop().run_in_executor(
        None, volmom_screener, stocks)
    msg = fmt_volmom_msg(results, flag)
    await m.edit_text(msg, parse_mode="Markdown")
    # Kirim chart saham teratas kalau ada
    if results:
        best = results[0]
        buf, _ = generate_chart(best["code"], "5M")
        if buf:
            is_idr = best["ticker"].endswith(".JK")
            px = f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
            await u.message.reply_photo(photo=buf,
                caption=(f"🌊 TOP VOLMOM: *{best['code']}* | `{px}` {best['chg']:+.2f}%\n"
                         f"5M:`{best['vr_5m']:.1f}x` 15M:`{best['vr_15m']:.1f}x` "
                         f"30M:`{best['vr_30m']:.1f}x` 1H:`{best['vr_1h']:.1f}x`\n"
                         f"MomScore:`{best['mom_score']}` | {fmt_now()}"),
                parse_mode="Markdown")

async def volmom_auto_scan(context):
    """Auto scan volume momentum tiap 30 menit saat IDX buka"""
    if not is_weekday(): return
    if not auto_users: return
    if not is_idx_market_open(): return
    bot = context.bot
    results = await asyncio.get_event_loop().run_in_executor(
        None, volmom_screener, IDX_STOCKS)
    # Hanya kirim kalau ada yang score tinggi (>=6) — filter noise
    hot = [r for r in results if r["mom_score"] >= 6]
    if not hot: return
    msg = fmt_volmom_msg(hot, "🇮🇩 IDX AUTO")
    for uid in auto_users:
        try:
            await bot.send_message(int(uid), msg, parse_mode="Markdown")
            buf, _ = generate_chart(hot[0]["code"], "5M")
            if buf:
                is_idr = hot[0]["ticker"].endswith(".JK")
                px = f"Rp {hot[0]['price']:,.0f}" if is_idr else f"${hot[0]['price']:,.2f}"
                await bot.send_photo(int(uid), photo=buf,
                    caption=(f"🌊 VOLMOM AUTO: *{hot[0]['code']}* | `{px}`\n"
                             f"5M:`{hot[0]['vr_5m']:.1f}x` 15M:`{hot[0]['vr_15m']:.1f}x` "
                             f"30M:`{hot[0]['vr_30m']:.1f}x` 1H:`{hot[0]['vr_1h']:.1f}x`\n"
                             f"MomScore:`{hot[0]['mom_score']}` | {fmt_now()}"),
                    parse_mode="Markdown")
        except Exception as e:
            log.error(f"volmom auto uid {uid}: {e}")

# ══ AUTO SCAN ══
async def auto_cmd(u,c):
    uid=str(u.effective_user.id); args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/auto on` atau `/auto off`",parse_mode="Markdown"); return
    if args[0].lower()=="on":
        auto_users[uid]=True; save_json(AUTO_FILE,auto_users)
        await u.message.reply_text(
            "🤖 *Auto Scan AKTIF v4!*\n\n"
            "🇮🇩 *IDX Scanner:* aktif *09:00-15:15 WIB* (weekday)\n"
            "🇺🇸 *US Scanner:* aktif *21:30-04:00 WIB* (weekday)\n"
            "⏰ Volume spike alert setiap *15 menit*\n"
            "🌊 Volume Momentum scan setiap *30 menit*\n"
            "🌅 Morning scan IDX setiap jam *09:00 WIB*\n"
            "🌆 *Evening summary* recap harian jam *16:05 WIB*\n"
            "🕯 *Doji scan* tiap 1 jam saat market buka\n"
            "⚡ *Parallel scan 10 thread — lebih cepat & akurat!*\n\n"
            "⚠️ LOW LIQUIDITY = saham illiquid otomatis diberi tanda",
            parse_mode="Markdown")
    else:
        auto_users.pop(uid,None); save_json(AUTO_FILE,auto_users)
        await u.message.reply_text("⏹ Auto scan dimatikan.",parse_mode="Markdown")

async def volume_cmd(u,c):
    m=await u.message.reply_text("💧 Mengambil data volume... (parallel ⚡)")
    def fetch_vol(code):
        try:
            df=yf.download(f"{code}.JK",period="5d",interval="1d",progress=False,auto_adjust=True)
            if len(df)>=2:
                lv=float(df["Volume"].iloc[-1]); av=float(df["Volume"].mean())
                lc=float(df["Close"].iloc[-1]); vr=lv/av if av>0 else 1
                return{"code":code,"price":lc,"vol":lv,"vr":vr,"avg_vol":av}
        except: pass
        return None
    with ThreadPoolExecutor(max_workers=10) as ex:
        results=[r for r in ex.map(fetch_vol,IDX_STOCKS) if r]
    results.sort(key=lambda x:x["vol"],reverse=True)
    lines=["💧 *TOP VOLUME IDX*","━━━━━━━━━━━━━━━━━━━━"]
    for i,v in enumerate(results[:12],1):
        vs=f"{v['vol']/1e9:.1f}B" if v['vol']>=1e9 else f"{v['vol']/1e6:.0f}M"
        ic="🌊" if v["vr"]>=2 else "📈" if v["vr"]>=1.5 else "📊"
        liq="⚠️" if not is_liquid_stock(v["avg_vol"],v["price"]) else ""
        lines.append(f"{i}. {ic} *{v['code']}* `{v['price']:,.0f}` Vol:`{vs}` ({v['vr']:.1f}x){liq}")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

async def trend_cmd(u,c):
    m=await u.message.reply_text("🌊 Analisis market trend...")
    try:
        ih=yf.download("^JKSE",period="5d",interval="1d",progress=False,auto_adjust=True)
        lc=float(ih["Close"].iloc[-1]); pc=float(ih["Close"].iloc[-2]); chg=(lc-pc)/pc*100
        ir=float(rsi(ih["Close"].squeeze()).iloc[-1])
        itxt=f"IHSG: `{lc:,.0f}` `{chg:+.2f}%` RSI:`{ir:.0f}`"
    except: itxt="IHSG: data tidak tersedia"
    scan_stocks=["BBCA","BBRI","TLKM","BMRI","ASII","ENRG","ANTM","GOTO","ADMR","MDKA"]
    res = await asyncio.get_event_loop().run_in_executor(
        None, parallel_signal_scan, scan_stocks, "D", 0)
    up=sum(1 for r in res if "UP" in r["trend"])
    dn=sum(1 for r in res if "DOWN" in r["trend"])
    sd=len(res)-up-dn; tot=len(res)
    hot=[f"  🔥 {r['code']} score:{r['score']}" for r in res if r["score"]>=5]
    mood="BULLISH 🟢" if up>dn else "BEARISH 🔴" if dn>up else "MIXED ↔"
    lines=["🌊 *MARKET TREND IDX*","━━━━━━━━━━━━━━━━━━━━",f"📊 {itxt}","",
           f"🎯 Mood: *{mood}*",f"🟢 Uptrend:   `{up}/{tot}`",
           f"🔴 Downtrend: `{dn}/{tot}`",f"↔️ Sideways:  `{sd}/{tot}`"]
    if hot: lines+=["","🔥 *Hot Signals:*"]+hot
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

# ══ BACKGROUND JOBS ══

async def volume_spike_scan_idx(context):
    # ✅ FIX: is_idx_market_open() sudah include weekday check, tapi eksplisit lebih aman
    if not is_weekday(): return
    if not is_idx_market_open(): return
    if not auto_users: return
    bot=context.bot
    # ✅ FIX: Parallel scan semua IDX stocks
    spikes = await asyncio.get_event_loop().run_in_executor(
        None, parallel_scan, IDX_STOCKS, "5M", 2.5)
    if not spikes: return
    for uid in auto_users:
        try:
            lines=["⚡ *🇮🇩 IDX VOLUME SPIKE ALERT!*","━━━━━━━━━━━━━━━━━━━━"]
            buy_spikes=[s for s in spikes if s["direction"]=="BUY"]
            sell_spikes=[s for s in spikes if s["direction"]=="SELL"]
            if buy_spikes:
                lines.append("🟢 *BUY VOLUME SPIKE:*")
                for s in buy_spikes[:5]:
                    liq=" ⚠️ILLIQUID" if not s.get("liquid",True) else ""
                    lines.append(f"  ▲ *{s['code']}* `Rp {s['price']:,.0f}` {s['chg']:+.2f}% Vol:{s['vr']:.1f}x{liq}")
            if sell_spikes:
                lines.append("🔴 *SELL VOLUME SPIKE:*")
                for s in sell_spikes[:5]:
                    liq=" ⚠️ILLIQUID" if not s.get("liquid",True) else ""
                    lines.append(f"  ▼ *{s['code']}* `Rp {s['price']:,.0f}` {s['chg']:+.2f}% Vol:{s['vr']:.1f}x{liq}")
            lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
            await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
            liquid_spikes=[s for s in spikes if s.get("liquid",True)]
            top=liquid_spikes[0] if liquid_spikes else spikes[0]
            buf,_=generate_chart(top["code"],"5M")
            if buf:
                dir_txt="🟢 BUY SPIKE" if top["direction"]=="BUY" else "🔴 SELL SPIKE"
                liq_tag=" | ⚠️LOW LIQ" if not top.get("liquid",True) else ""
                await bot.send_photo(int(uid),photo=buf,
                    caption=f"📊 {top['code']} | {dir_txt} | Vol:{top['vr']:.1f}x avg{liq_tag} | {fmt_now()}")
        except Exception as e: log.error(f"IDX spike alert error uid {uid}: {e}")

async def volume_spike_scan_us(context):
    # ✅ FIX: Skip weekend — US market juga tutup Sabtu/Minggu
    if not is_weekday(): return
    if not is_us_market_open(): return
    if not auto_users: return
    bot=context.bot
    # ✅ FIX: Scan SEMUA US stocks (bukan [:30])
    spikes = await asyncio.get_event_loop().run_in_executor(
        None, parallel_scan, US_STOCKS, "5M", 2.5)
    if not spikes: return
    for uid in auto_users:
        try:
            lines=["⚡ *🇺🇸 US VOLUME SPIKE ALERT!*","━━━━━━━━━━━━━━━━━━━━"]
            buy_spikes=[s for s in spikes if s["direction"]=="BUY"]
            sell_spikes=[s for s in spikes if s["direction"]=="SELL"]
            if buy_spikes:
                lines.append("🟢 *BUY VOLUME SPIKE:*")
                for s in buy_spikes[:5]:
                    lines.append(f"  ▲ *{s['code']}* `${s['price']:,.2f}` {s['chg']:+.2f}% Vol:{s['vr']:.1f}x")
            if sell_spikes:
                lines.append("🔴 *SELL VOLUME SPIKE:*")
                for s in sell_spikes[:5]:
                    lines.append(f"  ▼ *{s['code']}* `${s['price']:,.2f}` {s['chg']:+.2f}% Vol:{s['vr']:.1f}x")
            lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
            await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
            if spikes:
                top=spikes[0]
                buf,_=generate_chart(top["code"],"5M")
                if buf:
                    dir_txt="🟢 BUY SPIKE" if top["direction"]=="BUY" else "🔴 SELL SPIKE"
                    await bot.send_photo(int(uid),photo=buf,
                        caption=f"📊 {top['code']} | {dir_txt} | Vol:{top['vr']:.1f}x avg | {fmt_now()}")
        except Exception as e: log.error(f"US spike alert error uid {uid}: {e}")

async def flip_pixel_scan(context):
    if not is_weekday(): return
    if not auto_users: return
    if not (is_idx_market_open() or is_us_market_open()): return
    bot=context.bot
    all_stocks=[(c,"D") for c in IDX_STOCKS]+[(c,"D") for c in US_STOCKS[:20]]
    flips_bull=[]; flips_bear=[]

    def check_flip(code_tf):
        code,tf=code_tf
        new_state=get_trend_state(code,tf)
        if new_state is None: return None
        old_state=flip_state_db.get(code,"neutral")
        flip_state_db[code]=new_state
        if old_state in ("bear","neutral") and new_state=="bull":
            r=get_signal(code,tf)
            if "error" not in r and r.get("liquid",True): return ("bull",code,r)
        elif old_state in ("bull","neutral") and new_state=="bear":
            r=get_signal(code,tf)
            if "error" not in r: return ("bear",code,r)
        return None

    loop=asyncio.get_event_loop()
    results=await loop.run_in_executor(None,lambda:[check_flip(ct) for ct in all_stocks])
    for res in results:
        if res is None: continue
        direction,code,r=res
        if direction=="bull": flips_bull.append((code,r))
        else: flips_bear.append((code,r))
    save_json(FLIP_FILE,flip_state_db)
    if not flips_bull and not flips_bear: return
    now_str=datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
    for uid in auto_users:
        try:
            if flips_bull:
                lines=["🚀 *PIXEL FLIP — BEARISH ➜ BULLISH*",f"🕐 {now_str}","━━━━━━━━━━━━━━━━━━━━"]
                for code,r in flips_bull[:6]:
                    is_idr=code.endswith(".JK")
                    px=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                    chg=f"+{r['chg']:.2f}%" if r['chg']>=0 else f"{r['chg']:.2f}%"
                    sig=r['sigs'][0].split('-')[0].strip() if r['sigs'] else 'No Signal'
                    lines.append(f"✅ *{code}* `{px}` {chg} | Score:`{r['score']}/8` | {sig}")
                lines+=["━━━━━━━━━━━━━━━━━━━━","📊 EMA: Price > EMA9 > MA20 > MA50","💡 Konfirmasi entry!"]
                await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
                buf,_=generate_chart(flips_bull[0][0],"D")
                if buf: await bot.send_photo(int(uid),photo=buf,
                    caption=f"🚀 FLIP BULLISH: {flips_bull[0][0]} | Score:{flips_bull[0][1]['score']}/8 | {now_str}")
            if flips_bear:
                lines=["⚠️ *PIXEL FLIP — BULLISH ➜ BEARISH*",f"🕐 {now_str}","━━━━━━━━━━━━━━━━━━━━"]
                for code,r in flips_bear[:6]:
                    is_idr=code.endswith(".JK")
                    px=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                    chg=f"+{r['chg']:.2f}%" if r['chg']>=0 else f"{r['chg']:.2f}%"
                    lines.append(f"🔴 *{code}* `{px}` {chg} | Score:`{r['score']}/8` | CUT/AVOID")
                lines+=["━━━━━━━━━━━━━━━━━━━━","📊 EMA: Price < EMA9 < MA20 < MA50","⚡ Waspada distribusi!"]
                await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
        except Exception as e: log.error(f"flip alert uid {uid}: {e}")

async def doji_auto_scan(context):
    """Auto scan doji bullish reversal IDX tiap 1 jam saat market buka"""
    if not is_weekday(): return
    if not auto_users: return
    if not is_idx_market_open(): return
    bot = context.bot
    results = await asyncio.get_event_loop().run_in_executor(
        None, doji_scan_all_tf, IDX_STOCKS)
    total = sum(len(v) for v in results.values())
    if total == 0: return
    msg = fmt_doji_msg(results, "IDX")
    for uid in auto_users:
        try:
            await bot.send_message(int(uid), msg, parse_mode="Markdown")
            best = None; best_tf = "D"
            for tf in ["4H","D","1H"]:
                hits = results.get(tf,[])
                liquid_hits = [h for h in hits if h["liquid"]]
                if liquid_hits: best=liquid_hits[0]; best_tf=tf; break
                elif hits: best=hits[0]; best_tf=tf; break
            if best:
                buf, _ = generate_chart(best["code"], best_tf)
                if buf:
                    is_idr = best["ticker"].endswith(".JK")
                    px = f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
                    # Ambil sinyal untuk hitung TP/SL
                    r_best = get_signal(best["code"], best_tf)
                    ts_cap = ""
                    if "error" not in r_best:
                        ts = calculate_tp_sl(r_best)
                        ts_cap = (f"\n━━━━━━━━━━━━━━━\n"
                                  f"🎯 TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) "
                                  f"TP2:`{ts['tp2_str']}`({ts['tp2_pct']:+.1f}%)\n"
                                  f"🛡 SL1:`{ts['sl1_str']}`({ts['sl1_pct']:+.1f}%) "
                                  f"SL2:`{ts['sl2_str']}`({ts['sl2_pct']:+.1f}%)\n"
                                  f"⚖️ R/R:`{ts['rr']}x`")
                    await bot.send_photo(int(uid), photo=buf,
                        caption=(f"🕯 TOP DOJI: *{best['code']}* | TF:{best_tf} | `{px}`\n"
                                 f"{best['doji_emoji']} {best['doji_type']} | BullScore:`{best['bull_score']}`\n"
                                 f"RSI:`{best['rsi']:.0f}` STOCH:`{best['stoch']:.0f}`\n"
                                 f"💡 {' | '.join(best['bull_factors'][:3])}"
                                 f"{ts_cap}"),
                        parse_mode="Markdown")
        except Exception as e:
            log.error(f"doji auto scan uid {uid}: {e}")

async def evening_summary(context, force=False):
    """📋 Rekap harian otomatis jam 16:00 WIB — top sinyal, mover, vol spike"""
    if not force and not is_weekday(): return
    if not auto_users: return
    bot = context.bot
    now = datetime.now(WIB)
    date_str = now.strftime("%d %b %Y")

    res = await asyncio.get_event_loop().run_in_executor(
        None, parallel_signal_scan, IDX_STOCKS, "D", 3)
    res_liq = [r for r in res if r.get("liquid", True)]
    fire     = [r for r in res_liq if r["score"] >= 6][:5]
    vol_spike= [r for r in res if r.get("vr",0) >= 2][:5]
    top_bear = sorted([r for r in res_liq if r["chg"] < 0], key=lambda x: x["chg"])[:3]
    up_ct  = sum(1 for r in res if r["chg"] >= 0)
    dn_ct  = sum(1 for r in res if r["chg"] < 0)
    mood = "BULLISH 🟢" if up_ct > dn_ct else "BEARISH 🔴" if dn_ct > up_ct else "MIXED ↔"

    lines = [
        f"🌆 *EVENING SUMMARY IDX — {date_str}*",
        f"🕐 Penutupan | Recap {len(res)} saham",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📊 Mood Pasar: *{mood}*",
        f"🟢 Naik: `{up_ct}` | 🔴 Turun: `{dn_ct}`", ""
    ]
    if fire:
        lines.append("🔥 *TOP SIGNAL HARI INI (Score 6+):*")
        for r in fire:
            top = r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
            ts  = calculate_tp_sl(r)
            is_idr = r["ticker"].endswith(".JK")
            px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
            lines.append(
                f"  🔥 *{r['code']}* `{px}` {r['chg']:+.2f}% Score:`{r['score']}/8`\n"
                f"     {top} | TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) SL1:`{ts['sl1_str']}`"
            )
        lines.append("")
    if vol_spike:
        lines.append("🌊 *VOLUME SPIKE HARI INI:*")
        for r in vol_spike[:4]:
            em = "▲" if r["chg"] >= 0 else "▼"
            is_idr = r["ticker"].endswith(".JK")
            px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
            lines.append(f"  {em} *{r['code']}* `{px}` {r['chg']:+.2f}% Vol:`{r['vr']:.1f}x`")
        lines.append("")
    if top_bear:
        lines.append("📉 *TOP LOSERS:*")
        for r in top_bear:
            is_idr = r["ticker"].endswith(".JK")
            px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
            lines.append(f"  🔴 *{r['code']}* `{px}` {r['chg']:+.2f}%")
        lines.append("")
    lines += ["━━━━━━━━━━━━━━━━━━━━",
              "💡 Gunakan `/screener` untuk full scan",
              "🌅 Morning scan besok jam *09:00 WIB*",
              f"⏱ {fmt_now()}"]

    for uid in auto_users:
        try:
            await bot.send_message(int(uid), "\n".join(lines), parse_mode="Markdown")
            if fire:
                best = fire[0]; buf, _ = generate_chart(best["code"], "D")
                if buf:
                    ts = calculate_tp_sl(best)
                    is_idr = best["ticker"].endswith(".JK")
                    px = f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
                    await bot.send_photo(int(uid), photo=buf,
                        caption=(f"🏆 *TOP PICK: {best['code']}* | `{px}` {best['chg']:+.2f}%\n"
                                 f"Score:`{best['score']}/8` | {best['trend']}\n"
                                 f"━━━━━━━━━━━━━━━\n"
                                 f"🎯 TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) "
                                 f"TP2:`{ts['tp2_str']}`({ts['tp2_pct']:+.1f}%)\n"
                                 f"🛡 SL1:`{ts['sl1_str']}`({ts['sl1_pct']:+.1f}%) "
                                 f"R/R:`{ts['rr']}x`\n⏱ {fmt_now()}"),
                        parse_mode="Markdown")
        except Exception as e: log.error(f"evening summary uid {uid}: {e}")

async def morning_scan(context):
    if not is_weekday(): return
    if not auto_users: return
    now=datetime.now(WIB); bot=context.bot
    # ✅ FIX: Parallel morning scan
    res = await asyncio.get_event_loop().run_in_executor(
        None, parallel_signal_scan, IDX_STOCKS, "D", 4)
    res=[r for r in res if r.get("liquid",True)]
    for uid in auto_users:
        try:
            lines=["🌅 *MORNING SCAN IDX — "+now.strftime("%d %b %Y")+"*",
                   "━━━━━━━━━━━━━━━━━━━━","🔥 Top picks hari ini (liquid only):\n"]
            for r in res[:8]:
                em="🟢" if r["chg"]>=0 else "🔴"
                top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
                lines.append(f"{em} *{r['code']}* `{r['price']:,.0f}` Score:`{r['score']}/8` {top}")
            lines+=["━━━━━━━━━━━━━━━━━━━━",
                    "🤖 IDX scan aktif 09:00-15:15 WIB\n🇺🇸 US scan aktif 21:30-04:00 WIB"]
            await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
            for r in res[:3]:
                buf,_=generate_chart(r["code"],"D")
                if buf:
                    ts = calculate_tp_sl(r)
                    is_idr = r.get("ticker","").endswith(".JK")
                    await bot.send_photo(int(uid),photo=buf,
                        caption=(f"📊 *{r['code']}* | Score:{r['score']}/8 | {r['trend']}\n"
                                 f"━━━━━━━━━━━━━━━\n"
                                 f"🎯 TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) "
                                 f"TP2:`{ts['tp2_str']}`({ts['tp2_pct']:+.1f}%) "
                                 f"TP3:`{ts['tp3_str']}`({ts['tp3_pct']:+.1f}%)\n"
                                 f"🛡 SL1:`{ts['sl1_str']}`({ts['sl1_pct']:+.1f}%) "
                                 f"SL2:`{ts['sl2_str']}`({ts['sl2_pct']:+.1f}%) "
                                 f"SL3:`{ts['sl3_str']}`({ts['sl3_pct']:+.1f}%)\n"
                                 f"⚖️ R/R:`{ts['rr']}x`"),
                        parse_mode="Markdown")
        except Exception as e: log.error(f"morning scan error uid {uid}: {e}")

# ══ FLASK ══
app=Flask(__name__)
@app.route("/")
def index():
    f=os.path.join(os.path.dirname(__file__),"idx_dashboard_v4.html")
    return send_file(f) if os.path.exists(f) else ("IDX QUANT v4",200)
@app.route("/dashboard")
@app.route("/pixel")
def pixel_dashboard():
    f=os.path.join(os.path.dirname(__file__),"pixel_dashboard.html")
    return send_file(f) if os.path.exists(f) else ("pixel_dashboard.html not found",404)
@app.route("/health")
def health(): return jsonify({"status":"ok","version":"v4","alerts":len(alerts_db),
                               "auto_users":len(auto_users),"cache_size":len(_data_cache),
                               "idx_market_open":is_idx_market_open(),
                               "us_market_open":is_us_market_open()})
@app.route("/api/signal/<code>")
def api_sig(code):
    r=get_signal(code.upper(),"D")
    return jsonify({k:v for k,v in r.items() if k not in ["df","ema9","ema20","ema50","rsi_s","macd_l","macd_sg","macd_h","stoch_k","stoch_d"]})

def run_flask(): app.run(host="0.0.0.0",port=PORT,debug=False,use_reloader=False)

async def summary_cmd(u,c):
    """Command /summary [us] — trigger summary manual IDX atau US"""
    args   = c.args
    market = "us" if args and args[0].lower() == "us" else "idx"
    flag   = "🇺🇸" if market == "us" else "🇮🇩"
    label  = "US STOCKS" if market == "us" else "IDX"
    stocks = US_STOCKS if market == "us" else IDX_STOCKS

    m = await u.message.reply_text(
        f"📊 Menyiapkan {flag} *{label} Summary*, tunggu ~30 detik...",
        parse_mode="Markdown")
    now          = datetime.now(WIB)
    date_str     = now.strftime("%d %b %Y")
    weekend_note = " *(Data penutupan Jumat)*" if not is_weekday() else ""

    try:
        res = await asyncio.get_event_loop().run_in_executor(
            None, parallel_signal_scan, stocks, "D", 3)
        res_liq   = [r for r in res if r.get("liquid", True)]
        fire      = [r for r in res_liq if r["score"] >= 6][:5]
        vol_spike = [r for r in res if r.get("vr",0) >= 2][:5]
        top_bull  = sorted([r for r in res_liq if r["chg"] > 0], key=lambda x: -x["chg"])[:3]
        top_bear  = sorted([r for r in res_liq if r["chg"] < 0], key=lambda x: x["chg"])[:3]
        up_ct = sum(1 for r in res if r["chg"] >= 0)
        dn_ct = sum(1 for r in res if r["chg"] < 0)
        mood  = "BULLISH 🟢" if up_ct > dn_ct else "BEARISH 🔴" if dn_ct > up_ct else "MIXED ↔"

        lines = [
            f"{flag} *{label} SUMMARY — {date_str}*{weekend_note}",
            f"🕐 Recap {len(res)} saham",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 Mood Pasar: *{mood}*",
            f"🟢 Naik: `{up_ct}` | 🔴 Turun: `{dn_ct}`", ""
        ]
        if fire:
            lines.append("🔥 *TOP SIGNAL (Score 6+):*")
            for r in fire:
                top = r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
                ts  = calculate_tp_sl(r)
                is_idr = r["ticker"].endswith(".JK")
                px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                lines.append(
                    f"  🔥 *{r['code']}* `{px}` {r['chg']:+.2f}% Score:`{r['score']}/8`\n"
                    f"     {top} | TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) SL1:`{ts['sl1_str']}`"
                )
            lines.append("")
        if vol_spike:
            lines.append("🌊 *VOLUME SPIKE:*")
            for r in vol_spike[:4]:
                em = "▲" if r["chg"] >= 0 else "▼"
                is_idr = r["ticker"].endswith(".JK")
                px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                lines.append(f"  {em} *{r['code']}* `{px}` {r['chg']:+.2f}% Vol:`{r['vr']:.1f}x`")
            lines.append("")
        if market == "us" and top_bull:
            lines.append("🚀 *TOP GAINERS:*")
            for r in top_bull:
                ts = calculate_tp_sl(r)
                px = f"${r['price']:,.2f}"
                lines.append(f"  🟢 *{r['code']}* `{px}` {r['chg']:+.2f}% TP1:`{ts['tp1_str']}`")
            lines.append("")
        if top_bear:
            lines.append("📉 *TOP LOSERS:*")
            for r in top_bear:
                is_idr = r["ticker"].endswith(".JK")
                px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                lines.append(f"  🔴 *{r['code']}* `{px}` {r['chg']:+.2f}%")
            lines.append("")
        lines += ["━━━━━━━━━━━━━━━━━━━━",
                  f"💡 Gunakan `/screener{'_us' if market=='us' else ''}` untuk full scan",
                  f"📌 `/summary` = IDX | `/summary us` = 🇺🇸 US",
                  f"⏱ {fmt_now()}"]

        await m.delete()
        await u.message.reply_text("\n".join(lines), parse_mode="Markdown")

        # Kirim chart top pick
        if fire:
            best = fire[0]; buf, _ = generate_chart(best["code"], "D")
            if buf:
                ts = calculate_tp_sl(best)
                is_idr = best["ticker"].endswith(".JK")
                px = f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
                await u.message.reply_photo(photo=buf,
                    caption=(f"🏆 *TOP PICK {flag}: {best['code']}* | `{px}` {best['chg']:+.2f}%\n"
                             f"Score:`{best['score']}/8` | {best['trend']}\n"
                             f"━━━━━━━━━━━━━━━\n"
                             f"🎯 TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) "
                             f"TP2:`{ts['tp2_str']}`({ts['tp2_pct']:+.1f}%)\n"
                             f"🛡 SL1:`{ts['sl1_str']}`({ts['sl1_pct']:+.1f}%) "
                             f"R/R:`{ts['rr']}x`\n⏱ {fmt_now()}"),
                    parse_mode="Markdown")
    except Exception as e:
        await m.edit_text(f"❌ Error summary: {e}")

async def pattern_cmd(u, c):
    """Command /pattern [idx/us] — scan semua saham untuk pattern"""
    args = c.args
    market = "us" if args and args[0].lower() == "us" else "idx"
    flag = "🇺🇸" if market == "us" else "🇮🇩"
    stocks = US_STOCKS if market == "us" else IDX_STOCKS
    label = "US" if market == "us" else "IDX"
    m = await u.message.reply_text(
        "Scanning Pattern " + label + "...\nTriangle | Double Bottom | Cup&Handle | H&S\nHarap tunggu...",
        parse_mode="Markdown")
    try:
        results = await asyncio.get_event_loop().run_in_executor(
            None, parallel_pattern_scan, stocks, "D")
        if not results:
            await m.edit_text(f"Tidak ada pattern terdeteksi saat ini di {label}.")
            return
        now_str = datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
        lines = [f"🎯 *{flag} {label} PATTERN SCANNER*",
                 f"🕐 {now_str} | {len(results)} saham",
                 "━━━━━━━━━━━━━━━━━━━━"]
        db_list  = [r for r in results if any(p[0]=="double_bottom" for p in r["patterns"])]
        ch_list  = [r for r in results if any(p[0]=="cup_handle"    for p in r["patterns"])]
        tri_list = [r for r in results if any(p[0]=="triangle"      for p in r["patterns"])]
        hs_list  = [r for r in results if any(p[0]=="hs"            for p in r["patterns"])]
        if db_list:
            lines.append(f"\n〰️ *DOUBLE BOTTOM ({len(db_list)}):*")
            for r in db_list[:5]:
                is_idr=r["is_idr"]; px=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                db=next(p[1] for p in r["patterns"] if p[0]=="double_bottom")
                st="✅" if db["confirmed"] else "⏳"
                liq="⚠️" if not r.get("liquid",True) else ""
                lines.append(f"  {st} *{r['code']}* `{px}` {r['chg']:+.2f}% Depth:`{db['depth_pct']:.1f}%` {liq}")
        if ch_list:
            lines.append(f"\n☕ *CUP & HANDLE ({len(ch_list)}):*")
            for r in ch_list[:5]:
                is_idr=r["is_idr"]; px=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                ch2=next(p[1] for p in r["patterns"] if p[0]=="cup_handle")
                st="✅" if ch2["confirmed"] else "⏳"
                lines.append(f"  {st} *{r['code']}* `{px}` {r['chg']:+.2f}% Depth:`{ch2['cup_depth_pct']:.1f}%`")
        if tri_list:
            lines.append(f"\n📐 *TRIANGLE ({len(tri_list)}):*")
            for r in tri_list[:5]:
                is_idr=r["is_idr"]; px=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                tri2=next(p[1] for p in r["patterns"] if p[0]=="triangle")
                short=tri2["type"].split()[0]
                lines.append(f"  📐 *{r['code']}* `{px}` {r['chg']:+.2f}% {short} — {tri2['quality']}")
        if hs_list:
            lines.append(f"\n🔻 *HEAD & SHOULDERS ({len(hs_list)}):*")
            for r in hs_list[:5]:
                is_idr=r["is_idr"]; px=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                hs2=next(p[1] for p in r["patterns"] if p[0]=="hs")
                st="✅" if hs2["confirmed"] else "⏳"
                emoji="🔺" if hs2["is_inverse"] else "🔻"
                lines.append(f"  {st}{emoji} *{r['code']}* `{px}` {r['chg']:+.2f}% {hs2['signal']}")
        lines += ["━━━━━━━━━━━━━━━━━━━━",
                  "✅=Confirmed ⏳=Forming ⚠️=Low Liq",
                  "💡 `/chart KODE` untuk lihat chart lengkap",
                  "📌 `/pattern` = IDX | `/pattern us` = 🇺🇸 US"]
        await m.edit_text("\n".join(lines), parse_mode="Markdown")
        best = next((r for r in results if any(
            p[1].get("confirmed",False) for p in r["patterns"] if isinstance(p[1],dict)
        )), results[0] if results else None)
        if best:
            buf,_=generate_chart(best["code"],"D")
            if buf:
                is_idr=best["is_idr"]; px=f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
                pat_names=" | ".join(set(p[0].replace("_"," ").title() for p in best["patterns"]))
                await u.message.reply_photo(photo=buf,
                    caption=(f"🎯 *TOP PATTERN {flag}: {best['code']}*\n"
                             f"`{px}` {best['chg']:+.2f}% | Score:`{best['score']}/8`\n"
                             f"Pattern: {pat_names}\n⏱ {fmt_now()}"),
                    parse_mode="Markdown")
    except Exception as e:
        await m.edit_text(f"Error pattern scan: {e}")

async def breakout_alert_scan(context):
    """Auto scan breakout pattern tiap 30 menit."""
    if not is_weekday(): return
    if not (is_idx_market_open() or is_us_market_open()): return
    bot = context.bot
    stocks = IDX_STOCKS + US_STOCKS
    all_alerts = []
    for code in stocks:
        try:
            alerts = await asyncio.get_event_loop().run_in_executor(
                None, check_pattern_breakout, code, "D")
            all_alerts.extend(alerts)
        except: pass
    if not all_alerts: return
    for uid in list(auto_users.keys()):
        try:
            for alert in all_alerts[:5]:
                await bot.send_message(int(uid), alert["msg"], parse_mode="Markdown")
                buf,_=generate_chart(alert["code"],"D")
                if buf:
                    r2=get_signal(alert["code"],"D")
                    if "error" not in r2:
                        ts=calculate_tp_sl(r2)
                        is_idr=alert["is_idr"]
                        pf=lambda p:f"Rp {p:,.0f}" if is_idr else f"${p:,.2f}"
                        await bot.send_photo(int(uid),photo=buf,
                            caption=(f"📊 *{alert['code']}* | `{pf(alert['price'])}`\n"
                                     f"TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) "
                                     f"TP2:`{ts['tp2_str']}`({ts['tp2_pct']:+.1f}%)\n"
                                     f"SL1:`{ts['sl1_str']}`({ts['sl1_pct']:+.1f}%) R/R:`{ts['rr']}x`"),
                            parse_mode="Markdown")
        except Exception as e:
            log.error(f"Breakout alert uid {uid}: {e}")


async def pattern_cmd(u, c):
    args = c.args
    market = "us" if args and args[0].lower() == "us" else "idx"
    flag = "\U0001f1fa\U0001f1f8" if market == "us" else "\U0001f1ee\U0001f1e9"
    stocks = US_STOCKS if market == "us" else IDX_STOCKS
    label = "US" if market == "us" else "IDX"
    m = await u.message.reply_text(
        "Scanning Pattern " + label + "... Triangle | Double Bottom | Cup&Handle | H&S\nHarap tunggu...",
        parse_mode="Markdown")
    try:
        results = await asyncio.get_event_loop().run_in_executor(
            None, parallel_pattern_scan, stocks, "D")
        if not results:
            await m.edit_text("Tidak ada pattern terdeteksi di " + label + " saat ini.")
            return
        now_str = datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
        lines_msg = [
            "*" + flag + " " + label + " PATTERN SCANNER*",
            now_str + " | " + str(len(results)) + " saham",
            "━━━━━━━━━━━━━━━━━━━━"
        ]
        db_list  = [r for r in results if any(p[0]=="double_bottom" for p in r["patterns"])]
        ch_list  = [r for r in results if any(p[0]=="cup_handle"    for p in r["patterns"])]
        tri_list = [r for r in results if any(p[0]=="triangle"      for p in r["patterns"])]
        hs_list  = [r for r in results if any(p[0]=="hs"            for p in r["patterns"])]
        if db_list:
            lines_msg.append("\n\u3030\ufe0f *DOUBLE BOTTOM (" + str(len(db_list)) + "):*")
            for r in db_list[:5]:
                is_idr=r["is_idr"]
                px="Rp {:,.0f}".format(r["price"]) if is_idr else "${:,.2f}".format(r["price"])
                db=next(p[1] for p in r["patterns"] if p[0]=="double_bottom")
                st="\u2705" if db["confirmed"] else "\u23f3"
                liq="\u26a0\ufe0f" if not r.get("liquid",True) else ""
                lines_msg.append("  " + st + " *" + r["code"] + "* `" + px + "` {:+.2f}% Depth:`{:.1f}%` ".format(r["chg"],db["depth_pct"]) + liq)
        if ch_list:
            lines_msg.append("\n\u2615 *CUP & HANDLE (" + str(len(ch_list)) + "):*")
            for r in ch_list[:5]:
                is_idr=r["is_idr"]
                px="Rp {:,.0f}".format(r["price"]) if is_idr else "${:,.2f}".format(r["price"])
                ch2=next(p[1] for p in r["patterns"] if p[0]=="cup_handle")
                st="\u2705" if ch2["confirmed"] else "\u23f3"
                lines_msg.append("  " + st + " *" + r["code"] + "* `" + px + "` {:+.2f}% Depth:`{:.1f}%`".format(r["chg"],ch2["cup_depth_pct"]))
        if tri_list:
            lines_msg.append("\n\U0001f4d0 *TRIANGLE (" + str(len(tri_list)) + "):*")
            for r in tri_list[:5]:
                is_idr=r["is_idr"]
                px="Rp {:,.0f}".format(r["price"]) if is_idr else "${:,.2f}".format(r["price"])
                tri2=next(p[1] for p in r["patterns"] if p[0]=="triangle")
                short=tri2["type"].split()[0]
                lines_msg.append("  \U0001f4d0 *" + r["code"] + "* `" + px + "` {:+.2f}% {} - {}".format(r["chg"],short,tri2["quality"]))
        if hs_list:
            lines_msg.append("\n\U0001f53b *HEAD & SHOULDERS (" + str(len(hs_list)) + "):*")
            for r in hs_list[:5]:
                is_idr=r["is_idr"]
                px="Rp {:,.0f}".format(r["price"]) if is_idr else "${:,.2f}".format(r["price"])
                hs2=next(p[1] for p in r["patterns"] if p[0]=="hs")
                st="\u2705" if hs2["confirmed"] else "\u23f3"
                emoji="\U0001f53a" if hs2["is_inverse"] else "\U0001f53b"
                lines_msg.append("  " + st + emoji + " *" + r["code"] + "* `" + px + "` {:+.2f}% {}".format(r["chg"],hs2["signal"]))
        lines_msg += ["━━━━━━━━━━━━━━━━━━━━",
                      "\u2705=Confirmed \u23f3=Forming \u26a0\ufe0f=Low Liq",
                      "💡 `/chart KODE` lihat chart",
                      "📌 `/pattern` = IDX | `/pattern us` = US"]
        await m.edit_text("\n".join(lines_msg), parse_mode="Markdown")
        best = next((r for r in results if any(
            p[1].get("confirmed",False) for p in r["patterns"] if isinstance(p[1],dict)
        )), results[0] if results else None)
        if best:
            buf,_=generate_chart(best["code"],"D")
            if buf:
                is_idr=best["is_idr"]
                px="Rp {:,.0f}".format(best["price"]) if is_idr else "${:,.2f}".format(best["price"])
                pat_names=" | ".join(set(p[0].replace("_"," ").title() for p in best["patterns"]))
                await u.message.reply_photo(photo=buf,
                    caption="*TOP PATTERN " + flag + ": " + best["code"] + "*\n`" + px + "` {:+.2f}% Score:`{}/8`\nPattern: {}\n{}".format(best["chg"],best["score"],pat_names,fmt_now()),
                    parse_mode="Markdown")
    except Exception as e:
        await m.edit_text("Error pattern scan: " + str(e))

async def breakout_alert_scan(context):
    if not is_weekday(): return
    if not (is_idx_market_open() or is_us_market_open()): return
    bot = context.bot
    stocks = IDX_STOCKS + US_STOCKS
    all_alerts = []
    for code in stocks:
        try:
            alerts = await asyncio.get_event_loop().run_in_executor(
                None, check_pattern_breakout, code, "D")
            all_alerts.extend(alerts)
        except: pass
    if not all_alerts: return
    for uid in list(auto_users.keys()):
        try:
            for alert in all_alerts[:5]:
                await bot.send_message(int(uid), alert["msg"], parse_mode="Markdown")
                buf,_=generate_chart(alert["code"],"D")
                if buf:
                    r2=get_signal(alert["code"],"D")
                    if "error" not in r2:
                        ts=calculate_tp_sl(r2)
                        is_idr=alert["is_idr"]
                        pf=lambda p,idr=is_idr:"Rp {:,.0f}".format(p) if idr else "${:,.2f}".format(p)
                        await bot.send_photo(int(uid),photo=buf,
                            caption="*" + alert["code"] + "* | `" + pf(alert["price"]) + "`\nTP1:`" + ts["tp1_str"] + "`(" + "{:+.1f}%".format(ts["tp1_pct"]) + ") TP2:`" + ts["tp2_str"] + "`(" + "{:+.1f}%".format(ts["tp2_pct"]) + ")\nSL1:`" + ts["sl1_str"] + "`(" + "{:+.1f}%".format(ts["sl1_pct"]) + ") R/R:`" + str(ts["rr"]) + "x`",
                            parse_mode="Markdown")
        except Exception as e:
            log.error("Breakout alert uid " + str(uid) + ": " + str(e))

def run_bot():
    if not TOKEN: log.warning("TELEGRAM_TOKEN not set"); return
    tg=Application.builder().token(TOKEN).build()
    cmds=[("start",start),("help",help_cmd),("flipstatus",flipstatus_cmd),
          ("signal",signal_cmd),("chart",chart_cmd),
          ("tp",tp_cmd),("summary",summary_cmd),
          ("screener",screener_cmd),("screener_us",screener_us_cmd),
          ("doji",doji_cmd),("volmom",volmom_cmd),
          ("pattern",pattern_cmd),
          ("auto",auto_cmd),("volume",volume_cmd),("trend",trend_cmd)]
    for cmd,fn in cmds: tg.add_handler(CommandHandler(cmd,fn))
    jq=tg.job_queue
    jq.run_repeating(volume_spike_scan_idx,interval=900,first=120)
    jq.run_repeating(volume_spike_scan_us,interval=900,first=180)
    jq.run_daily(morning_scan,time=dtime(9,0,tzinfo=WIB))
    jq.run_daily(evening_summary,time=dtime(16,5,tzinfo=WIB))
    jq.run_repeating(flip_pixel_scan,interval=1800,first=300)
    jq.run_repeating(doji_auto_scan,interval=3600,first=600)
    jq.run_repeating(volmom_auto_scan,interval=1800,first=900)
    jq.run_repeating(breakout_alert_scan,interval=1800,first=1200)
    now=datetime.now(WIB)
    if now.weekday()>=5:
        log.info("Bot start " + now.strftime('%A') + " - auto scan OFF")
    else:
        log.info("IDX QUANT Bot v5 polling - aktif")
    tg.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    log.info("IDX QUANT v5 port " + str(PORT))
    import threading as _th
    _th.Thread(target=run_flask,daemon=True).start()
    run_bot()
