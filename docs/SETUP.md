# Manual Setup Guide

For an automated version of steps 1-4, see `install.sh` in the repo root.
This document walks through everything by hand, with the reasoning for each
step — useful if `install.sh` fails on your specific board/OS combination.

## 0. Hardware

- Orange Pi Zero 3 (this guide assumes 4GB RAM; less may not fit the LLM step)
- A USB Audio Class adapter (e.g. UGREEN CM383) with a mic input
- Wired headphones/headset with a TRRS mic plug
- Armbian installed and booted, SSH access available

## 1. System packages

```bash
apt update
apt install -y python3-pip python3-venv sox ffmpeg alsa-utils \
    portaudio19-dev libatlas-base-dev build-essential cmake git fake-hwclock
systemctl enable fake-hwclock
fake-hwclock save
```

## 2. Python environment

```bash
python3 -m venv ~/vosk-env
source ~/vosk-env/bin/activate
pip install vosk sounddevice numpy webrtcvad piper-tts
```

## 3. Verify the USB microphone

```bash
arecord -l
```
You should see your USB adapter listed under CAPTURE devices. Note its card
number (e.g. `hw:3,0`).

```bash
arecord -D plughw:3,0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/test.wav
aplay -D plughw:3,0 /tmp/test.wav
```

If the recording is silent or very quiet:
```bash
alsamixer -c 3   # substitute your card number; press F4 for Capture, raise the level
```

## 4. Download the STT and TTS models

```bash
mkdir -p ~/models && cd ~/models
wget https://alphacephei.com/vosk/models/vosk-model-small-uk-v3-small.zip
unzip vosk-model-small-uk-v3-small.zip
mv vosk-model-small-uk-v3-small /root/

python3 -m piper.download_voices uk_UA-lada-x_low
```

Test each independently before wiring them together — see the inline test
snippets in `scripts/llm_helper.py`'s `__main__` block and the Vosk quick
test below:

```bash
python3 -c "
import wave, json
from vosk import Model, KaldiRecognizer
wf = wave.open('/tmp/test.wav', 'rb')
model = Model('/root/vosk-model-small-uk-v3-small')
rec = KaldiRecognizer(model, wf.getframerate())
while True:
    data = wf.readframes(4000)
    if len(data) == 0: break
    rec.AcceptWaveform(data)
print(json.loads(rec.FinalResult())['text'])
"
```

## 5. LLM (optional, for open-ended questions) {#llm}

This step is optional — the assistant works fine with just `intents.py`
if you don't need open-domain Q&A.

```bash
git clone https://github.com/ggerganov/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build
cmake --build build --config Release -j2 --target llama-cli
```

`-j2` (not `-j4`) avoids out-of-memory kills during compilation on 4GB
boards — see `docs/JOURNEY.md` if the build silently produces no binary.

```bash
mkdir -p ~/llm-models && cd ~/llm-models
wget https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf
```

Test it directly before wiring it into the assistant:
```bash
~/llama.cpp/build/bin/llama-cli -m ~/llm-models/gemma-2-2b-it-Q4_K_M.gguf \
  -p "Привіт! Розкажи коротко, що таке фотосинтез." \
  -n 60 -t 4 --single-turn --simple-io -no-cnv
```
Expect ~30-60 seconds for a short answer on a 4-core ARM CPU without a GPU.

## 6. Copy the scripts

```bash
cp scripts/main.py scripts/intents.py scripts/llm_helper.py /root/
```

Edit the constants at the top of `main.py` if your paths differ
(`MODEL_PATH`, `PIPER_MODEL_PATH`, `USB_DEVICE_ALSA`).

## 7. Run it manually first

```bash
source ~/vosk-env/bin/activate
python3 -u /root/main.py
```

Say something — you should hear a short beep as soon as VAD detects speech,
then (after you pause) either an instant reply for a matched intent, or "one
moment, thinking about that" followed by the LLM's answer.

## 8. Install as a systemd service

```bash
cp systemd/voice-assistant.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now voice-assistant
systemctl status voice-assistant
journalctl -u voice-assistant -f
```

## 9. Optional: status on SSH login

```bash
cp systemd/99-voice-assistant /etc/update-motd.d/
chmod +x /etc/update-motd.d/99-voice-assistant
```

Next SSH login will show the service status, mic/speaker check results, and
the last few recognized phrases/responses.

## Troubleshooting

See `docs/JOURNEY.md` for a detailed account of problems encountered during
development (I²S mic issues, Bluetooth instability, sample rate mismatches,
Netplan conflicts) and how each was diagnosed and resolved.
