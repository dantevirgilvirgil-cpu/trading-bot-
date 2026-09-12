# ══════════════════════════════════════════════════════════
# PATTERN DETECTOR MODULE — Falling Wedge, Cup & Handle,
# Head & Shoulders (+ Inverse), Double Bottom
# Dibuat untuk plug-in ke IDX QUANT Bot (pakai df dari get_signal())
# Tidak butuh scipy — murni numpy/pandas biar aman di Railway
# ══════════════════════════════════════════════════════════
import numpy as np

# ── util: cari swing high / swing low pakai window lokal ──
def find_swings(high, low, window=5):
    """
    Swing high = titik tertinggi lokal dalam radius `window` candle.
    Swing low  = titik terendah lokal dalam radius `window` candle.
    Return: list index (posisi array) swing high & swing low.
    """
    n = len(high)
    sh_idx, sl_idx = [], []
    for i in range(window, n - window):
        seg_h = high[i - window:i + window + 1]
        seg_l = low[i - window:i + window + 1]
        if high[i] == seg_h.max() and np.argmax(seg_h) == window:
            sh_idx.append(i)
        if low[i] == seg_l.min() and np.argmin(seg_l) == window:
            sl_idx.append(i)
    return sh_idx, sl_idx


def _slope(idx_list, val_list):
    """Regresi linear sederhana -> (slope, intercept)"""
    x = np.array(idx_list, dtype=float)
    y = np.array(val_list, dtype=float)
    if len(x) < 2:
        return 0.0, float(y[-1]) if len(y) else 0.0
    m, b = np.polyfit(x, y, 1)
    return float(m), float(b)


def compute_atr(highs, lows, closes, period=14):
    """
    Average True Range — dipakai sebagai patokan 'volatilitas wajar'.
    Fungsinya: membedakan swing yang BENERAN pola vs cuma noise harian biasa.
    Tanpa filter ini, random noise gampang banget kebaca sebagai 'pola'.
    """
    n = len(highs)
    if n < 2:
        return float(np.mean(highs - lows)) if n else 0.0
    trs = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    trs = np.array(trs[-period:]) if len(trs) >= period else np.array(trs)
    return float(np.mean(trs)) if len(trs) else float(np.mean(highs - lows))


# ══════════════════════════════════════════
# 1. FALLING WEDGE (Bullish reversal/continuation)
# ══════════════════════════════════════════
def detect_falling_wedge(opens, closes, highs, lows, vols, lookback=60, window=4):
    """
    Falling wedge: highs & lows sama2 turun, tapi garis atas turun LEBIH LANDAI
    dari garis bawah -> makin lama makin menyempit (konvergen).
    Breakout = close terakhir tembus ke atas garis resistance (upper trendline).
    """
    n = len(closes)
    if n < lookback + window * 2:
        lookback = n - window * 2 - 1
    if lookback < 20:
        return None

    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]
    v = vols[-lookback:]

    atr = compute_atr(h, l, c)
    if atr <= 0:
        return None

    sh_idx, sl_idx = find_swings(h, l, window=window)
    if len(sh_idx) < 2 or len(sl_idx) < 2:
        return None

    sh_vals = [h[i] for i in sh_idx]
    sl_vals = [l[i] for i in sl_idx]

    # Syarat pola turun: swing high & swing low terakhir < swing high & low pertama
    if not (sh_vals[-1] < sh_vals[0] and sl_vals[-1] < sl_vals[0]):
        return None

    # Pola harus punya "tinggi" yang berarti (bukan noise harian) -> minimal 3x ATR
    if (sh_vals[0] - sh_vals[-1]) < 3 * atr:
        return None

    m_hi, b_hi = _slope(sh_idx, sh_vals)   # garis resistance (upper)
    m_lo, b_lo = _slope(sl_idx, sl_vals)   # garis support (lower)

    # Wedge turun: kedua slope negatif, garis atas lebih landai (konvergen)
    if not (m_hi < 0 and m_lo < 0):
        return None
    if not (m_hi > m_lo * 1.05):  # upper line falls slower -> converging
        return None

    last_i = lookback - 1
    upper_now = m_hi * last_i + b_hi
    lower_now = m_lo * last_i + b_lo
    if upper_now <= lower_now:  # garis sudah ketemu / invalid
        return None

    width_ratio = (upper_now - lower_now) / upper_now if upper_now else 1
    if width_ratio > 0.35:  # terlalu lebar, belum cukup konvergen
        return None

    last_close = c[-1]
    avg_vol = float(np.mean(v[-20:])) if len(v) >= 20 else float(np.mean(v))
    last_vol = v[-1]
    vr = last_vol / avg_vol if avg_vol > 0 else 1

    # breakout harus meyakinkan: minimal 0.4x ATR di atas garis resistance
    breakout = last_close > upper_now + 0.4 * atr
    near_breakout = (not breakout) and (upper_now * 0.985 <= last_close <= upper_now + 0.4 * atr)

    if not (breakout or near_breakout):
        return None

    status = "BREAKOUT ✅" if breakout else "MENDEKATI RESISTANCE ⏳"
    return {
        "pattern": "Falling Wedge",
        "emoji": "📉➡️📈",
        "bias": "BULLISH",
        "status": status,
        "confirmed": breakout,
        "resistance": float(upper_now),
        "support": float(lower_now),
        "vr": float(vr),
        "score": (3 if breakout else 1) + (1 if vr >= 1.5 else 0),
    }


# ══════════════════════════════════════════
# 2. CUP AND HANDLE (Bullish continuation)
# ══════════════════════════════════════════
def detect_cup_and_handle(opens, closes, highs, lows, vols, lookback=100):
    """
    Cup: rim kiri (high) -> turun ke dasar (low) -> naik ke rim kanan (~= rim kiri).
    Handle: konsolidasi/pullback dangkal setelah rim kanan.
    Breakout: close tembus level rim (resistance) dengan volume naik.
    """
    n = len(closes)
    if n < lookback:
        lookback = n
    if lookback < 30:
        return None

    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]
    v = vols[-lookback:]
    m = len(c)

    atr = compute_atr(h, l, c)
    if atr <= 0:
        return None

    left_zone_end = max(int(m * 0.25), 3)
    left_i = int(np.argmax(h[:left_zone_end]))
    left_rim = h[left_i]

    # cari dasar cup setelah rim kiri, sebelum 85% window
    search_end = int(m * 0.85)
    if search_end <= left_i + 5:
        return None
    bottom_rel = int(np.argmin(l[left_i:search_end]))
    bottom_i = left_i + bottom_rel
    cup_bottom = l[bottom_i]

    depth = (left_rim - cup_bottom) / left_rim if left_rim else 0
    if not (0.10 <= depth <= 0.55):
        return None  # kedalaman gak masuk kriteria cup wajar
    if (left_rim - cup_bottom) < 4 * atr:
        return None  # cup terlalu dangkal dibanding volatilitas normal -> kemungkinan noise

    # cari rim kanan: high tertinggi setelah dasar, sebelum zona handle (90%)
    right_search_end = int(m * 0.92)
    if right_search_end <= bottom_i + 3:
        return None
    right_rel = int(np.argmax(h[bottom_i:right_search_end]))
    right_i = bottom_i + right_rel
    right_rim = h[right_i]

    # rim kanan harus mendekati rim kiri (bentuk U, bukan tangga naik/turun ekstrem)
    if not (0.85 * left_rim <= right_rim <= 1.15 * left_rim):
        return None

    # cek bentuk "U" bukan "V": durasi turun & naik harus sebanding
    down_len = bottom_i - left_i
    up_len = right_i - bottom_i
    if down_len <= 0 or up_len <= 0:
        return None
    ratio = max(down_len, up_len) / min(down_len, up_len)
    if ratio > 3.0:
        return None  # terlalu njomplang -> bukan rounding bottom

    rim_level = max(left_rim, right_rim)

    # ══ HANDLE: sisa candle setelah rim kanan ══
    handle = c[right_i:]
    handle_h = h[right_i:]
    handle_l = l[right_i:]
    if len(handle) < 3:
        # belum ada handle -> masih fase cup selesai, tunggu
        return None

    handle_low = handle_l.min()
    handle_high = handle_h.max()
    handle_depth = (rim_level - handle_low) / rim_level if rim_level else 1
    cup_depth_frac = depth
    if handle_depth > cup_depth_frac * 0.6 or handle_depth > 0.25:
        return None  # handle terlalu dalam, gak valid

    last_close = c[-1]
    avg_vol = float(np.mean(v[-20:])) if len(v) >= 20 else float(np.mean(v))
    last_vol = v[-1]
    vr = last_vol / avg_vol if avg_vol > 0 else 1

    breakout = last_close > rim_level + 0.3 * atr
    near_breakout = (not breakout) and (last_close >= rim_level * 0.97)

    if not (breakout or near_breakout):
        return None

    status = "BREAKOUT ✅" if breakout else "MENDEKATI RIM/HANDLE ⏳"
    return {
        "pattern": "Cup and Handle",
        "emoji": "☕",
        "bias": "BULLISH",
        "status": status,
        "confirmed": breakout,
        "resistance": float(rim_level),
        "cup_depth_pct": float(depth * 100),
        "vr": float(vr),
        "score": (3 if breakout else 1) + (1 if vr >= 1.5 else 0),
    }


# ══════════════════════════════════════════
# 3. HEAD & SHOULDERS  (+ INVERSE)
# ══════════════════════════════════════════
def detect_head_shoulders(opens, closes, highs, lows, vols, lookback=80, window=4):
    """
    H&S normal (bearish top): shoulder-head-shoulder di swing HIGH, neckline di swing LOW.
    Inverse H&S (bullish bottom): shoulder-head-shoulder di swing LOW, neckline di swing HIGH.
    Return salah satu (yang paling baru & valid), atau None.
    """
    n = len(closes)
    if n < lookback:
        lookback = n
    if lookback < 30:
        return None

    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]
    v = vols[-lookback:]

    atr = compute_atr(h, l, c)
    if atr <= 0:
        return None

    sh_idx, sl_idx = find_swings(h, l, window=window)

    def check_hs(peak_idx, peak_vals, neck_idx, neck_vals, bullish):
        # butuh minimal 3 puncak/lembah terakhir buat shoulder-head-shoulder
        if len(peak_idx) < 3:
            return None
        p_i = peak_idx[-3:]
        p_v = peak_vals[-3:]
        s1_i, h_i, s2_i = p_i
        s1_v, hd_v, s2_v = p_v

        if bullish:
            # inverse: head harus PALING RENDAH, dan bedanya harus berarti (bukan noise)
            if not (hd_v < s1_v - 2*atr and hd_v < s2_v - 2*atr):
                return None
        else:
            # normal: head harus PALING TINGGI
            if not (hd_v > s1_v + 2*atr and hd_v > s2_v + 2*atr):
                return None

        # dua shoulder relatif setara (selisih < 12% ATAU < 1.5x ATR, mana yg lebih ketat)
        avg_sh = (s1_v + s2_v) / 2
        if avg_sh == 0:
            return None
        shoulder_diff = abs(s1_v - s2_v)
        if shoulder_diff / avg_sh > 0.12 or shoulder_diff > 2.0 * atr:
            return None

        # neckline: 2 titik lembah/puncak di antara shoulder & head
        neck_between = [(i, val) for i, val in zip(neck_idx, neck_vals) if s1_i < i < s2_i]
        if len(neck_between) < 2:
            return None
        n1_i, n1_v = neck_between[0]
        n2_i, n2_v = neck_between[-1]
        neck_avg = (n1_v + n2_v) / 2
        if neck_avg == 0:
            return None
        # neckline gak boleh miring ekstrem
        if abs(n1_v - n2_v) / neck_avg > 0.15:
            return None

        last_close = c[-1]
        avg_vol = float(np.mean(v[-20:])) if len(v) >= 20 else float(np.mean(v))
        last_vol = v[-1]
        vr = last_vol / avg_vol if avg_vol > 0 else 1

        if bullish:
            breakout = last_close > neck_avg + 0.3 * atr
            near = (not breakout) and (neck_avg * 0.97 <= last_close <= neck_avg + 0.3 * atr)
        else:
            breakout = last_close < neck_avg - 0.3 * atr
            near = (not breakout) and (neck_avg - 0.3 * atr <= last_close <= neck_avg * 1.03)

        if not (breakout or near):
            return None

        status = "BREAKOUT ✅" if breakout else "MENDEKATI NECKLINE ⏳"
        return {
            "pattern": "Inverse Head & Shoulders" if bullish else "Head & Shoulders",
            "emoji": "🙌📈" if bullish else "🙌📉",
            "bias": "BULLISH" if bullish else "BEARISH",
            "status": status,
            "confirmed": breakout,
            "neckline": float(neck_avg),
            "vr": float(vr),
            "score": (3 if breakout else 1) + (1 if vr >= 1.5 else 0),
        }

    sh_vals = [h[i] for i in sh_idx]
    sl_vals = [l[i] for i in sl_idx]

    inverse = check_hs(sl_idx, sl_vals, sh_idx, sh_vals, bullish=True)
    if inverse:
        return inverse
    normal = check_hs(sh_idx, sh_vals, sl_idx, sl_vals, bullish=False)
    if normal:
        return normal
    return None


# ══════════════════════════════════════════
# 4. DOUBLE BOTTOM (Bullish reversal, pola "W")
# ══════════════════════════════════════════
def detect_double_bottom(opens, closes, highs, lows, vols, lookback=80, window=4):
    n = len(closes)
    if n < lookback:
        lookback = n
    if lookback < 25:
        return None

    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]
    v = vols[-lookback:]

    atr = compute_atr(h, l, c)
    if atr <= 0:
        return None

    sh_idx, sl_idx = find_swings(h, l, window=window)
    if len(sl_idx) < 2:
        return None

    # ambil 2 swing low terakhir sebagai bottom1 & bottom2
    b1_i, b2_i = sl_idx[-2], sl_idx[-1]
    b1_v, b2_v = l[b1_i], l[b2_i]
    if b2_i <= b1_i:
        return None
    if (b2_i - b1_i) < 8:
        return None  # kedua bottom kejauhan/kedekatan wajar butuh jarak minimal

    avg_b = (b1_v + b2_v) / 2
    if avg_b == 0:
        return None
    bottom_diff = abs(b1_v - b2_v)
    if bottom_diff / avg_b > 0.05 or bottom_diff > 1.5 * atr:
        return None  # dua bottom harus mirip level (toleransi 5% ATAU 1.5x ATR, mana lebih ketat)

    # cari puncak (neckline) di antara dua bottom
    mid_peaks = [(i, h[i]) for i in sh_idx if b1_i < i < b2_i]
    if not mid_peaks:
        return None
    peak_i, peak_v = max(mid_peaks, key=lambda x: x[1])

    rise = peak_v - avg_b
    if rise < 4 * atr or rise / avg_b < 0.05:
        return None  # peak harus cukup tinggi dari bottom (min 5% & min 4x ATR)

    last_close = c[-1]
    avg_vol = float(np.mean(v[-20:])) if len(v) >= 20 else float(np.mean(v))
    last_vol = v[-1]
    vr = last_vol / avg_vol if avg_vol > 0 else 1

    breakout = last_close > peak_v + 0.3 * atr
    near_breakout = (not breakout) and (peak_v * 0.97 <= last_close <= peak_v + 0.3 * atr)

    if not (breakout or near_breakout):
        return None

    status = "BREAKOUT ✅" if breakout else "MENDEKATI NECKLINE ⏳"
    return {
        "pattern": "Double Bottom",
        "emoji": "🔽🔽📈",
        "bias": "BULLISH",
        "status": status,
        "confirmed": breakout,
        "neckline": float(peak_v),
        "vr": float(vr),
        "score": (3 if breakout else 1) + (1 if vr >= 1.5 else 0),
    }


# ══════════════════════════════════════════
# MASTER SCAN — panggil 1 fungsi ini per saham
# ══════════════════════════════════════════
def detect_all_patterns(df):
    """
    df: dataframe dari get_signal(code, tf)["df"] (kolom Open/High/Low/Close/Volume)
    Return: list of pattern dict (bisa lebih dari 1 pola terdeteksi bersamaan)
    """
    if df is None or len(df) < 30:
        return []
    opens = df["Open"].squeeze().values.astype(float)
    closes = df["Close"].squeeze().values.astype(float)
    highs = df["High"].squeeze().values.astype(float)
    lows = df["Low"].squeeze().values.astype(float)
    vols = df["Volume"].squeeze().values.astype(float)

    found = []
    try:
        r = detect_falling_wedge(opens, closes, highs, lows, vols)
        if r: found.append(r)
    except Exception:
        pass
    try:
        r = detect_cup_and_handle(opens, closes, highs, lows, vols)
        if r: found.append(r)
    except Exception:
        pass
    try:
        r = detect_head_shoulders(opens, closes, highs, lows, vols)
        if r: found.append(r)
    except Exception:
        pass
    try:
        r = detect_double_bottom(opens, closes, highs, lows, vols)
        if r: found.append(r)
    except Exception:
        pass
    return found
