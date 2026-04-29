import { useState, useEffect, useCallback, useRef } from "react";

// --- DEFAULT WATCHLIST (IDX + US) ---
const DEFAULT_TICKERS = [
  "TAPG.JK","DSNG.JK","MEDC.JK","ITMG.JK","PTBA.JK",
  "NCKL.JK","MBMA.JK","ANTM.JK","INCO.JK","MDKA.JK",
  "BULL.JK","TMAS.JK","PLTR","KOTA.JK"
];

// ─── YAHOO FINANCE FETCH via allorigins proxy ─────────────────────────────────
async function fetchYahoo(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=1d&range=60d`;
  const proxy = `https://api.allorigins.win/get?url=${encodeURIComponent(url)}`;
  const res = await fetch(proxy, { signal: AbortSignal.timeout(10000) });
  const json = await res.json();
  const data = JSON.parse(json.contents);
  const chart = data.chart.result[0];
  const closes = chart.indicators.quote[0].close;
  const volumes = chart.indicators.quote[0].volume;
  const meta = chart.meta;
  return { closes, volumes, meta, symbol };
}

// ─── COMPUTE INDICATORS ───────────────────────────────────────────────────────
function ema(arr, period) {
  const k = 2 / (period + 1);
  const result = [];
  let prev = arr.slice(0, period).reduce((a, b) => a + b, 0) / period;
  result.push(...Array(period - 1).fill(null), prev);
  for (let i = period; i < arr.length; i++) {
    prev = arr[i] * k + prev * (1 - k);
    result.push(prev);
  }
  return result;
}

function computeRSI(closes, period = 14) {
  if (closes.length < period + 1) return 50;
  let gains = 0, losses = 0;
  for (let i = closes.length - period; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) gains += diff; else losses -= diff;
  }
  const rs = gains / (losses || 1);
  return 100 - 100 / (1 + rs);
}

function computeStoch(closes, period = 14) {
  const slice = closes.slice(-period);
  const low = Math.min(...slice);
  const high = Math.max(...slice);
  return high === low ? 50 : ((closes[closes.length - 1] - low) / (high - low)) * 100;
}

function computeMACD(closes) {
  const ema12 = ema(closes, 12);
  const ema26 = ema(closes, 26);
  const macdLine = ema12.map((v, i) => (v && ema26[i] ? v - ema26[i] : null));
  const signal = ema(macdLine.filter(Boolean), 9);
  return { macd: macdLine[macdLine.length - 1] || 0, signal: signal[signal.length - 1] || 0 };
}

function computeMomentum(closes, volumes) {
  // momentum = weighted combo of RSI, MACD, price change, stoch
  const rsi = computeRSI(closes);
  const stoch = computeStoch(closes);
  const { macd, signal } = computeMACD(closes);
  const pctChange = closes.length >= 2
    ? (closes[closes.length - 1] - closes[closes.length - 2]) / closes[closes.length - 2]
    : 0;

  // normalize each to -1..1
  const rsiN = (rsi - 50) / 50;
  const stochN = (stoch - 50) / 50;
  const macdN = Math.tanh((macd - signal) * 2);
  const priceN = Math.tanh(pctChange * 20);

  const composite = rsiN * 0.25 + stochN * 0.2 + macdN * 0.35 + priceN * 0.2;

  // vol-adjusted
  const recentVol = volumes.slice(-5).filter(Boolean);
  const avgVol = recentVol.reduce((a, b) => a + b, 0) / (recentVol.length || 1);
  const volSpike = volumes[volumes.length - 1] / (avgVol || 1);
  const volFactor = Math.min(2, Math.max(0.5, volSpike));

  return Math.max(-1, Math.min(1, composite * volFactor));
}

function buildPixelBars(closes, volumes, n = 40) {
  const bars = [];
  for (let i = Math.max(n, closes.length) - n; i < closes.length; i++) {
    const slice = closes.slice(0, i + 1);
    const vSlice = volumes.slice(0, i + 1);
    const m = computeMomentum(slice, vSlice);
    const v = Math.min(1, (volumes[i] || 0) / (Math.max(...volumes.slice(Math.max(0, i - 20), i + 1)) || 1));
    bars.push({ momentum: m, vol: v });
  }
  return bars;
}

// ─── COLOR LOGIC ──────────────────────────────────────────────────────────────
function momentumColor(m) {
  if (m < -0.6) return "#7B0000";
  if (m < -0.2) return "#E53935";
  if (m < 0.2)  return "#F9A825";
  if (m < 0.6)  return "#43A047";
  return "#1B5E20";
}
function momentumLabel(m) {
  if (m < -0.6) return "JUAL KUAT ▼▼";
  if (m < -0.2) return "JUAL ▼";
  if (m < 0.2)  return "NETRAL ─";
  if (m < 0.6)  return "BELI ▲";
  return "BELI KUAT ▲▲";
}
function signalBadge(m) {
  if (m > 0.6)  return { label: "HAWK1 🦅", color: "#1B5E20", bg: "#0a1f0a" };
  if (m > 0.2)  return { label: "GREEN BULL", color: "#43A047", bg: "#0a150a" };
  if (m < -0.6) return { label: "BEAR 🐻", color: "#7B0000", bg: "#1f0a0a" };
  if (m < -0.2) return { label: "SELL", color: "#E53935", bg: "#180a0a" };
  return { label: "WAIT", color: "#888", bg: "#111" };
}

// ─── PIXEL BAR COMPONENT ──────────────────────────────────────────────────────
function PixelBar({ momentum, vol, maxH = 52, px = 3, gap = 1 }) {
  const color = momentumColor(momentum);
  const filledH = Math.max(px, Math.round(Math.abs(momentum) * vol * maxH));
  const count = Math.floor(filledH / (px + gap));
  return (
    <div style={{ display:"flex", flexDirection:"column-reverse", alignItems:"center", gap:`${gap}px`, height:maxH, flexShrink:0 }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} style={{
          width: px, height: px,
          background: color,
          opacity: 0.35 + 0.65 * ((i + 1) / count),
          borderRadius: 1,
          boxShadow: i === count - 1 ? `0 0 3px ${color}` : "none",
        }} />
      ))}
    </div>
  );
}

// ─── TICKER CARD ──────────────────────────────────────────────────────────────
function TickerCard({ data, onRemove }) {
  const { symbol, price, change, pct, rsi, stoch, macdVal, bars, error, loading } = data;
  const latestM = bars.length ? bars[bars.length - 1].momentum : 0;
  const color = momentumColor(latestM);
  const badge = signalBadge(latestM);

  return (
    <div style={{
      background: "#0c0c0e",
      border: `1px solid ${color}33`,
      borderRadius: 8,
      padding: "10px 12px",
      position: "relative",
      transition: "border-color 0.3s",
      minWidth: 0,
    }}>
      {/* top row */}
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start", marginBottom:4 }}>
        <div>
          <span style={{ color:"#fff", fontWeight:700, fontSize:13, letterSpacing:1 }}>{symbol.replace(".JK","")}</span>
          {symbol.endsWith(".JK") && <span style={{ color:"#333", fontSize:9, marginLeft:4 }}>IDX</span>}
          {!symbol.endsWith(".JK") && <span style={{ color:"#334", fontSize:9, marginLeft:4 }}>US</span>}
        </div>
        <button onClick={() => onRemove(symbol)} style={{ background:"none", border:"none", color:"#333", cursor:"pointer", fontSize:12, lineHeight:1 }}>✕</button>
      </div>

      {loading && <div style={{ color:"#333", fontSize:10, marginBottom:6 }}>loading...</div>}
      {error && <div style={{ color:"#E53935", fontSize:9, marginBottom:6 }}>⚠ {error}</div>}

      {!loading && !error && (
        <>
          {/* price row */}
          <div style={{ display:"flex", alignItems:"baseline", gap:6, marginBottom:6 }}>
            <span style={{ color:"#eee", fontSize:16, fontWeight:700 }}>
              {price < 1000 ? price?.toFixed(2) : price?.toLocaleString("id-ID")}
            </span>
            <span style={{ color: pct >= 0 ? "#43A047" : "#E53935", fontSize:11, fontWeight:600 }}>
              {pct >= 0 ? "+" : ""}{pct?.toFixed(2)}%
            </span>
          </div>

          {/* badge */}
          <div style={{
            display:"inline-flex", alignItems:"center", gap:4,
            background: badge.bg, border:`1px solid ${badge.color}55`,
            padding:"2px 7px", borderRadius:3, marginBottom:6,
          }}>
            <div style={{ width:5, height:5, borderRadius:1, background:badge.color, boxShadow:`0 0 4px ${badge.color}` }} />
            <span style={{ color:badge.color, fontSize:9, fontWeight:700, letterSpacing:1.5 }}>{badge.label}</span>
          </div>

          {/* indicators */}
          <div style={{ display:"flex", gap:8, marginBottom:6, flexWrap:"wrap" }}>
            {[["RSI", rsi?.toFixed(0)], ["STOCH", stoch?.toFixed(0)], ["MACD", macdVal?.toFixed(1)]].map(([k,v]) => (
              <span key={k} style={{ fontSize:9, color:"#444" }}>
                <span style={{ color:"#555" }}>{k}:</span>
                <span style={{ color:"#888", marginLeft:2 }}>{v}</span>
              </span>
            ))}
          </div>

          {/* ── PIXEL MOMENTUM PANEL ── */}
          <div style={{
            background:"#07070a",
            borderRadius:4,
            padding:"4px 3px 3px",
            position:"relative",
            overflow:"hidden",
          }}>
            {/* scanlines */}
            <div style={{
              position:"absolute", inset:0, pointerEvents:"none", zIndex:2,
              background:"repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.2) 2px,rgba(0,0,0,0.2) 3px)"
            }} />
            <div style={{ fontSize:7, color:"#222", letterSpacing:2, marginBottom:2 }}>PIXEL MOMENTUM</div>
            <div style={{ display:"flex", gap:"1px", alignItems:"flex-end", height:52, position:"relative", zIndex:1 }}>
              {bars.map((b, i) => (
                <PixelBar key={i} momentum={b.momentum} vol={b.vol} maxH={52} px={3} gap={1} />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ─── MAIN APP ─────────────────────────────────────────────────────────────────
export default function PixelWatchlist() {
  const [tickers, setTickers] = useState(DEFAULT_TICKERS);
  const [stockData, setStockData] = useState({});
  const [input, setInput] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);

  const loadTicker = useCallback(async (symbol) => {
    const sym = symbol.trim().toUpperCase();
    setStockData(prev => ({ ...prev, [sym]: { symbol:sym, loading:true, bars:[], error:null } }));
    try {
      const { closes, volumes, meta } = await fetchYahoo(sym);
      const clean = closes.map((v, i) => ({ c: v, v: volumes[i] })).filter(x => x.c != null);
      const c = clean.map(x => x.c);
      const v = clean.map(x => x.v);
      const price = meta.regularMarketPrice || c[c.length - 1];
      const prevClose = meta.chartPreviousClose || c[c.length - 2];
      const pct = prevClose ? ((price - prevClose) / prevClose) * 100 : 0;
      const rsi = computeRSI(c);
      const stoch = computeStoch(c);
      const { macd } = computeMACD(c);
      const bars = buildPixelBars(c, v, 40);
      setStockData(prev => ({
        ...prev,
        [sym]: { symbol:sym, price, change: price - prevClose, pct, rsi, stoch, macdVal:macd, bars, loading:false, error:null }
      }));
    } catch (e) {
      setStockData(prev => ({ ...prev, [sym]: { symbol:sym, loading:false, bars:[], error:"Gagal fetch" } }));
    }
  }, []);

  const loadAll = useCallback(async (list) => {
    setRefreshing(true);
    await Promise.all(list.map(s => loadTicker(s)));
    setLastUpdate(new Date().toLocaleTimeString("id-ID"));
    setRefreshing(false);
  }, [loadTicker]);

  useEffect(() => { loadAll(tickers); }, []);

  const addTicker = () => {
    const sym = input.trim().toUpperCase();
    if (!sym || tickers.includes(sym)) return;
    const updated = [...tickers, sym];
    setTickers(updated);
    loadTicker(sym);
    setInput("");
  };

  const removeTicker = (sym) => {
    setTickers(prev => prev.filter(t => t !== sym));
    setStockData(prev => { const n = { ...prev }; delete n[sym]; return n; });
  };

  // sort by momentum descending
  const sorted = tickers
    .map(s => stockData[s] || { symbol:s, loading:true, bars:[], error:null })
    .sort((a, b) => {
      const ma = a.bars.length ? a.bars[a.bars.length-1].momentum : -99;
      const mb = b.bars.length ? b.bars[b.bars.length-1].momentum : -99;
      return mb - ma;
    });

  // summary counts
  const counts = { bull:0, netral:0, bear:0 };
  sorted.forEach(d => {
    if (!d.bars.length) return;
    const m = d.bars[d.bars.length-1].momentum;
    if (m > 0.2) counts.bull++;
    else if (m < -0.2) counts.bear++;
    else counts.netral++;
  });

  return (
    <div style={{
      background:"#080809",
      minHeight:"100vh",
      color:"#fff",
      fontFamily:"'Courier New', Courier, monospace",
      padding:"16px 12px",
    }}>
      {/* ── HEADER ── */}
      <div style={{ maxWidth:900, margin:"0 auto" }}>
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:12 }}>
          <div>
            <div style={{ fontSize:18, fontWeight:700, letterSpacing:3, color:"#e0e0e0" }}>
              DANTEBADAI <span style={{ color:"#43A047" }}>PIXEL</span> WATCHLIST
            </div>
            <div style={{ fontSize:9, color:"#333", letterSpacing:2, marginTop:1 }}>
              T1MO PIXEL ENGINE · YAHOO FINANCE · {lastUpdate ? `UPDATE ${lastUpdate}` : "LOADING..."}
            </div>
          </div>
          <button
            onClick={() => loadAll(tickers)}
            disabled={refreshing}
            style={{
              background: refreshing ? "#111" : "#0a1f0a",
              border:`1px solid ${refreshing ? "#222" : "#43A047"}`,
              color: refreshing ? "#444" : "#43A047",
              fontSize:10, padding:"6px 14px", borderRadius:4,
              cursor: refreshing ? "not-allowed" : "pointer",
              letterSpacing:1,
            }}
          >
            {refreshing ? "⟳ LOADING..." : "⟳ REFRESH"}
          </button>
        </div>

        {/* ── SUMMARY BAR ── */}
        <div style={{ display:"flex", gap:16, marginBottom:14, padding:"8px 12px", background:"#0c0c0e", borderRadius:6, border:"1px solid #1a1a1e" }}>
          {[
            [counts.bull, "#43A047", "BELI/BULL"],
            [counts.netral, "#F9A825", "NETRAL"],
            [counts.bear, "#E53935", "JUAL/BEAR"],
          ].map(([n, c, l]) => (
            <div key={l} style={{ display:"flex", alignItems:"center", gap:6 }}>
              <div style={{ width:8, height:8, borderRadius:2, background:c, boxShadow:`0 0 4px ${c}` }} />
              <span style={{ color:c, fontSize:13, fontWeight:700 }}>{n}</span>
              <span style={{ color:"#333", fontSize:9 }}>{l}</span>
            </div>
          ))}
          <div style={{ marginLeft:"auto", fontSize:9, color:"#222", alignSelf:"center" }}>
            {tickers.length} SAHAM
          </div>
        </div>

        {/* ── ADD TICKER ── */}
        <div style={{ display:"flex", gap:6, marginBottom:16 }}>
          <input
            value={input}
            onChange={e => setInput(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && addTicker()}
            placeholder="Tambah ticker... (mis: BBCA.JK)"
            style={{
              flex:1, background:"#0c0c0e", border:"1px solid #222",
              color:"#aaa", fontSize:11, padding:"6px 10px", borderRadius:4,
              outline:"none", fontFamily:"inherit", letterSpacing:1,
            }}
          />
          <button
            onClick={addTicker}
            style={{
              background:"#0a1a0a", border:"1px solid #43A04766",
              color:"#43A047", fontSize:11, padding:"6px 14px",
              borderRadius:4, cursor:"pointer", letterSpacing:1,
            }}
          >
            + ADD
          </button>
        </div>

        {/* ── LEGEND ── */}
        <div style={{ display:"flex", gap:12, marginBottom:14, flexWrap:"wrap" }}>
          {[
            ["#7B0000","JUAL KUAT"],["#E53935","JUAL"],
            ["#F9A825","NETRAL"],["#43A047","BELI"],["#1B5E20","BELI KUAT"]
          ].map(([color, label]) => (
            <div key={label} style={{ display:"flex", alignItems:"center", gap:4 }}>
              <div style={{ display:"flex", flexDirection:"column", gap:1 }}>
                {[1, 0.65, 0.35].map((op, i) => (
                  <div key={i} style={{ width:4, height:4, background:color, opacity:op, borderRadius:1 }} />
                ))}
              </div>
              <span style={{ color:"#333", fontSize:9, letterSpacing:1 }}>{label}</span>
            </div>
          ))}
        </div>

        {/* ── GRID ── */}
        <div style={{
          display:"grid",
          gridTemplateColumns:"repeat(auto-fill, minmax(200px, 1fr))",
          gap:10,
        }}>
          {sorted.map(d => (
            <TickerCard key={d.symbol} data={d} onRemove={removeTicker} />
          ))}
        </div>

        <div style={{ textAlign:"center", marginTop:20, color:"#1a1a1e", fontSize:9, letterSpacing:2 }}>
          DANTEBADAI BOT · PIXEL ENGINE v2.0 · DATA: YAHOO FINANCE
        </div>
      </div>
    </div>
  );
}
