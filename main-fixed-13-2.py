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
IDX_STOCKS=[
    # ══ BANKING ══
    "BBCA","BBRI","BMRI","BBNI","BRIS","BTPS","BNGA","NISP","BDMN","MEGA",
    "PNBN","ARTO","BBYB","AGRO","BJTM","BJBR",
    # ══ TELCO & TECH ══
    "TLKM","ISAT","MTEL","EXCL","GOTO","EMTK","FILM","NFCX","SLIS","DMMX",
    # ══ ENERGY & COAL ══
    "ADRO","BYAN","PTBA","ITMG","HRUM","INDY","BSSR","DEWA","FIRE","RAJA",
    "PTRO","ELSA","PGAS","MEDC","ENRG","ADMR","RUIS",
    # ══ MINING & METAL ══
    "ANTM","INCO","NCKL","MBMA","MDKA","TINS","ZINC","NICK","DKFT","HAIS",
    "AMMN","AMNT","PSAB","PNRE","CITA",
    # ══ PLANTATION & AGRI ══
    "AALI","LSIP","SSMS","DSNG","TAPG","SGRO","PALM","BWPT","TBLA",
    # ══ CONSUMER & RETAIL ══
    "ICBP","INDF","UNVR","MYOR","CLEO","ACES","MAPA","RALS","AMRT","HERO",
    "HMSP","GGRM","WIIM","SIDO","KLBF","KAEF","TSPC","MIKA","HEAL",
    # ══ PROPERTY & KONSTRUKSI ══
    "CTRA","BSDE","SMRA","PWON","KIJA","LPKR","ASRI","PANI","MDLN","DMAS",
    "WIKA","WSKT","PTPP","ADHI","TOTL",
    # ══ INDUSTRI & MANUFAKTUR ══
    "ASII","ASTRA","INTP","SMGR","WTON","TPIA","BRPT","INKP","TKIM","FASW",
    "SMBR","CPIN","JPFA","MAIN","BISI","GJTL","AUTO","SMSM","IMAS","LPIN",
    # ══ INFRASTRUKTUR & TRANSPORTASI ══
    "JSMR","BULL","TMAS","SMDR","MBSS","TRUK","BPJT",
    # ══ TELEKOMUNIKASI & MEDIA ══
    "SCMA","MNCN","KPIG",
    # ══ KEUANGAN NON-BANK ══
    "PNIN","MFIN","BFIN","WOMF","BPFI",
    # ══ DIVERSIFIED & KONGLOMERAT ══
    "CUAN","PACK","BSBK",
]

# ✅ FIX: Hapus duplikat MU, tambah SNDK/COHR/GLW
US_STOCKS=[
    # ══ MAG 7 + MEGA CAP ══
    "AAPL","MSFT","NVDA","GOOGL","GOOG","AMZN","META","TSLA","AVGO","BRK.B",
    # ══ QQQ TOP HOLDINGS ══
    "AMD","QCOM","INTC","TXN","MU","AMAT","LRCX","KLAC","MRVL","NXPI",
    "ON","ADI","MCHP","MPWR","SWKS","ENPH","FSLR",
    "CSCO","ORCL","IBM","ACN","NOW","CRM","ADBE","INTU","WDAY","TEAM",
    "SNPS","CDNS","ANSS","PTC","FTNT","PANW","CRWD","NET","ZS","OKTA",
    "ABNB","BKNG","EXPE","LYFT","UBER","DASH",
    "NFLX","SPOT","WBD","PARA","DIS",
    "PYPL","SQ","AFRM","SOFI","HOOD","COIN","MSTR",
    # ══ S&P 500 BLUE CHIPS ══
    "JPM","BAC","WFC","GS","MS","C","AXP","BLK","SCHW","V","MA",
    "UNH","JNJ","PFE","MRK","ABBV","LLY","BMY","AMGN","GILD","BIIB",
    "XOM","CVX","COP","SLB","EOG","PSX","VLO","MPC",
    "CAT","DE","HON","GE","MMM","RTX","LMT","NOC","GD","BA",
    "COST","WMT","TGT","HD","LOW","NKE","SBUX","MCD","YUM",
    "TSCO","ROST","TJX","ULTA","LULU",
    "NEE","DUK","SO","D","AEP","EXC","PCG",
    "AMT","PLD","EQIX","SPG","O","PSA","WELL",
    "LIN","APD","SHW","DD","DOW","PPG",
    "UPS","FDX","DAL","UAL","AAL","JBLU",
    # ══ GROWTH & TECH LAINNYA ══
    "PLTR","SNOW","DDOG","DATADOG","ZM","DOCN","CFLT","MDB","ESTC",
    "SHOP","MELI","SE","GRAB","BABA","JD","PDD","NIO","LI","XPEV",
    "RKLB","SPCE","LUNR","ACHR","JOBY",
    # ══ QUANTUM & AI PLAY ══
    "IONQ","QUBT","RGTI","QBTS","ARQQ","QTUM",
    "SMCI","HPE","DELL","NTAP","PSTG",
    # ══ SEMICONDUCTOR ══
    "TSM","ASML","SNDK","COHR","GLW","WOLF","CREE","ACLS","ONTO",
    "ARM","SLAB","SITM","DIOD","AMBA",
    # ══ ETF BENCHMARK ══
    "SPY","QQQ","IWM","DIA","XLK","XLF","XLE","XLV","XLI","SOXX",
    # ══ CRYPTO & FINTECH ══
    "MARA","CLSK","RIOT","BITF","HUT","CIFR",
    # ══ HEALTHCARE & BIOTECH ══
    "MRNA","BNTX","REGN","VRTX","ISRG","DXCM","ALGN","IDXX",
    # ══ MEDIA & ENTERTAINMENT ══
    "SNAP","PINS","TWTR","RDDT","APP","TTD","MGNI",
]

# ══ HARI LIBUR BURSA IDX 2026 ══
from datetime import date as _date
IDX_HOLIDAYS = {
    _date(2026,1,1),   # Tahun Baru
    _date(2026,1,27),  # Isra Miraj
    _date(2026,1,28),  # Cuti bersama Imlek
    _date(2026,1,29),  # Tahun Baru Imlek
    _date(2026,3,20),  # Nyepi
    _date(2026,3,31),  # Idul Fitri
    _date(2026,4,1),   # Idul Fitri
    _date(2026,4,2),   # Cuti bersama
    _date(2026,4,3),   # Cuti bersama
    _date(2026,4,6),   # Cuti bersama
    _date(2026,5,1),   # Hari Buruh
    _date(2026,5,14),  # Kenaikan Isa Almasih
    _date(2026,5,27),  # Waisak
    _date(2026,6,1),   # Hari Pancasila
    _date(2026,6,4),   # Idul Adha
    _date(2026,6,26),  # Tahun Baru Islam
    _date(2026,8,17),  # HUT RI
    _date(2026,9,4),   # Maulid Nabi
    _date(2026,12,24), # Cuti bersama Natal
    _date(2026,12,25), # Natal
    _date(2026,12,31), # Cuti bersama
}

def is_idx_holiday():
    """Return True kalau hari ini libur bursa IDX"""
    return datetime.now(WIB).date() in IDX_HOLIDAYS

def is_idx_trading_day():
    """Return True hanya kalau weekday DAN bukan libur bursa"""
    return is_weekday() and not is_idx_holiday()

# ══ MARKET HOURS ══
def is_idx_market_open():
    now=datetime.now(WIB)
    if now.weekday()>=5: return False
    if now.date() in IDX_HOLIDAYS: return False  # skip libur nasional
    t=now.time()
    return dtime(9,0)<=t<=dtime(15,15)

def is_us_market_open():
    now=datetime.now(WIB)
    if now.weekday()>=5: return False
    t=now.time()
    # US market: 20:30-03:00 WIB (EDT, berlaku Apr-Nov)
    return t>=dtime(20,30) or t<=dtime(3,0)

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
doji_auto_enabled  = True   # /doji_auto on|off
volmom_auto_enabled = True  # /volmom_auto on|off

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
    except Exception as e:
        log.error(f"yfinance GAGAL [{ticker} {interval} {period}]: {e}")
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
        if df.empty or len(df)<5: return{"error":"Data kurang"}
        # Extra guard: fix MultiIndex kalau lolos dari cache
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(-1)
        df.columns = [col.title() for col in df.columns]
        c=df["Close"].squeeze(); h=df["High"].squeeze()
        l=df["Low"].squeeze(); v=df["Volume"].squeeze()
        e9=ema(c,9); e20=ema(c,20); e50=ema(c,50)
        r=rsi(c); ml,sg,hs=macd(c); sk,sd=stoch(h,l,c)
        lc=float(c.iloc[-1]); pc=float(c.iloc[-2])
        le9=float(e9.iloc[-1]); le20=float(e20.iloc[-1]); le50=float(e50.iloc[-1])
        lr=float(r.iloc[-1]); lm=float(ml.iloc[-1]); ls=float(sg.iloc[-1])
        lh=float(hs.iloc[-1]); ph=float(hs.iloc[-2]); lsk=float(sk.iloc[-1])
        lv=float(v.iloc[-1]); av=float(v.tail(20).mean()); vr=lv/av if av>0 else 1
        # NaN guard: RSI/Stoch butuh 14+ candle valid — fallback ke nilai netral
        import math
        if math.isnan(lc) or lc <= 0: return {"error": "Price NaN"}
        if math.isnan(lr):  lr  = 50.0   # RSI netral
        if math.isnan(lsk): lsk = 50.0   # Stoch netral
        if math.isnan(lm):  lm  = 0.0
        if math.isnan(ls):  ls  = 0.0
        if math.isnan(lh):  lh  = 0.0
        if math.isnan(ph):  ph  = 0.0
        if math.isnan(le9):  le9  = lc
        if math.isnan(le20): le20 = lc
        if math.isnan(le50): le50 = lc
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
    except Exception as e:
        log.error(f"get_signal ERROR [{code} {tf}]: {e}")
        return{"error":str(e)}

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

def doji_scan_tf(stock_list, tf="4H"):
    """Scan doji bullish reversal di 1 TF saja — hemat resource"""
    return {tf: doji_screener_tf(stock_list, tf)}

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
    gs=GridSpec(8,1,figure=fig,height_ratios=[5,1.0,1.0,1.0,0.6,0.28,0.28,0.28],hspace=0.04)
    ax1=fig.add_subplot(gs[0]); ax2=fig.add_subplot(gs[1])
    ax3=fig.add_subplot(gs[2]); ax4=fig.add_subplot(gs[3])
    ax_rs=fig.add_subplot(gs[4])  # RS vs Index panel
    ax_p1=fig.add_subplot(gs[5]); ax_p2=fig.add_subplot(gs[6]); ax_p3=fig.add_subplot(gs[7])

    for ax in [ax1,ax2,ax3,ax4,ax_rs]:
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
            # Pivot arrows (ganti titik → panah kecil)
            for px2, py2 in zip([tl_offset+px3 for px3 in u["pivot_x"]], u["pivot_y"]):
                ax1.annotate('', xy=(px2, py2), xytext=(px2, py2 * 1.012),
                    arrowprops=dict(arrowstyle='->', color='#e67e22', lw=1.2),
                    zorder=8)

        if "lower" in tl:
            lo2 = tl["lower"]
            x0 = tl_offset + lo2["x0"]; x1 = tl_offset + lo2["x1"]
            ax1.plot([x0, x1],[lo2["y0"], lo2["y1"]],
                     color="#27ae60", linewidth=1.4, linestyle='--', alpha=0.85, zorder=7,
                     label="Lower TL")
            for px2, py2 in zip([tl_offset+px3 for px3 in lo2["pivot_x"]], lo2["pivot_y"]):
                ax1.annotate('', xy=(px2, py2), xytext=(px2, py2 * 0.988),
                    arrowprops=dict(arrowstyle='->', color='#27ae60', lw=1.2),
                    zorder=8)

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
    # ══ RS (RELATIVE STRENGTH) — Internal MA50 ══
    # Tidak download ticker lain — 100% reliable
    try:
        rs_ok = False
        if len(closes) >= 10 and len(e50v) >= 10:
            sc  = np.array(closes, dtype=float)
            ma  = np.array(e50v,   dtype=float)
            valid = ~(np.isnan(sc) | np.isnan(ma) | (ma == 0))
            sc = sc[valid]; ma = ma[valid]
            use_n = min(len(sc), len(ma), n)
            if use_n >= 5:
                sc = sc[-use_n:]; ma = ma[-use_n:]
                rs_line = (sc / ma - 1.0) * 100.0
                for xi in range(use_n):
                    v   = float(rs_line[xi])
                    col = "#26a69a" if v >= 0 else "#ef5350"
                    ax_rs.bar(xi, v, color=col, width=0.85, zorder=3, alpha=0.85)
                ax_rs.axhline(0, color="#90a4ae", linewidth=0.8, alpha=0.7)
                ax_rs.set_xlim(-0.5, use_n - 0.5)
                ax_rs.set_yticks([]); ax_rs.set_xticks([])
                last_v   = float(rs_line[-1])
                rs_color = "#26a69a" if last_v >= 0 else "#ef5350"
                ax_rs.text(0.01, 0.5, f"RS/MA50: {last_v:+.1f}%",
                           color=rs_color, fontsize=6, va='center',
                           fontweight='bold', transform=ax_rs.transAxes)
                rs_ok = True
        if not rs_ok:
            ax_rs.set_yticks([]); ax_rs.set_xticks([])
            ax_rs.set_facecolor(BG2)
            ax_rs.text(0.01, 0.5, "RS: —",
                      transform=ax_rs.transAxes, color=TEXT2, fontsize=6, va='center')
    except Exception as e:
        try:
            ax_rs.set_yticks([]); ax_rs.set_xticks([])
            ax_rs.set_facecolor(BG2)
            ax_rs.text(0.01, 0.5, "RS: —",
                      transform=ax_rs.transAxes, color=TEXT2, fontsize=6, va='center')
        except: pass
        log.warning(f"RS panel error: {e}")

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

    # ── Stop Loss (SL) — berbasis ATR lebih ketat ──
    # SL1: 1x ATR di bawah harga (tight stop)
    # SL2: 1.5x ATR di bawah harga (normal stop)
    # SL3: Di bawah MA50 atau 2.5x ATR (max stop)
    sl1 = price - (1.0 * atr)
    sl2 = price - (1.5 * atr)
    sl3 = min(e50 * 0.97, price - (2.5 * atr))

    # Pastikan SL tidak terbalik dan tidak terlalu dalam (max -15%)
    sl1 = max(sl1, price * 0.85)
    sl2 = max(sl2, price * 0.82)
    sl3 = max(sl3, price * 0.78)

    # Pastikan urutan sl1 > sl2 > sl3
    sl1 = min(sl1, price * 0.97)
    sl2 = min(sl2, sl1  * 0.97)
    sl3 = min(sl3, sl2  * 0.97)

    # ── Risk/Reward Ratio: pakai TP3 vs SL1 ──
    risk   = price - sl1
    reward = tp3   - price
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
        "⚡ *IDX QUANT Bot v5.1 — T1MO × Wisdom*\n\n"
        "📊 *Chart & Signal:*\n"
        "`/signal BBCA` — Signal + indikator\n"
        "`/signal PLTR D` — Saham US juga bisa!\n"
        "`/chart ENRG 1H` — Chart candlestick\n"
        "`/tp ENRG` — TP1/TP2/TP3 + SL1/SL2/SL3 otomatis\n\n"
        "🔍 *Screener:*\n"
        "`/screener` — Top picks IDX\n"
        "`/screener us` — Top picks US stocks\n"
        "`/screener_ideal` — 🏆 Ideal picks (filter ketat)\n"
        "`/screener_ideal us` — 🏆 US Ideal picks\n"
        "`/doji` — Doji scan IDX (manual: 1H+4H+D) / auto: TF 4H\n"
        "`/doji us` — Doji scan US stocks\n\n"
        "🟢 *First Green Screener (BARU):*\n"
        "`/firstgreen` — IDX: candle hijau pertama setelah ≥2 merah (30M/1H/4H/D)\n"
        "`/firstgreen us` — US stocks first green\n\n"
        "🤖 *Auto Scan:*\n"
        "`/auto on` — Aktifkan auto scan\n"
        "`/auto off` — Matikan auto scan\n\n"
        "📈 *Market:*\n"
        "`/volume` — Top volume IDX\n"
        "`/trend` — Market overview\n"
        "`/help` — Bantuan lengkap\n\n"
        "⚡ *v5.1: Holiday skip + ATR SL + Ideal Screener + Doji/Volmom 4H*",
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
    msg1 = (
        "📖 *IDX QUANT v5.1 — Command List*\n\n"
        "*📊 Signal & Chart:*\n"
        "`/signal KODE [TF]` — Analisis (TF: 5M 15M 30M 1H 4H D W M)\n"
        "`/chart KODE [TF]` — Chart candlestick + indikator\n"
        "`/tp KODE [TF]` — TP/SL + R/R Ratio\n\n"
        "*🏆 Screener:*\n"
        "`/screener` — IDX screener\n"
        "`/screener us` — US stock screener\n"
        "`/screener_ideal` — IDX Ideal Score≥6\n"
        "`/screener_ideal us` — US Ideal Screener\n\n"
        "*🕯 Doji Reversal:*\n"
        "`/doji` — Scan doji IDX (1H+4H+D)\n"
        "`/doji us` — Scan doji US\n"
        "`/doji_auto on|off` — Toggle auto doji\n\n"
        "*🌊 Volume Momentum:*\n"
        "*🟢 First Green (BARU):*\n"
        "`/firstgreen` — IDX first green 30M/1H/4H/D\n"
        "`/firstgreen us` — US first green\n\n"
        "*💧 MDP — Market Depth Pressure (BARU):*\n"
        "`/mdp` — Scan IDX buy/sell pressure + score\n"
        "`/mdp us` — Scan US stocks MDP\n"
        "`/mdp detail KODE` — Detail 1 saham (auto 3x/hari)"
    )
    msg2 = (
        "*🤖 Auto Scan:*\n"
        "`/auto on` — Aktifkan auto scan\n"
        "`/auto off` — Matikan auto scan\n\n"
        "*📋 Summary & Market:*\n"
        "`/volume` — Top volume IDX\n"
        "`/trend` — Trend market + IHSG\n\n"
        "*🔔 Flip Alert:*\n"
        "`/flipstatus` — Status flip pixel\n"
        "Auto alert flip tiap 30 menit\n\n"
        "*⚡ Auto aktif saat /auto on:*\n"
        "• Ideal Screener: open/close + tiap 1j + 4j\n"
        "• Evening summary 16:05 WIB\n"
        "• Flip scan tiap 30 menit\n\n"
        "Score: 1-3 Lemah | 4-5 OK | 6+ 🔥\n"
        "⚠️ LOW LIQUIDITY = saham illiquid\n"
        "🏝 Auto skip libur nasional IDX"
    )
    await u.message.reply_text(msg1, parse_mode="Markdown")
    await u.message.reply_text(msg2, parse_mode="Markdown")

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
    try:
        buf,err=generate_chart(code,tf)
    except Exception as ex:
        await m.edit_text(f"Exception: {ex}"); return
    if err: await m.edit_text(f"Error: {err} | code={code} tf={tf}"); return
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

async def flip_pixel_scan(context):
    if not is_idx_trading_day(): return  # skip weekend + libur nasional
    if not auto_users: return
    if not (is_idx_market_open() or is_us_market_open()): return
    bot=context.bot

    # ✅ FIX: Scan multi-TF — 30M, 1H, 4H, D untuk IDX; D+4H untuk US
    idx_tfs  = ["30M", "1H", "4H", "D"]
    us_tfs   = ["4H", "D"]
    all_stocks = ([(c, tf) for c in IDX_STOCKS for tf in idx_tfs] +
                  [(c, tf) for c in US_STOCKS[:30] for tf in us_tfs])

    flips_bull=[]; flips_bear=[]

    def check_flip(code_tf):
        code, tf = code_tf
        new_state = get_trend_state(code, tf)
        if new_state is None: return None
        # ✅ FIX: key include TF supaya tidak overwrite satu sama lain
        state_key = f"{code}_{tf}"
        old_state = flip_state_db.get(state_key, "neutral")
        flip_state_db[state_key] = new_state
        if old_state in ("bear", "neutral") and new_state == "bull":
            r = get_signal(code, tf)
            if "error" not in r and r.get("liquid", True): return ("bull", code, tf, r)
        elif old_state in ("bull", "neutral") and new_state == "bear":
            r = get_signal(code, tf)
            if "error" not in r: return ("bear", code, tf, r)
        return None

    loop=asyncio.get_event_loop()
    results=await loop.run_in_executor(None,lambda:[check_flip(ct) for ct in all_stocks])
    for res in results:
        if res is None: continue
        direction,code,tf,r=res
        if direction=="bull": flips_bull.append((code,tf,r))
        else: flips_bear.append((code,tf,r))
    save_json(FLIP_FILE,flip_state_db)
    if not flips_bull and not flips_bear: return
    now_str=datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
    for uid in auto_users:
        try:
            if flips_bull:
                lines=["🚀 *PIXEL FLIP — BEARISH ➜ BULLISH*",f"🕐 {now_str}","━━━━━━━━━━━━━━━━━━━━"]
                for code,tf,r in flips_bull[:8]:
                    is_idr=r["ticker"].endswith(".JK")
                    px=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                    chg=f"+{r['chg']:.2f}%" if r['chg']>=0 else f"{r['chg']:.2f}%"
                    sig=r['sigs'][0].split('-')[0].strip() if r['sigs'] else 'No Signal'
                    lines.append(f"✅ *{code}* TF:`{tf}` `{px}` {chg} | Score:`{r['score']}/8` | {sig}")
                lines+=["━━━━━━━━━━━━━━━━━━━━","📊 EMA: Price > EMA9 > MA20 > MA50","💡 Konfirmasi entry!"]
                await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
                best_code,best_tf,best_r=flips_bull[0]
                buf,_=generate_chart(best_code,best_tf)
                if buf: await bot.send_photo(int(uid),photo=buf,
                    caption=f"🚀 FLIP BULLISH: {best_code} TF:{best_tf} | Score:{best_r['score']}/8 | {now_str}")
            if flips_bear:
                lines=["⚠️ *PIXEL FLIP — BULLISH ➜ BEARISH*",f"🕐 {now_str}","━━━━━━━━━━━━━━━━━━━━"]
                for code,tf,r in flips_bear[:8]:
                    is_idr=r["ticker"].endswith(".JK")
                    px=f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                    chg=f"+{r['chg']:.2f}%" if r['chg']>=0 else f"{r['chg']:.2f}%"
                    lines.append(f"🔴 *{code}* TF:`{tf}` `{px}` {chg} | Score:`{r['score']}/8` | CUT/AVOID")
                lines+=["━━━━━━━━━━━━━━━━━━━━","📊 EMA: Price < EMA9 < MA20 < MA50","⚡ Waspada distribusi!"]
                await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
        except Exception as e: log.error(f"flip alert uid {uid}: {e}")


async def auto_cmd(u,c):
    uid=str(u.effective_user.id); args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/auto on` atau `/auto off`",parse_mode="Markdown"); return
    if args[0].lower()=="on":
        auto_users[uid]=True; save_json(AUTO_FILE,auto_users)
        await u.message.reply_text(
            "🤖 *Auto Scan AKTIF!*\n\n"
            "🇮🇩 *IDX Scanner:* aktif *09:00-15:15 WIB* (weekday)\n"
            "🇺🇸 *US Scanner:* aktif *20:30-03:00 WIB* (weekday)\n"
            "⏰ Volume spike alert setiap *15 menit*\n"
            "🌅 Morning scan IDX setiap jam *09:00 WIB*\n"
            "🕯 *Doji scan* tiap 1 jam — TF 4H\n"
            "🏆 *Ideal Screener:* open/close IDX&US + tiap 1jam + tiap 4jam\n"
            "⚡ *Parallel scan 10 thread*\n\n"
            "🏖 *Auto skip libur nasional IDX*\n"
            "📊 *Score rendah = notif ringkas*\n"
            "🔄 On/off doji: `/doji_auto on|off`\n"
            "⚠️ LOW LIQUIDITY = saham illiquid otomatis diberi tanda",
            parse_mode="Markdown")
    else:
        auto_users.pop(uid,None); save_json(AUTO_FILE,auto_users)
        await u.message.reply_text("⏹ Auto scan dimatikan.",parse_mode="Markdown")

async def doji_auto_cmd(u, c):
    """Command /doji_auto on|off"""
    global doji_auto_enabled
    args = c.args
    if not args:
        status = "✅ ON" if doji_auto_enabled else "❌ OFF"
        await u.message.reply_text(f"🕯 Doji Auto Scan: *{status}*\nGunakan `/doji_auto on` atau `/doji_auto off`", parse_mode="Markdown")
        return
    if args[0].lower() == "on":
        doji_auto_enabled = True
        await u.message.reply_text("🕯 *Doji Auto Scan: ✅ AKTIF*", parse_mode="Markdown")
    else:
        doji_auto_enabled = False
        await u.message.reply_text("🕯 *Doji Auto Scan: ❌ MATI*", parse_mode="Markdown")

async def doji_auto_scan(context):
    """Auto scan doji bullish reversal IDX — hanya TF 4H (hemat resource)"""
    if not is_idx_trading_day(): return
    if not auto_users: return
    if not doji_auto_enabled: return   # bisa dimatikan via /doji_auto off
    if not is_idx_market_open(): return
    bot = context.bot
    # ✅ Hanya scan 4H — hemat usage
    results_4h = await asyncio.get_event_loop().run_in_executor(
        None, lambda: doji_scan_tf(IDX_STOCKS, "4H"))
    results = {"4H": results_4h} if results_4h else {}
    if not results or not results_4h: return
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
    if not force and not is_idx_trading_day(): return  # skip weekend + libur nasional
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

def get_net_foreign(code):
    """
    Estimasi Net Buy/Sell Asing dari data Yahoo Finance.
    Pakai perbandingan volume vs harga untuk mengestimasi tekanan beli/jual.
    Catatan: Yahoo tidak punya data asing langsung — ini estimasi dari
    price action + volume. Untuk data asing akurat perlu Stockbit/IDX API.
    """
    try:
        ticker = get_ticker(code)
        df = get_cached_data(ticker, "1d", "5d")
        if df is None or df.empty or len(df) < 2: return None
        close = df["Close"].squeeze()
        vol   = df["Volume"].squeeze()
        # Estimasi: candle hijau besar = net buy, candle merah besar = net sell
        last_chg = float(close.iloc[-1] - close.iloc[-2])
        last_vol  = float(vol.iloc[-1])
        avg_vol   = float(vol.iloc[:-1].mean())
        vr = last_vol / avg_vol if avg_vol > 0 else 1
        est = last_chg * last_vol / 1e9  # estimasi dalam miliar
        return {"est_net": est, "vr": vr, "last_chg": last_chg}
    except:
        return None

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
def health(): return jsonify({"status":"ok","version":"v5.1","alerts":len(alerts_db),
                               "auto_users":len(auto_users),"cache_size":len(_data_cache),
                               "idx_market_open":is_idx_market_open(),
                               "us_market_open":is_us_market_open()})
@app.route("/api/signal/<code>")
def api_sig(code):
    r=get_signal(code.upper(),"D")
    return jsonify({k:v for k,v in r.items() if k not in ["df","ema9","ema20","ema50","rsi_s","macd_l","macd_sg","macd_h","stoch_k","stoch_d"]})

def run_flask(): app.run(host="0.0.0.0",port=PORT,debug=False,use_reloader=False)

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
    """Auto scan breakout pattern tiap 30 menit — pisah IDX vs US."""
    if not is_weekday(): return
    if is_idx_holiday() and not is_us_market_open(): return
    bot = context.bot

    stocks_to_scan = []
    # IDX hanya scan saat market IDX buka
    if is_idx_market_open():
        stocks_to_scan += [(c, "IDX") for c in IDX_STOCKS]
    # US hanya scan saat market US buka
    if is_us_market_open():
        stocks_to_scan += [(c, "US") for c in US_STOCKS[:20]]

    if not stocks_to_scan: return

    all_alerts = []
    for code, market in stocks_to_scan:
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
                        pf=lambda p,idr=is_idr:f"Rp {p:,.0f}" if idr else f"${p:,.2f}"
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


# ══════════════════════════════════════════════════════════════
# IDEAL SCREENER — Filter ketat: Score≥6, R/R≥1.5x, Uptrend,
# RSI 40-65, MACD positif, Harga > MA20 & MA50
# ══════════════════════════════════════════════════════════════

MIN_SCORE_IDEAL   = 6      # Score minimum dari 8
MIN_RR_IDEAL      = 1.5    # Risk/Reward minimum
RSI_MIN_IDEAL     = 40     # RSI lower bound
RSI_MAX_IDEAL     = 65     # RSI upper bound (hindari overbought)

def ideal_screener_scan(stock_list, tf="D", max_workers=10):
    """
    Scan semua saham dan filter dengan kriteria ideal:
    - Score >= 6/8
    - Uptrend (harga > MA20 & MA50)
    - RSI 40-65 (zona sehat)
    - MACD positif (macd line > signal line)
    - R/R >= 1.5x
    Returns list of (signal_dict, tp_sl_dict) sorted by score desc.
    """
    candidates = []

    def scan_one(code):
        try:
            r = get_signal(code, tf)
            if "error" in r: return None
            # ── Filter 1: Score ──
            if r["score"] < MIN_SCORE_IDEAL: return None
            # ── Filter 2: Uptrend (harga di atas MA20 & MA50) ──
            if not (r["price"] > r["e20"] and r["price"] > r["e50"]): return None
            # ── Filter 3: RSI zona sehat ──
            if not (RSI_MIN_IDEAL <= r["rsi"] <= RSI_MAX_IDEAL): return None
            # ── Filter 4: MACD positif ──
            if not (r["macd"] > r["msig"]): return None
            # ── Filter 5: Harga juga di atas EMA9 (momentum) ──
            if r["price"] < r["e9"]: return None
            # ── Hitung R/R ──
            ts = calculate_tp_sl(r)
            if ts["rr"] < MIN_RR_IDEAL: return None
            return (r, ts)
        except Exception as e:
            log.warning(f"ideal screener error {code}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scan_one, code): code for code in stock_list}
        for future in as_completed(futures):
            try:
                res = future.result(timeout=20)
                if res: candidates.append(res)
            except Exception as e:
                log.warning(f"ideal screener future error {futures[future]}: {e}")

    # Sort: score tertinggi dulu, lalu R/R tertinggi
    candidates.sort(key=lambda x: (-x[0]["score"], -x[1]["rr"]))
    return candidates


def fmt_ideal_screener_msg(candidates, market_name="IDX", tf="D"):
    """Format pesan Telegram untuk ideal screener."""
    now_str = datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
    flag = "🇺🇸" if market_name == "US" else "🇮🇩"
    lines = [
        f"🏆 *IDEAL SCREENER {flag} {market_name} — TF:{tf}*",
        f"🕐 {now_str}",
        f"🎯 Filter: Score≥{MIN_SCORE_IDEAL} | Uptrend | RSI {RSI_MIN_IDEAL}-{RSI_MAX_IDEAL} | MACD+ | R/R≥{MIN_RR_IDEAL}x",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    if not candidates:
        lines.append("❌ Belum ada saham yang memenuhi semua kriteria saat ini.")
        lines.append("💡 Coba lagi nanti atau turunkan filter via /screener")
    else:
        lines.append(f"✅ *{len(candidates)} saham lolos filter:*\n")
        for i, (r, ts) in enumerate(candidates[:8], 1):
            is_idr = r["ticker"].endswith(".JK")
            px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
            top_sig = r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
            liq_tag = " ⚠️" if not r.get("liquid", True) else ""
            lines.append(
                f"{i}. 🔥 *{r['code']}* `{px}` {r['chg']:+.2f}%{liq_tag}\n"
                f"   Score:`{r['score']}/8` | RSI:`{r['rsi']:.0f}` | R/R:`{ts['rr']}x`\n"
                f"   {top_sig}\n"
                f"   🎯TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) "
                f"🛡SL1:`{ts['sl1_str']}`({ts['sl1_pct']:+.1f}%)"
            )
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "✅=Liquid ⚠️=Low Liq | Selalu konfirmasi sebelum entry!",
        f"⏱ {now_str}"
    ]
    return "\n".join(lines)


async def screener_ideal_cmd(u, c):
    """Command /screener_ideal [us] — manual trigger ideal screener"""
    args   = c.args
    market = "us" if args and args[0].lower() == "us" else "idx"
    flag   = "🇺🇸" if market == "us" else "🇮🇩"
    label  = "US" if market == "us" else "IDX"
    stocks = US_STOCKS if market == "us" else IDX_STOCKS
    tf     = "D"

    m = await u.message.reply_text(
        f"🔍 Scanning *{flag} {label} IDEAL SCREENER*...\n"
        f"Filter: Score≥{MIN_SCORE_IDEAL} | Uptrend | RSI {RSI_MIN_IDEAL}-{RSI_MAX_IDEAL} | MACD+ | R/R≥{MIN_RR_IDEAL}x\n"
        f"Harap tunggu ~30 detik...",
        parse_mode="Markdown")
    try:
        candidates = await asyncio.get_event_loop().run_in_executor(
            None, ideal_screener_scan, stocks, tf)
        msg = fmt_ideal_screener_msg(candidates, label, tf)
        await m.edit_text(msg, parse_mode="Markdown")

        # Kirim chart top 3 lolos
        for r, ts in candidates[:3]:
            buf, _ = generate_chart(r["code"], tf)
            if buf:
                is_idr = r["ticker"].endswith(".JK")
                px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
                await u.message.reply_photo(photo=buf,
                    caption=(f"🏆 *IDEAL PICK {flag}: {r['code']}*\n"
                             f"`{px}` {r['chg']:+.2f}% | Score:`{r['score']}/8`\n"
                             f"{r['trend']} | RSI:`{r['rsi']:.0f}` | MACD:`{r['macd']:.1f}`\n"
                             f"━━━━━━━━━━━━━━━\n"
                             f"🎯 TP1:`{ts['tp1_str']}`({ts['tp1_pct']:+.1f}%) "
                             f"TP2:`{ts['tp2_str']}`({ts['tp2_pct']:+.1f}%)\n"
                             f"🛡 SL1:`{ts['sl1_str']}`({ts['sl1_pct']:+.1f}%) "
                             f"SL2:`{ts['sl2_str']}`({ts['sl2_pct']:+.1f}%)\n"
                             f"⚖️ R/R:`{ts['rr']}x` | {fmt_now()}"),
                    parse_mode="Markdown")
    except Exception as e:
        await m.edit_text(f"❌ Error ideal screener: {e}")


async def ideal_screener_auto(context):
    """
    Auto scan IDEAL SCREENER — kirim ke semua auto_users.
    Dipanggil pada:
    - Market open IDX (09:05 WIB)
    - Market open US (21:35 WIB)
    - Tiap 1 jam saat market buka
    - Tiap 4 jam (cross-session)
    - Market close IDX (15:20 WIB) & US (04:05 WIB)
    """
    if not is_idx_trading_day() and not is_weekday(): return
    if not idx_open and not us_open: return

    tasks = []
    if idx_open:
        tasks.append(("IDX", IDX_STOCKS, "🇮🇩"))
    if us_open:
        tasks.append(("US", US_STOCKS, "🇺🇸"))

    for label, stocks, flag in tasks:
        try:
            candidates = await asyncio.get_event_loop().run_in_executor(
                None, ideal_screener_scan, stocks, "D")
            if not candidates:
                log.info(f"Ideal screener {label}: tidak ada yang lolos filter")
                continue
            msg = fmt_ideal_screener_msg(candidates, label, "D")
            for uid in list(auto_users.keys()):
                try:
                    await bot.send_message(int(uid), msg, parse_mode="Markdown")
                    # Kirim chart top 1
                    best_r, best_ts = candidates[0]
                    buf, _ = generate_chart(best_r["code"], "D")
                    if buf:
                        is_idr = best_r["ticker"].endswith(".JK")
                        px = f"Rp {best_r['price']:,.0f}" if is_idr else f"${best_r['price']:,.2f}"
                        await bot.send_photo(int(uid), photo=buf,
                            caption=(f"🏆 *BEST IDEAL PICK {flag}: {best_r['code']}*\n"
                                     f"`{px}` {best_r['chg']:+.2f}% | Score:`{best_r['score']}/8`\n"
                                     f"{best_r['trend']} | RSI:`{best_r['rsi']:.0f}` | R/R:`{best_ts['rr']}x`\n"
                                     f"━━━━━━━━━━━━━━━\n"
                                     f"🎯 TP1:`{best_ts['tp1_str']}`({best_ts['tp1_pct']:+.1f}%) "
                                     f"TP2:`{best_ts['tp2_str']}`({best_ts['tp2_pct']:+.1f}%)\n"
                                     f"🛡 SL1:`{best_ts['sl1_str']}`({best_ts['sl1_pct']:+.1f}%) "
                                     f"R/R:`{best_ts['rr']}x`\n⏱ {fmt_now()}"),
                            parse_mode="Markdown")
                except Exception as e:
                    log.error(f"ideal screener auto send uid {uid}: {e}")
        except Exception as e:
            log.error(f"ideal screener auto {label}: {e}")


# ══════════════════════════════════════════════════════════════
# FIRST GREEN SCREENER
# Deteksi saham yang candle terakhir PERTAMA KALI HIJAU
# setelah sebelumnya minimal 2 candle merah berturut-turut
# Multi-TF: 30M, 1H, 4H, D
# ══════════════════════════════════════════════════════════════

def detect_first_green(code, tf="D"):
    """
    Deteksi 'First Green' — candle terakhir hijau setelah ≥2 candle merah sebelumnya.
    Tambah filter: RSI tidak overbought, volume konfirmasi, harga di atas MA50 (opsional).
    Return dict info atau None.
    """
    try:
        r = get_signal(code, tf)
        if "error" in r: return None
        df = r["df"]
        if len(df) < 6: return None

        opens  = df["Open"].squeeze().values
        closes = df["Close"].squeeze().values
        vols   = df["Volume"].squeeze().values

        # Candle terakhir harus HIJAU (close > open)
        if closes[-1] <= opens[-1]: return None

        # Minimal 1 candle merah sebelumnya (lebih sensitif), hitung berapa banyak
        red_count = 0
        for i in range(-2, -8, -1):
            if closes[i] < opens[i]:
                red_count += 1
            else:
                break
        if red_count < 1: return None  # minimal 1 candle merah

        # Filter: RSI tidak overbought (< 78, lebih longgar)
        rsi_val = r["rsi"]
        if rsi_val >= 78: return None

        # Volume konfirmasi: candle hijau ini volumenya >= 1.1x rata-rata
        avg_vol = float(np.mean(vols[-10:-1])) if len(vols) >= 10 else float(np.mean(vols[:-1]))
        last_vol = float(vols[-1])
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0

        # Hitung body size candle hijau terakhir (%)
        body_pct = (closes[-1] - opens[-1]) / opens[-1] * 100

        price   = r["price"]
        chg     = r["chg"]
        e20     = r["e20"]
        e50     = r["e50"]
        ticker  = r["ticker"]
        is_idr  = ticker.endswith(".JK")
        liquid  = r.get("liquid", True)

        # Score
        score = 0
        factors = []

        # Jumlah candle merah sebelumnya (makin banyak makin bagus)
        if red_count >= 4:   score += 3; factors.append(f"🔴×{red_count} reversal kuat")
        elif red_count >= 3: score += 2; factors.append(f"🔴×{red_count} reversal")
        else:                score += 1; factors.append(f"🔴×{red_count} first green")

        # Volume konfirmasi
        if vol_ratio >= 2.0:   score += 3; factors.append(f"🌊 Vol {vol_ratio:.1f}x")
        elif vol_ratio >= 1.5: score += 2; factors.append(f"📈 Vol {vol_ratio:.1f}x")
        elif vol_ratio >= 1.1: score += 1; factors.append(f"📊 Vol {vol_ratio:.1f}x")
        else:                  factors.append(f"Vol {vol_ratio:.1f}x (lemah)")

        # RSI zona sehat (30-60 = ideal entry)
        if 30 <= rsi_val <= 60:  score += 2; factors.append(f"RSI sehat ({rsi_val:.0f})")
        elif rsi_val < 30:       score += 2; factors.append(f"RSI oversold ({rsi_val:.0f})")
        elif rsi_val <= 70:      score += 1; factors.append(f"RSI ok ({rsi_val:.0f})")

        # Harga vs MA50
        if price > e50:   score += 1; factors.append("Di atas MA50")
        elif price > e20: factors.append("Di atas MA20")

        # Stoch oversold
        stoch_val = r["stoch"]
        if stoch_val < 25:   score += 2; factors.append(f"Stoch OS ({stoch_val:.0f})")
        elif stoch_val < 40: score += 1; factors.append(f"Stoch rendah ({stoch_val:.0f})")

        # Body candle hijau besar = sinyal kuat
        if body_pct >= 2.0:   score += 1; factors.append(f"Body besar {body_pct:.1f}%")
        elif body_pct >= 1.0: factors.append(f"Body {body_pct:.1f}%")

        if score < 2: return None  # minimal score 2

        return {
            "code":      code,
            "tf":        tf,
            "ticker":    ticker,
            "price":     price,
            "chg":       chg,
            "rsi":       rsi_val,
            "stoch":     stoch_val,
            "vol_ratio": vol_ratio,
            "red_count": red_count,
            "body_pct":  body_pct,
            "score":     score,
            "factors":   factors,
            "e20":       e20,
            "e50":       e50,
            "liquid":    liquid,
        }
    except Exception as e:
        log.warning(f"first_green {code} {tf}: {e}")
        return None


def first_green_scan_tf(stock_list, tf, max_workers=10):
    """Scan first green untuk satu TF secara paralel"""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(detect_first_green, code, tf): code for code in stock_list}
        for future in as_completed(futures):
            try:
                res = future.result(timeout=20)
                if res: results.append(res)
            except Exception as e:
                log.warning(f"first_green scan error: {e}")
    results.sort(key=lambda x: (-x["score"], -x["vol_ratio"]))
    return results


def first_green_scan_multitf(stock_list):
    """Scan first green di 4 TF sekaligus: 30M, 1H, 4H, D"""
    all_results = {}
    for tf in ["30M", "1H", "4H", "D"]:
        all_results[tf] = first_green_scan_tf(stock_list, tf)
    return all_results


def fmt_first_green_msg(results_by_tf, market_name="IDX"):
    """Format pesan Telegram untuk first green screener"""
    now_str = datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
    flag = "🇺🇸" if market_name == "US" else "🇮🇩"
    lines = [
        f"🟢 *FIRST GREEN SCREENER {flag} {market_name}*",
        f"🕐 {now_str}",
        f"📌 Candle hijau pertama setelah ≥2 candle merah",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    total_found = 0
    tf_labels = {"30M": "30 MENIT", "1H": "1 JAM", "4H": "4 JAM", "D": "HARIAN"}
    for tf in ["30M", "1H", "4H", "D"]:
        hits = results_by_tf.get(tf, [])
        if not hits: continue
        lines.append(f"\n⏱ *TF {tf_labels[tf]}:* ({len(hits)} saham)")
        for h in hits[:5]:
            is_idr = h["ticker"].endswith(".JK")
            px = f"Rp {h['price']:,.0f}" if is_idr else f"${h['price']:,.2f}"
            liq = " ⚠️" if not h["liquid"] else ""
            vol_tag = "🌊" if h["vol_ratio"] >= 2 else "📈" if h["vol_ratio"] >= 1.5 else ""
            fac = " | ".join(h["factors"][:2])
            lines.append(
                f"  🟢 *{h['code']}* `{px}` {h['chg']:+.2f}%{liq} {vol_tag}\n"
                f"    ↳ 🔴×{h['red_count']} | Score:`{h['score']}` | {fac}"
            )
        total_found += len(hits)
    if total_found == 0:
        lines.append("❌ Tidak ada first green terdeteksi saat ini.")
        lines.append("💡 Coba lagi saat market aktif atau ganti TF")
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "💡 First Green = potensi reversal naik jangka pendek",
        "⚠️ Konfirmasi volume & candle berikutnya sebelum entry!",
        f"⏱ {now_str}"
    ]
    return "\n".join(lines)


async def firstgreen_cmd(u, c):
    """Command /firstgreen [us] — scan saham first green multi-TF"""
    args   = c.args
    market = "us" if args and args[0].lower() == "us" else "idx"
    flag   = "🇺🇸" if market == "us" else "🇮🇩"
    label  = "US" if market == "us" else "IDX"
    stocks = US_STOCKS if market == "us" else IDX_STOCKS

    m = await u.message.reply_text(
        f"🟢 Scanning *First Green {flag} {label}*...\n"
        f"TF: 30M | 1H | 4H | D\n"
        f"⏳ Harap tunggu ~30 detik...",
        parse_mode="Markdown")
    try:
        results = await asyncio.get_event_loop().run_in_executor(
            None, first_green_scan_multitf, stocks)
        msg = fmt_first_green_msg(results, label)
        await m.edit_text(msg, parse_mode="Markdown")

        # Kirim chart saham first green terbaik (prioritas D → 4H → 1H → 30M)
        best = None; best_tf = "D"
        for tf in ["D", "4H", "1H", "30M"]:
            hits = results.get(tf, [])
            liquid_hits = [h for h in hits if h["liquid"]]
            if liquid_hits:   best = liquid_hits[0]; best_tf = tf; break
            elif hits:        best = hits[0];        best_tf = tf; break
        if best:
            buf, _ = generate_chart(best["code"], best_tf)
            if buf:
                is_idr = best["ticker"].endswith(".JK")
                px = f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
                await u.message.reply_photo(
                    photo=buf,
                    caption=(f"🟢 *FIRST GREEN: {best['code']}* | TF:{best_tf}\n"
                             f"`{px}` {best['chg']:+.2f}% | Score:`{best['score']}`\n"
                             f"🔴×{best['red_count']} candle merah sebelumnya\n"
                             f"Vol:`{best['vol_ratio']:.1f}x` RSI:`{best['rsi']:.0f}` STOCH:`{best['stoch']:.0f}`\n"
                             f"💡 {' | '.join(best['factors'][:3])}\n"
                             f"⏱ {fmt_now()}"),
                    parse_mode="Markdown")
    except Exception as e:
        await m.edit_text(f"❌ Error first green scan: {e}")


def calc_mdp_score(code):
    """
    Hitung MDP (Market Depth Pressure) untuk satu saham.
    Pakai data intraday 1H + Daily untuk cross-validate.

    Returns dict:
        mdp_pct   : net buy pressure % (-100 to +100)
        cp        : candle pressure score (-100 to +100)
        two       : two-way flow score (0-100, makin tinggi makin dua arah)
        weight    : relative weight vs universe
        score_mdp : integer 0-10 (probability naik)
        price, chg, vol_ratio, rsi, trend
    """
    import math
    try:
        ticker = get_ticker(code)
        # Ambil data 1H 30 hari + Daily 1 tahun
        df_h  = get_cached_data(ticker, "60m", "30d")
        df_d  = get_cached_data(ticker, "1d",  "1y")
        if df_h.empty or len(df_h) < 10: return None
        if df_d.empty or len(df_d) < 20: return None

        c_h = df_h["Close"].squeeze()
        h_h = df_h["High"].squeeze()
        l_h = df_h["Low"].squeeze()
        v_h = df_h["Volume"].squeeze()
        c_d = df_d["Close"].squeeze()
        v_d = df_d["Volume"].squeeze()

        price  = float(c_h.iloc[-1])
        pc     = float(c_h.iloc[-2]) if len(c_h) >= 2 else price
        if math.isnan(price) or price <= 0: return None

        chg    = (price - pc) / pc * 100 if pc > 0 else 0.0

        # ── 1. Candle Pressure (CP) ──
        # Ratio candle bullish vs bearish di 20 candle terakhir (1H)
        tail20 = df_h.tail(20)
        bull_c = (tail20["Close"] > tail20["Open"]).sum()
        bear_c = (tail20["Close"] < tail20["Open"]).sum()
        total_c = bull_c + bear_c
        cp = round((bull_c - bear_c) / total_c * 100, 1) if total_c > 0 else 0.0

        # ── 2. MDP% — Net Buy Pressure ──
        # Proxy: upper shadow kecil + close dekat high = buy pressure
        # Formula: (Close - Low) / (High - Low) → "buying tail ratio"
        ranges = (h_h - l_h).tail(20)
        closes = c_h.tail(20)
        lows   = l_h.tail(20)
        buy_tail = (closes - lows) / ranges.replace(0, np.nan)
        buy_tail = buy_tail.fillna(0.5)
        # Scale ke -100..+100: 0.5 = netral
        mdp_pct = round((buy_tail.mean() - 0.5) * 200, 1)

        # ── 3. Volume pressure — weight terhadap avg ──
        avg_vol_d = float(v_d.tail(20).mean())
        last_vol  = float(v_d.iloc[-1]) if not v_d.empty else 0
        vol_ratio = last_vol / avg_vol_d if avg_vol_d > 0 else 1.0
        weight    = round(min(vol_ratio * 10, 100), 1)  # cap 100

        # ── 4. Two-way flow (TWO) ──
        # Makin tinggi = dua arah (volatile), makin rendah = one-sided
        # Pakai std candle body / avg price sebagai proxy
        bodies = abs(df_h["Close"] - df_h["Open"]).tail(20)
        avg_body = bodies.mean()
        two = round(min(avg_body / price * 1000, 100), 1) if price > 0 else 0.0

        # ── 5. Trend dari Daily ──
        e9_d  = ema(c_d, 9)
        e20_d = ema(c_d, 20)
        e50_d = ema(c_d, 50)
        le9   = float(e9_d.iloc[-1])
        le20  = float(e20_d.iloc[-1])
        le50  = float(e50_d.iloc[-1])
        if math.isnan(le9):  le9  = price
        if math.isnan(le20): le20 = price
        if math.isnan(le50): le50 = price
        trend = "UP" if price > le20 > le50 else "DN" if price < le20 < le50 else "SW"

        # ── 6. RSI dari Daily ──
        r_d  = rsi(c_d)
        lr_d = float(r_d.iloc[-1]) if not r_d.empty else 50.0
        if math.isnan(lr_d): lr_d = 50.0

        # ── 7. Score MDP (0-10) — probability naik ──
        # Komponen:
        sc = 0
        if mdp_pct > 20:  sc += 2   # strong buy pressure
        elif mdp_pct > 5: sc += 1   # mild buy pressure
        if cp > 30:  sc += 2         # candle dominan bullish
        elif cp > 10: sc += 1
        if trend == "UP": sc += 2    # uptrend daily
        elif trend == "SW": sc += 1
        if 40 < lr_d < 70: sc += 1  # RSI zona sehat
        elif lr_d < 35:    sc += 1  # oversold potential bounce
        if vol_ratio > 1.5: sc += 1 # volume naik
        if chg > 0:         sc += 1 # green hari ini

        is_idx = ticker.endswith(".JK")
        liquid = is_liquid_stock(avg_vol_d, price) if is_idx else True

        return {
            "code":      code.upper(),
            "ticker":    ticker,
            "price":     price,
            "chg":       chg,
            "mdp_pct":   mdp_pct,
            "cp":        cp,
            "two":       two,
            "weight":    weight,
            "score_mdp": sc,
            "trend":     trend,
            "rsi":       lr_d,
            "vol_ratio": vol_ratio,
            "liquid":    liquid,
        }
    except Exception as e:
        return None


def mdp_scan(stock_list, min_score=5, max_workers=12):
    """Scan MDP untuk semua saham secara paralel."""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(calc_mdp_score, c): c for c in stock_list}
        for f in as_completed(futures):
            try:
                r = f.result(timeout=20)
                if r and r["score_mdp"] >= min_score and r.get("liquid", True):
                    results.append(r)
            except Exception: pass
    results.sort(key=lambda x: (x["score_mdp"], x["mdp_pct"]), reverse=True)
    return results


def fmt_mdp_msg(results, market="IDX", session_label=""):
    """Format pesan MDP untuk Telegram."""
    now_str = datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
    flag = "🇮🇩" if market == "IDX" else "🇺🇸"
    lines = [
        f"💧 *MDP SCREENER — {flag} {market}*",
        f"🕐 {now_str}" + (f" | {session_label}" if session_label else ""),
        f"📊 Kriteria: Score MDP ≥ 5 | Liquid | Sorted by pressure",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{'No':<3} {'Kode':<8} {'Harga':>8} {'Chg':>7} | {'MDP%':>6} {'CP':>6} {'TWO':>5} | Sc",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not results:
        lines.append("❌ Tidak ada saham memenuhi kriteria MDP saat ini.")
    else:
        for i, r in enumerate(results[:25], 1):
            is_idr = r["ticker"].endswith(".JK")
            px     = f"{r['price']:,.0f}" if is_idr else f"{r['price']:.2f}"
            chg_s  = f"{r['chg']:+.1f}%"
            mdp_s  = f"{r['mdp_pct']:+.1f}"
            cp_s   = f"{r['cp']:+.1f}"
            two_s  = f"{r['two']:.1f}"
            sc_s   = f"{r['score_mdp']}/10"
            trend_icon = "⬆" if r["trend"]=="UP" else "⬇" if r["trend"]=="DN" else "↔"
            lines.append(
                f"{i:<3} *{r['code']:<7}* `{px:>8}` `{chg_s:>7}` | "
                f"`{mdp_s:>6}` `{cp_s:>6}` `{two_s:>5}` | `{sc_s}` {trend_icon}"
            )
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "📌 *Keterangan kolom:*",
        "  MDP% = net buy pressure (-100 s/d +100)",
        "  CP   = candle pressure (bullish dominan)",
        "  TWO  = two-way flow (volatilitas)",
        "  Sc   = score probabilitas naik (0-10)",
        f"💡 Ketik `/mdp detail KODE` untuk analisis 1 saham",
    ]
    return "\n".join(lines)


async def mdp_cmd(u, c):
    """
    /mdp [us] [min_score]   — MDP screener IDX atau US
    /mdp detail KODE        — Detail MDP 1 saham
    """
    args = c.args or []
    # Detail mode: /mdp detail KODE
    if args and args[0].lower() == "detail":
        code = args[1].upper() if len(args) > 1 else None
        if not code:
            await u.message.reply_text("❓ Format: `/mdp detail KODE`\nContoh: `/mdp detail BBCA`",
                                       parse_mode="Markdown")
            return
        m = await u.message.reply_text(f"⏳ Hitung MDP untuk *{code}*...", parse_mode="Markdown")
        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(None, calc_mdp_score, code)
        if not r:
            await m.edit_text(f"❌ Gagal ambil data MDP untuk *{code}*", parse_mode="Markdown")
            return
        is_idr = r["ticker"].endswith(".JK")
        px = f"Rp {r['price']:,.0f}" if is_idr else f"${r['price']:,.2f}"
        now_str = datetime.now(WIB).strftime("%d-%b-%Y %H:%M WIB")
        trend_icon = "⬆ UPTREND" if r["trend"]=="UP" else "⬇ DOWNTREND" if r["trend"]=="DN" else "↔ SIDEWAYS"
        mdp_bar = "█" * max(0, min(10, int((r["mdp_pct"]+100)/20))) + "░" * (10 - max(0, min(10, int((r["mdp_pct"]+100)/20))))
        msg = (
            f"💧 *MDP DETAIL — {r['code']}*\n"
            f"🕐 {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Harga: `{px}` {r['chg']:+.2f}%\n"
            f"📈 Trend: {trend_icon}\n"
            f"🔵 RSI: `{r['rsi']:.1f}`\n"
            f"📦 Volume: `{r['vol_ratio']:.1f}x` avg\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💧 *MDP%*: `{r['mdp_pct']:+.1f}` — Net Buy Pressure\n"
            f"   [{mdp_bar}]\n"
            f"🕯 *CP*: `{r['cp']:+.1f}` — Candle Pressure\n"
            f"🔀 *TWO*: `{r['two']:.1f}` — Two-way Flow\n"
            f"⚖️ *Weight*: `{r['weight']:.1f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Score MDP*: `{r['score_mdp']}/10`\n"
        )
        # Score interpretation
        if r["score_mdp"] >= 8:
            msg += "✅ *STRONG BUY PRESSURE* — probabilitas naik tinggi\n"
        elif r["score_mdp"] >= 6:
            msg += "🟡 *MODERATE BUY* — perlu konfirmasi volume\n"
        elif r["score_mdp"] >= 4:
            msg += "⚪ *NETRAL* — tunggu sinyal lebih jelas\n"
        else:
            msg += "🔴 *SELL PRESSURE* — hindari entry\n"
        await m.edit_text(msg, parse_mode="Markdown")
        return

    # Screener mode
    market = "us" if (args and args[0].lower() == "us") else "idx"
    try:
        min_sc = int(args[-1]) if args and args[-1].isdigit() else 5
    except: min_sc = 5
    flag  = "🇺🇸" if market == "us" else "🇮🇩"
    label = "US" if market == "us" else "IDX"
    stocks = US_STOCKS if market == "us" else IDX_STOCKS

    # Session label
    now_wib = datetime.now(WIB)
    h = now_wib.hour
    if h < 10:   sess = "📅 Pra-Buka"
    elif h < 12: sess = "🌅 Sesi 1"
    elif h < 14: sess = "🌞 Istirahat"
    elif h < 16: sess = "🌆 Sesi 2"
    else:        sess = "🌙 After-Hours"

    m = await u.message.reply_text(
        f"⏳ Scanning MDP {flag} {label}... (bisa 30-60 detik)",
        parse_mode="Markdown")
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: mdp_scan(stocks, min_score=min_sc))
        msg = fmt_mdp_msg(results, label, sess)
        await m.edit_text(msg, parse_mode="Markdown")
        # Kirim chart top pick
        if results:
            best = results[0]
            buf, _ = generate_chart(best["code"], "D")
            if buf:
                is_idr = best["ticker"].endswith(".JK")
                px = f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
                await u.message.reply_photo(
                    photo=buf,
                    caption=(f"💧 MDP TOP PICK: *{best['code']}*\n"
                             f"`{px}` {best['chg']:+.2f}%\n"
                             f"MDP:`{best['mdp_pct']:+.1f}` CP:`{best['cp']:+.1f}` "
                             f"TWO:`{best['two']:.1f}` Score:`{best['score_mdp']}/10`\n"
                             f"📈 Trend: {best['trend']} | RSI:`{best['rsi']:.1f}` "
                             f"Vol:`{best['vol_ratio']:.1f}x`"),
                    parse_mode="Markdown")
    except Exception as e:
        await m.edit_text(f"❌ Error MDP scan: {e}")


async def mdp_auto_scan(context):
    """Auto scan MDP 3x sehari: 08:45, 11:00, 15:30 WIB"""
    if not is_idx_trading_day(): return
    if not auto_users: return
    bot = context.bot
    now_wib = datetime.now(WIB)
    h, mn = now_wib.hour, now_wib.minute
    # Tentukan session label
    if h == 8:   sess = "📅 Pra-Buka (08:45)"
    elif h == 11: sess = "🌅 Sesi 1 (11:00)"
    elif h == 15: sess = "🌆 Sesi 2 (15:30)"
    else: return  # bukan jam auto

    now_str = now_wib.strftime("%d-%b-%Y %H:%M WIB")
    results = mdp_scan(IDX_STOCKS, min_score=6)
    if not results: return
    msg = fmt_mdp_msg(results[:15], "IDX", sess)
    for uid in auto_users:
        try:
            await bot.send_message(int(uid), msg, parse_mode="Markdown")
            if results:
                best = results[0]
                buf, _ = generate_chart(best["code"], "D")
                if buf:
                    is_idr = best["ticker"].endswith(".JK")
                    px = f"Rp {best['price']:,.0f}" if is_idr else f"${best['price']:,.2f}"
                    await bot.send_photo(
                        int(uid), photo=buf,
                        caption=(f"💧 MDP TOP: *{best['code']}* {sess}\n"
                                 f"`{px}` {best['chg']:+.2f}% | "
                                 f"Score:`{best['score_mdp']}/10` | "
                                 f"MDP:`{best['mdp_pct']:+.1f}`\n{now_str}"),
                        parse_mode="Markdown")
        except Exception as e:
            log.error(f"MDP auto uid {uid}: {e}")




def run_bot():
    if not TOKEN: log.warning("TELEGRAM_TOKEN not set"); return
    tg=Application.builder().token(TOKEN).build()
    cmds=[("start",start),("help",help_cmd),("flipstatus",flipstatus_cmd),
          ("signal",signal_cmd),("chart",chart_cmd),

          ("screener",screener_cmd),("screener_us",screener_us_cmd),
          ("screener_ideal",screener_ideal_cmd),

          ("firstgreen",firstgreen_cmd),
          ("pattern",pattern_cmd),
          ("auto",auto_cmd),
          ("doji_auto",doji_auto_cmd),

          ("volume",volume_cmd),("trend",trend_cmd),
          ("mdp",mdp_cmd)]
    for cmd,fn in cmds: tg.add_handler(CommandHandler(cmd,fn))
    jq=tg.job_queue
    jq.run_daily(evening_summary,time=dtime(16,5,tzinfo=WIB))
    jq.run_repeating(flip_pixel_scan,interval=1800,first=300)
    jq.run_repeating(doji_auto_scan,interval=3600,first=600)
    jq.run_repeating(breakout_alert_scan,interval=1800,first=1200)
    # MDP auto 3x sehari
    jq.run_daily(mdp_auto_scan, time=dtime(8,45,tzinfo=WIB))
    jq.run_daily(mdp_auto_scan, time=dtime(11,0,tzinfo=WIB))
    jq.run_daily(mdp_auto_scan, time=dtime(15,30,tzinfo=WIB))

    # ══ IDEAL SCREENER AUTO SCHEDULE ══
    # Market open pagi IDX: 09:05 WIB
    jq.run_daily(ideal_screener_auto, time=dtime(9,5,tzinfo=WIB))
    # Market close IDX: 15:20 WIB
    jq.run_daily(ideal_screener_auto, time=dtime(15,20,tzinfo=WIB))
    # Market open US: 21:35 WIB
    jq.run_daily(ideal_screener_auto, time=dtime(20,35,tzinfo=WIB))
    # Market close US: 04:05 WIB
    jq.run_daily(ideal_screener_auto, time=dtime(3,5,tzinfo=WIB))
    # Tiap 1 jam saat market aktif (interval 3600 detik, first=1800)
    jq.run_repeating(ideal_screener_auto, interval=3600, first=1800)
    # Tiap 4 jam cross-session (interval 14400 detik, first=7200)
    jq.run_repeating(ideal_screener_auto, interval=14400, first=7200)

    now=datetime.now(WIB)
    if now.weekday()>=5:
        log.info("Bot start " + now.strftime('%A') + " - auto scan OFF")
    else:
        log.info("IDX QUANT Bot v5.2 polling - Holiday+SL fix aktif")
    tg.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    log.info("IDX QUANT v5.2 port " + str(PORT))
    import threading as _th
    _th.Thread(target=run_flask,daemon=True).start()
    run_bot()
