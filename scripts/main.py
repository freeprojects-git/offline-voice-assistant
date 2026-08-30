"""
Offline Voice Assistant — main loop.

Pipeline: USB microphone -> VAD -> Vosk (STT) -> intents / LLM fallback -> Piper (TTS) -> USB speaker

See docs/SETUP.md for installation instructions and docs/JOURNEY.md for the
full debugging story behind the design decisions in this file.
"""

import sounddevice as sd
import webrtcvad
import numpy as np
import audioop
import subprocess
import time
import json
import wave
from datetime import datetime
from vosk import Model, KaldiRecognizer, SetLogLevel
from piper import PiperVoice
from intents import match_intent
from llm_helper import ask_llm

SetLogLevel(-1)

# --- Audio config ---
SR = 48000
TARGET_SR = 16000
FRAME_MS = 30
FRAME_SIZE = int(SR * FRAME_MS / 1000)
vad = webrtcvad.Vad(0)  # 0 = least aggressive; tune per your mic's noise floor

SILENCE_FRAMES_TO_STOP = 20  # ~0.6s of silence ends the utterance
SPEECH_FRAMES_TO_START = 6   # ~0.18s of speech starts recording

# --- Paths ---
MODEL_PATH = "/root/vosk-model-small-uk-v3-small"
PIPER_MODEL_PATH = "/root/uk_UA-lada-x_low.onnx"
RECORD_PATH = "/tmp/vad-capture.wav"
TTS_PATH = "/tmp/say.wav"
USB_DEVICE_ALSA = "plughw:3,0"  # adjust to your `arecord -l` output
EVENT_LOG_PATH = "/root/voice-assistant-events.log"


def log_event(text):
    """Append a timestamped line to the event log, used by the MOTD status script."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {text}\n"
    with open(EVENT_LOG_PATH, "a") as f:
        f.write(line)
    with open(EVENT_LOG_PATH, "r") as f:
        lines = f.readlines()
    if len(lines) > 50:
        with open(EVENT_LOG_PATH, "w") as f:
            f.writelines(lines[-50:])


def find_usb_input_device():
    """Find the USB audio input device by name, regardless of its index."""
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if "USB" in d['name'] and d['max_input_channels'] > 0:
            return i
    raise RuntimeError(
        "USB microphone not found — check that a device WITH a microphone "
        "(not just headphones) is plugged in."
    )


USB_DEVICE_SD = find_usb_input_device()

print("Loading Vosk model...", flush=True)
model = Model(MODEL_PATH)

print("Loading Piper voice...", flush=True)
voice = PiperVoice.load(PIPER_MODEL_PATH)

# Short acknowledgment beep played the instant VAD detects speech —
# feels instant, unlike a synthesized word which takes time to generate.
ACK_SOUND_PATH = "/tmp/ack.wav"
_ack_sr = 16000
_ack_duration = 0.15
_t = np.linspace(0, _ack_duration, int(_ack_sr * _ack_duration), False)
_ack_tone = (np.sin(2 * np.pi * 880 * _t) * 0.4 * 32767).astype(np.int16)
with wave.open(ACK_SOUND_PATH, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(_ack_sr)
    wf.writeframes(_ack_tone.tobytes())


def check_microphone():
    print("Checking microphone...", flush=True)
    try:
        test_audio = sd.rec(int(0.5 * SR), samplerate=SR, channels=1,
                             dtype='float32', device=USB_DEVICE_SD)
        sd.wait()
        level = float(np.abs(test_audio).mean())
        if level < 1e-6:
            print(f"WARNING: microphone reads zero signal (level: {level})", flush=True)
            return False
        print(f"OK: microphone working (signal level: {level:.6f})", flush=True)
        return True
    except Exception as e:
        print(f"ERROR: microphone check failed: {e}", flush=True)
        return False


def check_speaker():
    print("Checking speaker...", flush=True)
    try:
        test_path = "/tmp/speaker_check.wav"
        tone_sr = 16000
        duration = 0.3
        t = np.linspace(0, duration, int(tone_sr * duration), False)
        tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
        with wave.open(test_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(tone_sr)
            wf.writeframes(tone.tobytes())
        result = subprocess.run(["aplay", "-D", USB_DEVICE_ALSA, test_path],
                                 capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print(f"ERROR: speaker check failed: {result.stderr}", flush=True)
            return False
        print("OK: speaker working", flush=True)
        return True
    except Exception as e:
        print(f"ERROR: speaker check failed: {e}", flush=True)
        return False


# Example intents — extend RESPONSES / intents.py with real integrations
# (smart home API calls, weather API, system clock, etc.)
RESPONSES = {
    "weather": "I'm not connected to a weather service yet.",
    "light_on": "Turning on the light.",
    "light_off": "Turning off the light.",
    "time": "I can't check the clock right now, sorry.",
    "greeting": "Hi! How can I help?",
}

speech_streak = 0
silence_streak = 0
is_recording = False
buffer = []


def speak(text):
    print(">>> Speaking:", text, flush=True)
    log_event(f"Speaking: {text}")
    with wave.open(TTS_PATH, "wb") as wf:
        voice.synthesize_wav(text, wf)
    subprocess.run(["aplay", "-D", USB_DEVICE_ALSA, TTS_PATH])


def handle_intent(name):
    speak(RESPONSES.get(name, "I didn't understand that command."))


def handle_fallback_llm(question):
    print(">>> No matching intent, asking the LLM (may take up to a minute)...", flush=True)
    speak("One moment, thinking about that.")
    answer = ask_llm(question)
    speak(answer)


def save_and_recognize(frames_int16, samplerate):
    raw = b"".join(frames_int16)
    resampled, _ = audioop.ratecv(raw, 2, 1, samplerate, TARGET_SR, None)

    with wave.open(RECORD_PATH, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SR)
        wf.writeframes(resampled)

    rec = KaldiRecognizer(model, TARGET_SR)
    with wave.open(RECORD_PATH, "rb") as wf:
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            rec.AcceptWaveform(data)
    result = json.loads(rec.FinalResult()).get("text", "")

    if result:
        print(">>> Recognized:", result, flush=True)
        log_event(f"Recognized: {result}")
        intent = match_intent(result)
        if intent:
            handle_intent(intent)
        else:
            handle_fallback_llm(result)
    else:
        print(">>> (nothing recognized)", flush=True)


def callback(indata, frames, time_, status):
    global speech_streak, silence_streak, is_recording, buffer

    pcm16 = (indata[:, 0] * 32767).astype(np.int16)
    pcm16_bytes = pcm16.tobytes()
    is_speech = vad.is_speech(pcm16_bytes, SR)

    if is_speech:
        speech_streak += 1
        silence_streak = 0
    else:
        silence_streak += 1
        speech_streak = 0

    if not is_recording and speech_streak >= SPEECH_FRAMES_TO_START:
        is_recording = True
        buffer = []
        print(">>> Listening...", flush=True)
        log_event("Listening...")
        subprocess.Popen(["aplay", "-D", USB_DEVICE_ALSA, ACK_SOUND_PATH],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if is_recording:
        buffer.append(pcm16_bytes)

    if is_recording and silence_streak >= SILENCE_FRAMES_TO_STOP:
        is_recording = False
        # NOTE: this call blocks the audio callback for as long as recognition
        # (and, if triggered, the LLM fallback) takes. This is a deliberate
        # simplification — see docs/JOURNEY.md for why running it in a thread
        # caused overlapping responses without extra locking.
        save_and_recognize(buffer, SR)
        buffer = []


mic_ok = check_microphone()
speaker_ok = check_speaker()
log_event(f"Microphone: {'OK' if mic_ok else 'FAILED'}")
log_event(f"Speaker: {'OK' if speaker_ok else 'FAILED'}")

if not mic_ok:
    print("CRITICAL: microphone not working, check the connection", flush=True)
if not speaker_ok:
    print("CRITICAL: speaker not working, check the connection", flush=True)

print("Voice assistant started. Ctrl+C to exit.", flush=True)
log_event("Voice assistant started")
speak("I'm ready to listen.")

with sd.InputStream(samplerate=SR, blocksize=FRAME_SIZE, channels=1,
                     dtype='float32', callback=callback, device=USB_DEVICE_SD):
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Exiting", flush=True)
