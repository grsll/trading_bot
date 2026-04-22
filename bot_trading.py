"""
╔══════════════════════════════════════════════════════════════════════╗
║          XAUUSD SMART MONEY CONFLUENCE BOT v4.0                    ║
║   SMC | Fibonacci | S/R | Multi-Indicator | Auto + On-Demand       ║
╚══════════════════════════════════════════════════════════════════════╝

INSTALL:
  pip install tradingview-ta yfinance pandas numpy requests schedule \
              google-generativeai mplfinance matplotlib

RUN:
  python xauusd_bot_v4.py
"""

# ═══════════════════════════════════════════════════════════════
#  IMPORTS
# ═══════════════════════════════════════════════════════════════

import os
import re
import json
import time
import logging
import datetime
import threading
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import requests
import schedule
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import mplfinance as mpf
import google.generativeai as genai
from tradingview_ta import TA_Handler, Interval

warnings.filterwarnings("ignore")

# XAUUSD v5.0 Modular Imports
from risk_engine import RiskEngine
from analytics import PerformanceAnalytics
from market_regime import MarketRegimeDetector
from sentiment_engine import SentimentEngine
from entry_timing import generate_entry_timing_report

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION  ← Edit hanya bagian ini
# ═══════════════════════════════════════════════════════════════

TELEGRAM_TOKEN  = "8328646674:AAFNJ09lbYXBxQmgv15VxJjJ8d0HLHv3dCI"
CHAT_ID         = "6017032889"
GEMINI_API_KEY  = "AIzaSyDPDwNZDQCnQDNzSG3Gc-YTqM6OvLxA2mU"

# TradingView sources
TV_SYMBOL       = "XAUUSD"
TV_SCREENER     = "forex"
TV_EXCHANGE     = "OANDA"
TV_SYMBOL_FB    = "GOLD"
TV_SCREENER_FB  = "cfd"
TV_EXCHANGE_FB  = "TVC"

# Yahoo Finance (OHLCV candles)
YF_TICKER       = "GC=F"
PAIR_LABEL      = "XAUUSD"

# Signal thresholds
MIN_CONFIDENCE      = 70    # minimum AI confidence to send signal
MIN_SCORE           = 5     # minimum confluence score (out of 10)
SCHEDULE_MINS       = 15    # auto-cycle interval
SIGNAL_COOLDOWN     = 7200  # 2 hours cooldown between same signals

# Files
LOG_FILE        = "xauusd_bot_v4.log"
SIGNAL_LOG      = "signals_v4.json"
ACTIVE_SIGNAL   = "active_signal_v4.json"
CHART_FILE      = "latest_chart.png"

# Active trading sessions (UTC)
SESSIONS = {
    "London":   (7,  16),
    "New York": (13, 22),
}

# Fibonacci levels for retracement
FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]

# ═══════════════════════════════════════════════════════════════
#  LOGGING SETUP
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  GEMINI AI SETUP
# ═══════════════════════════════════════════════════════════════

genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel("gemini-2.0-flash")

# v5.0 Engine Initialization
risk_engine = RiskEngine()
analytics = PerformanceAnalytics()
regime_detector = MarketRegimeDetector()
sentiment_engine = SentimentEngine(ai_model)

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM UTILITIES
# ═══════════════════════════════════════════════════════════════

def tg_send_text(chat_id: str, text: str) -> bool:
    """Send plain text message to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False

def tg_escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def tg_send_photo(chat_id: str, photo_path: str, caption: str = "") -> bool:
    """Send photo with caption to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo:
            r = requests.post(url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": photo},
                timeout=15)
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram photo error: {e}")
        return False

def tg_send(chat_id: str, text: str, photo_path: str = None) -> bool:
    """Send text or photo+caption."""
    if photo_path and os.path.exists(photo_path):
        return tg_send_photo(chat_id, photo_path, caption=text)
    return tg_send_text(chat_id, text)

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM LONG POLLING
# ═══════════════════════════════════════════════════════════════

_poll_offset: int = 0

def start_polling():
    """Background thread: listen for Telegram commands."""
    global _poll_offset
    log.info("Telegram polling started")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            r = requests.get(url, params={"offset": _poll_offset, "timeout": 30}, timeout=35)
            updates = r.json().get("result", [])
            for upd in updates:
                _poll_offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                text = msg.get("text", "").strip()
                cid  = str(msg.get("chat", {}).get("id", ""))
                if not cid:
                    continue
                log.info(f"Command from {cid}: {text}")
                if text.startswith("/start"):
                    handle_start_command(cid)
                elif text.startswith("/signal"):
                    threading.Thread(target=handle_signal_command, args=(cid,), daemon=True).start()
                elif text.startswith("/status"):
                    threading.Thread(target=handle_status_command, args=(cid,), daemon=True).start()
                elif text.startswith("/analytics"):
                    handle_analytics_command(cid)
                elif text.startswith("/help"):
                    handle_help_command(cid)
                elif text.startswith("/ask") or (text and not text.startswith("/")):
                    query = text.replace("/ask", "").strip()
                    if query:
                        log.info(f"AI Query from {cid}: {query}")
                        threading.Thread(target=handle_chat_query, args=(cid, query), daemon=True).start()
        except Exception as e:
            log.warning(f"Polling error: {e}")
            time.sleep(5)

# ═══════════════════════════════════════════════════════════════
#  SESSION CHECK
# ═══════════════════════════════════════════════════════════════

def get_active_session() -> str | None:
    h = datetime.datetime.now(datetime.timezone.utc).hour
    for name, (start, end) in SESSIONS.items():
        if start <= h < end:
            return name
    return None

# ═══════════════════════════════════════════════════════════════
#  MODULE 1: DATA FETCHING
# ═══════════════════════════════════════════════════════════════

TV_INTERVAL_MAP = {
    "1D":  Interval.INTERVAL_1_DAY,
    "4H":  Interval.INTERVAL_4_HOURS,
    "1H":  Interval.INTERVAL_1_HOUR,
    "15M": Interval.INTERVAL_15_MINUTES,
}

def fetch_tv_data(timeframe: str) -> dict | None:
    """Fetch indicator data from TradingView via tradingview-ta."""
    interval = TV_INTERVAL_MAP.get(timeframe)
    if not interval:
        return None

    sources = [
        (TV_SYMBOL,    TV_SCREENER,    TV_EXCHANGE),
        (TV_SYMBOL_FB, TV_SCREENER_FB, TV_EXCHANGE_FB),
    ]

    for symbol, screener, exchange in sources:
        try:
            handler = TA_Handler(symbol=symbol, screener=screener,
                                 exchange=exchange, interval=interval)
            analysis = handler.get_analysis()
            ind = analysis.indicators
            summary = analysis.summary
            oscil   = analysis.oscillators
            moving_avg = analysis.moving_averages

            return {
                "timeframe":   timeframe,
                "source":      f"{exchange}:{symbol}",
                # Price
                "close":       round(float(ind.get("close", 0)), 2),
                "open":        round(float(ind.get("open", 0)), 2),
                "high":        round(float(ind.get("high", 0)), 2),
                "low":         round(float(ind.get("low", 0)), 2),
                # Momentum
                "rsi":         round(float(ind.get("RSI", 50)), 2),
                "rsi_prev":    round(float(ind.get("RSI[1]", 50)), 2),
                "stoch_k":     round(float(ind.get("Stoch.K", 50)), 2),
                "stoch_d":     round(float(ind.get("Stoch.D", 50)), 2),
                "macd":        round(float(ind.get("MACD.macd", 0)), 4),
                "macd_signal": round(float(ind.get("MACD.signal", 0)), 4),
                "macd_hist":   round(float(ind.get("MACD.macd", 0) - ind.get("MACD.signal", 0)), 4),
                # Moving Averages
                "ema_20":      round(float(ind.get("EMA20", 0)), 2),
                "ema_50":      round(float(ind.get("EMA50", 0)), 2),
                "ema_100":     round(float(ind.get("EMA100", 0)), 2),
                "ema_200":     round(float(ind.get("EMA200", 0)), 2),
                "sma_50":      round(float(ind.get("SMA50", 0)), 2),
                "sma_200":     round(float(ind.get("SMA200", 0)), 2),
                # Volatility
                "atr":         round(float(ind.get("ATR", 0)), 2),
                "bb_upper":    round(float(ind.get("BB.upper", 0)), 2),
                "bb_lower":    round(float(ind.get("BB.lower", 0)), 2),
                "bb_basis":    round(float(ind.get("BB.basis", 0)), 2),
                # TradingView summary
                "tv_rec":      summary.get("RECOMMENDATION", "NEUTRAL"),
                "tv_buy":      summary.get("BUY", 0),
                "tv_sell":     summary.get("SELL", 0),
                "tv_neutral":  summary.get("NEUTRAL", 0),
                "ma_rec":      moving_avg.get("RECOMMENDATION", "NEUTRAL"),
                "osc_rec":     oscil.get("RECOMMENDATION", "NEUTRAL"),
                "change_pct":  round(float(ind.get("change", 0)), 4),
            }
        except Exception as e:
            log.warning(f"TV fetch failed [{exchange}:{symbol} {timeframe}]: {e}")
            continue

    log.error(f"All TV sources failed for {timeframe}")
    return None

def fetch_ohlcv(interval: str, period: str) -> pd.DataFrame | None:
    """Fetch OHLCV candles from Yahoo Finance."""
    try:
        df = yf.download(YF_TICKER, interval=interval, period=period, progress=False)
        if df is None or df.empty or len(df) < 20:
            return None
        df.dropna(inplace=True)
        # Fix MultiIndex columns (yfinance 0.2.40+)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        log.error(f"yfinance error [{interval}]: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
#  MODULE 2: TECHNICAL ANALYSIS
# ═══════════════════════════════════════════════════════════════

def detect_market_structure(df: pd.DataFrame) -> dict:
    """
    Detect market structure: HH/HL = Bullish, LH/LL = Bearish, else Sideways.
    Returns trend label + swing points.
    """
    highs = df["High"].squeeze().tail(30)
    lows  = df["Low"].squeeze().tail(30)
    rh, ph = float(highs.iloc[-1]), float(highs.iloc[-8])
    rl, pl = float(lows.iloc[-1]),  float(lows.iloc[-8])

    if rh > ph and rl > pl:
        trend = "BULLISH"
        label = "HH + HL confirmed"
    elif rh < ph and rl < pl:
        trend = "BEARISH"
        label = "LH + LL confirmed"
    else:
        trend = "SIDEWAYS"
        label = "No clear BOS — ranging"

    return {"trend": trend, "label": label, "rh": rh, "ph": ph, "rl": rl, "pl": pl}

def detect_support_resistance(df: pd.DataFrame, n_levels: int = 5) -> dict:
    """
    Auto-detect S/R levels from swing highs/lows using price clustering.
    Returns dict with resistance and support lists.
    """
    tail   = df.tail(100)
    highs  = tail["High"].squeeze().values
    lows   = tail["Low"].squeeze().values
    closes = tail["Close"].squeeze().values

    # Find swing highs (local maxima)
    swing_highs = []
    swing_lows  = []
    for i in range(2, len(highs) - 2):
        if highs[i] == max(highs[i-2:i+3]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i-2:i+3]):
            swing_lows.append(lows[i])

    def cluster(prices: list, tolerance_pct: float = 0.003) -> list:
        """Cluster nearby price levels."""
        if not prices:
            return []
        prices = sorted(prices)
        clusters = [[prices[0]]]
        for p in prices[1:]:
            if abs(p - clusters[-1][-1]) / clusters[-1][-1] < tolerance_pct:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [round(float(np.mean(c)), 2) for c in clusters]

    resistances = sorted(cluster(swing_highs), reverse=True)[:n_levels]
    supports    = sorted(cluster(swing_lows))[:n_levels]

    current_price = float(closes[-1])
    # Filter: resistance above price, support below price
    resistances = [r for r in resistances if r > current_price][:3]
    supports    = [s for s in supports    if s < current_price][:3]

    return {
        "resistance": resistances,
        "support":    supports,
        "nearest_res": resistances[0] if resistances else None,
        "nearest_sup": supports[0]    if supports    else None,
    }

def calculate_fibonacci(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Calculate Fibonacci retracement levels from recent swing high/low.
    """
    tail = df.tail(lookback)
    swing_high = float(tail["High"].squeeze().max())
    swing_low  = float(tail["Low"].squeeze().min())
    diff = swing_high - swing_low

    levels = {}
    for fib in FIB_LEVELS:
        # Retracement from high to low (for uptrend pullback)
        levels[f"fib_{int(fib*1000)}"] = round(swing_high - diff * fib, 2)

    levels["swing_high"] = round(swing_high, 2)
    levels["swing_low"]  = round(swing_low, 2)
    levels["range"]      = round(diff, 2)

    return levels

def detect_liquidity_zones(df: pd.DataFrame, lookback: int = 50) -> dict:
    """Detect equal highs/lows (liquidity pools) and stop hunt zones."""
    tail   = df.tail(lookback)
    highs  = tail["High"].squeeze()
    lows   = tail["Low"].squeeze()

    swing_h = round(float(highs.max()), 2)
    swing_l = round(float(lows.min()), 2)
    tol     = swing_h * 0.001  # 0.1% tolerance

    eq_h = int(((highs - highs.max()).abs() < tol).sum())
    eq_l = int(((lows  - lows.min()).abs()  < tol).sum())

    recent_h = round(float(highs.iloc[-10:].max()), 2)
    recent_l = round(float(lows.iloc[-10:].min()),  2)

    return {
        "swing_high": swing_h,
        "swing_low":  swing_l,
        "recent_high": recent_h,
        "recent_low":  recent_l,
        "equal_highs": eq_h,   # high liquidity pool above
        "equal_lows":  eq_l,   # low liquidity pool below
        "bsl": swing_h,        # buy-side liquidity
        "ssl": swing_l,        # sell-side liquidity
    }

def detect_fair_value_gaps(df: pd.DataFrame) -> list[dict]:
    """Detect Fair Value Gaps (3-candle imbalance pattern)."""
    tail   = df.tail(60)
    highs  = tail["High"].squeeze()
    lows   = tail["Low"].squeeze()
    fvgs   = []

    for i in range(1, len(tail) - 1):
        prev_high = float(highs.iloc[i - 1])
        next_low  = float(lows.iloc[i + 1])
        prev_low  = float(lows.iloc[i - 1])
        next_high = float(highs.iloc[i + 1])

        if next_low > prev_high:  # Bullish FVG
            fvgs.append({
                "type": "BULLISH FVG", "filled": False,
                "top": round(next_low, 2), "bot": round(prev_high, 2),
                "mid": round((next_low + prev_high) / 2, 2),
            })
        elif next_high < prev_low:  # Bearish FVG
            fvgs.append({
                "type": "BEARISH FVG", "filled": False,
                "top": round(prev_low, 2), "bot": round(next_high, 2),
                "mid": round((prev_low + next_high) / 2, 2),
            })

    return fvgs[-4:] if fvgs else []

def detect_order_blocks(df: pd.DataFrame) -> dict:
    """Detect most recent significant bullish/bearish order blocks."""
    tail   = df.tail(80)
    closes = tail["Close"].squeeze()
    opens  = tail["Open"].squeeze()
    highs  = tail["High"].squeeze()
    lows   = tail["Low"].squeeze()

    bull_ob = None
    bear_ob = None

    for i in range(len(tail) - 2, 0, -1):
        c  = float(closes.iloc[i])
        o  = float(opens.iloc[i])
        nc = float(closes.iloc[i + 1]) if i + 1 < len(tail) else c

        if c < o and nc > o and bull_ob is None:
            bull_ob = {
                "high": round(float(highs.iloc[i]), 2),
                "low":  round(float(lows.iloc[i]),  2),
                "mid":  round((float(highs.iloc[i]) + float(lows.iloc[i])) / 2, 2),
            }
        if c > o and nc < o and bear_ob is None:
            bear_ob = {
                "high": round(float(highs.iloc[i]), 2),
                "low":  round(float(lows.iloc[i]),  2),
                "mid":  round((float(highs.iloc[i]) + float(lows.iloc[i])) / 2, 2),
            }

        if bull_ob and bear_ob:
            break

    return {"bull_ob": bull_ob, "bear_ob": bear_ob}

def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate ATR manually from OHLCV data."""
    tail = df.tail(period + 5)
    highs  = tail["High"].squeeze().values.astype(float)
    lows   = tail["Low"].squeeze().values.astype(float)
    closes = tail["Close"].squeeze().values.astype(float)

    tr_list = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i]  - closes[i - 1]))
        tr_list.append(tr)
    return round(float(np.mean(tr_list[-period:])), 2) if tr_list else 0.0

def measure_volatility(df: pd.DataFrame) -> dict:
    """Check if volatility is suitable for trading."""
    atr = calculate_atr(df)
    # Average candle range
    tail = df.tail(20)
    ranges = (tail["High"].squeeze() - tail["Low"].squeeze()).values.astype(float)
    avg_range = float(np.mean(ranges))

    # BB width as %
    close = float(tail["Close"].squeeze().iloc[-1])

    return {
        "atr":       atr,
        "avg_range": round(avg_range, 2),
        "is_volatile_enough": atr > 3.0,   # Gold: ATR > $3 on 15M is tradeable
    }

# ═══════════════════════════════════════════════════════════════
#  MODULE 3: CONFLUENCE SCORING ENGINE
# ═══════════════════════════════════════════════════════════════

def score_confluence(snap: dict) -> dict:
    """
    Score market confluence from 0–10.
    Returns score + breakdown of each factor.
    """
    score   = 0
    factors = []
    bias    = "NEUTRAL"
    bull_pts = 0
    bear_pts = 0

    tv1h  = snap.get("tv_1H")  or {}
    tv4h  = snap.get("tv_4H")  or {}
    tv1d  = snap.get("tv_1D")  or {}
    tv15m = snap.get("tv_15M") or {}
    price = snap.get("current_price", 0)
    str15 = snap.get("structure_15m", {})
    str1h = snap.get("structure_1h",  {})

    # ── 1. Higher Timeframe Trend Alignment (1D + 4H) ──────────────
    d_rec  = tv1d.get("tv_rec", "NEUTRAL")
    h4_rec = tv4h.get("tv_rec", "NEUTRAL")

    if "BUY" in d_rec and "BUY" in h4_rec:
        bull_pts += 2; factors.append("✅ 1D + 4H both BULLISH (+2)")
    elif "SELL" in d_rec and "SELL" in h4_rec:
        bear_pts += 2; factors.append("✅ 1D + 4H both BEARISH (+2)")
    elif "BUY" in d_rec or "BUY" in h4_rec:
        bull_pts += 1; factors.append("⚠️ Partial HTF bullish (+1)")
    elif "SELL" in d_rec or "SELL" in h4_rec:
        bear_pts += 1; factors.append("⚠️ Partial HTF bearish (+1)")

    # ── 2. Market Structure (BOS / CHoCH) ──────────────────────────
    if str1h.get("trend") == "BULLISH":
        bull_pts += 1.5; factors.append("✅ 1H Structure: BULLISH (+1.5)")
    elif str1h.get("trend") == "BEARISH":
        bear_pts += 1.5; factors.append("✅ 1H Structure: BEARISH (+1.5)")
    else:
        factors.append("⚪ 1H Structure: Sideways (0)")

    if str15.get("trend") == "BULLISH":
        bull_pts += 1; factors.append("✅ 15M Structure: BULLISH (+1)")
    elif str15.get("trend") == "BEARISH":
        bear_pts += 1; factors.append("✅ 15M Structure: BEARISH (+1)")

    # ── 3. EMA Stack (Trend Filter) ────────────────────────────────
    ema20 = tv1h.get("ema_20", 0)
    ema50 = tv1h.get("ema_50", 0)
    ema200 = tv1h.get("ema_200", 0)

    if price > 0 and ema20 > 0 and ema50 > 0 and ema200 > 0:
        if price > ema20 > ema50 > ema200:
            bull_pts += 1.5; factors.append("✅ EMA Stack: Bullish alignment (+1.5)")
        elif price < ema20 < ema50 < ema200:
            bear_pts += 1.5; factors.append("✅ EMA Stack: Bearish alignment (+1.5)")
        elif price > ema50:
            bull_pts += 0.5; factors.append("⚠️ Price above EMA50 (+0.5)")
        elif price < ema50:
            bear_pts += 0.5; factors.append("⚠️ Price below EMA50 (+0.5)")

    # ── 4. RSI Momentum ────────────────────────────────────────────
    rsi = tv1h.get("rsi", 50)
    rsi_prev = tv1h.get("rsi_prev", 50)

    if 50 < rsi < 70 and rsi > rsi_prev:
        bull_pts += 1; factors.append(f"✅ RSI bullish momentum ({rsi:.1f}) (+1)")
    elif 30 < rsi < 50 and rsi < rsi_prev:
        bear_pts += 1; factors.append(f"✅ RSI bearish momentum ({rsi:.1f}) (+1)")
    elif rsi >= 70:
        bear_pts += 0.5; factors.append(f"⚠️ RSI overbought ({rsi:.1f}) — caution BUY")
    elif rsi <= 30:
        bull_pts += 0.5; factors.append(f"⚠️ RSI oversold ({rsi:.1f}) — caution SELL")

    # ── 5. MACD ────────────────────────────────────────────────────
    macd_hist = tv1h.get("macd_hist", 0)
    if macd_hist > 0:
        bull_pts += 0.5; factors.append("✅ MACD histogram bullish (+0.5)")
    elif macd_hist < 0:
        bear_pts += 0.5; factors.append("✅ MACD histogram bearish (+0.5)")

    # ── 6. Fibonacci Zone (Price near 0.618 / 0.5 retracement) ────
    fib = snap.get("fib_1h", {})
    fib_618 = fib.get("fib_618", 0)
    fib_500 = fib.get("fib_500", 0)
    fib_382 = fib.get("fib_382", 0)

    if price > 0:
        for fib_price, label in [(fib_618, "61.8%"), (fib_500, "50%"), (fib_382, "38.2%")]:
            if fib_price > 0 and abs(price - fib_price) / price < 0.003:  # within 0.3%
                factors.append(f"✅ Price near Fibonacci {label} support/resistance (+1)")
                # Direction depends on overall bias
                bull_pts += 0.7
                bear_pts += 0.3
                break

    # ── 7. Support / Resistance Proximity ─────────────────────────
    sr = snap.get("sr_1h", {})
    near_res = sr.get("nearest_res")
    near_sup = sr.get("nearest_sup")

    if near_res and price > 0 and abs(price - near_res) / price < 0.005:
        bear_pts += 1; factors.append(f"✅ Price near Resistance {near_res} — potential rejection (+1)")
    if near_sup and price > 0 and abs(price - near_sup) / price < 0.005:
        bull_pts += 1; factors.append(f"✅ Price near Support {near_sup} — potential bounce (+1)")

    # ── 8. Order Block / FVG ───────────────────────────────────────
    ob = snap.get("ob_1h", {})
    fvgs = snap.get("fvg_1h", [])

    if ob.get("bull_ob"):
        obb = ob["bull_ob"]
        if obb["low"] <= price <= obb["high"]:
            bull_pts += 1; factors.append(f"✅ Price inside Bullish OB [{obb['low']}–{obb['high']}] (+1)")

    if ob.get("bear_ob"):
        obb = ob["bear_ob"]
        if obb["low"] <= price <= obb["high"]:
            bear_pts += 1; factors.append(f"✅ Price inside Bearish OB [{obb['low']}–{obb['high']}] (+1)")

    for fvg in fvgs:
        if fvg.get("bot", 0) <= price <= fvg.get("top", 0):
            if "BULLISH" in fvg["type"]:
                bull_pts += 0.5; factors.append(f"✅ Price in Bullish FVG [{fvg['bot']}–{fvg['top']}] (+0.5)")
            else:
                bear_pts += 0.5; factors.append(f"✅ Price in Bearish FVG [{fvg['bot']}–{fvg['top']}] (+0.5)")
            break

    # ── Determine Overall Bias and Score ──────────────────────────
    if bull_pts > bear_pts + 1.5:
        bias  = "BULLISH"
        score = min(10, round(bull_pts, 1))
    elif bear_pts > bull_pts + 1.5:
        bias  = "BEARISH"
        score = min(10, round(bear_pts, 1))
    else:
        bias  = "NEUTRAL"
        score = min(bull_pts, bear_pts)
        factors.append("⚪ Conflicting signals — market is choppy")

    return {
        "bias":      bias,
        "score":     score,
        "bull_pts":  round(bull_pts, 1),
        "bear_pts":  round(bear_pts, 1),
        "factors":   factors,
        "tradeable": score >= MIN_SCORE and bias != "NEUTRAL",
    }

# ═══════════════════════════════════════════════════════════════
#  MODULE 4: SNAPSHOT BUILDER
# ═══════════════════════════════════════════════════════════════

def build_snapshot() -> dict | None:
    """Fetch all data and build complete market snapshot."""
    snap = {}

    # TradingView indicators
    log.info("Fetching TradingView data...")
    for tf in ["1D", "4H", "1H", "15M"]:
        snap[f"tv_{tf}"] = fetch_tv_data(tf)
        log.info(f"  TV {tf}: {'✓' if snap[f'tv_{tf}'] else '✗'}")

    # OHLCV candles
    log.info("Fetching OHLCV data...")
    df_1h  = fetch_ohlcv("1h",  "15d")
    df_15m = fetch_ohlcv("15m", "5d")
    log.info(f"  1H bars: {len(df_1h) if df_1h is not None else '✗'}")
    log.info(f"  15M bars: {len(df_15m) if df_15m is not None else '✗'}")

    # Store raw DFs for chart rendering
    snap["raw_df_1h"]  = df_1h
    snap["raw_df_15m"] = df_15m

    # Current price
    price = None
    for tf in ["tv_15M", "tv_1H", "tv_4H"]:
        d = snap.get(tf)
        if d and d.get("close", 0) > 0:
            price = d["close"]
            break
    if price is None and df_15m is not None:
        price = round(float(df_15m["Close"].squeeze().iloc[-1]), 2)
    if price is None:
        log.error("Cannot determine current price")
        return None

    snap["current_price"] = price

    # Structure analysis
    if df_1h is not None:
        snap["structure_1h"] = detect_market_structure(df_1h)
        snap["sr_1h"]        = detect_support_resistance(df_1h)
        snap["fib_1h"]       = calculate_fibonacci(df_1h)
        snap["liq_1h"]       = detect_liquidity_zones(df_1h)
        snap["fvg_1h"]       = detect_fair_value_gaps(df_1h)
        snap["ob_1h"]        = detect_order_blocks(df_1h)
        snap["vol_1h"]       = measure_volatility(df_1h)
    else:
        for k in ["structure_1h", "sr_1h", "fib_1h", "liq_1h", "fvg_1h", "ob_1h", "vol_1h"]:
            snap[k] = {} if k != "fvg_1h" else []

    if df_15m is not None:
        snap["structure_15m"] = detect_market_structure(df_15m)
        snap["sr_15m"]        = detect_support_resistance(df_15m)
        snap["fib_15m"]       = calculate_fibonacci(df_15m)
        snap["liq_15m"]       = detect_liquidity_zones(df_15m)
        snap["fvg_15m"]       = detect_fair_value_gaps(df_15m)
        snap["ob_15m"]        = detect_order_blocks(df_15m)
        snap["vol_15m"]       = measure_volatility(df_15m)
    else:
        for k in ["structure_15m", "sr_15m", "fib_15m", "liq_15m", "fvg_15m", "ob_15m", "vol_15m"]:
            snap[k] = {} if k != "fvg_15m" else []

    # Market News
    snap["news"] = fetch_xauusd_news()

    # v5.0 Advanced Modules
    snap["regime"] = regime_detector.detect(df_1h) if df_1h is not None else {"regime": "UNKNOWN", "volatility": "NORMAL", "trade_allowed": True}
    snap["sentiment"] = sentiment_engine.analyze_news(snap.get("news", []))
    
    # Technical Confluence
    snap["confluence"] = score_confluence(snap)
    
    # Execution Filter
    snap["is_trade_allowed"] = snap["regime"]["trade_allowed"] and not snap["sentiment"]["trade_block"]

    return snap


def fetch_xauusd_news() -> list:
    """Fetch latest gold and USD related news headlines using yfinance."""
    try:
        # Gold Futures (GC=F) usually has the most relevant news
        ticker = yf.Ticker("GC=F")
        news = ticker.news
        results = []
        if news:
            for item in news[:5]: # Get top 5 news items
                results.append({
                    "title": item.get("title", "No Title"),
                    "publisher": item.get("publisher", "Unknown"),
                    "time": datetime.datetime.fromtimestamp(item.get("providerPublishTime", 0)).strftime("%Y-%m-%d %H:%M") if item.get("providerPublishTime") else "N/A"
                })
        return results
    except Exception as e:
        log.warning(f"News fetch failed: {e}")
        return []

# ═══════════════════════════════════════════════════════════════
#  MODULE 5: AI ANALYSIS (GEMINI)
# ═══════════════════════════════════════════════════════════════

def fmt_tv(d: dict | None, label: str) -> str:
    if not d:
        return f"\n  [{label}]: Unavailable"
    return (
        f"\n  Close={d['close']} | H={d['high']} | L={d['low']}"
        f"\n  RSI={d['rsi']} (prev={d['rsi_prev']}) | Stoch K/D={d['stoch_k']}/{d['stoch_d']}"
        f"\n  MACD={d['macd']} | Sig={d['macd_signal']} | Hist={d['macd_hist']}"
        f"\n  EMA20={d['ema_20']} | EMA50={d['ema_50']} | EMA200={d['ema_200']}"
        f"\n  ATR={d['atr']} | BB[{d['bb_lower']}–{d['bb_upper']}]"
        f"\n  TV: {d['tv_rec']} (B:{d['tv_buy']} S:{d['tv_sell']} N:{d['tv_neutral']})"
    )

def fmt_fvg(fvgs: list) -> str:
    if not fvgs:
        return "None"
    return " | ".join(f"{f['type']} [{f['bot']}–{f['top']}]" for f in fvgs)

def fmt_ob(ob: dict) -> str:
    parts = []
    if ob.get("bull_ob"):
        o = ob["bull_ob"]
        parts.append(f"Bull OB [{o['low']}–{o['high']}]")
    if ob.get("bear_ob"):
        o = ob["bear_ob"]
        parts.append(f"Bear OB [{o['low']}–{o['high']}]")
    return " | ".join(parts) if parts else "None"

def fmt_fib(fib: dict) -> str:
    if not fib:
        return "N/A"
    return (f"H={fib.get('swing_high','?')} | 78.6%={fib.get('fib_786','?')} | "
            f"61.8%={fib.get('fib_618','?')} | 50%={fib.get('fib_500','?')} | "
            f"38.2%={fib.get('fib_382','?')} | L={fib.get('swing_low','?')}")

def fmt_sr(sr: dict) -> str:
    if not sr:
        return "N/A"
    return (f"Resistance: {sr.get('resistance', [])} | "
            f"Support: {sr.get('support', [])}")

def fmt_swing(d: dict) -> str:
    if not d: return "N/A"
    return f"High: {d.get('swing_high','?')} | Low: {d.get('swing_low','?')}"

def fmt_structure(d: dict) -> str:
    if not d: return "N/A"
    return f"{d.get('trend','?')} ({d.get('label','?')})"

def build_ai_prompt(snap: dict, session: str, conf: dict) -> str:
    price  = snap["current_price"]
    tv1h   = snap.get("tv_1H") or {}
    atr    = tv1h.get("atr", "N/A")
    regime = snap.get("regime", {})
    sentim = snap.get("sentiment", {})
    ob_1h  = snap.get("ob_1h", {})
    liq    = snap.get("liq_1h", {})

    # Build order block context
    bull_ob_str = f"[{ob_1h['bull_ob']['low']}–{ob_1h['bull_ob']['high']}]" if ob_1h.get("bull_ob") else "None"
    bear_ob_str = f"[{ob_1h['bear_ob']['low']}–{ob_1h['bear_ob']['high']}]" if ob_1h.get("bear_ob") else "None"

    # Liquidity context
    bsl = liq.get("bsl", "N/A")
    ssl = liq.get("ssl", "N/A")
    eq_highs = liq.get("equal_highs", 0)
    eq_lows  = liq.get("equal_lows", 0)

    return f"""
You are a SENIOR INSTITUTIONAL TRADER specializing in XAUUSD (Gold).
You trade using Smart Money Concepts (SMC) with strict confluence requirements.

════════════════════════════════════════
CURRENT MARKET SNAPSHOT
════════════════════════════════════════
Price: ${price} | Session: {session} | ATR (1H): ${atr}
Time Zone: WIB (UTC+7)

REGIME: {regime.get('regime','UNKNOWN')} | Volatility: {regime.get('volatility','NORMAL')}
SENTIMENT: {sentim.get('sentiment','NEUTRAL')} (Score: {sentim.get('score',0)}/10)
NEWS: {sentim.get('reason','N/A')}

Latest Headlines:
{chr(10).join([f"• {n['title']} [{n['publisher']}]" for n in snap.get('news', [])]) or "No news available."}

════════════════════════════════════════
MULTI-TIMEFRAME ANALYSIS
════════════════════════════════════════

[DAILY — Macro Bias]
{fmt_tv(snap.get('tv_1D'), '1D')}

[4H — Swing Direction]
{fmt_tv(snap.get('tv_4H'), '4H')}

[1H — Entry Timeframe]
{fmt_tv(snap.get('tv_1H'), '1H')}
Structure: {fmt_structure(snap.get('structure_1h'))}
S/R: {fmt_sr(snap.get('sr_1h'))}
Order Blocks: Bull OB {bull_ob_str} | Bear OB {bear_ob_str}
FVG: {fmt_fvg(snap.get('fvg_1h', []))}
Fibonacci: {fmt_fib(snap.get('fib_1h', {}))}
Liquidity: BSL=${bsl} | SSL=${ssl} | Equal Highs: {eq_highs} | Equal Lows: {eq_lows}

[15M — Trigger Timeframe]
{fmt_tv(snap.get('tv_15M'), '15M')}
Structure: {fmt_structure(snap.get('structure_15m'))}
Order Blocks: {fmt_ob(snap.get('ob_15m', {}))}
FVG: {fmt_fvg(snap.get('fvg_15m', []))}

════════════════════════════════════════
SMC ANALYSIS FRAMEWORK (FOLLOW STRICTLY)
════════════════════════════════════════

STEP 1 — BIAS (from 1D/4H):
• What is the dominant trend on Daily and 4H?
• Is price in a discount zone (below 50% fib = potential buy) or premium zone (above 50% = potential sell)?
• Are EMAs (20/50/200) stacked in trend direction?

STEP 2 — POINT OF INTEREST (POI):
• Identify ONE primary entry zone: Order Block, FVG, or key S/R level
• Is price currently AT the POI or APPROACHING it?
• Is there liquidity (equal highs/lows) above/below that the market might sweep first?

STEP 3 — CONFIRMATION (from 15M):
• Has there been a Break of Structure (BOS) in trade direction on 15M?
• Is there a 15M candle rejection (engulfing, pin bar, displacement) at the POI?
• Does RSI show momentum alignment? (>50 for buy, <50 for sell)
• Does MACD histogram show directional conviction?

STEP 4 — ENTRY DECISION:
• ENTRY ALLOWED only if: HTF bias + POI confluence + LTF confirmation ALL align
• If ANY of the 3 steps is missing → DO NOT TRADE
• Risk-Reward must be minimum 1:2.0 (prefer 1:3+)

STEP 5 — TIMING:
• Best entries are during London (07:00–12:00 UTC) or NY (13:00–18:00 UTC)
• Avoid entries during Asian session low liquidity (01:00–06:00 UTC)
• If setup is valid but session is bad → state "WAIT" with expected window

════════════════════════════════════════
ENTRY TIMING REQUIREMENT (NEW v5.1)
════════════════════════════════════════
IMPORTANT: Even if setup is valid, you MUST assess timing.
If it is currently outside London/NY sessions, or price is mid-range (not at POI),
state WHEN the setup may be executable instead of forcing an entry now.

════════════════════════════════════════
RESPONSE FORMAT
════════════════════════════════════════

IF VALID TRADE (all 3 steps confirmed + good timing):

PAIR: XAUUSD
BIAS: [BULLISH / BEARISH]
ENTRY: [exact price — use OB midpoint or FVG midpoint, not current price unless confirmed]
STOP LOSS: [below/above OB or recent swing, specific price]
TP1: [nearest S/R or FVG fill]
TP2: [next key level]
TP3: [liquidity target / swing high-low]
RISK-REWARD: [x:x ratio]
CONFIDENCE: [70–95]%
ENTRY TIMING: [NOW / or "Wait until HH:MM UTC — London/NY open"]
REASON:
- [HTF bias reasoning]
- [POI confluence: which OB/FVG/S/R]
- [LTF confirmation: BOS/rejection/momentum]
- [Liquidity context]

IF SETUP FORMING BUT NOT READY:

PENDING SETUP
BIAS: [BULLISH / BEARISH]
PENDING CONDITION:
- [What needs to happen: e.g., "Price needs to pull back to Bull OB at $XXXX"]
- [What confirmation to wait for: e.g., "15M bullish engulfing at OB"]
WATCH PRICE: [the POI price level to monitor]
IDEAL ENTRY WINDOW: [HH:MM–HH:MM UTC (WIB equivalent)]
CONFIDENCE IF TRIGGERED: [X]%

IF NO VALID SETUP:

NO TRADE
MARKET CONDITION:
- Trend: [state]
- Condition: [why no trade]
- Key Levels: [what to watch]
- Wait For: [specific condition that would create a setup]
- Next Opportunity: [estimated session/time window in UTC and WIB]

════════════════════════════════════════
DISCIPLINE RULES:
- Never force a trade. Patience is profit.
- Max 1 setup per analysis. Quality over quantity.
- If in doubt → NO TRADE.
- Entry at POI, not mid-range.
- Always protect capital first.
════════════════════════════════════════
""".strip()

def run_ai_analysis(snap: dict, session: str) -> str:
    conf   = snap.get("confluence", {})
    prompt = build_ai_prompt(snap, session, conf)
    try:
        response = ai_model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return f"NO TRADE - AI error: {e}"

def run_market_summary(snap: dict, session: str) -> str:
    """Get a brief market summary when no trade is available."""
    price = snap["current_price"]
    conf  = snap.get("confluence", {})

    prompt = f"""
You are a professional XAUUSD market analyst. Write a concise, professional market summary.

Current Price: ${price} | Session: {session}
1H Structure: {fmt_structure(snap.get('structure_1h'))}
15M Structure: {fmt_structure(snap.get('structure_15m'))}
S/R Levels (1H): {fmt_sr(snap.get('sr_1h'))}
Fibonacci (1H): {fmt_fib(snap.get('fib_1h'))}
Liquidity: {snap.get('liq_1h', {})}

Confluence Score: {conf.get('score', 0)}/10 ({conf.get('bias', '?')})
Top Factors: {', '.join(conf.get('factors', ['N/A'])[:3])}

Write 3-4 lines covering:
1. Current market bias and sentiment.
2. Key levels being tested or targeted.
3. What traders should wait for before looking for a setup.

Keep it highly professional and data-driven. Max 4 lines.
"""
    try:
        resp = ai_model.generate_content(prompt)
        return resp.text.strip()
    except Exception as e:
        log.error(f"Market summary error: {e}")
        return "Market data unavailable at this time."

# ═══════════════════════════════════════════════════════════════
#  MODULE 6: SIGNAL PARSING & VALIDATION
# ═══════════════════════════════════════════════════════════════

def parse_field(text: str, key: str) -> str:
    """Parse a field value from AI response."""
    try:
        line = [l for l in text.split("\n") if l.startswith(key)][0]
        return line.split(":", 1)[1].strip()
    except (IndexError, ValueError):
        return ""

def parse_price_field(text: str, key: str) -> float | None:
    """Parse a price from a field."""
    raw = parse_field(text, key)
    nums = re.findall(r'[\d,]+\.?\d*', raw.replace(",", ""))
    try:
        return float(nums[0]) if nums else None
    except:
        return None

def parse_confidence(text: str) -> int:
    """Extract confidence percentage from AI response."""
    try:
        raw = parse_field(text, "CONFIDENCE")
        nums = re.findall(r'\d+', raw)
        return int(nums[0]) if nums else 0
    except:
        return 0

def is_valid_signal(text: str) -> bool:
    """Check if AI response is a valid tradeable signal (not NO TRADE, not PENDING)."""
    upper = text.upper()
    if "NO TRADE" in upper:
        return False
    if "PENDING SETUP" in upper:
        return False
    required = ["ENTRY:", "STOP LOSS:", "TP1:", "CONFIDENCE:"]
    if not all(k in text for k in required):
        return False
    if parse_confidence(text) < MIN_CONFIDENCE:
        return False
    if parse_price_field(text, "ENTRY") is None:
        return False
    return True

# ═══════════════════════════════════════════════════════════════
#  MODULE 7: CHART GENERATOR
# ═══════════════════════════════════════════════════════════════

def generate_chart(df: pd.DataFrame, snap: dict, ai_result: str, filename: str) -> bool:
    """
    Generate a professional chart with:
    - Candlesticks (15M)
    - EMA 20, 50, 200
    - Support / Resistance bands
    - Fibonacci levels
    - Entry / SL / TP markers
    """
    if df is None or df.empty:
        return False

    try:
        df_plot = df.tail(60).copy()
        for col in ["Open", "High", "Low", "Close"]:
            df_plot[col] = df_plot[col].astype(float)

        # ── Parse signal levels ──
        entry  = parse_price_field(ai_result, "ENTRY")
        sl     = parse_price_field(ai_result, "STOP LOSS")
        tp1    = parse_price_field(ai_result, "TP1")
        tp2    = parse_price_field(ai_result, "TP2")
        tp3    = parse_price_field(ai_result, "TP3")

        # ── EMA overlays ──
        ema_lines = []
        colors    = []
        close_series = df_plot["Close"].squeeze()

        for period, color in [(20, "#00bcd4"), (50, "#ff9800"), (200, "#e91e63")]:
            if len(df_plot) >= period:
                ema = close_series.ewm(span=period, adjust=False).mean()
                ema_lines.append(mpf.make_addplot(ema, color=color, width=1.2, label=f"EMA{period}"))

        # ── S/R horizontal lines ──
        sr = snap.get("sr_15m") or snap.get("sr_1h", {})
        for res_price in (sr.get("resistance") or [])[:2]:
            rline = pd.Series(res_price, index=df_plot.index)
            ema_lines.append(mpf.make_addplot(rline, color="#ff4444", width=0.8,
                                               linestyle="--", alpha=0.7))
        for sup_price in (sr.get("support") or [])[:2]:
            sline = pd.Series(sup_price, index=df_plot.index)
            ema_lines.append(mpf.make_addplot(sline, color="#44ff44", width=0.8,
                                               linestyle="--", alpha=0.7))

        # ── Signal level lines ──
        level_map = [
            (entry, "#ffffff", 1.5, "solid"),
            (sl,    "#ff4444", 1.2, "dashed"),
            (tp1,   "#44ff44", 1.0, "dashed"),
            (tp2,   "#44ff44", 1.0, "dashed"),
            (tp3,   "#44ff44", 1.0, "dashed"),
        ]
        for lvl, clr, lw, ls in level_map:
            if lvl and lvl > 0:
                line = pd.Series(lvl, index=df_plot.index)
                ema_lines.append(mpf.make_addplot(line, color=clr, width=lw,
                                                   linestyle=ls, alpha=0.9))

        # ── mplfinance style ──
        mc = mpf.make_marketcolors(
            up="#26a69a", down="#ef5350",
            edge="inherit", wick="inherit",
            volume="in"
        )
        style = mpf.make_mpf_style(
            marketcolors=mc,
            base_mpf_style="nightclouds",
            gridstyle="--",
            gridcolor="#2a2a2a",
            facecolor="#131722",
            edgecolor="#2a2a2a",
            figcolor="#131722",
            y_on_right=True,
        )

        # ── Fibonacci text annotations via fig, axes ──
        fib = snap.get("fib_1h", {})
        fib_display = {
            "61.8%": fib.get("fib_618"),
            "50.0%": fib.get("fib_500"),
            "38.2%": fib.get("fib_382"),
        }

        conf  = snap.get("confluence", {})
        bias  = conf.get("bias", "?")
        score = conf.get("score", 0)
        price = snap.get("current_price", 0)

        title = f"{PAIR_LABEL} 15M  |  ${price}  |  {bias}  |  Score: {score}/10"

        fig, axes = mpf.plot(
            df_plot,
            type="candle",
            style=style,
            volume=False,
            addplot=ema_lines if ema_lines else None,
            title=f"\n{title}",
            tight_layout=True,
            returnfig=True,
            figsize=(14, 7),
        )

        ax = axes[0]
        ax.set_facecolor("#131722")

        # Fibonacci lines (horizontal text+line)
        fib_colors = {"61.8%": "#a29bfe", "50.0%": "#fd79a8", "38.2%": "#55efc4"}
        for label, lvl in fib_display.items():
            if lvl:
                ax.axhline(y=lvl, color=fib_colors.get(label, "#dfe6e9"),
                           linewidth=0.8, linestyle=":", alpha=0.6)
                ax.text(1, lvl, f" Fib {label} {lvl:.2f}",
                        color=fib_colors.get(label, "#dfe6e9"),
                        fontsize=7, va="center", transform=ax.get_yaxis_transform())

        # Entry / SL / TP annotations (right-side labels)
        label_map = [
            (entry, "ENTRY", "#ffffff"),
            (sl,    "SL",    "#ff4444"),
            (tp1,   "TP1",   "#44ff44"),
            (tp2,   "TP2",   "#44ff88"),
            (tp3,   "TP3",   "#88ffaa"),
        ]
        for lvl, lbl, clr in label_map:
            if lvl and lvl > 0:
                ax.text(1.001, lvl, f" {lbl} {lvl:.2f}", color=clr,
                        fontsize=8, fontweight="bold", va="center",
                        transform=ax.get_yaxis_transform())

        # Legend for EMAs
        legend_handles = [
            mpatches.Patch(color="#00bcd4", label="EMA 20"),
            mpatches.Patch(color="#ff9800", label="EMA 50"),
            mpatches.Patch(color="#e91e63", label="EMA 200"),
            mpatches.Patch(color="#ff4444", label="Resistance"),
            mpatches.Patch(color="#44ff44", label="Support"),
        ]
        ax.legend(handles=legend_handles, loc="upper left",
                  fontsize=7, framealpha=0.3, facecolor="#1e2230")

        plt.savefig(filename, dpi=120, bbox_inches="tight",
                    facecolor="#131722", edgecolor="none")
        plt.close(fig)
        log.info(f"Chart saved: {filename}")
        return True

    except Exception as e:
        log.error(f"Chart generation error: {e}")
        import traceback
        traceback.print_exc()
        return False

# ═══════════════════════════════════════════════════════════════
#  MODULE 8: MESSAGE FORMATTING
# ═══════════════════════════════════════════════════════════════

def format_pending_signal_message(ai_result: str, snap: dict, session: str) -> str:
    """Format a PENDING SETUP message when AI detects setup forming but not yet triggered."""
    import re

    price  = snap.get("current_price", 0)
    conf   = snap.get("confluence", {})
    score  = conf.get("score", 0)
    bias   = conf.get("bias", "?")
    regime = snap.get("regime", {})

    def extract(text, key):
        try:
            pattern = rf"{key}:\s*(.+)"
            match = re.search(pattern, text, re.IGNORECASE)
            return match.group(1).strip() if match else "N/A"
        except:
            return "N/A"

    def extract_multiline(text, key):
        try:
            lines = text.split("\n")
            collecting = False
            result = []
            for line in lines:
                if key.upper() in line.upper() and ":" in line:
                    collecting = True
                    continue
                if collecting:
                    if line.strip().startswith("-") or line.strip().startswith("•"):
                        result.append(line.strip())
                    elif line.strip() == "" or (":" in line and not line.strip().startswith("-")):
                        break
            return "\n".join(result[:3]) if result else "N/A"
        except:
            return "N/A"

    pending_bias     = extract(ai_result, "BIAS")
    watch_price      = extract(ai_result, "WATCH PRICE")
    ideal_window     = extract(ai_result, "IDEAL ENTRY WINDOW")
    confidence_trig  = extract(ai_result, "CONFIDENCE IF TRIGGERED")
    conditions       = extract_multiline(ai_result, "PENDING CONDITION")

    bias_icon   = "🟢" if "BULL" in pending_bias.upper() else "🔴" if "BEAR" in pending_bias.upper() else "🟡"
    score_bar   = "▓" * int(score) + "░" * (10 - int(score))
    vol_st      = regime.get("volatility", "NORMAL")

    msg = (
        f"🔔 <b>PENDING SETUP DETECTED</b> | XAUUSD v5.1\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"<b>📍 Setup Context</b>\n"
        f"├ 💲 Current Price : <b>${price}</b>\n"
        f"├ 🏦 Session       : <b>{session}</b>\n"
        f"├ ⚡ Volatility    : <b>{vol_st}</b>\n"
        f"└ 📊 Confluence    : <b>{score}/10</b> [{score_bar}]\n"
        f"\n"
        f"<b>{bias_icon} Anticipated Direction: {pending_bias}</b>\n"
        f"\n"
        f"<b>🎯 What to Watch</b>\n"
        f"├ 📌 Watch Price   : <b>${watch_price}</b>\n"
        f"└ 🕐 Entry Window  : <b>{ideal_window}</b>\n"
        f"\n"
        f"<b>⚠️ Conditions Needed</b>\n"
        f"{conditions}\n"
        f"\n"
        f"<b>🔥 If Triggered: ~{confidence_trig} Confidence</b>\n"
        f"\n"
        f"─────────────────────────\n"
        f"⏳ <i>Setup not yet confirmed — monitoring price action</i>\n"
        f"📲 <i>Run /signal again when price approaches watch level</i>\n"
        f"🤖 <i>Vanguard AI v5.1 · XAUUSD</i>"
    )
    return msg

def format_signal_message(ai_result: str, snap: dict, session: str) -> str:
    """Format a clean signal message for Telegram."""
    price  = snap["current_price"]
    conf   = snap.get("confluence", {})
    bias   = conf.get("bias", "?")
    score  = conf.get("score", 0)

    entry  = parse_price_field(ai_result, "ENTRY")
    sl     = parse_price_field(ai_result, "STOP LOSS")
    tp1    = parse_price_field(ai_result, "TP1")
    tp2    = parse_price_field(ai_result, "TP2")
    tp3    = parse_price_field(ai_result, "TP3")
    rr     = parse_field(ai_result, "RISK-REWARD")
    conf_n = parse_confidence(ai_result)
    reason = parse_field(ai_result, "REASON")

    direction = "🟢 BUY" if bias == "BULLISH" else "🔴 SELL"
    session_label = session or "Off-Session"

    sl_pips   = abs(price - sl) if sl else 0
    sl_dollar = round(sl_pips, 2)

    factors_top3 = "\n".join(f"  • {f}" for f in conf.get("factors", [])[:4])

    regime = snap.get("regime", {}).get("regime", "UNKNOWN")
    sentim = snap.get("sentiment", {}).get("sentiment", "NEUTRAL")

    conf_bar = "▓" * int(conf_n / 10) + "░" * (10 - int(conf_n / 10))
    score_bar = "▓" * int(score) + "░" * (10 - int(score))
    direction_block = "🟢 ━━ BUY / LONG ━━" if bias == "BULLISH" else "🔴 ━━ SELL / SHORT ━━"

    msg = (
        f"{'🟢' if bias == 'BULLISH' else '🔴'} <b>XAUUSD SIGNAL</b> | <b>v5.0 INSTITUTIONAL</b>\n"
        f"┌{'─'*24}┐\n"
        f"│  {direction_block:<22}│\n"
        f"└{'─'*24}┘\n"
        f"\n"
        f"<b>📍 Market Context</b>\n"
        f"├ 🏦 Session   : <b>{session_label}</b>\n"
        f"├ 💲 Price     : <b>${price}</b>\n"
        f"├ 🛡 Regime    : <b>{regime}</b> | {snap.get('regime', {}).get('volatility', 'NORMAL')}\n"
        f"└ 📰 Sentiment : <b>{sentim}</b>\n"
        f"\n"
        f"<b>🎯 Trade Levels</b>\n"
        f"├ 🟡 Entry     : <b>${entry}</b>\n"
        f"├ 🔴 Stop Loss : <b>${sl}</b>  <i>(risk ${sl_dollar:.1f})</i>\n"
        f"├ 🟢 TP1       : <b>${tp1}</b>\n"
        f"├ 🟢 TP2       : <b>${tp2}</b>\n"
        f"└ 🟢 TP3       : <b>${tp3}</b>\n"
        f"\n"
        f"<b>📊 Signal Quality</b>\n"
        f"├ ⚖️  R:R Ratio  : <b>{rr}</b>\n"
        f"├ 🔥 Confidence : <b>{conf_n}%</b>  [{conf_bar}]\n"
        f"└ 📈 Confluence : <b>{score}/10</b> [{score_bar}]\n"
        f"\n"
        f"<b>📌 Confluence Evidence</b>\n"
        f"{factors_top3}\n"
        f"\n"
        f"<b>💡 AI Analysis</b>\n"
        f"<i>{reason}</i>\n"
        f"\n"
        f"─────────────────────────\n"
        f"🤖 <i>Vanguard AI v5.0 · XAUUSD · {session_label}</i>"
    )
    return msg

def format_no_trade_message(summary: str, snap: dict, session: str) -> str:
    """Format enhanced NO TRADE market update message."""
    price   = snap.get("current_price", 0)
    conf    = snap.get("confluence", {})
    bias    = conf.get("bias", "?")
    score   = conf.get("score", 0)
    str1h   = snap.get("structure_1h", {})
    str15   = snap.get("structure_15m", {})
    sr      = snap.get("sr_1h", {})
    fib     = snap.get("fib_1h", {})
    ob      = snap.get("ob_1h", {})
    liq     = snap.get("liq_1h", {})
    regime  = snap.get("regime", {})
    sentim  = snap.get("sentiment", {})
    session_label = session or "Off-Session"

    reason    = regime.get("reason", "N/A")
    score_bar = "▓" * int(score) + "░" * (10 - int(score))
    t1h  = str1h.get("trend", "?")
    t15m = str15.get("trend", "?")
    trend_icon_1h  = "📈" if t1h  == "BULLISH" else "📉" if t1h  == "BEARISH" else "↔️"
    trend_icon_15m = "📈" if t15m == "BULLISH" else "📉" if t15m == "BEARISH" else "↔️"
    near_sup = sr.get("nearest_sup", "–")
    near_res = sr.get("nearest_res", "–")

    # Order block info
    bull_ob_str = f"${ob['bull_ob']['low']}–${ob['bull_ob']['high']}" if ob.get("bull_ob") else "None"
    bear_ob_str = f"${ob['bear_ob']['low']}–${ob['bear_ob']['high']}" if ob.get("bear_ob") else "None"

    # Liquidity
    bsl = liq.get("bsl", "–")
    ssl = liq.get("ssl", "–")
    eq_h = liq.get("equal_highs", 0)
    eq_l = liq.get("equal_lows", 0)

    # Sentiment
    sent_icon = "🟢" if sentim.get("sentiment") == "BULLISH" else "🔴" if sentim.get("sentiment") == "BEARISH" else "⚪"
    sent_score = sentim.get("score", 0)

    msg = (
        f"📊 <b>XAUUSD MARKET UPDATE</b> | <b>v5.1 — NO TRADE</b>\n"
        f"\n"
        f"<b>📍 Snapshot</b>\n"
        f"├ 🏦 Session    : <b>{session_label}</b>\n"
        f"├ 💲 Price      : <b>${price}</b>\n"
        f"├ 🛡 Regime     : <b>{regime.get('regime','?')}</b> | {regime.get('volatility','?')}\n"
        f"└ {sent_icon} Sentiment  : <b>{sentim.get('sentiment','NEUTRAL')}</b> ({sent_score:+}/10)\n"
        f"\n"
        f"<b>📐 Market Structure</b>\n"
        f"├ {trend_icon_1h}  1H Trend   : <b>{t1h}</b> — {str1h.get('label','?')}\n"
        f"├ {trend_icon_15m}  15M Trend  : <b>{t15m}</b> — {str15.get('label','?')}\n"
        f"└ ⚡ Confluence : <b>{bias}</b> | <b>{score}/10</b> [{score_bar}]\n"
        f"\n"
        f"<b>🔑 Key Levels</b>\n"
        f"├ 🔺 Resistance : <b>${near_res}</b>\n"
        f"├ 🔻 Support    : <b>${near_sup}</b>\n"
        f"├ 🟩 Bull OB    : <b>{bull_ob_str}</b>\n"
        f"└ 🟥 Bear OB    : <b>{bear_ob_str}</b>\n"
        f"\n"
        f"<b>💧 Liquidity Map</b>\n"
        f"├ 🔼 BSL (Buy-Side Liq) : <b>${bsl}</b> {f'({eq_h}x equal highs)' if eq_h > 1 else ''}\n"
        f"└ 🔽 SSL (Sell-Side Liq): <b>${ssl}</b> {f'({eq_l}x equal lows)' if eq_l > 1 else ''}\n"
        f"\n"
        f"<b>🔢 Fibonacci (1H)</b>\n"
        f"├ 61.8% → <b>${fib.get('fib_618','?')}</b>\n"
        f"├ 50.0% → <b>${fib.get('fib_500','?')}</b>\n"
        f"└ 38.2% → <b>${fib.get('fib_382','?')}</b>\n"
        f"\n"
        f"<b>💬 AI Market Read</b>\n"
        f"<i>{summary}</i>\n"
        f"\n"
        f"─────────────────────────\n"
        f"⏳ <i>No setup detected · Entry timing forecast below ↓</i>\n"
        f"🤖 <i>Vanguard AI v5.1 · XAUUSD</i>"
    )
    return msg

# ═══════════════════════════════════════════════════════════════
#  MODULE 9: SIGNAL LOGGING & DEDUP
# ═══════════════════════════════════════════════════════════════

_last_signal: dict = {}

def is_duplicate_signal(ai_result: str) -> bool:
    """Prevent sending the same signal twice within cooldown period."""
    global _last_signal
    try:
        entry = parse_price_field(ai_result, "ENTRY")
        bias  = parse_field(ai_result, "BIAS")
        key   = f"{bias}_{entry}"

        if (_last_signal.get("key") == key and
                time.time() - _last_signal.get("ts", 0) < SIGNAL_COOLDOWN):
            return True

        _last_signal = {"key": key, "ts": time.time()}
        return False
    except:
        return False

def log_signal(ai_result: str, snap: dict, session: str, sent: bool):
    """Log signal to JSON file."""
    record = {
        "time":       datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "price":      snap.get("current_price"),
        "session":    session,
        "sent":       sent,
        "confidence": parse_confidence(ai_result),
        "entry":      parse_price_field(ai_result, "ENTRY"),
        "bias":       parse_field(ai_result, "BIAS"),
        "score":      snap.get("confluence", {}).get("score", 0),
        "signal":     ai_result[:800],
    }
    try:
        path = Path(SIGNAL_LOG)
        existing = []
        if path.exists():
            with open(path) as f:
                existing = json.load(f)
        existing.append(record)
        existing = existing[-300:]
        with open(path, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        log.warning(f"Signal log error: {e}")

def save_active_signal(data: dict):
    """Save the current active signal to file."""
    try:
        with open(ACTIVE_SIGNAL, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.error(f"Error saving active signal: {e}")

def load_active_signal() -> dict | None:
    """Load the current active signal from file."""
    try:
        path = Path(ACTIVE_SIGNAL)
        if not path.exists():
            return None
        with open(path) as f:
            data = json.load(f)
            return data if data else None
    except Exception as e:
        log.error(f"Error loading active signal: {e}")
        return None

def clear_active_signal():
    """Remove the active signal file."""
    try:
        path = Path(ACTIVE_SIGNAL)
        if path.exists():
            path.unlink()
    except Exception as e:
        log.error(f"Error clearing active signal: {e}")


def send_v5_signal(chat_id: str, ai_result: str, snap: dict, session: str):
    """v5.0 Centralized signal sender with risk management."""
    entry = parse_price_field(ai_result, "ENTRY")
    sl = parse_price_field(ai_result, "STOP LOSS")
    lot_size = risk_engine.calculate_lot_size(entry, sl) if entry and sl else 0.01

    chart_ok = generate_chart(snap.get("raw_df_15m"), snap, ai_result, CHART_FILE)
    msg = format_signal_message(ai_result, snap, session)
    
    # Append lot size info to message
    msg = msg.replace("⚖️ <b>Risk/Reward:</b>", f"💰 <b>Recommended Lot:</b> {lot_size}\n⚖️ <b>Risk/Reward:</b>")
    
    sent = tg_send(chat_id, msg, CHART_FILE if chart_ok else None)
    if sent:
        active_data = {
            "bias":  parse_field(ai_result, "BIAS"),
            "entry": entry,
            "sl":    sl,
            "tp1":   parse_price_field(ai_result, "TP1"),
            "tp2":   parse_price_field(ai_result, "TP2"),
            "tp3":   parse_price_field(ai_result, "TP3"),
            "lot_size": lot_size,
            "time":  datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        save_active_signal(active_data)
        log_signal(ai_result, snap, session, sent=True)
        return True
    return False

def monitor_active_signal():
    """
    Background check: see if current price hit TP or SL of active signal.
    """
    active = load_active_signal()
    if not active:
        return

    # Fetch fresh price
    try:
        # Use TV or YF to get current price. Snapshot building is heavy, 
        # so we just fetch the price.
        handler = TA_Handler(symbol=TV_SYMBOL, screener=TV_SCREENER, 
                             exchange=TV_EXCHANGE, interval=Interval.INTERVAL_1_MINUTE)
        analysis = handler.get_analysis()
        current_price = float(analysis.indicators.get("close", 0))
    except Exception as e:
        log.warning(f"Monitor price fetch failed: {e}")
        return

    bias   = active.get("bias")
    entry  = active.get("entry")
    sl     = active.get("sl")
    tp1    = active.get("tp1")
    tp2    = active.get("tp2")
    tp3    = active.get("tp3")
    
    # Check SL
    hit_sl = False
    if bias == "BULLISH" and current_price <= sl: hit_sl = True
    if bias == "BEARISH" and current_price >= sl: hit_sl = True

    if hit_sl:
        pips = round(abs(current_price - entry) * 10, 1)
        msg = (
            f"🛑 <b>SIGNAL CLOSED: STOP LOSS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Final Price:</b> ${current_price}\n"
            f"📉 <b>Loss:</b> -{pips} pips\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Market hit SL. Waiting for next setup..."
        )
        tg_send_text(CHAT_ID, msg)
        
        # v5.0 Record result in Analytics and Risk Engine
        profit_usd = -pips * 10.0 * active.get("lot_size", 0.1)
        analytics.log_trade({
            "pair": "XAUUSD", "bias": bias, "entry": entry, "sl": sl,
            "tp1": tp1, "tp2": tp2, "tp3": tp3, "exit_price": current_price,
            "pips": -pips, "profit_usd": profit_usd, "result": "LOSS"
        })
        risk_engine.record_trade_result(profit_usd)
        
        clear_active_signal()
        return

    # Check TPs
    hit_tp = None
    if bias == "BULLISH":
        if tp3 and current_price >= tp3: hit_tp = "TP3"
        elif tp2 and current_price >= tp2: hit_tp = "TP2"
        elif tp1 and current_price >= tp1: hit_tp = "TP1"
    else: # BEARISH
        if tp3 and current_price <= tp3: hit_tp = "TP3"
        elif tp2 and current_price <= tp2: hit_tp = "TP2"
        elif tp1 and current_price <= tp1: hit_tp = "TP1"

    if hit_tp:
        pips = round(abs(current_price - entry) * 10, 1)
        msg = (
            f"✅ <b>SIGNAL CLOSED: TAKE PROFIT ({hit_tp})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Final Price:</b> ${current_price}\n"
            f"📈 <b>Profit:</b> +{pips} pips\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Target reached! Ready for next setup."
        )
        tg_send_text(CHAT_ID, msg)
        
        # v5.0 Record result in Analytics and Risk Engine
        profit_usd = pips * 10.0 * active.get("lot_size", 0.1)
        analytics.log_trade({
            "pair": "XAUUSD", "bias": bias, "entry": entry, "sl": sl,
            "tp1": tp1, "tp2": tp2, "tp3": tp3, "exit_price": current_price,
            "pips": pips, "profit_usd": profit_usd, "result": "WIN"
        })
        risk_engine.record_trade_result(profit_usd)
        
        clear_active_signal()

# ═══════════════════════════════════════════════════════════════
#  MODULE 10: MAIN CYCLES
# ═══════════════════════════════════════════════════════════════

def run_auto_cycle():
    """
    Automatic cycle (every 15 min):
    Only sends signal if confluence is strong and not duplicate.
    """
    log.info("═" * 55)
    log.info("AUTO CYCLE START")
    log.info("═" * 55)

    session = get_active_session()
    if not session:
        log.info("Outside trading session — skip")
        return

    snap = build_snapshot()
    if snap is None:
        log.error("Snapshot failed")
        return

    conf = snap.get("confluence", {})
    log.info(f"Price: ${snap['current_price']} | {conf.get('bias','?')} | Score: {conf.get('score',0)}/10")

    # v5.0 Check daily limits
    allowed, reason = risk_engine.check_daily_limits()
    if not allowed:
        log.info(f"Risk Engine: {reason} — skip")
        return

    # v5.0 Check Regime & Sentiment
    if not snap.get("is_trade_allowed", True):
        log.info(f"Market Regime/Sentiment block: {snap['regime']['reason']} — skip")
        return

    ai_result = run_ai_analysis(snap, session)
    log.info(f"AI result:\n{ai_result}\n")

    if not is_valid_signal(ai_result):
        log.info("AI returned NO TRADE or low confidence")
        log_signal(ai_result, snap, session, sent=False)
        return

    if is_duplicate_signal(ai_result):
        log.info("Duplicate signal — skip")
        return

    # Check if a signal is already active
    if load_active_signal():
        log.info("A signal is already active — skipping auto-signal")
        return

    # v5.0 Send Signal using Execution Helper
    send_v5_signal(CHAT_ID, ai_result, snap, session)


def handle_signal_command(chat_id: str):
    """
    /signal command handler v5.1:
    - VALID TRADE:   send full signal + chart
    - PENDING SETUP: send pending alert + entry timing forecast
    - NO TRADE:      send market update + entry timing forecast
    """
    log.info(f"/signal from {chat_id}")
    tg_send_text(chat_id,
        "🔍 <b>Analyzing XAUUSD...</b>\n"
        "⏳ Building multi-timeframe institutional snapshot...\n"
        "<i>ETA: 15–30 seconds</i>")

    session = get_active_session()
    snap    = build_snapshot()

    if snap is None:
        tg_send_text(chat_id, "❌ <b>Data fetch failed.</b> Please try again in a moment.")
        return

    conf  = snap.get("confluence", {})
    price = snap.get("current_price", 0)
    log.info(f"On-demand: ${price} | {conf.get('bias','?')} | Score {conf.get('score',0)}/10")

    ai_result = run_ai_analysis(snap, session or "Off-Session")
    log.info(f"On-demand AI:\n{ai_result}\n")

    # ── CASE 1: Valid Trade Signal ──
    if is_valid_signal(ai_result):
        if load_active_signal():
            tg_send_text(chat_id,
                "⚠️ <b>Signal Blocked</b>\n"
                "There is already an active trade running.\n"
                "Wait for current TP or SL before taking a new position.")
            return

        if not send_v5_signal(chat_id, ai_result, snap, session or "Off-Session"):
            tg_send_text(chat_id, "❌ Signal generation failed. Try again.")
        return

    # ── CASE 2: Pending Setup Detected ──
    if "PENDING SETUP" in ai_result.upper():
        timing_report = generate_entry_timing_report(snap)
        pending_msg   = format_pending_signal_message(ai_result, snap, session or "Off-Session")
        # Chart without entry lines (monitoring mode)
        chart_ok = generate_chart(snap.get("raw_df_15m"), snap, "NO TRADE", CHART_FILE)
        # Send pending analysis first, then timing forecast
        tg_send(chat_id, pending_msg, CHART_FILE if chart_ok else None)
        tg_send_text(chat_id, timing_report)
        log_signal(ai_result, snap, session or "Off-Session", sent=False)
        return

    # ── CASE 3: No Trade ──
    summary      = run_market_summary(snap, session or "Off-Session")
    no_trade_msg = format_no_trade_message(summary, snap, session or "Off-Session")
    timing_report = generate_entry_timing_report(snap)

    chart_ok = generate_chart(snap.get("raw_df_15m"), snap, "NO TRADE", CHART_FILE)
    tg_send(chat_id, no_trade_msg, CHART_FILE if chart_ok else None)
    tg_send_text(chat_id, timing_report)
    log_signal(ai_result, snap, session or "Off-Session", sent=False)


def handle_status_command(chat_id: str):
    """
    /status command: quick market snapshot without full AI analysis.
    """
    log.info(f"/status from {chat_id}")
    tg_send_text(chat_id, "⏳ <b>Fetching quick status...</b>")

    snap = build_snapshot()
    if snap is None:
        tg_send_text(chat_id, "❌ Data unavailable.")
        return

    conf  = snap.get("confluence", {})
    str1h = snap.get("structure_1h", {})
    str15 = snap.get("structure_15m", {})
    sr    = snap.get("sr_1h", {})
    vol   = snap.get("vol_1h", {})
    session = get_active_session() or "Off-Session"

    regime = snap.get("regime", {}).get("regime", "UNKNOWN")
    vol_st = snap.get("regime", {}).get("volatility", "NORMAL")
    stats  = risk_engine.get_daily_stats()

    score = conf.get('score', 0)
    score_bar = "▓" * int(score) + "░" * (10 - int(score))
    tradeable = conf.get('tradeable') and snap.get('regime', {}).get('trade_allowed')
    trade_status = "✅ <b>TRADEABLE</b> — High-probability setup available" if tradeable else "⏳ <b>MONITORING</b> — No valid setup yet"
    pnl = stats.get('daily_pnl', 0)
    pnl_icon = "📈" if pnl >= 0 else "📉"

    msg = (
        f"📡 <b>XAUUSD LIVE STATUS</b> | <b>v5.0</b>\n"
        f"\n"
        f"<b>💲 Price & Session</b>\n"
        f"├ 💰 Price    : <b>${snap['current_price']}</b>\n"
        f"├ 🏦 Session  : <b>{session}</b>\n"
        f"├ 🛡 Regime   : <b>{regime}</b>\n"
        f"└ ⚡ Volatility: <b>{vol_st}</b>\n"
        f"\n"
        f"<b>📐 Market Structure</b>\n"
        f"├ 📈 1H Trend : <b>{str1h.get('trend','?')}</b>\n"
        f"├ 📉 15M Trend: <b>{str15.get('trend','?')}</b>\n"
        f"└ 🎯 Confluence: <b>{conf.get('bias','?')}</b> | <b>{score}/10</b> [{score_bar}]\n"
        f"\n"
        f"<b>💹 Daily Performance</b>\n"
        f"├ {pnl_icon} PnL    : <b>${pnl:.2f}</b>\n"
        f"└ 🔻 Drawdown: <b>{stats.get('daily_loss_percent', 0):.2f}%</b>\n"
        f"\n"
        f"{trade_status}\n"
        f"─────────────────────────\n"
        f"🤖 <i>Vanguard AI v5.0 · Auto-refresh every 15m</i>"
    )
    tg_send_text(chat_id, msg)


def handle_analytics_command(chat_id: str):
    """Show performance analytics report."""
    report = analytics.get_summary_report()
    tg_send_text(chat_id, report)


def handle_help_command(chat_id: str):
    """Show help message."""
    msg = (
        "🤖 <b>XAUUSD SMART BOT v5.0</b>\n"
        "<i>Institutional-Grade AI Trading Assistant</i>\n"
        "\n"
        "<b>📌 Commands</b>\n"
        "├ /signal    → Full SMC analysis + chart\n"
        "├ /status    → Live price, regime & PnL\n"
        "├ /analytics → Win rate, drawdown & stats\n"
        "└ /help      → This guide\n"
        "\n"
        "<b>💬 AI Chat</b>\n"
        "└ Just type any question about Gold!\n"
        "\n"
        "<b>🛡 Risk Framework</b>\n"
        "├ Risk per trade : 1% of account\n"
        "├ Min RR ratio   : 1:2\n"
        "├ Daily drawdown : 3% circuit breaker\n"
        "└ Max consec loss: 3 trades\n"
        "\n"
        "<b>🏛 Methodology</b>\n"
        "├ Smart Money Concepts (SMC)\n"
        "├ Fibonacci + Order Blocks + FVG\n"
        "├ Market Regime Detection\n"
        "├ News Sentiment Engine\n"
        "└ Google Gemini 2.0 Flash AI\n"
        "\n"
        "─────────────────────────\n"
        "⚠️ <i>For educational purposes. Trade responsibly.</i>"
    )
    tg_send_text(chat_id, msg)


def handle_start_command(chat_id: str):
    """Welcome message for /start."""
    msg = (
        "👋 <b>Welcome to XAUUSD Smart Bot v5.0</b>\n"
        "<i>Your institutional-grade AI trading partner</i>\n"
        "\n"
        "I analyze Gold (XAUUSD) using:\n"
        "├ 🏛 Smart Money Concepts (SMC)\n"
        "├ 🤖 Google Gemini 2.0 AI\n"
        "├ 📊 4-Timeframe Confluence\n"
        "└ 🛡 Automated Risk Management\n"
        "\n"
        "<b>📌 Get started:</b>\n"
        "├ /signal    → Get a trade signal now\n"
        "├ /status    → Check market conditions\n"
        "├ /analytics → View your statistics\n"
        "└ /help      → Full command guide\n"
        "\n"
        "─────────────────────────\n"
        "✅ <i>System online · Monitoring 24/7</i>"
    )
    tg_send_text(chat_id, msg)


def handle_chat_query(chat_id: str, text: str):
    """Handle general questions using Gemini."""
    prompt = f"""
You are XAUUSDSmartBot AI Assistant. You are an expert in Gold (XAUUSD) trading and technical analysis.
The user is asking: "{text}"

Answer concisely and professionally in the same language as the user. 
If the question is about trading, provide helpful insights based on Smart Money Concepts (SMC).
Keep the response helpful but brief.
"""
    try:
        response = ai_model.generate_content(prompt)
        content = response.text.strip()
        # Use a safe way to send text if HTML might fail
        if not tg_send_text(chat_id, content):
            # If HTML failed, try plain text with escaped HTML
            tg_send_text(chat_id, tg_escape_html(content))
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg:
            wait_match = re.search(r"retry in ([\d\.]+)s", err_msg)
            wait_time = f" {wait_match.group(1)}s" if wait_match else ""
            tg_send_text(chat_id, f"⚠️ <b>Gemini API Quota Exceeded.</b>\nPlease wait a moment before bertanya kembali.{wait_time}")
        else:
            log.error(f"Chat query error: {e}")
            tg_send_text(chat_id, f"❌ <b>AI Error:</b> {err_msg[:100]}")

# ═══════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════

def send_startup_message():
    """Send startup notification to Telegram."""
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        "🚀 <b>XAUUSD SMART BOT v5.0 — ONLINE</b>\n"
        "<i>Institutional AI Trading System Active</i>\n"
        "\n"
        "<b>📡 Data Sources</b>\n"
        "├ TradingView (4 timeframes)\n"
        "├ Yahoo Finance (OHLCV candles)\n"
        "└ Google Gemini 2.0 Flash (AI)\n"
        "\n"
        "<b>🏛 Modules Loaded</b>\n"
        "├ ✅ SMC + Fibonacci Confluence\n"
        "├ ✅ Market Regime Detector\n"
        "├ ✅ News Sentiment Engine\n"
        "├ ✅ Risk Management Engine\n"
        "└ ✅ Performance Analytics (SQLite)\n"
        "\n"
        "<b>🛡 Risk Parameters</b>\n"
        "├ Account Risk  : 1% per trade\n"
        "├ Daily Drawdown: 3% limit\n"
        "└ Max Loss Streak: 3 trades\n"
        "\n"
        "<b>📌 Commands:</b> /signal | /status | /analytics | /help\n"
        "\n"
        "─────────────────────────\n"
        f"🕐 Started: {now_utc}\n"
        "🤖 <i>Vanguard AI v5.0 · Running 24/7</i>"
    )
    tg_send(CHAT_ID, msg)

# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info("═" * 55)
    log.info("XAUUSDSmartBot v5.0 starting...")
    log.info("═" * 55)

    send_startup_message()

    # Start Telegram polling in background thread
    poll_thread = threading.Thread(target=start_polling, daemon=True)
    poll_thread.start()
    log.info("Telegram polling: active")

    # Run first cycle immediately
    run_auto_cycle()

    # Schedule auto cycle
    schedule.every(SCHEDULE_MINS).minutes.do(run_auto_cycle)
    log.info(f"Scheduler: every {SCHEDULE_MINS} minutes | Active")

    # Keep alive
    while True:
        schedule.run_pending()
        monitor_active_signal()
        time.sleep(10)