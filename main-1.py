import os,threading,logging
import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz
from flask import Flask,send_file,jsonify
from telegram import Update
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
    return 100-(100/(1+g/l))
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
        elif lr<30: sigs.append(f"🔄 RSI Oversold ({lr:.1f}) - Rebound"); sc+=1
        elif lr>70: sigs.append(f"⚠️ RSI Overbought ({lr:.1f})"); sc-=1
        if vr>2: sigs.append(f"🌊 BUY LAUTAN - Volume {vr:.1f}x avg"); sc+=2
        elif vr>1.5: sigs.append(f"📈 Volume {vr:.1f}x avg"); sc+=1
        if lsk<20: sigs.append(f"🟣 BUY MAGENTA - Stoch Oversold ({lsk:.1f})"); sc+=1
        elif lsk>80: sigs.append(f"⚠️ Stoch Overbought ({lsk:.1f})")
        trend="UPTREND ⬆" if lc>le50 else "DOWNTREND ⬇" if lc<le50 else "SIDEWAYS ↔"
        return{"code":code.upper(),"tf":tf.upper(),"price":lc,"chg":chg,"e9":le9,"e20":le20,"e50":le50,"rsi":lr,"macd":lm,"msig":ls,"stoch":lsk,"vr":vr,"sigs":sigs,"score":sc,"trend":trend}
    except Exception as e: return{"error":str(e)}

def fmt_now(): return datetime.now(WIB).strftime("%d-%b-%Y %H:%M")+" WIB"

async def start(u,c): await u.message.reply_text("⚡ *IDX QUANT Bot — T1MO × Wisdom*\n\n/signal BBCA\n/signal ENRG 4H\n/chart ADMR\n/screener\n/screener 5\n/volume\n/trend\n/help",parse_mode="Markdown")
async def help_cmd(u,c): await u.message.reply_text("📖 *Commands:*\n`/signal KODE` — Signal daily\n`/signal KODE TF` — TF: 5M 15M 30M 1H 4H D W M\n`/chart KODE` — Multi-TF scan\n`/screener` — Top picks IDX\n`/screener 5` — Min score 5\n`/volume` — Top volume\n`/trend` — Market overview\n\nScore: 1-3 Lemah | 4-5 OK | 6+ 🔥",parse_mode="Markdown")

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
    if not args: await u.message.reply_text("⚠️ `/chart BBCA`",parse_mode="Markdown"); return
    code=args[0].upper().replace(".JK","")
    m=await u.message.reply_text(f"📊 Scan *{code}* semua TF...",parse_mode="Markdown")
    lines=[f"📊 *{code}* — Multi-TF Analysis","━━━━━━━━━━━━━━━━━━━━"]
    for tf in ["5M","15M","30M","1H","4H","D","W","M"]:
        r=get_signal(code,tf)
        if "error" in r: lines.append(f"❌ `{tf:>5}` Error"); continue
        ic="🟢" if r["score"]>=5 else "🟡" if r["score"]>=3 else "🔴"
        tr="↑" if "UP" in r["trend"] else "↓" if "DOWN" in r["trend"] else "↔"
        top=r["sigs"][0].split("-")[0].strip() if r["sigs"] else "No Signal"
        lines.append(f"{ic} `{tf:>5}` Score:`{r['score']}/8` RSI:`{r['rsi']:.0f}` {tr} {top}")
    lines+=["━━━━━━━━━━━━━━━━━━━━",f"⏱ {fmt_now()}"]
    await m.edit_text("\n".join(lines),parse_mode="Markdown")

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
    except: itxt="IHSG: error"
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

app=Flask(__name__)
@app.route("/")
def index():
    f=os.path.join(os.path.dirname(__file__),"idx_dashboard_v4.html")
    return send_file(f) if os.path.exists(f) else ("IDX QUANT - HTML not found",404)
@app.route("/health")
def health(): return jsonify({"status":"ok"})
@app.route("/api/signal/<code>")
def api_sig(code): return jsonify(get_signal(code.upper(),"D"))
@app.route("/api/signal/<code>/<tf>")
def api_sig_tf(code,tf): return jsonify(get_signal(code.upper(),tf.upper()))

def run_flask(): app.run(host="0.0.0.0",port=PORT,debug=False,use_reloader=False)
def run_bot():
    if not TOKEN: log.warning("TELEGRAM_TOKEN not set"); return
    tg=Application.builder().token(TOKEN).build()
    for cmd,fn in [("start",start),("help",help_cmd),("signal",signal_cmd),("chart",chart_cmd),("screener",screener_cmd),("volume",volume_cmd),("trend",trend_cmd)]:
        tg.add_handler(CommandHandler(cmd,fn))
    log.info("Bot polling..."); tg.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    log.info(f"IDX QUANT v4 port {PORT}")
    threading.Thread(target=run_flask,daemon=True).start()
    run_bot()
