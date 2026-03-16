#!/bin/bash
# PicoGallery / Helles-Galerie — Raspberry Pi setup script
# Run once on a fresh Pi: bash setup.sh

set -e

echo "==> Updating package list..."
sudo apt update -y

echo "==> Installing dependencies..."
sudo apt install -y ffmpeg imagemagick python3 python3-pip

echo "==> Creating app directory..."
mkdir -p ~/PicoGallery
mkdir -p ~/PicoGallery/Chatbot

echo "==> Installing Python dependencies for chatbot..."
pip3 install -r ~/PicoGallery/Chatbot/requirements.txt --break-system-packages

echo "==> Installing systemd services..."
sudo cp picogallery.service /etc/systemd/system/
sudo cp cloudflared.service /etc/systemd/system/
sudo cp chatbot.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "==> Enabling services to start on boot..."
sudo systemctl enable picogallery
sudo systemctl enable cloudflared
sudo systemctl enable chatbot

echo ""
echo "Setup complete. Next steps:"
echo "  1. Copy the binary:   scp bin/picogallery-arm32 admin@<pi-ip>:~/PicoGallery/picogallery"
echo "  2. Copy the tunnel:   scp deploy/start-tunnel.sh admin@<pi-ip>:~/PicoGallery/"
echo "  3. Copy the chatbot:  scp -r Chatbot/ admin@<pi-ip>:~/PicoGallery/Chatbot/"
echo "  4. Start services:    sudo systemctl start picogallery cloudflared chatbot"
