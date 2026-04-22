#!/bin/bash
# ============================================================
#  Install XAUUSD Bot as systemd service (auto-start on boot)
# ============================================================

set -e
BOT_DIR="/home/huawei/bot"
SERVICE_NAME="xauusd-bot"

echo "🚀 Installing XAUUSD Smart Bot service..."

# 1. Copy service file
sudo cp "$BOT_DIR/xauusd-bot.service" /etc/systemd/system/

# 2. Reload systemd
sudo systemctl daemon-reload

# 3. Enable service (auto-start on boot)
sudo systemctl enable $SERVICE_NAME

# 4. Start service now
sudo systemctl start $SERVICE_NAME

# 5. Show status
echo ""
echo "✅ Service installed successfully!"
echo ""
sudo systemctl status $SERVICE_NAME --no-pager -l
echo ""
echo "📌 Useful commands:"
echo "  sudo systemctl status  $SERVICE_NAME   # cek status"
echo "  sudo systemctl restart $SERVICE_NAME   # restart bot"
echo "  sudo systemctl stop    $SERVICE_NAME   # stop bot"
echo "  sudo journalctl -u     $SERVICE_NAME -f # live logs"
echo "  tail -f $BOT_DIR/xauusd_bot.log         # file logs"
