import os,threading,logging,io,json
import yfinance as yf
import pandas as pd
import numpy as np
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
            "FIRE","TINS","ZINC","KIJA","LSIP","SSMS","SLIS","NFCX","CUAN","NICK"]

US_STOCKS=["PLTR","MU","NVDA","AAPL","TSLA","AMD","META","GOOGL","MSFT","AMZN",
           "APP","MSTR","COIN","SOFI","HOOD","RKLB","IONQ","QUBT","RGTI","JOBY"]

# ══ PERSISTENT STORAGE (JSON files) ══
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

alerts_db=load_json(ALERT_FILE)      # {user_id: [{code,price,direction}]}
watchlist_db=load_json(WL_FILE)      # {user_id: [codes]}
auto_users=load_json(AUTO_FILE)      # {user_id: True}

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
    code=code.upper().replace(".JK","")
    if code in US_STOCKS or not code.isalpha() or len(code)>5: return code
    return f"{code}.JK"

def get_signal(code,tf="D"):
    iv,per=TF_MAP.get(tf.upper(),("1d","1y"))
    ticker=get_ticker(code)
    try:
        df=yf.download(ticker,period=per,interval=iv,progress=False,auto_adjust=True)
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
        return{"code":code.upper(),"ticker":ticker,"tf":tf.upper(),"price":lc,"chg":chg,
               "e9":le9,"e20":le20,"e50":le50,"rsi":lr,"macd":lm,"msig":ls,"stoch":lsk,
               "vr":vr,"vol":lv,"avg_vol":av,"sigs":sigs,"score":sc,"trend":trend,
               "df":df,"ema9":e9,"ema20":e20,"ema50":e50,"rsi_s":r,
               "macd_l":ml,"macd_sg":sg,"macd_h":hs,"stoch_k":sk,"stoch_d":sd}
    except Exception as e: return{"error":str(e)}

# ══ VOLUME SPIKE DETECTION ══
def detect_volume_spike(code, tf="5M", threshold=2.0):
    r=get_signal(code, tf)
    if "error" in r: return None
    if r["vr"]>=threshold:
        direction="BUY" if r["chg"]>=0 else "SELL"
        return{"code":code,"price":r["price"],"chg":r["chg"],"vr":r["vr"],"direction":direction,"r":r}
    return None

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
    BG="#0a0e14"; BG2="#0f1520"; GRID="#1a2438"
    GREEN="#26a69a"; RED="#ef5350"; ORANGE="#f07020"
    BLUE="#2288cc"; PINK="#e040c8"; TEXT="#c8d6e5"; TEXT2="#7a90a8"
    DARK_GREEN="#1a5c3a"; DARK_RED="#8b0000"

    fig=plt.figure(figsize=(14,10),facecolor=BG)
    gs=GridSpec(4,1,figure=fig,height_ratios=[5,1.2,1.2,1.2],hspace=0.04)
    ax1=fig.add_subplot(gs[0]); ax2=fig.add_subplot(gs[1])
    ax3=fig.add_subplot(gs[2]); ax4=fig.add_subplot(gs[3])
    for ax in [ax1,ax2,ax3,ax4]:
        ax.set_facecolor(BG2); ax.tick_params(colors=TEXT2,labelsize=7)
        for s in ax.spines.values(): s.set_color(GRID)
        ax.grid(True,color=GRID,linewidth=0.4,alpha=0.6)

    opens=df["Open"].squeeze().values; closes=close.values
    highs=high.values; lows=low.values; vols=vol.values

    # Candlestick
    for i in idx:
        o,c_,h_,l_=opens[i],closes[i],highs[i],lows[i]
        color=GREEN if c_>=o else RED
        ax1.plot([i,i],[l_,h_],color=color,linewidth=0.8,zorder=2)
        ax1.bar(i,abs(c_-o),bottom=min(o,c_),color=color,width=0.7,zorder=3)

    # ── VOLUME SPIKE ARROWS ──
    avg_v=np.mean(vols)
    for i in idx:
        vr_i=vols[i]/avg_v if avg_v>0 else 1
        if vr_i>=2.0:
            is_buy=closes[i]>=opens[i]
            arr_color=DARK_GREEN if is_buy else DARK_RED
            arr_dir="^" if is_buy else "v"
            y_pos=lows[i]*0.998 if is_buy else highs[i]*1.002
            offset=-abs(highs[i]-lows[i])*2 if is_buy else abs(highs[i]-lows[i])*2
            ax1.annotate("",
                xy=(i,y_pos),
                xytext=(i,y_pos+offset),
                arrowprops=dict(arrowstyle="->",color=arr_color,lw=2.5),
                zorder=10)
            ax1.text(i,y_pos+offset*1.3,f"{vr_i:.1f}x",
                    color=arr_color,fontsize=6,ha='center',fontweight='bold')

    # EMA Lines
    ax1.plot(idx,e50.values,color=BLUE,linewidth=1.4,label=f"MA50:{r['e50']:,.0f}",zorder=4)
    ax1.plot(idx,e20.values,color=ORANGE,linewidth=1.6,label=f"MA20:{r['e20']:,.0f}",zorder=5)
    ax1.plot(idx,e9.values,color=PINK,linewidth=1.1,linestyle='--',label=f"MA9:{r['e9']:,.0f}",zorder=6)

    # BB
    bb_m=close.rolling(20).mean(); bb_s=close.rolling(20).std()
    bb_u=(bb_m+2*bb_s).iloc[-n:]; bb_l=(bb_m-2*bb_s).iloc[-n:]
    ax1.fill_between(idx,bb_u.values,bb_l.values,alpha=0.06,color=BLUE)
    ax1.plot(idx,bb_u.values,color=BLUE,linewidth=0.5,linestyle=':',alpha=0.5)
    ax1.plot(idx,bb_l.values,color=BLUE,linewidth=0.5,linestyle=':',alpha=0.5)

    # Price tag
    lp=closes[-1]; pc_=GREEN if lp>=closes[-2] else RED
    ax1.axhline(lp,color=pc_,linewidth=0.7,linestyle='--',alpha=0.7)
    ax1.text(n-0.5,lp,f" {lp:,.0f}",color=pc_,fontsize=8,fontweight='bold',va='center',
             bbox=dict(boxstyle='round,pad=0.2',facecolor=BG2,edgecolor=pc_,linewidth=0.8))

    # Color bar
    bar_h=(highs.max()-lows.min())*0.015; bar_y=lows.min()-bar_h*2
    for i in idx:
        o,c_=opens[i],closes[i]; p=(c_-o)/o*100 if o>0 else 0
        col=(GREEN if p>1 else "#4db6ac" if p>0 else "#ef9a9a" if p>-1 else RED)
        ax1.bar(i,bar_h,bottom=bar_y,color=col,width=0.85,zorder=1)

    sig_txt=r['sigs'][0].split('-')[0].strip() if r['sigs'] else 'No Signal'
    chg_s=f"+{r['chg']:.2f}%" if r['chg']>=0 else f"{r['chg']:.2f}%"
    ax1.set_title(f"  {r['ticker']}  |  TF:{r['tf']}  |  {lp:,.2f}  {chg_s}  |  {r['trend']}  |  Score:{r['score']}/8  |  {sig_txt}",
                  color=TEXT,fontsize=9,fontweight='bold',loc='left',pad=6,
                  bbox=dict(boxstyle='round,pad=0.3',facecolor='#0f1a2e',edgecolor=GRID))
    ax1.legend(loc='upper left',fontsize=7,facecolor=BG2,edgecolor=GRID,labelcolor=TEXT2)
    ax1.set_xlim(-0.5,n-0.5); ax1.tick_params(labelbottom=False)

    # Volume
    vol_colors=[GREEN if closes[i]>=opens[i] else RED for i in idx]
    ax2.bar(idx,vols,color=vol_colors,alpha=0.8,width=0.7)
    ax2.axhline(avg_v,color=TEXT2,linewidth=0.7,linestyle='--',alpha=0.6)
    # Spike markers on volume
    for i in idx:
        vr_i=vols[i]/avg_v if avg_v>0 else 1
        if vr_i>=2.0:
            is_buy=closes[i]>=opens[i]
            ax2.bar(i,vols[i],color=DARK_GREEN if is_buy else DARK_RED,alpha=0.9,width=0.7)
    ax2.set_ylabel("VOL",color=TEXT2,fontsize=7)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x,_: f"{x/1e9:.1f}B" if x>=1e9 else f"{x/1e6:.0f}M" if x>=1e6 else f"{x/1e3:.0f}K"))
    ax2.tick_params(labelbottom=False); ax2.set_xlim(-0.5,n-0.5)

    # MACD
    hist_colors=[GREEN if v>=0 else RED for v in macd_h.values]
    ax3.bar(idx,macd_h.values,color=hist_colors,alpha=0.8,width=0.7)
    ax3.plot(idx,macd_l.values,color=BLUE,linewidth=1.1,label=f"MACD:{r['macd']:.1f}")
    ax3.plot(idx,macd_sg.values,color=RED,linewidth=0.9,label=f"Sig:{r['msig']:.1f}")
    ax3.axhline(0,color=TEXT2,linewidth=0.5)
    ax3.set_ylabel("MACD",color=TEXT2,fontsize=7)
    ax3.legend(loc='upper left',fontsize=6,facecolor=BG2,edgecolor=GRID,labelcolor=TEXT2)
    ax3.tick_params(labelbottom=False); ax3.set_xlim(-0.5,n-0.5)

    # Stoch + RSI
    ax4.plot(idx,sk.values,color=BLUE,linewidth=1.1,label=f"K:{r['stoch']:.1f}")
    ax4.plot(idx,sd.values,color=PINK,linewidth=0.9,label="D")
    ax4.plot(idx,rsi_s.values,color=ORANGE,linewidth=0.9,linestyle='--',label=f"RSI:{r['rsi']:.1f}")
    ax4.axhline(80,color=RED,linewidth=0.5,linestyle='--',alpha=0.6)
    ax4.axhline(20,color=GREEN,linewidth=0.5,linestyle='--',alpha=0.6)
    ax4.axhline(50,color=TEXT2,linewidth=0.4,alpha=0.4)
    ax4.fill_between(idx,80,100,alpha=0.06,color=RED)
    ax4.fill_between(idx,0,20,alpha=0.06,color=GREEN)
    ax4.set_ylim(0,100); ax4.set_ylabel("STOCH",color=TEXT2,fontsize=7)
    ax4.legend(loc='upper left',fontsize=6,facecolor=BG2,edgecolor=GRID,labelcolor=TEXT2)
    ax4.set_xlim(-0.5,n-0.5)

    step=max(1,n//10); ticks=list(range(0,n,step))
    fmt="%d/%m" if tf in ["D","W","M"] else "%H:%M"
    labels=[df.index[i].strftime(fmt) for i in ticks]
    ax4.set_xticks(ticks); ax4.set_xticklabels(labels,fontsize=7,color=TEXT2)

    fig.text(0.5,0.5,"IDX QUANT\nT1MO Style",color='white',alpha=0.04,
             fontsize=48,ha='center',va='center',rotation=30,fontweight='bold')
    plt.tight_layout(pad=0.5)
    buf=io.BytesIO()
    plt.savefig(buf,format='png',dpi=130,bbox_inches='tight',facecolor=BG)
    buf.seek(0); plt.close(fig)
    return buf,None

def fmt_now(): return datetime.now(WIB).strftime("%d-%b-%Y %H:%M")+" WIB"

# ══════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════
async def start(u,c):
    await u.message.reply_text(
        "⚡ *IDX QUANT Bot — T1MO × Wisdom*\n\n"
        "📊 *Chart & Signal:*\n"
        "`/signal BBCA` — Signal + indikator\n"
        "`/signal PLTR D` — Saham US juga bisa!\n"
        "`/chart ENRG 1H` — Chart candlestick\n"
        "`/chart PLTR D` — Chart saham US\n\n"
        "🔍 *Screener:*\n"
        "`/screener` — Top picks IDX\n"
        "`/screener_us` — Top picks US stocks\n\n"
        "🔔 *Alert:*\n"
        "`/alert ENRG 2000` — Notif kalau ENRG tembus 2000\n"
        "`/alerts` — Lihat semua alert aktif\n"
        "`/delalert ENRG` — Hapus alert\n\n"
        "⭐ *Watchlist:*\n"
        "`/wl` — Lihat watchlist\n"
        "`/wladd ENRG` — Tambah saham\n"
        "`/wldel ENRG` — Hapus saham\n"
        "`/wlscan` — Scan semua watchlist\n\n"
        "🤖 *Auto Scan:*\n"
        "`/auto on` — Aktifkan auto scan jam 9 pagi + volume spike alert\n"
        "`/auto off` — Matikan auto scan\n\n"
        "📈 *Market:*\n"
        "`/volume` — Top volume IDX\n"
        "`/trend` — Market overview\n"
        "`/help` — Bantuan lengkap",
        parse_mode="Markdown")

async def help_cmd(u,c):
    await u.message.reply_text(
        "📖 *IDX QUANT — Command List*\n\n"
        "*Signal & Chart:*\n"
        "`/signal KODE [TF]` — TF: 5M 15M 30M 1H 4H D W M\n"
        "`/chart KODE [TF]` — Gambar chart candlestick\n\n"
        "*Screener:*\n"
        "`/screener [min_score]` — IDX screener\n"
        "`/screener_us [min_score]` — US stock screener\n\n"
        "*Alert Harga:*\n"
        "`/alert KODE HARGA` — Set price alert\n"
        "`/alerts` — Lihat semua alert\n"
        "`/delalert KODE` — Hapus alert\n\n"
        "*Watchlist:*\n"
        "`/wl` — Lihat watchlist\n"
        "`/wladd KODE` — Tambah ke watchlist\n"
        "`/wldel KODE` — Hapus dari watchlist\n"
        "`/wlscan` — Scan signal semua watchlist\n\n"
        "*Auto Scan:*\n"
        "`/auto on` — Aktifkan (jam 9 WIB + volume spike)\n"
        "`/auto off` — Matikan\n\n"
        "*Market:*\n"
        "`/volume` — Top volume IDX\n"
        "`/trend` — Trend market + IHSG\n\n"
        "Score: 1-3 Lemah | 4-5 OK | 6+ 🔥\n"
        "Volume Spike: ▲ hijau tua (buy) | ▼ merah tua (sell)",
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
    await m.edit_text(
        f"⚡ *{r['ticker']}* | TF:`{r['tf']}`\n━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Harga: *{r['price']:,.2f}*\n{em} Change: `{r['chg']:+.2f}%`\n"
        f"📊 Trend: *{r['trend']}* {vspike}\n\n"
        f"📐 *Indikator:*\n  EMA9:  `{r['e9']:,.2f}`\n  EMA20: `{r['e20']:,.2f}`\n"
        f"  EMA50: `{r['e50']:,.2f}`\n  RSI:   `{r['rsi']:.1f}`\n"
        f"  MACD:  `{r['macd']:.2f}` Sig:`{r['msig']:.2f}`\n"
        f"  STOCH: `{r['stoch']:.1f}`\n  Vol:   `{r['vr']:.1f}x` avg\n\n"
        f"🎯 *Signals:*\n{sx}\n\n"
        f"{sc} Score:`[{bar}]` {r['score']}/8\n━━━━━━━━━━━━━━━━━━━━\n⏱ {fmt_now()}",
        parse_mode="Markdown")

async def chart_cmd(u,c):
    args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/chart BBCA` atau `/chart PLTR D`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK",""); tf=args[1].upper() if len(args)>1 else "D"
    m=await u.message.reply_text(f"📊 Membuat chart *{code}* TF:{tf}...",parse_mode="Markdown")
    buf,err=generate_chart(code,tf)
    if err: await m.edit_text(f"❌ Error: {err}"); return
    await m.delete()
    r=get_signal(code,tf)
    sig_txt=r['sigs'][0].split('-')[0].strip() if r.get('sigs') else 'No Signal'
    vspike="🌊 VOL SPIKE!" if r.get('vr',0)>=2 else ""
    caption=(f"📊 *{r['ticker']}* | TF:`{tf}` | `{r['price']:,.2f}` `{r['chg']:+.2f}%`\n"
             f"📈 {r['trend']} | Score:`{r['score']}/8` | {sig_txt} {vspike}\n"
             f"EMA9:`{r['e9']:,.2f}` MA20:`{r['e20']:,.2f}` MA50:`{r['e50']:,.2f}`\n"
             f"RSI:`{r['rsi']:.1f}` MACD:`{r['macd']:.1f}` STOCH:`{r['stoch']:.1f}`\n"
             f"⏱ {fmt_now()}")
    await u.message.reply_photo(photo=buf,caption=caption,parse_mode="Markdown")

# ══ SCREENER ══
async def screener_cmd(u,c):
    args=c.args; ms=int(args[0]) if args and args[0].isdigit() else 3
    m=await u.message.reply_text(f"🔍 Screener IDX min score {ms}... (~30 detik)")
    res=[]
    for code in IDX_STOCKS[:25]:
        r=get_signal(code,"D")
        if "error" not in r and r["score"]>=ms: res.append(r)
    res.sort(key=lambda x:x["score"],reverse=True)
    if not res: await m.edit_text("❌ Tidak ada hasil."); return
    lines=[f"🔍 *IDX SCREENER* | Min Score:{ms}","━━━━━━━━━━━━━━━━━━━━"]
    for r in res[:12]:
        em="🟢" if r["chg"]>=0 else "🔴"
        top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
        vs="🌊" if r["vr"]>=2 else ""
        lines.append(f"{em} *{r['code']}* `{r['price']:,.0f}` {r['chg']:+.2f}% Score:`{r['score']}/8` {top}{vs}")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

async def screener_us_cmd(u,c):
    args=c.args; ms=int(args[0]) if args and args[0].isdigit() else 2
    m=await u.message.reply_text(f"🔍 Screener US Stocks min score {ms}... (~30 detik)")
    res=[]
    for code in US_STOCKS:
        r=get_signal(code,"D")
        if "error" not in r and r["score"]>=ms: res.append(r)
    res.sort(key=lambda x:x["score"],reverse=True)
    if not res: await m.edit_text("❌ Tidak ada hasil."); return
    lines=[f"🇺🇸 *US STOCK SCREENER* | Min Score:{ms}","━━━━━━━━━━━━━━━━━━━━"]
    for r in res[:12]:
        em="🟢" if r["chg"]>=0 else "🔴"
        top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
        vs="🌊" if r["vr"]>=2 else ""
        lines.append(f"{em} *{r['code']}* `${r['price']:,.2f}` {r['chg']:+.2f}% Score:`{r['score']}/8` {top}{vs}")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

# ══ ALERT ══
async def alert_cmd(u,c):
    uid=str(u.effective_user.id); args=c.args
    if len(args)<2: await u.message.reply_text("⚠️ Format: `/alert ENRG 2000`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK",""); target=float(args[1].replace(",",""))
    r=get_signal(code,"D")
    if "error" in r: await u.message.reply_text(f"❌ Saham {code} tidak ditemukan"); return
    cur=r["price"]; direction="above" if target>cur else "below"
    if uid not in alerts_db: alerts_db[uid]=[]
    alerts_db[uid]=[a for a in alerts_db[uid] if a["code"]!=code]
    alerts_db[uid].append({"code":code,"target":target,"direction":direction,"ticker":r["ticker"]})
    save_json(ALERT_FILE,alerts_db)
    em="⬆️" if direction=="above" else "⬇️"
    await u.message.reply_text(
        f"🔔 *Alert Set!*\n{code} | Harga skrg: `{cur:,.2f}`\n{em} Notif kalau `{target:,.2f}` {'tertembus ke atas' if direction=='above' else 'tertembus ke bawah'}",
        parse_mode="Markdown")

async def alerts_cmd(u,c):
    uid=str(u.effective_user.id)
    al=alerts_db.get(uid,[])
    if not al: await u.message.reply_text("📭 Tidak ada alert aktif.\nGunakan `/alert ENRG 2000`",parse_mode="Markdown"); return
    lines=["🔔 *Alert Aktif:*","━━━━━━━━━━━━━━━━━━━━"]
    for a in al:
        em="⬆️" if a["direction"]=="above" else "⬇️"
        lines.append(f"{em} *{a['code']}* → target: `{a['target']:,.2f}`")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"Total: {len(al)} alert"]
    await u.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def delalert_cmd(u,c):
    uid=str(u.effective_user.id); args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/delalert ENRG`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK","")
    if uid in alerts_db:
        alerts_db[uid]=[a for a in alerts_db[uid] if a["code"]!=code]
        save_json(ALERT_FILE,alerts_db)
    await u.message.reply_text(f"✅ Alert *{code}* dihapus",parse_mode="Markdown")

# ══ WATCHLIST ══
async def wl_cmd(u,c):
    uid=str(u.effective_user.id); wl=watchlist_db.get(uid,[])
    if not wl: await u.message.reply_text("📭 Watchlist kosong.\nGunakan `/wladd ENRG`",parse_mode="Markdown"); return
    lines=["⭐ *WATCHLIST KAMU*","━━━━━━━━━━━━━━━━━━━━"]
    for code in wl:
        r=get_signal(code,"D")
        if "error" not in r:
            em="🟢" if r["chg"]>=0 else "🔴"
            lines.append(f"{em} *{code}* `{r['price']:,.2f}` {r['chg']:+.2f}% Score:`{r['score']}/8`")
        else: lines.append(f"❓ *{code}*")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await u.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def wladd_cmd(u,c):
    uid=str(u.effective_user.id); args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/wladd ENRG`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK","")
    if uid not in watchlist_db: watchlist_db[uid]=[]
    if code not in watchlist_db[uid]:
        watchlist_db[uid].append(code); save_json(WL_FILE,watchlist_db)
        await u.message.reply_text(f"✅ *{code}* ditambahkan ke watchlist ⭐",parse_mode="Markdown")
    else: await u.message.reply_text(f"ℹ️ *{code}* sudah ada di watchlist",parse_mode="Markdown")

async def wldel_cmd(u,c):
    uid=str(u.effective_user.id); args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/wldel ENRG`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK","")
    if uid in watchlist_db and code in watchlist_db[uid]:
        watchlist_db[uid].remove(code); save_json(WL_FILE,watchlist_db)
    await u.message.reply_text(f"✅ *{code}* dihapus dari watchlist",parse_mode="Markdown")

async def wlscan_cmd(u,c):
    uid=str(u.effective_user.id); wl=watchlist_db.get(uid,[])
    if not wl: await u.message.reply_text("📭 Watchlist kosong.",parse_mode="Markdown"); return
    m=await u.message.reply_text(f"🔍 Scanning {len(wl)} saham watchlist...")
    lines=["⭐ *WATCHLIST SCAN*","━━━━━━━━━━━━━━━━━━━━"]
    for code in wl:
        r=get_signal(code,"D")
        if "error" not in r:
            em="🟢" if r["chg"]>=0 else "🔴"
            top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
            vs="🌊" if r["vr"]>=2 else ""
            lines.append(f"{em} *{code}* `{r['price']:,.2f}` {r['chg']:+.2f}% Score:`{r['score']}/8` {top}{vs}")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

# ══ AUTO SCAN ══
async def auto_cmd(u,c):
    uid=str(u.effective_user.id); args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/auto on` atau `/auto off`",parse_mode="Markdown"); return
    if args[0].lower()=="on":
        auto_users[uid]=True; save_json(AUTO_FILE,auto_users)
        await u.message.reply_text(
            "🤖 *Auto Scan AKTIF!*\n\n"
            "✅ Scan otomatis jam *09:00 WIB* setiap hari\n"
            "✅ Volume spike alert setiap *15 menit*\n"
            "🟢 Panah hijau tua = Volume BUY spike\n"
            "🔴 Panah merah tua = Volume SELL spike",
            parse_mode="Markdown")
    else:
        auto_users.pop(uid,None); save_json(AUTO_FILE,auto_users)
        await u.message.reply_text("⏹ Auto scan dimatikan.",parse_mode="Markdown")

async def volume_cmd(u,c):
    m=await u.message.reply_text("💧 Mengambil data volume...")
    vd=[]
    for code in IDX_STOCKS[:20]:
        try:
            df=yf.download(f"{code}.JK",period="5d",interval="1d",progress=False,auto_adjust=True)
            if len(df)>=2:
                lv=float(df["Volume"].iloc[-1]); av=float(df["Volume"].mean())
                lc=float(df["Close"].iloc[-1]); vr=lv/av if av>0 else 1
                vd.append({"code":code,"price":lc,"vol":lv,"vr":vr})
        except: continue
    vd.sort(key=lambda x:x["vol"],reverse=True)
    lines=["💧 *TOP VOLUME IDX*","━━━━━━━━━━━━━━━━━━━━"]
    for i,v in enumerate(vd[:12],1):
        vs=f"{v['vol']/1e9:.1f}B" if v['vol']>=1e9 else f"{v['vol']/1e6:.0f}M"
        ic="🌊" if v["vr"]>=2 else "📈" if v["vr"]>=1.5 else "📊"
        lines.append(f"{i}. {ic} *{v['code']}* `{v['price']:,.0f}` Vol:`{vs}` ({v['vr']:.1f}x)")
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
    up=dn=sd=0; hot=[]
    for code in ["BBCA","BBRI","TLKM","BMRI","ASII","ENRG","ANTM","GOTO","ADMR","MDKA"]:
        r=get_signal(code,"D")
        if "error" not in r:
            if "UP" in r["trend"]: up+=1
            elif "DOWN" in r["trend"]: dn+=1
            else: sd+=1
            if r["score"]>=5: hot.append(f"  🔥 {code} score:{r['score']}")
    tot=up+dn+sd; mood="BULLISH 🟢" if up>dn else "BEARISH 🔴" if dn>up else "MIXED ↔"
    lines=["🌊 *MARKET TREND IDX*","━━━━━━━━━━━━━━━━━━━━",f"📊 {itxt}","",
           f"🎯 Mood: *{mood}*",f"🟢 Uptrend:   `{up}/{tot}`",
           f"🔴 Downtrend: `{dn}/{tot}`",f"↔️ Sideways:  `{sd}/{tot}`"]
    if hot: lines+=["","🔥 *Hot Signals:*"]+hot
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

# ══════════════════════════════════════════
# BACKGROUND JOBS
# ══════════════════════════════════════════
async def check_alerts(context):
    """Check price alerts every 5 minutes"""
    if not alerts_db: return
    bot=context.bot
    for uid,al in list(alerts_db.items()):
        triggered=[]
        remaining=[]
        for a in al:
            try:
                r=get_signal(a["code"],"D")
                if "error" in r: remaining.append(a); continue
                cur=r["price"]
                hit=(a["direction"]=="above" and cur>=a["target"]) or \
                    (a["direction"]=="below" and cur<=a["target"])
                if hit:
                    em="⬆️" if a["direction"]=="above" else "⬇️"
                    await bot.send_message(int(uid),
                        f"🔔 *ALERT TRIGGERED!*\n\n"
                        f"{em} *{a['code']}* sudah tembus `{a['target']:,.2f}`\n"
                        f"Harga sekarang: *{cur:,.2f}*\n"
                        f"Score: {r['score']}/8 | {r['trend']}\n"
                        f"⏱ {fmt_now()}",parse_mode="Markdown")
                    # Send chart
                    buf,_=generate_chart(a["code"],"D")
                    if buf: await bot.send_photo(int(uid),photo=buf,caption=f"📊 Chart {a['code']} saat alert triggered")
                else: remaining.append(a)
            except: remaining.append(a)
        alerts_db[uid]=remaining
    save_json(ALERT_FILE,alerts_db)

async def volume_spike_scan(context):
    """Scan volume spikes every 15 minutes during market hours"""
    now=datetime.now(WIB)
    # Only during IDX market hours (9:00-16:00 WIB weekdays)
    if now.weekday()>=5: return
    if not (dtime(9,0)<=now.time()<=dtime(16,0)): return
    if not auto_users: return

    bot=context.bot
    all_stocks=IDX_STOCKS[:20]+US_STOCKS[:10]
    spikes=[]
    for code in all_stocks:
        tf="5M" if code in IDX_STOCKS else "5M"
        spike=detect_volume_spike(code,tf,threshold=2.5)
        if spike: spikes.append(spike)

    if not spikes: return

    for uid in auto_users:
        try:
            lines=["⚡ *VOLUME SPIKE ALERT!*","━━━━━━━━━━━━━━━━━━━━"]
            buy_spikes=[s for s in spikes if s["direction"]=="BUY"]
            sell_spikes=[s for s in spikes if s["direction"]=="SELL"]
            if buy_spikes:
                lines.append("🟢 *BUY VOLUME SPIKE:*")
                for s in buy_spikes[:5]:
                    lines.append(f"  ▲ *{s['code']}* `{s['price']:,.2f}` +{s['chg']:.2f}% Vol:{s['vr']:.1f}x")
            if sell_spikes:
                lines.append("🔴 *SELL VOLUME SPIKE:*")
                for s in sell_spikes[:5]:
                    lines.append(f"  ▼ *{s['code']}* `{s['price']:,.2f}` {s['chg']:.2f}% Vol:{s['vr']:.1f}x")
            lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
            await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
            # Send chart for top spike
            if spikes:
                top=spikes[0]
                buf,_=generate_chart(top["code"],"5M")
                if buf:
                    dir_txt="🟢 BUY SPIKE" if top["direction"]=="BUY" else "🔴 SELL SPIKE"
                    await bot.send_photo(int(uid),photo=buf,
                        caption=f"📊 {top['code']} | {dir_txt} | Vol:{top['vr']:.1f}x avg | {fmt_now()}")
        except Exception as e: log.error(f"spike alert error uid {uid}: {e}")

async def morning_scan(context):
    """Morning scan at 9:00 WIB"""
    now=datetime.now(WIB)
    if now.weekday()>=5: return  # Skip weekend
    if not auto_users: return

    bot=context.bot
    res=[]
    for code in IDX_STOCKS[:20]:
        r=get_signal(code,"D")
        if "error" not in r and r["score"]>=4: res.append(r)
    res.sort(key=lambda x:x["score"],reverse=True)

    for uid in auto_users:
        try:
            lines=["🌅 *MORNING SCAN — "+now.strftime("%d %b %Y")+"*",
                   "━━━━━━━━━━━━━━━━━━━━",
                   "🔥 Top picks hari ini:\n"]
            for r in res[:8]:
                em="🟢" if r["chg"]>=0 else "🔴"
                top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
                lines.append(f"{em} *{r['code']}* `{r['price']:,.0f}` Score:`{r['score']}/8` {top}")
            lines+=["━━━━━━━━━━━━━━━━━━━━","🤖 Auto scan aktif — volume spike dipantau setiap 15 menit"]
            await bot.send_message(int(uid),"\n".join(lines),parse_mode="Markdown")
            # Send top 3 charts
            for r in res[:3]:
                buf,_=generate_chart(r["code"],"D")
                if buf: await bot.send_photo(int(uid),photo=buf,
                    caption=f"📊 {r['code']} | Score:{r['score']}/8 | {r['trend']}")
        except Exception as e: log.error(f"morning scan error uid {uid}: {e}")

# ══ FLASK ══
app=Flask(__name__)
@app.route("/")
def index():
    f=os.path.join(os.path.dirname(__file__),"idx_dashboard_v4.html")
    return send_file(f) if os.path.exists(f) else ("IDX QUANT v4",404)
@app.route("/health")
def health(): return jsonify({"status":"ok","alerts":len(alerts_db),"auto_users":len(auto_users)})
@app.route("/api/signal/<code>")
def api_sig(code):
    r=get_signal(code.upper(),"D")
    return jsonify({k:v for k,v in r.items() if k not in ["df","ema9","ema20","ema50","rsi_s","macd_l","macd_sg","macd_h","stoch_k","stoch_d"]})

def run_flask(): app.run(host="0.0.0.0",port=PORT,debug=False,use_reloader=False)

def run_bot():
    if not TOKEN: log.warning("TELEGRAM_TOKEN not set"); return
    tg=Application.builder().token(TOKEN).build()
    # Commands
    cmds=[("start",start),("help",help_cmd),("signal",signal_cmd),("chart",chart_cmd),
          ("screener",screener_cmd),("screener_us",screener_us_cmd),
          ("alert",alert_cmd),("alerts",alerts_cmd),("delalert",delalert_cmd),
          ("wl",wl_cmd),("wladd",wladd_cmd),("wldel",wldel_cmd),("wlscan",wlscan_cmd),
          ("auto",auto_cmd),("volume",volume_cmd),("trend",trend_cmd)]
    for cmd,fn in cmds: tg.add_handler(CommandHandler(cmd,fn))
    # Jobs
    jq=tg.job_queue
    jq.run_repeating(check_alerts,interval=300,first=60)           # Check alerts every 5 min
    jq.run_repeating(volume_spike_scan,interval=900,first=120)     # Volume spike every 15 min
    jq.run_daily(morning_scan,time=dtime(9,0,tzinfo=WIB))         # Morning scan 9:00 WIB
    log.info("Bot polling with jobs..."); tg.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    log.info(f"IDX QUANT v4 FULL port {PORT}")
    threading.Thread(target=run_flask,daemon=True).start()
    run_bot()
