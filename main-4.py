import os,threading,logging,io
import yfinance as yf
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from datetime import datetime
import pytz
from flask import Flask,send_file,jsonify
from telegram import Update,InputFile
from telegram.ext import Application,CommandHandler,ContextTypes

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s",level=logging.INFO)
log=logging.getLogger(__name__)
TOKEN=os.environ.get("TELEGRAM_TOKEN","")
PORT=int(os.environ.get("PORT",8080))
WIB=pytz.timezone("Asia/Jakarta")

STOCKS=["ADMR","ENRG","ANTM","NCKL","MBMA","PTBA","MEDC","BULL","TMAS","INCO","MDKA","ITMG","AALI","TAPG","ELSA","SMDR","ADRO","INDY","BSSR","RAJA","DEWA","DSNG","GOTO","TLKM","BBRI","BBCA","BMRI","PGAS","BYAN","HRUM","FIRE","TINS","ZINC","KIJA","LSIP","SSMS","SLIS","NFCX","CUAN","NICK"]
TF_MAP={"5M":("5m","5d"),"15M":("15m","5d"),"30M":("30m","10d"),"1H":("60m","20d"),"4H":("60m","30d"),"D":("1d","90d"),"W":("1wk","2y"),"M":("1mo","5y")}

def ema(s,p): return s.ewm(span=p,adjust=False).mean()
def rsi(s,p=14):
    d=s.diff(); g=d.clip(lower=0).rolling(p).mean(); l=(-d.clip(upper=0)).rolling(p).mean()
    rs=g/l; return 100-(100/(1+rs))
def macd(s):
    m=ema(s,12)-ema(s,26); sg=ema(m,9); return m,sg,m-sg
def stoch(h,l,c,k=15,d=3):
    lo=l.rolling(k).min(); hi=h.rolling(k).max()
    K=100*(c-lo)/(hi-lo); return K,K.rolling(d).mean()

def get_signal(code,tf="D"):
    iv,per=TF_MAP.get(tf.upper(),("1d","90d"))
    try:
        df=yf.download(f"{code.upper()}.JK",period=per,interval=iv,progress=False,auto_adjust=True)
        if df.empty or len(df)<26: return{"error":"Data kurang"}
        c=df["Close"].squeeze(); h=df["High"].squeeze(); l=df["Low"].squeeze(); v=df["Volume"].squeeze()
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
        return{"code":code.upper(),"tf":tf.upper(),"price":lc,"chg":chg,"e9":le9,"e20":le20,"e50":le50,
               "rsi":lr,"macd":lm,"msig":ls,"stoch":lsk,"vr":vr,"sigs":sigs,"score":sc,"trend":trend,
               "df":df,"ema9":e9,"ema20":e20,"ema50":e50,"rsi_s":r,"macd_l":ml,"macd_sg":sg,"macd_h":hs,"stoch_k":sk,"stoch_d":sd}
    except Exception as e: return{"error":str(e)}

# ══════════════════════════════════════════
# CHART GENERATOR — Wisdom & T1MO Style
# ══════════════════════════════════════════
def generate_chart(code, tf="D"):
    r = get_signal(code, tf)
    if "error" in r:
        return None, r["error"]

    df = r["df"]
    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    vol   = df["Volume"].squeeze()
    n = min(len(df), 80)  # show last 80 candles
    df = df.iloc[-n:]
    close = close.iloc[-n:]
    high  = high.iloc[-n:]
    low   = low.iloc[-n:]
    vol   = vol.iloc[-n:]
    e9    = r["ema9"].iloc[-n:]
    e20   = r["ema20"].iloc[-n:]
    e50   = r["ema50"].iloc[-n:]
    rsi_s = r["rsi_s"].iloc[-n:]
    macd_l= r["macd_l"].iloc[-n:]
    macd_sg=r["macd_sg"].iloc[-n:]
    macd_h= r["macd_h"].iloc[-n:]
    sk    = r["stoch_k"].iloc[-n:]
    sd    = r["stoch_d"].iloc[-n:]
    idx   = range(n)

    # ── Style ──
    BG    = "#0a0e14"
    BG2   = "#0f1520"
    GRID  = "#1a2438"
    GREEN = "#26a69a"
    RED   = "#ef5350"
    ORANGE= "#f07020"
    BLUE  = "#2288cc"
    PINK  = "#e040c8"
    TEXT  = "#c8d6e5"
    TEXT2 = "#7a90a8"

    fig = plt.figure(figsize=(14, 10), facecolor=BG)
    gs  = GridSpec(4, 1, figure=fig, height_ratios=[5,1.2,1.2,1.2], hspace=0.04)

    ax1 = fig.add_subplot(gs[0])  # Candle
    ax2 = fig.add_subplot(gs[1])  # Volume
    ax3 = fig.add_subplot(gs[2])  # MACD
    ax4 = fig.add_subplot(gs[3])  # Stoch/RSI

    for ax in [ax1,ax2,ax3,ax4]:
        ax.set_facecolor(BG2)
        ax.tick_params(colors=TEXT2, labelsize=7)
        ax.spines['bottom'].set_color(GRID)
        ax.spines['top'].set_color(GRID)
        ax.spines['left'].set_color(GRID)
        ax.spines['right'].set_color(GRID)
        ax.yaxis.label.set_color(TEXT2)
        ax.grid(True, color=GRID, linewidth=0.4, alpha=0.6)

    # ── CANDLESTICK ──
    opens  = df["Open"].squeeze().values
    closes = close.values
    highs  = high.values
    lows   = low.values

    for i in idx:
        o,c_,h_,l_ = opens[i],closes[i],highs[i],lows[i]
        color = GREEN if c_>=o else RED
        ax1.plot([i,i],[l_,h_],color=color,linewidth=0.8,zorder=2)
        ax1.bar(i, abs(c_-o), bottom=min(o,c_), color=color, width=0.7, zorder=3)

    # ── EMA Lines ──
    ax1.plot(idx, e50.values, color=BLUE,   linewidth=1.4, label=f"MA50:{r['e50']:,.0f}", zorder=4)
    ax1.plot(idx, e20.values, color=ORANGE, linewidth=1.6, label=f"MA20:{r['e20']:,.0f}", zorder=5)
    ax1.plot(idx, e9.values,  color=PINK,   linewidth=1.1, linestyle='--', label=f"MA9:{r['e9']:,.0f}", zorder=6)

    # ── BB ──
    bb_m = close.rolling(20).mean()
    bb_s = close.rolling(20).std()
    bb_u = (bb_m + 2*bb_s).iloc[-n:]
    bb_l = (bb_m - 2*bb_s).iloc[-n:]
    ax1.fill_between(idx, bb_u.values, bb_l.values, alpha=0.06, color=BLUE)
    ax1.plot(idx, bb_u.values, color=BLUE, linewidth=0.5, linestyle=':', alpha=0.5)
    ax1.plot(idx, bb_l.values, color=BLUE, linewidth=0.5, linestyle=':', alpha=0.5)

    # ── Price tag ──
    lp = closes[-1]
    pc = GREEN if lp>=closes[-2] else RED
    ax1.axhline(lp, color=pc, linewidth=0.7, linestyle='--', alpha=0.7)
    ax1.text(n-0.5, lp, f" {lp:,.0f}", color=pc, fontsize=8, fontweight='bold', va='center',
             bbox=dict(boxstyle='round,pad=0.2', facecolor=BG2, edgecolor=pc, linewidth=0.8))

    # ── Title & Legend ──
    sig_txt = r['sigs'][0].split('-')[0].strip() if r['sigs'] else 'No Signal'
    chg_s = f"+{r['chg']:.2f}%" if r['chg']>=0 else f"{r['chg']:.2f}%"
    ax1.set_title(
        f"  {r['code']}.JK  |  TF: {r['tf']}  |  Rp {lp:,.0f}  {chg_s}  |  {r['trend']}  |  Score: {r['score']}/8  |  {sig_txt}",
        color=TEXT, fontsize=10, fontweight='bold', loc='left', pad=6,
        bbox=dict(boxstyle='round,pad=0.3', facecolor='#0f1a2e', edgecolor=GRID)
    )
    legend = ax1.legend(loc='upper left', fontsize=7, facecolor=BG2, edgecolor=GRID, labelcolor=TEXT2)
    ax1.set_xlim(-0.5, n-0.5)
    ax1.tick_params(labelbottom=False)

    # Color bar (T1MO style) at bottom of ax1
    bar_h = (highs.max()-lows.min())*0.015
    bar_y = lows.min() - bar_h*2
    for i in idx:
        o,c_ = opens[i],closes[i]
        p=(c_-o)/o*100 if o>0 else 0
        col=(GREEN if p>1 else "#4db6ac" if p>0 else "#ef9a9a" if p>-1 else RED)
        ax1.bar(i, bar_h, bottom=bar_y, color=col, width=0.85, zorder=1)

    # ── VOLUME ──
    vol_colors = [GREEN if closes[i]>=opens[i] else RED for i in idx]
    ax2.bar(idx, vol.values, color=vol_colors, alpha=0.8, width=0.7)
    avg_v = vol.mean()
    ax2.axhline(avg_v, color=TEXT2, linewidth=0.7, linestyle='--', alpha=0.6)
    ax2.set_ylabel("VOL", color=TEXT2, fontsize=7)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x,_: f"{x/1e6:.0f}M" if x>=1e6 else f"{x/1e3:.0f}K"))
    ax2.tick_params(labelbottom=False)
    ax2.set_xlim(-0.5, n-0.5)

    # ── MACD ──
    hist_colors = [GREEN if v>=0 else RED for v in macd_h.values]
    ax3.bar(idx, macd_h.values, color=hist_colors, alpha=0.8, width=0.7)
    ax3.plot(idx, macd_l.values, color=BLUE,  linewidth=1.1, label=f"MACD:{r['macd']:.1f}")
    ax3.plot(idx, macd_sg.values,color=RED,   linewidth=0.9, label=f"Sig:{r['msig']:.1f}")
    ax3.axhline(0, color=TEXT2, linewidth=0.5)
    ax3.set_ylabel("MACD", color=TEXT2, fontsize=7)
    ax3.legend(loc='upper left', fontsize=6, facecolor=BG2, edgecolor=GRID, labelcolor=TEXT2)
    ax3.tick_params(labelbottom=False)
    ax3.set_xlim(-0.5, n-0.5)

    # ── STOCHASTIC + RSI ──
    ax4.plot(idx, sk.values, color=BLUE,  linewidth=1.1, label=f"K:{r['stoch']:.1f}")
    ax4.plot(idx, sd.values, color=PINK,  linewidth=0.9, label=f"D")
    ax4.plot(idx, rsi_s.values, color=ORANGE, linewidth=0.9, linestyle='--', label=f"RSI:{r['rsi']:.1f}")
    ax4.axhline(80, color=RED,   linewidth=0.5, linestyle='--', alpha=0.6)
    ax4.axhline(20, color=GREEN, linewidth=0.5, linestyle='--', alpha=0.6)
    ax4.axhline(50, color=TEXT2, linewidth=0.4, alpha=0.4)
    ax4.fill_between(idx, 80, 100, alpha=0.06, color=RED)
    ax4.fill_between(idx, 0,  20,  alpha=0.06, color=GREEN)
    ax4.set_ylim(0,100)
    ax4.set_ylabel("STOCH", color=TEXT2, fontsize=7)
    ax4.legend(loc='upper left', fontsize=6, facecolor=BG2, edgecolor=GRID, labelcolor=TEXT2)
    ax4.set_xlim(-0.5, n-0.5)

    # ── X axis labels ──
    step = max(1, n//10)
    ticks = list(range(0, n, step))
    labels = [df.index[i].strftime("%d/%m" if tf in ["D","W","M"] else "%H:%M") for i in ticks]
    ax4.set_xticks(ticks)
    ax4.set_xticklabels(labels, fontsize=7, color=TEXT2)

    # ── Watermark ──
    fig.text(0.5, 0.5, "IDX QUANT\nT1MO Style", color='white', alpha=0.04,
             fontsize=48, ha='center', va='center', rotation=30, fontweight='bold')

    plt.tight_layout(pad=0.5)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor=BG)
    buf.seek(0)
    plt.close(fig)
    return buf, None

def fmt_now(): return datetime.now(WIB).strftime("%d-%b-%Y %H:%M")+" WIB"

# ══════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════
async def start(u,c): await u.message.reply_text("⚡ *IDX QUANT Bot — T1MO × Wisdom*\n\n/signal BBCA\n/signal ENRG 4H\n/chart ADMR\n/chart ENRG D\n/screener\n/screener 5\n/volume\n/trend\n/help",parse_mode="Markdown")

async def help_cmd(u,c): await u.message.reply_text("📖 *Commands:*\n`/signal KODE` — Signal + indikator\n`/signal KODE TF` — TF: 5M 15M 30M 1H 4H D W M\n`/chart KODE` — 📊 Gambar chart candlestick\n`/chart KODE TF` — Chart timeframe tertentu\n`/screener` — Top picks IDX\n`/screener 5` — Min score 5\n`/volume` — Top volume\n`/trend` — Market overview\n\nScore: 1-3 Lemah | 4-5 OK | 6+ 🔥",parse_mode="Markdown")

async def signal_cmd(u,c):
    args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/signal BBCA` atau `/signal BBCA 1H`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK",""); tf=args[1].upper() if len(args)>1 else "D"
    m=await u.message.reply_text(f"🔍 Analisis *{code}* TF:{tf}...",parse_mode="Markdown")
    r=get_signal(code,tf)
    if "error" in r: await m.edit_text(f"❌ {r['error']}"); return
    em="🟢" if r["chg"]>=0 else "🔴"; bar="█"*min(r["score"],8)+"░"*max(0,8-r["score"])
    sx="\n".join([f"  • {s}" for s in r["sigs"]]) or "  • Tidak ada signal kuat"
    sc="🔥" if r["score"]>=6 else "💪" if r["score"]>=4 else "📊"
    await m.edit_text(f"⚡ *{r['code']}* | TF:`{r['tf']}`\n━━━━━━━━━━━━━━━━━━━━\n💰 Harga: *Rp {r['price']:,.0f}*\n{em} Change: `{r['chg']:+.2f}%`\n📊 Trend: *{r['trend']}*\n\n📐 *Indikator:*\n  EMA9:  `{r['e9']:,.0f}`\n  EMA20: `{r['e20']:,.0f}`\n  EMA50: `{r['e50']:,.0f}`\n  RSI:   `{r['rsi']:.1f}`\n  MACD:  `{r['macd']:.2f}` Sig:`{r['msig']:.2f}`\n  STOCH: `{r['stoch']:.1f}`\n  Vol:   `{r['vr']:.1f}x` avg\n\n🎯 *Signals:*\n{sx}\n\n{sc} Score:`[{bar}]` {r['score']}/8\n━━━━━━━━━━━━━━━━━━━━\n⏱ {fmt_now()}",parse_mode="Markdown")

async def chart_cmd(u,c):
    args=c.args
    if not args: await u.message.reply_text("⚠️ Format: `/chart BBCA` atau `/chart ENRG D`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK",""); tf=args[1].upper() if len(args)>1 else "D"
    m=await u.message.reply_text(f"📊 Membuat chart *{code}* TF:{tf}...",parse_mode="Markdown")
    buf, err = generate_chart(code, tf)
    if err: await m.edit_text(f"❌ Error: {err}"); return
    await m.delete()
    r=get_signal(code,tf)
    sig_txt=r['sigs'][0].split('-')[0].strip() if r.get('sigs') else 'No Signal'
    caption=(f"📊 *{code}.JK* | TF:`{tf}` | Rp `{r['price']:,.0f}` `{r['chg']:+.2f}%`\n"
             f"📈 {r['trend']} | Score:`{r['score']}/8` | {sig_txt}\n"
             f"EMA9:`{r['e9']:,.0f}` MA20:`{r['e20']:,.0f}` MA50:`{r['e50']:,.0f}`\n"
             f"RSI:`{r['rsi']:.1f}` MACD:`{r['macd']:.1f}` STOCH:`{r['stoch']:.1f}`\n"
             f"⏱ {fmt_now()}")
    await u.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")

async def screener_cmd(u,c):
    args=c.args; ms=int(args[0]) if args and args[0].isdigit() else 3
    m=await u.message.reply_text(f"🔍 Screener IDX min score {ms}... (~30 detik)")
    res=[]
    for code in STOCKS[:25]:
        r=get_signal(code,"D")
        if "error" not in r and r["score"]>=ms: res.append(r)
    res.sort(key=lambda x:x["score"],reverse=True)
    if not res: await m.edit_text("❌ Tidak ada saham yang memenuhi kriteria."); return
    lines=[f"🔍 *IDX SCREENER* | Min Score:{ms}","━━━━━━━━━━━━━━━━━━━━"]
    for r in res[:12]:
        em="🟢" if r["chg"]>=0 else "🔴"; top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "—"
        lines.append(f"{em} *{r['code']}* `Rp{r['price']:,.0f}` {r['chg']:+.2f}% Score:`{r['score']}/8` {top}")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

async def volume_cmd(u,c):
    m=await u.message.reply_text("💧 Mengambil data volume...")
    vd=[]
    for code in STOCKS[:20]:
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
        lines.append(f"{i}. {ic} *{v['code']}* `Rp{v['price']:,.0f}` Vol:`{vs}` ({v['vr']:.1f}x)")
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

# ── Flask ──
app=Flask(__name__)
@app.route("/")
def index():
    f=os.path.join(os.path.dirname(__file__),"idx_dashboard_v4.html")
    return send_file(f) if os.path.exists(f) else ("IDX QUANT v4",404)
@app.route("/health")
def health(): return jsonify({"status":"ok"})
@app.route("/api/signal/<code>")
def api_sig(code): return jsonify({k:v for k,v in get_signal(code.upper(),"D").items() if k not in ["df","ema9","ema20","ema50","rsi_s","macd_l","macd_sg","macd_h","stoch_k","stoch_d"]})
@app.route("/api/signal/<code>/<tf>")
def api_sig_tf(code,tf): return jsonify({k:v for k,v in get_signal(code.upper(),tf.upper()).items() if k not in ["df","ema9","ema20","ema50","rsi_s","macd_l","macd_sg","macd_h","stoch_k","stoch_d"]})

def run_flask(): app.run(host="0.0.0.0",port=PORT,debug=False,use_reloader=False)
def run_bot():
    if not TOKEN: log.warning("TELEGRAM_TOKEN not set"); return
    tg=Application.builder().token(TOKEN).build()
    for cmd,fn in [("start",start),("help",help_cmd),("signal",signal_cmd),("chart",chart_cmd),("screener",screener_cmd),("volume",volume_cmd),("trend",trend_cmd)]:
        tg.add_handler(CommandHandler(cmd,fn))
    log.info("Bot polling..."); tg.run_polling(allowed_updates=Update.ALL_TYPES)
TF_MAP={
  "5M":("5m","5d"),
  "15M":("15m","5d"), 
  "30M":("30m","10d"),
  "1H":("60m","60d"),   # ← dari 20d jadi 60d
  "4H":("60m","60d"),   # ← dari 30d jadi 60d
  "D":("1d","1y"),      # ← dari 90d jadi 1y
  "W":("1wk","5y"),     # ← dari 2y jadi 5y
  "M":("1mo","10y")     # ← dari 5y jadi 10y
}
if __name__=="__main__":
    log.info(f"IDX QUANT v4 port {PORT}")
    threading.Thread(target=run_flask,daemon=True).start()
    run_bot()
