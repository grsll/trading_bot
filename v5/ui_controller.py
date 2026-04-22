import requests
import json
import logging

log = logging.getLogger(__name__)

class VanguardUI:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.url = f"https://api.telegram.org/bot{token}"

    def send_menu(self):
        """Send the main control menu with interactive buttons."""
        text = (
            "⚙️ <b>Vanguard v5.0 Control Panel</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "System: 🟢 ACTIVE\n"
            "Mode:   🤖 AUTO\n"
            "Risk:   🛡️ PROTECTED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Select a command below:"
        )
        
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🔍 Force Scan", "callback_data": "scan_now"},
                    {"text": "📊 Status", "callback_data": "get_status"}
                ],
                [
                    {"text": "🤖 Auto: ON", "callback_data": "toggle_auto"},
                    {"text": "📰 Sentiment", "callback_data": "get_news"}
                ],
                [
                    {"text": "📉 Analytics", "callback_data": "get_analytics"},
                    {"text": "🛑 Panic Stop", "callback_data": "panic_stop"}
                ]
            ]
        }
        
        return self._send_request("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        })

    def send_signal(self, msg, photo_path=None):
        """Send a rich signal card."""
        if photo_path:
            return self._send_request("sendPhoto", {
                "chat_id": self.chat_id,
                "photo": open(photo_path, 'rb'),
                "caption": msg,
                "parse_mode": "HTML"
            })
        return self._send_request("sendMessage", {
            "chat_id": self.chat_id,
            "text": msg,
            "parse_mode": "HTML"
        })

    def _send_request(self, method, data):
        try:
            r = requests.post(f"{self.url}/{method}", data=data, timeout=15)
            return r.json()
        except Exception as e:
            log.error(f"UI Error: {e}")
            return None
