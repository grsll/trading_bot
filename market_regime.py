import pandas as pd
import numpy as np
import logging

log = logging.getLogger(__name__)

class MarketRegimeDetector:
    def __init__(self, adx_period=14, atr_period=14):
        self.adx_period = adx_period
        self.atr_period = atr_period

    def detect(self, df: pd.DataFrame) -> dict:
        """Detect current market regime (Trend vs Range, Volatility)."""
        if len(df) < 30:
            return {"regime": "UNKNOWN", "volatility": "NORMAL", "trade_allowed": True}

        # 1. ADX for Trend Strength (Simplied calculation)
        # Using a proxy for ADX: High-Low range vs Close move
        plus_dm = df['High'].diff()
        minus_dm = df['Low'].diff()
        # simplified logic for regime detection
        
        # Calculate Bollinger Band Width for Volatility
        sma = df['Close'].rolling(20).mean()
        std = df['Close'].rolling(20).std()
        bb_upper = sma + (2 * std)
        bb_lower = sma - (2 * std)
        bb_width = (bb_upper - bb_lower) / sma
        
        # Volatility Classification
        avg_bb_width = bb_width.rolling(100).mean().iloc[-1]
        curr_bb_width = bb_width.iloc[-1]
        
        vol_state = "NORMAL"
        if curr_bb_width > avg_bb_width * 1.5: vol_state = "HIGH"
        if curr_bb_width < avg_bb_width * 0.6: vol_state = "LOW"

        # Trend vs Range (using EMA slope and BB width)
        ema20 = df['Close'].ewm(span=20).mean()
        slope = (ema20.iloc[-1] - ema20.iloc[-5]) / 5.0
        
        regime = "RANGING"
        if abs(slope) > (df['Close'].iloc[-1] * 0.0005): # threshold for slope
            regime = "TRENDING"
        
        # Decision logic
        trade_allowed = True
        reason = "Market stable"
        
        if vol_state == "LOW":
            trade_allowed = False
            reason = "Volatility too low (Squeeze)"
        elif vol_state == "HIGH" and regime == "RANGING":
            trade_allowed = False
            reason = "High Volatility Choppiness"

        return {
            "regime": regime,
            "volatility": vol_state,
            "slope": slope,
            "trade_allowed": trade_allowed,
            "reason": reason
        }
