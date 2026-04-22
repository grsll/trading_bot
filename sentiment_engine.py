import logging
import re

log = logging.getLogger(__name__)

class SentimentEngine:
    def __init__(self, ai_model):
        self.ai_model = ai_model
        # High impact keywords for Gold
        self.high_impact = ["CPI", "NFP", "FOMC", "Fed", "Interest Rate", "Inflation", "Payroll", "War", "Geopolitical"]

    def analyze_news(self, news_items: list) -> dict:
        """Classify and score news sentiment for XAUUSD."""
        if not news_items:
            return {"sentiment": "NEUTRAL", "score": 0, "impact": "LOW", "trade_block": False}

        headlines = "\n".join([f"- {n['title']}" for n in news_items])
        
        prompt = f"""
Analyze these news headlines for XAUUSD (Gold) impact:
{headlines}

Assign a sentiment score from -10 (Very Bearish) to +10 (Very Bullish).
Identify if any headlines contain high-impact events: {', '.join(self.high_impact)}.

Respond ONLY in this format:
SCORE: [number]
IMPACT: [LOW/MEDIUM/HIGH]
EVENT: [True/False]
REASON: [1 sentence]
"""
        try:
            resp = self.ai_model.generate_content(prompt).text.strip()
            
            score = int(re.search(r"SCORE:\s*(-?\d+)", resp).group(1))
            impact = re.search(r"IMPACT:\s*(\w+)", resp).group(1)
            is_event = "True" in re.search(r"EVENT:\s*(\w+)", resp).group(1)
            
            sentiment = "NEUTRAL"
            if score >= 3: sentiment = "BULLISH"
            if score <= -3: sentiment = "BEARISH"
            
            # Block trading if high impact event is detected in news
            trade_block = (impact == "HIGH" and is_event)

            return {
                "sentiment": sentiment,
                "score": score,
                "impact": impact,
                "trade_block": trade_block,
                "reason": re.search(r"REASON:\s*(.*)", resp).group(1)
            }
        except Exception as e:
            log.error(f"Sentiment analysis error: {e}")
            return {"sentiment": "NEUTRAL", "score": 0, "impact": "LOW", "trade_block": False, "reason": str(e)}
