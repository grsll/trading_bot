import logging
import json
import os
from datetime import datetime

log = logging.getLogger(__name__)

class RiskEngine:
    def __init__(self, config_path="risk_config.json"):
        self.config_path = config_path
        self.default_config = {
            "account_balance": 1000.0,
            "risk_per_trade_percent": 1.0,
            "max_daily_loss_percent": 3.0,
            "max_consecutive_losses": 3,
            "min_rr_ratio": 2.0,
            "max_lot_size": 1.0,
            "min_lot_size": 0.01
        }
        self.config = self.load_config()
        self.daily_stats = self.load_stats()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    return {**self.default_config, **json.load(f)}
            except:
                return self.default_config
        return self.default_config

    def load_stats(self):
        stats_path = "daily_stats.json"
        today = datetime.now().strftime("%Y-%m-%d")
        if os.path.exists(stats_path):
            with open(stats_path, 'r') as f:
                stats = json.load(f)
                if stats.get("date") == today:
                    return stats
        return {"date": today, "loss_count": 0, "daily_pnl": 0.0}

    def save_stats(self):
        with open("daily_stats.json", 'w') as f:
            json.dump(self.daily_stats, f)

    def calculate_lot_size(self, entry: float, stop_loss: float) -> float:
        """Calculate lot size based on SL distance and risk amount."""
        sl_dist = abs(entry - stop_loss)
        if sl_dist < 0.1: return self.config["min_lot_size"] # Min SL protection
        
        risk_amount = (self.config["account_balance"] * self.config["risk_per_trade_percent"]) / 100.0
        
        # Standard XAUUSD: $1.00 move = $100 profit/loss for 1.0 lot
        raw_lot = risk_amount / (sl_dist * 100.0)
        
        lot = round(raw_lot, 2)
        return max(self.config["min_lot_size"], min(lot, self.config["max_lot_size"]))

    def check_daily_limits(self) -> tuple[bool, str]:
        """Check if trading should be paused."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_stats["date"] != today:
            self.daily_stats = {"date": today, "loss_count": 0, "daily_pnl": 0.0}
            self.save_stats()

        # Daily loss check
        if self.daily_stats["daily_pnl"] <= -(self.config["account_balance"] * self.config["max_daily_loss_percent"] / 100.0):
            return False, "Daily drawdown limit reached."

        if self.daily_stats["loss_count"] >= self.config["max_consecutive_losses"]:
            return False, f"Maximum consecutive losses ({self.config['max_consecutive_losses']}) reached."

        return True, "Safe"

    def record_trade_result(self, pnl: float):
        """Update daily stats with trade result."""
        self.daily_stats["daily_pnl"] += pnl
        if pnl < 0:
            self.daily_stats["loss_count"] += 1
        else:
            self.daily_stats["loss_count"] = 0
        self.save_stats()
