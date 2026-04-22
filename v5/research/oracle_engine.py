import pandas as pd
import numpy as np
import logging

log = logging.getLogger(__name__)

class BacktestEngine:
    """
    Vanguard Oracle: Step-by-step historical replay engine.
    """
    def __init__(self, symbol, initial_balance=10000, spread_pips=2.0):
        self.symbol = symbol
        self.balance = initial_balance
        self.equity = initial_balance
        self.spread = spread_pips
        self.trades = []
        self.current_step = 0
        
    def simulate_execution(self, entry_price, sl, tp, lot_size, bias):
        """Simulate trade execution with spread and slippage."""
        # Add spread to entry for Longs, subtract for Shorts
        real_entry = entry_price + (self.spread / 10.0) if bias == "BULLISH" else entry_price - (self.spread / 10.0)
        
        trade = {
            "entry_time": None,
            "entry_price": real_entry,
            "sl": sl,
            "tp": tp,
            "lot": lot_size,
            "bias": bias,
            "status": "OPEN",
            "pips": 0,
            "profit": 0
        }
        return trade

    def run_replay(self, df_15m, df_1h, strategy_func):
        """
        Step through history and trigger strategy logic.
        """
        results = []
        # We start from bar 100 to have enough history for indicators
        for i in range(100, len(df_15m)):
            # Slice the data to 'hide' the future
            historical_slice_15m = df_15m.iloc[:i]
            # Find the corresponding 1H bar
            current_time = df_15m.index[i]
            historical_slice_1h = df_1h[df_1h.index <= current_time]
            
            # Execute strategy on this 'moment in time'
            signal = strategy_func(historical_slice_15m, historical_slice_1h)
            
            if signal:
                log.info(f"Oracle Signal at {current_time}: {signal['bias']}")
                results.append(signal)
                
        return results
