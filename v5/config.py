import os
import yaml
import logging

# XAUUSD Vanguard v5.0 Configuration
CONFIG = {
    "system": {
        "version": "5.0.0",
        "name": "XAUUSD Vanguard",
        "environment": "production",
        "log_level": "INFO"
    },
    "trading": {
        "symbol": "XAUUSD",
        "timeframes": ["15M", "1H", "4H", "1D"],
        "schedule_minutes": 15,
        "session_only": True
    },
    "risk": {
        "max_risk_percent": 1.0,
        "daily_loss_limit": 3.0,
        "min_rr_ratio": 2.0,
        "max_lot_size": 1.0,
        "circuit_breaker": True
    },
    "confluence": {
        "min_score": 5.0,
        "min_ai_confidence": 75,
        "require_regime_alignment": True
    }
}

def load_settings():
    # In a real system, we would load from config/settings.yaml
    return CONFIG
