import sqlite3
import pandas as pd
import logging
from datetime import datetime

log = logging.getLogger(__name__)

class PerformanceAnalytics:
    def __init__(self, db_path="trading_history.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    pair TEXT,
                    bias TEXT,
                    entry REAL,
                    sl REAL,
                    tp1 REAL,
                    tp2 REAL,
                    tp3 REAL,
                    exit_price REAL,
                    pips REAL,
                    profit_usd REAL,
                    result TEXT -- 'WIN', 'LOSS', 'BE'
                )
            """)

    def log_trade(self, trade_data: dict):
        """Log a completed trade to the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO trades (pair, bias, entry, sl, tp1, tp2, tp3, exit_price, pips, profit_usd, result)
                    VALUES (:pair, :bias, :entry, :sl, :tp1, :tp2, :tp3, :exit_price, :pips, :profit_usd, :result)
                """, trade_data)
            log.info(f"Trade logged: {trade_data['result']} | {trade_data['profit_usd']}$")
        except Exception as e:
            log.error(f"Failed to log trade: {e}")

    def get_summary_report(self):
        """Generate a Telegram-formatted performance summary."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                df = pd.read_sql_query("SELECT * FROM trades", conn)
            
            if df.empty:
                return "📈 <b>Performance:</b> No trades recorded yet."

            total_trades = len(df)
            wins = len(df[df['result'] == 'WIN'])
            losses = len(df[df['result'] == 'LOSS'])
            win_rate = (wins / total_trades) * 100
            total_profit = df['profit_usd'].sum()
            avg_profit = df['profit_usd'].mean()
            
            # Simple Max Drawdown calculation
            cum_profit = df['profit_usd'].cumsum()
            running_max = cum_profit.expanding().max()
            drawdown = running_max - cum_profit
            max_dd = drawdown.max()

            return (
                f"📈 <b>V5.0 PERFORMANCE ANALYTICS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Total Net PnL:</b>  ${total_profit:.2f}\n"
                f"🎯 <b>Win Rate:</b>       {win_rate:.1f}%\n"
                f"📊 <b>Total Trades:</b>   {total_trades} (W:{wins}/L:{losses})\n"
                f"📉 <b>Max Drawdown:</b>   ${max_dd:.2f}\n"
                f"⚖️ <b>Avg Trade:</b>      ${avg_profit:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 <i>Institutional Analytics Engine</i>"
            )
        except Exception as e:
            log.error(f"Report generation error: {e}")
            return "❌ Error generating performance report."
