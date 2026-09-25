# 🎙️ System Accent Changer - AI Virtual Microphone Studio

A real-time Linux audio engine and virtual microphone powered by **Google Gemini Live Speech-to-Speech (STS)**. It captures your speech from your physical microphone, removes hardware noise and DC bias, streams audio to Gemini Live to convert accent, tone, and grammar in real time, and feeds the synthesized voice into a virtual system microphone.

Any application that uses a microphone (such as **Google Meet**, **Zoom**, **Discord**, **Slack**, or **OBS**) can select the virtual microphone to receive the converted audio.

---

## 🌟 Key Features

- **Real-Time Gemini Live STS**: Bidirectional audio streaming over WebSocket with ultra-low latency using `gemini-3.1-flash-live-preview`.
- **Accent & Tone Rewriting**: Convert regional accents (e.g. Indian, Chinese, Spanish) to target accents (American, British, Australian, etc.) with customizable registers (`professional`, `friendly`, `casual`, `formal`, `empathetic`).
- **Verbatim Relay Mode**: Option to relay speech directly with clean native pronunciation without altering grammar or words.
- **Hardware DC Blocker & Voice Activity Detection (VAD)**: Intelligent energy-based noise gate with automatic DC offset filtering for laptop analog microphones.
- **Virtual PipeWire / PulseAudio Routing**: Creates a virtual audio sink and remapped source compatible with all Linux desktop environments.
- **Auto-reconnection**: Gracefully recovers from WebSocket idle timeouts or network interruptions.

---

## 🏗️ Architecture

```text
[ Physical Microphone ] ──► (alsa_input / hw mic)
           │
           ▼
[ Hardware DC Blocker & NoiseGate ] ──► (Filters hum & DC offset)
           │
           ▼
[ Gemini Live STS Client ] ──► (WebSocket Bidirectional Audio)
           │                   - Transcribes input intent
           │                   - Rewrites tone / accent
           │                   - Synthesizes 24kHz audio
           ▼
[ Resampler (24kHz → 48kHz) ]
           │
           ▼
[ Virtual Null Sink ] ──────► ('US_Voice_Sink' or 'ai_mic')
           │
           ▼
[ Remapped Virtual Source ] ─► ('US_Voice_Mic' / 'AI_Virtual_Microphone')
           │
           ▼
[ Video Call / Apps ] ──────► (Zoom, Google Meet, Discord, Slack)
```

---

## 📋 Prerequisites

### 1. System Audio Libraries (Linux / Ubuntu / Debian)
This project uses `sounddevice` which relies on PortAudio:

```bash
sudo apt update
sudo apt install -y portaudio19-dev libasound2-dev pulseaudio-utils
```

### 2. Python Environment
Python 3.10 or newer (tested up to Python 3.14).

---

## ⚙️ Installation & Setup

### Step 1: Create and Activate Virtual Environment

```bash
# Clone the repository
git clone git@github.com:kartikgetitnow-spec/SystemAccentChanger.git
cd SystemAccentChanger

# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate
```

*(On Windows: `.venv\Scripts\activate`)*

### Step 2: Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 🔐 Environment Configuration (`.env`)

> [!WARNING]
> **Never commit your `.env` file to Git!** It contains your secret Google Gemini API key. The repository's `.gitignore` is pre-configured to ignore `.env`.

Create your private `.env` file from the provided template:

```bash
cp .env.example .env
```

Open `.env` in an editor and configure the parameters:

```env
# ------------------------------------------------------------------------------
# 1. Gemini API Credentials
# ------------------------------------------------------------------------------
# Obtain your free API key at: https://aistudio.google.com/app/apikey
GEMINI_API_KEY=your_gemini_api_key_here

# Gemini Live bidirectional model
GEMINI_MODEL=gemini-3.1-flash-live-preview
GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview

# Voice name: Puck | Charon | Aoede | Fenrir | Kore
GEMINI_VOICE=Puck

# ------------------------------------------------------------------------------
# 2. Audio Hardware & Virtual Devices
# ------------------------------------------------------------------------------
# Physical microphone name or substring.
# Setting 'alsa_input' binds directly to your real mic and prevents loopbacks.
INPUT_DEVICE=alsa_input
OUTPUT_DEVICE=default

# Virtual sink device where output is written
VIRTUAL_MIC_DEVICE=US_Voice_Sink

# ------------------------------------------------------------------------------
# 3. Stream & Format
# ------------------------------------------------------------------------------
SAMPLE_RATE=16000
VIRTUAL_MIC_SAMPLE_RATE=48000
CHANNELS=1
CHUNK_SIZE=2048

# ------------------------------------------------------------------------------
# 4. Accent & Tone Conversion Settings
# ------------------------------------------------------------------------------
ENABLE_ACCENT_CONVERSION=true
TARGET_ACCENT=american       # american | british | australian | indian | neutral
SOURCE_ACCENT=indian         # indian | chinese | spanish | french | etc.
ACCENT_MODE=professional     # professional | friendly | casual | formal | empathetic
TARGET_LANGUAGE=english

# ------------------------------------------------------------------------------
# 5. Noise Gate & Logging
# ------------------------------------------------------------------------------
ENABLE_NOISE_GATE=true
NOISE_GATE_THRESHOLD_DB=10.0
NOISE_GATE_HANGOVER_MS=300.0
LOG_LEVEL=INFO
```

---

## 🎛️ Setting Up the Virtual Microphone on Linux

To create the virtual audio devices in PulseAudio / PipeWire, run the setup script:

```bash
chmod +x scripts/setup_virtual_mic.sh
./scripts/setup_virtual_mic.sh
```

Or execute manually:
```bash
# 1. Create the null sink
pactl load-module module-null-sink sink_name=US_Voice_Sink sink_properties=device.description=US_Voice_Sink

# 2. Create the virtual microphone source
pactl load-module module-remap-source master=US_Voice_Sink.monitor source_name=US_Voice_Mic source_properties="device.description=US_Voice_Mic"
```

To verify the audio devices are active:
```bash
python main.py --list-devices
```

---

## 🚀 Running the Project

### 1. Dry-Run Mode (Test Without API)
Echoes your microphone directly to the virtual microphone without calling the Gemini API. Useful for testing latency and audio loopback:

```bash
python main.py --dry-run
```

### 2. Live AI Accent Conversion Mode
Starts the real-time AI conversion pipeline:

```bash
python main.py
```

### 3. CLI Overrides & Options

You can override any `.env` setting directly from the command line:

```bash
# Convert to British accent with a friendly tone
python main.py --target-accent british --mode friendly

# Verbatim speech relay (keep original words, speak clearly with target accent)
python main.py --no-accent-conversion

# Use a specific Gemini voice
python main.py --voice Charon

# Pass a custom system instruction prompt
python main.py --system-prompt "You are a voice translator. Translate Spanish speech to clear English."

# View available audio devices
python main.py --list-devices
```

---

## 🎧 Using in Apps (Zoom, Google Meet, Discord, etc.)

1. Open **Google Meet**, **Zoom**, **Discord**, or system settings.
2. In the **Audio / Input Device** dropdown, select:
   - **`US_Voice_Mic`** (or **`AI_Virtual_Microphone`**).
3. Set your **Output Device (Speakers/Headphones)** to your physical headphones.
4. Speak normally into your physical microphone — participants will hear your voice transformed in real time!

---

## ❓ Troubleshooting & FAQs

### Why do I hear a double voice / echo?
- **Acoustic Feedback**: If your speakers are on, your laptop microphone will pick up Gemini's converted voice and send it back to Gemini, causing a repeating echo. **Always wear headphones when testing.**
- **Mic Monitoring / Sidetone**: Make sure your operating system or meeting app does not have "Listen to this device" or mic sidetone turned on.
- **Multiple Pipeline Instances**: Ensure only one instance of `main.py` is running (`pkill -f "python main.py"`).

### Why does Gemini disconnect with Error 1008 (`The operation was aborted`)?
- Gemini Live closes idle WebSocket sessions if no audio is received for ~2.5 minutes.
- The pipeline automatically reconnects. To avoid long idle disconnects, ensure `INPUT_DEVICE` points to your real microphone (`INPUT_DEVICE=alsa_input`) so your speech is detected.

### How do I stop the pipeline?
Press `Ctrl + C` in the terminal to trigger a clean shutdown of all audio streams.

---

## 📜 License

MIT License. Free for personal and commercial use.
