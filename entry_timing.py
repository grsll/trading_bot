"""
╔══════════════════════════════════════════════════════════════════════╗
║          XAUUSD ENTRY TIMING PREDICTOR v1.0                        ║
║   Smart Money Entry Window Estimation Engine                       ║
╚══════════════════════════════════════════════════════════════════════╝

Predicts WHEN a valid entry is likely to form based on:
- Market structure gaps
- Session timing (London/NY open volatility expansion)
- Price distance to key levels (OB, FVG, S/R, Fibonacci)
- Volatility cycle (ATR rhythm)
- Confluence score trajectory
"""

import datetime
import math
import logging

log = logging.getLogger(__name__)

# ── Session windows (UTC hours) ──────────────────────────────────────
SESSION_WINDOWS = {
    "Asian":       (0,  7),
    "London":      (7,  12),
    "London-NY":   (12, 16),
    "New York":    (16, 21),
    "NY Close":    (21, 23),
}

# High-probability entry windows in UTC (Gold-specific)
PRIME_ENTRY_WINDOWS = [
    (7,  9,  "London Open — institutional order flow begins"),
    (13, 15, "NY Open — maximum liquidity, trend confirmation"),
    (16, 18, "NY afternoon — second wave momentum"),
]


def get_current_utc_hour() -> int:
    return datetime.datetime.now(datetime.timezone.utc).hour


def get_current_session_label() -> str:
    h = get_current_utc_hour()
    for name, (s, e) in SESSION_WINDOWS.items():
        if s <= h < e:
            return name
    return "Off-Hours"


def minutes_until_utc_hour(target_hour: int) -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return int((target - now).total_seconds() / 60)


def format_eta(minutes: int, target_utc_hour: int) -> str:
    """Convert minutes + UTC hour into human-friendly WIB time string."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    target_dt = now_utc + datetime.timedelta(minutes=minutes)
    # WIB = UTC+7
    wib_dt = target_dt + datetime.timedelta(hours=7)
    return wib_dt.strftime("%H:%M WIB")


def price_distance_pct(price: float, level: float) -> float:
    """Return % distance from current price to a level."""
    if not level or level <= 0 or price <= 0:
        return 999.0
    return abs(price - level) / price * 100


def find_nearest_key_level(snap: dict) -> tuple[float | None, str, float]:
    """
    Find the nearest key level (OB, FVG, S/R, Fib) to current price.
    Returns (level_price, level_name, distance_pct)
    """
    price = snap.get("current_price", 0)
    if not price:
        return None, "unknown", 999.0

    candidates = []

    # Order Blocks
    ob_1h = snap.get("ob_1h", {})
    if ob_1h.get("bull_ob"):
        ob = ob_1h["bull_ob"]
        mid = ob["mid"]
        candidates.append((mid, f"Bullish OB [{ob['low']}–{ob['high']}]", price_distance_pct(price, mid)))
    if ob_1h.get("bear_ob"):
        ob = ob_1h["bear_ob"]
        mid = ob["mid"]
        candidates.append((mid, f"Bearish OB [{ob['low']}–{ob['high']}]", price_distance_pct(price, mid)))

    # FVGs
    for fvg in snap.get("fvg_1h", [])[:2]:
        mid = fvg.get("mid", 0)
        if mid:
            candidates.append((mid, f"{fvg['type']} [{fvg['bot']}–{fvg['top']}]", price_distance_pct(price, mid)))

    # S/R
    sr = snap.get("sr_1h", {})
    for r in (sr.get("resistance") or [])[:2]:
        candidates.append((r, f"Resistance ${r}", price_distance_pct(price, r)))
    for s in (sr.get("support") or [])[:2]:
        candidates.append((s, f"Support ${s}", price_distance_pct(price, s)))

    # Fibonacci
    fib = snap.get("fib_1h", {})
    for key, label in [("fib_618", "61.8%"), ("fib_500", "50.0%"), ("fib_382", "38.2%")]:
        val = fib.get(key)
        if val:
            candidates.append((val, f"Fib {label} @ ${val}", price_distance_pct(price, val)))

    if not candidates:
        return None, "no key level found", 999.0

    candidates.sort(key=lambda x: x[2])
    return candidates[0]


def estimate_time_to_level(snap: dict, atr_per_hour: float = None) -> dict:
    """
    Estimate how many hours/minutes until price might reach nearest key level,
    based on ATR movement rate.
    """
    price = snap.get("current_price", 0)
    level, level_name, dist_pct = find_nearest_key_level(snap)

    if not level or not price:
        return {"level": None, "level_name": "N/A", "dist_usd": 0, "est_hours": None}

    dist_usd = abs(price - level)

    # Estimate ATR per hour from 1H ATR
    vol = snap.get("vol_1h", {})
    atr = vol.get("atr", 0) or (snap.get("tv_1H") or {}).get("atr", 0)

    if not atr or atr <= 0:
        atr = 5.0  # fallback Gold ATR ~$5/hr

    # ATR represents average range per candle (1H). Rate = atr per hour.
    est_hours = round(dist_usd / max(atr * 0.6, 0.5), 1)  # 60% of ATR as directional move pace

    return {
        "level":      round(level, 2),
        "level_name": level_name,
        "dist_usd":   round(dist_usd, 2),
        "dist_pct":   round(dist_pct, 3),
        "atr_1h":     round(atr, 2),
        "est_hours":  est_hours,
    }


def find_next_prime_window(current_utc_hour: int) -> dict:
    """
    Find the next high-probability entry window.
    Returns window info + ETA in minutes.
    """
    best = None
    for (start, end, reason) in PRIME_ENTRY_WINDOWS:
        if current_utc_hour < start:
            # Window is later today
            eta_min = minutes_until_utc_hour(start)
            best = {"start_utc": start, "end_utc": end, "reason": reason, "eta_min": eta_min}
            break
        elif start <= current_utc_hour < end:
            # We're IN a prime window
            best = {"start_utc": start, "end_utc": end, "reason": reason, "eta_min": 0}
            break

    if not best:
        # Next window is tomorrow (first window)
        start, end, reason = PRIME_ENTRY_WINDOWS[0]
        eta_min = minutes_until_utc_hour(start)
        best = {"start_utc": start, "end_utc": end, "reason": reason, "eta_min": eta_min}

    # Add WIB times
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    wib_start = (now_utc.replace(hour=best["start_utc"], minute=0, second=0) + datetime.timedelta(hours=7)).strftime("%H:%M")
    wib_end   = (now_utc.replace(hour=best["end_utc"],   minute=0, second=0) + datetime.timedelta(hours=7)).strftime("%H:%M")
    best["wib_start"] = wib_start
    best["wib_end"]   = wib_end
    best["in_window"] = best["eta_min"] == 0
    return best


def score_missing_conditions(snap: dict) -> list[str]:
    """
    Identify what's missing for a valid entry to form.
    Returns list of conditions that need to be satisfied.
    """
    missing = []
    conf = snap.get("confluence", {})
    score = conf.get("score", 0)
    bias  = conf.get("bias", "NEUTRAL")

    str1h  = snap.get("structure_1h",  {})
    str15m = snap.get("structure_15m", {})
    tv1h   = snap.get("tv_1H") or {}
    tv15m  = snap.get("tv_15M") or {}
    regime = snap.get("regime", {})
    price  = snap.get("current_price", 0)
    sr     = snap.get("sr_1h", {})
    ob     = snap.get("ob_1h", {})

    # 1. Confluence score
    if score < 5:
        missing.append(f"Confluence score too low ({score}/10) — need ≥5/10")

    # 2. Trend alignment
    trend_1h  = str1h.get("trend", "SIDEWAYS")
    trend_15m = str15m.get("trend", "SIDEWAYS")
    if trend_1h == "SIDEWAYS":
        missing.append("1H structure unclear — wait for BOS (Break of Structure)")
    if trend_1h != trend_15m and trend_15m != "SIDEWAYS":
        missing.append(f"HTF/LTF trend conflict: 1H={trend_1h} vs 15M={trend_15m} — wait for 15M alignment")

    # 3. RSI
    rsi = tv1h.get("rsi", 50)
    if 45 <= rsi <= 55:
        missing.append(f"RSI neutral zone ({rsi:.1f}) — wait for directional push above 55 or below 45")
    if rsi >= 72:
        missing.append(f"RSI overbought ({rsi:.1f}) — avoid new BUY, wait for RSI reset to 50–60")
    if rsi <= 28:
        missing.append(f"RSI oversold ({rsi:.1f}) — avoid new SELL, wait for RSI reset to 40–50")

    # 4. MACD
    macd_hist = tv1h.get("macd_hist", 0)
    macd      = tv1h.get("macd", 0)
    sig       = tv1h.get("macd_signal", 0)
    if abs(macd_hist) < 0.5:
        missing.append("MACD histogram flat — wait for MACD cross or increasing histogram")

    # 5. Volatility regime
    vol_state = regime.get("volatility", "NORMAL")
    if vol_state == "LOW":
        missing.append("Volatility too low (squeeze phase) — wait for BB expansion / breakout candle")
    elif vol_state == "HIGH" and regime.get("regime") == "RANGING":
        missing.append("High volatility chop — dangerous to enter, wait for trending regime")

    # 6. Price near key level
    near_res = sr.get("nearest_res")
    near_sup = sr.get("nearest_sup")
    at_level = False
    if near_res and abs(price - near_res) / price < 0.005:
        at_level = True
    if near_sup and abs(price - near_sup) / price < 0.005:
        at_level = True
    if ob.get("bull_ob") and ob["bull_ob"]["low"] <= price <= ob["bull_ob"]["high"]:
        at_level = True
    if ob.get("bear_ob") and ob["bear_ob"]["low"] <= price <= ob["bear_ob"]["high"]:
        at_level = True

    if not at_level:
        level, level_name, dist_pct = find_nearest_key_level(snap)
        if level:
            missing.append(f"Price not at key level — nearest: {level_name} ({dist_pct:.2f}% away)")

    # 7. Neutral bias
    if bias == "NEUTRAL":
        missing.append("Market bias neutral — wait for directional bias ≥5/10 score")

    return missing


def generate_entry_timing_report(snap: dict) -> str:
    """
    Full entry timing analysis report for Telegram.
    Called when /signal returns NO TRADE.
    """
    now_utc    = datetime.datetime.now(datetime.timezone.utc)
    wib_now    = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M WIB")
    utc_hour   = now_utc.hour
    session    = get_current_session_label()
    price      = snap.get("current_price", 0)
    conf       = snap.get("confluence", {})
    score      = conf.get("score", 0)
    bias       = conf.get("bias", "NEUTRAL")

    # Find next prime window
    window = find_next_prime_window(utc_hour)

    # Estimate time to nearest key level
    level_info = estimate_time_to_level(snap)

    # What's missing
    missing = score_missing_conditions(snap)

    # Session-based wait guidance
    if window["in_window"]:
        session_advice = f"✅ You're IN a prime window ({session}) — setup may form soon"
        session_eta    = f"Active until {window['wib_end']} WIB"
    else:
        eta_h  = window["eta_min"] // 60
        eta_m  = window["eta_min"] %  60
        eta_str = f"{eta_h}h {eta_m}m" if eta_h > 0 else f"{eta_m} min"
        session_advice = f"⏳ Next prime window in {eta_str}"
        session_eta    = f"{window['wib_start']}–{window['wib_end']} WIB ({window['reason']})"

    # Level ETA text
    if level_info["est_hours"] is not None:
        est_h = level_info["est_hours"]
        if est_h < 0.5:
            level_eta_text = "Price may reach key level <b>within 30 min</b>"
        elif est_h < 1.5:
            level_eta_text = f"Price may reach key level in ~<b>{int(est_h*60)} minutes</b>"
        else:
            eta_wib_dt = now_utc + datetime.timedelta(hours=est_h + 7)
            eta_wib    = eta_wib_dt.strftime("%H:%M WIB")
            level_eta_text = f"Price may reach key level around <b>~{eta_wib}</b>"
    else:
        level_eta_text = "Level timing estimate unavailable"

    # Score bar
    score_bar = "▓" * int(score) + "░" * (10 - int(score))
    bias_icon = "📈" if bias == "BULLISH" else "📉" if bias == "BEARISH" else "↔️"

    # Missing conditions (max 4, concise)
    if missing:
        missing_lines = "\n".join(f"  ⚠️ {m}" for m in missing[:4])
    else:
        missing_lines = "  ✅ Conditions nearly met — wait for trigger candle"

    # Suggested action
    actions = []
    if not window["in_window"]:
        actions.append(f"🕐 Set alert for {window['wib_start']} WIB (session open)")
    if level_info["level"]:
        direction = "drops to" if snap.get("current_price", 0) > level_info["level"] else "rises to"
        actions.append(f"📍 Watch if price {direction} ${level_info['level']} ({level_info['level_name']})")
    actions.append("🔁 Re-run /signal when price touches key level")
    actions.append("📊 Use /status to monitor confluence score live")

    action_lines = "\n".join(f"  {a}" for a in actions)

    msg = (
        f"⏰ <b>ENTRY TIMING FORECAST</b> | XAUUSD\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"<b>📍 Current State</b>\n"
        f"├ 🕐 Time Now  : <b>{wib_now}</b> ({session})\n"
        f"├ 💲 Price     : <b>${price}</b>\n"
        f"├ {bias_icon} Bias      : <b>{bias}</b>\n"
        f"└ 📊 Confluence: <b>{score}/10</b> [{score_bar}]\n"
        f"\n"
        f"<b>🏦 Session Timing</b>\n"
        f"├ {session_advice}\n"
        f"└ 🗓 Window    : {session_eta}\n"
        f"\n"
        f"<b>🎯 Nearest Key Level</b>\n"
        f"├ 📌 Level     : <b>{level_info.get('level_name', 'N/A')}</b>\n"
        f"├ 💰 At Price  : <b>${level_info.get('level', 'N/A')}</b>\n"
        f"├ 📏 Distance  : <b>${level_info.get('dist_usd', 0):.1f}</b> ({level_info.get('dist_pct', 0):.2f}%)\n"
        f"└ ⏱ {level_eta_text}\n"
        f"\n"
        f"<b>❌ What's Blocking Entry</b>\n"
        f"{missing_lines}\n"
        f"\n"
        f"<b>✅ Suggested Actions</b>\n"
        f"{action_lines}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <i>Auto-monitoring every 15m · /signal to re-check</i>"
    )
    return msg
