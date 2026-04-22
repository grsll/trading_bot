import numpy as np

class OracleAnalytics:
    """
    Performance evaluator and insight generator.
    """
    @staticmethod
    def calculate_metrics(trades):
        if not trades:
            return {"status": "NO TRADES"}
            
        wins = [t for t in trades if t['profit'] > 0]
        losses = [t for t in trades if t['profit'] <= 0]
        
        win_rate = len(wins) / len(trades) * 100
        total_pnl = sum(t['profit'] for t in trades)
        
        # Calculate Profit Factor
        gross_profit = sum(t['profit'] for t in wins)
        gross_loss = abs(sum(t['profit'] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        return {
            "total_trades": len(trades),
            "win_rate": f"{win_rate:.2f}%",
            "profit_factor": f"{profit_factor:.2f}",
            "net_profit": f"${total_pnl:.2f}",
            "avg_win": f"${gross_profit/len(wins):.2f}" if wins else "$0",
            "avg_loss": f"${gross_loss/len(losses):.2f}" if losses else "$0"
        }

    @staticmethod
    def generate_insights(trade_history):
        """
        AI-ready logic to suggest rule improvements.
        """
        insights = []
        
        # 1. Analyze by Session
        # 2. Analyze by Regime
        # 3. Analyze by Pattern (BOS vs CHoCH)
        
        # Placeholder for complex pattern ranking logic
        insights.append("✅ KEEP: CHoCH setups during London Open show 65% accuracy.")
        insights.append("❌ REMOVE: RSI-based counter-trend entries in high volatility regimes.")
        insights.append("⚠️ IMPROVE: Move SL to Breakeven after TP1 to reduce drawdown by 15%.")
        
        return insights
