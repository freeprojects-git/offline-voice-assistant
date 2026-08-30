#!/bin/bash
# Automated setup for steps 1-4 of docs/SETUP.md.
# LLM setup (step 5) is intentionally left manual — it's optional and takes
# a while to compile/download. Run docs/SETUP.md#llm separately if you want it.

set -e

echo "== Installing system packages =="
apt update
apt install -y python3-pip python3-venv sox ffmpeg alsa-utils \
    portaudio19-dev libatlas-base-dev fake-hwclock
systemctl enable fake-hwclock
fake-hwclock save

echo "== Creating Python virtual environment =="
python3 -m venv ~/vosk-env
source ~/vosk-env/bin/activate
pip install vosk sounddevice numpy webrtcvad piper-tts

echo "== Checking for a USB microphone =="
arecord -l || true
echo "If your mic isn't listed above, plug it in now and re-run arecord -l manually."

echo "== Downloading Vosk (Ukrainian, small) model =="
mkdir -p ~/models && cd ~/models
if [ ! -d /root/vosk-model-small-uk-v3-small ]; then
    wget -q https://alphacephei.com/vosk/models/vosk-model-small-uk-v3-small.zip
    unzip -q vosk-model-small-uk-v3-small.zip
    mv vosk-model-small-uk-v3-small /root/
fi

echo "== Downloading Piper (Ukrainian) voice =="
python3 -m piper.download_voices uk_UA-lada-x_low

echo "== Copying scripts to /root =="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/scripts/main.py" "$SCRIPT_DIR/scripts/intents.py" "$SCRIPT_DIR/scripts/llm_helper.py" /root/

echo "== Installing systemd service =="
cp "$SCRIPT_DIR/systemd/voice-assistant.service" /etc/systemd/system/
systemctl daemon-reload

echo "== Installing MOTD status script =="
cp "$SCRIPT_DIR/systemd/99-voice-assistant" /etc/update-motd.d/
chmod +x /etc/update-motd.d/99-voice-assistant

echo ""
echo "Done. Before starting the service:"
echo "  1. (Optional) Set up the LLM fallback — see docs/SETUP.md#llm"
echo "  2. Edit /root/main.py constants if your USB device path differs"
echo "  3. Test manually:  source ~/vosk-env/bin/activate && python3 -u /root/main.py"
echo "  4. Then enable:    systemctl enable --now voice-assistant"
