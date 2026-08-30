
# The Journey: Problems, Dead Ends, and What Actually Worked

This is the real debugging history behind this project — including the parts
that didn't work, because those are usually the most useful part of a
write-up like this.

## 1. I²S microphone: audible but unrecognizable

**Setup:** INMP441 (digital mic) + MAX98357A (amp), both on I²S, wired to
PH6/PH7/PH8/PH9.

**Symptom:** `arecord` produced a WAV file that a human could listen to and
understand — but neither Whisper nor Vosk could transcribe it. The same Vosk
setup transcribed a synthetic `espeak-ng` recording perfectly.

**What we ruled out:**
- Model choice (tested Whisper tiny/base/small, Vosk small) — none worked on
  the real recording, all worked on synthetic audio
- Bit-depth/shift errors — tried 9 different shift values, no improvement
- Pinout — confirmed via `pinctrl` debugfs that PH5–PH9 were correctly
  mapped to the `i2s3` function

**What looked suspicious:** an oscilloscope reading of the WS (word select)
line showed ~96 kHz instead of the expected 48 kHz. This turned out to be
a **measurement artifact**, not a real clocking bug — measuring between
adjacent edges instead of same-polarity edges (rising→rising) doubles the
apparent frequency.

**Outcome:** the root cause (likely a device-tree overlay/DAI-format
mismatch) was never conclusively found in reasonable time. Rather than keep
debugging a hardware layer indefinitely, we pivoted to USB audio — see next
section. If you want to pick up where we left off: check the
`simple-audio-card`/`audio-graph-card` overlay's `dai-format` setting
against what INMP441 expects (standard I2S, one bit-clock delay).

## 2. Pivot to USB audio

Bought a UGREEN CM383 (USB Audio Class adapter) and a cheap wired headset
with a TRRS mic. Zero device-tree work needed — Linux sees it as a normal
ALSA capture device immediately.

```bash
arecord -l
arecord -D plughw:3,0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/test.wav
```

First test transcribed correctly ("один два три чотири п'ять") on the very
first try. **Gotcha:** initial recording level was far too low
(`RMS amplitude: 0.000023`) — fixed by raising capture gain to maximum in
`alsamixer -c <card> -> F4 (Capture)`.

**Lesson:** don't assume a "better", more integrated interface (I²S) is the
right choice for a given board/OS combination. USB Audio Class's ubiquity
and lack of configuration surface made it strictly more reliable here.

## 3. VAD + Vosk sample rate mismatch

`webrtcvad` and PortAudio (via `sounddevice`) on this USB chip refused to
open a stream at 16 kHz directly (`Invalid sample rate` from PortAudio) —
the chip only accepted 48 kHz through this path. Fixed by recording at
48 kHz and resampling to 16 kHz right before feeding Vosk:

```python
import audioop
resampled, _ = audioop.ratecv(raw_audio, 2, 1, 48000, 16000, None)
```

## 4. Intent matching missed inflected verb forms

Vosk output "вимкнути", "вимкнули", "вимкни" depending on how the phrase
was spoken. A rigid regex like `r"вимкни (світло|лампу)"` only matched one
form. Fixed with a wildcard on the stem: `r"вимкн\w* (світло|лампу)"`.

## 5. Static IP: two "192.168.1.1" routers

Two different physical Wi-Fi routers on the same network both defaulted to
`192.168.1.1`, causing real confusion about which network the Ethernet vs.
Wi-Fi interface was actually on. Diagnosed with:

```bash
ip route show default
```

which showed two `default via 192.168.1.1` routes on different interfaces
— they were, in fact, different physical networks that happened to share a
gateway address.

**Netplan gotcha:** Ethernet was managed by `systemd-networkd` via Netplan,
not NetworkManager (`nmcli` showed it as "unmanaged"). A new Netplan file
with a narrower `match` (`name: "eth*"` instead of `name: "e*"`) was needed
to stop it clashing with Armbian's default DHCP-for-all-ethernet config —
simply naming the new file to sort alphabetically after the old one wasn't
enough, because Netplan generates `.network` filenames based on the device
match, not the source YAML filename.

## 6. LLM: speed vs. quality trade-off

Compared three GGUF models (Q4_K_M) via `llama.cpp` on this ARM CPU:

| Model | Size | Generation speed | Ukrainian quality |
|---|---|---|---|
| Qwen2.5-0.5B | ~400MB | 3.5 tok/s | Incoherent |
| Qwen2.5-1.5B | ~1GB | 1.3 tok/s | Worse than Gemma |
| Gemma-2-2B | ~1.6GB | 0.8–1.3 tok/s | Best, chosen |

A short answer (~60-80 tokens) from Gemma-2-2B takes 30-60 seconds. We
accepted this trade-off and added a spoken "one moment, thinking about
that" cue so the user knows the system hasn't frozen, rather than chasing
speed at the cost of coherent output.

**System time drift:** no RTC battery on this board means the clock resets
on power loss, which broke `wget`'s TLS certificate validation ("certificate
not yet valid") when downloading the model. Fixed with `fake-hwclock`.

## 7. Bluetooth audio: partial success, then a wall

Wanted to output audio via a Bluetooth speaker (JBL Charge 2+).

- **PulseAudio in system mode:** Bluetooth module refused to load
  (`Failed to initialize module`) — a known limitation of running PulseAudio
  as root/system-wide with Bluetooth's D-Bus requirements.
- **Switched to PipeWire + WirePlumber:** A2DP output worked immediately and
  reliably (`paplay` routed cleanly to the speaker).
- **Tried adding Bluetooth mic input (HFP profile) on the same speaker:**
  every real recording attempt crashed both `pipewire` and `wireplumber`
  outright (`mSBC buffer overrun`, dropped connections), reproducibly,
  across multiple attempts and both mSBC and CVSD codecs.

**Decision:** stopped pursuing Bluetooth mic input. It's a genuinely less
stable path on this board/software combination than USB, and continuing to
experiment risked destabilizing an already-working system. Went back to a
USB-only setup (mic + speaker on the same adapter) as the final, stable
architecture. Bluetooth *output only* (A2DP, via PipeWire) does work, if you
want to revisit it — just don't try to record from the same Bluetooth
device's mic without expecting instability.

**Also discovered along the way:** running the LLM call directly inside
the `sounddevice` audio callback blocked the whole audio pipeline for the
full 30-60s LLM response time, eventually throwing a
`subprocess.TimeoutExpired` that `sounddevice` silently swallowed. Moving it
to a background thread fixed the freeze but introduced overlapping
responses when a second utterance was captured while the first was still
being answered — worth adding a proper "busy" lock if you go this route.

## Summary of what's stable vs. experimental

| Component | Status |
|---|---|
| USB mic + speaker (same adapter) | ✅ Stable, recommended |
| I²S mic (INMP441) | ❌ Unresolved ASR issue, not recommended as-is |
| Bluetooth output (A2DP) | ✅ Works via PipeWire |
| Bluetooth input (HFP) | ❌ Crashes the audio stack, avoid |
| LLM fallback in the main audio callback | ⚠️ Works but blocks the pipeline; consider threading + a busy-lock |
