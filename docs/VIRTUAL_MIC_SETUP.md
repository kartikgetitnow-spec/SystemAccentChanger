# Virtual Microphone Setup Guide

This guide details how to configure a system-level virtual audio loopback cable on **Windows**, **macOS**, and **Linux** so that applications like **Zoom**, **Discord**, **Google Meet**, and **Microsoft Teams** can receive AI-generated speech.

---

## 🧭 How Virtual Cables Work

```
┌────────────────────────────────────────────────────────┐
│               AI Virtual Microphone App                │
│             (Streams synthesized PCM audio)             │
└───────────────────────────┬────────────────────────────┘
                            │ Writes to Playback Endpoint
                            ▼
┌────────────────────────────────────────────────────────┐
│           System Virtual Loopback Device               │
│                                                        │
│  Playback Endpoint (Sink) ───► Recording Endpoint (Source) │
└───────────────────────────┬────────────────────────────┘
                            │ Reads from Recording Endpoint
                            ▼
┌────────────────────────────────────────────────────────┐
│           Communication Apps (Zoom / Discord)          │
│                Selected Microphone Input               │
└────────────────────────────────────────────────────────┘
```

---

## 🪟 Windows Setup: VB-Audio Virtual Cable

1. **Download and Install**:
   - Download the free driver from [VB-Audio Virtual Cable](https://vb-audio.com/Cable/).
   - Extract the `.zip` file, right-click `VBCABLE_Setup_x64.exe`, and select **Run as Administrator**.
   - Click **Install Driver**.
2. **Reboot**:
   - Restart your PC to complete driver registration.
3. **Endpoint Mapping**:
   - **Playback / Output**: Appears in Windows Sound settings as `CABLE Input (VB-Audio Virtual Cable)`.
   - **Recording / Microphone**: Appears as `CABLE Output (VB-Audio Virtual Cable)`.
4. **App Configuration**:
   - In `.env`:
     ```ini
     VIRTUAL_MIC_DEVICE="CABLE Input"
     ```
   - In **Zoom / Discord / Teams**:
     - Go to Audio Settings $\rightarrow$ Microphone $\rightarrow$ select **CABLE Output (VB-Audio Virtual Cable)**.
     - Turn OFF noise suppression and echo cancellation in Zoom/Discord for maximum audio fidelity.

---

## 🍏 macOS Setup: BlackHole (2ch)

1. **Install BlackHole via Homebrew**:
   ```bash
   brew install blackhole-2ch
   ```
   *(Or download the installer pkg from [ExistentialAudio/BlackHole](https://github.com/ExistentialAudio/BlackHole))*
2. **Endpoint Mapping**:
   - BlackHole provides a bidirectional 2-channel audio loopback driver named `BlackHole 2ch`.
3. **App Configuration**:
   - In `.env`:
     ```ini
     VIRTUAL_MIC_DEVICE="BlackHole 2ch"
     ```
   - In **Zoom / Discord / Teams**:
     - Go to Preferences $\rightarrow$ Audio $\rightarrow$ Microphone $\rightarrow$ select **BlackHole 2ch**.
4. **Optional: Multi-Output Device (Monitoring)**:
   - If you also want to hear the AI audio through your headphones simultaneously:
     1. Open **Audio MIDI Setup** (press `Cmd + Space`, search "Audio MIDI Setup").
     2. Click `+` at the bottom left $\rightarrow$ **Create Multi-Output Device**.
     3. Check both **BlackHole 2ch** and your physical headphones/speakers.
     4. Set your headphones as the primary clock source.

---

## 🐧 Linux Setup: PulseAudio / PipeWire Null Sink

On modern Linux distributions running PulseAudio or PipeWire (`pipewire-pulse`), you can instantiate a loopback null sink dynamically using `pactl`.

### 1. Create the Virtual Mic Loopback Device

Run the automated helper command:
```bash
make virtual-mic
# or: bash scripts/setup_virtual_mic.sh
```

Or manually:
```bash
# 1. Create the virtual speaker sink for AI output
pactl load-module module-null-sink sink_name=ai_mic sink_properties=device.description=ai_mic

# 2. Create the virtual microphone source so it appears as a real microphone in Zoom/Discord/Chrome
pactl load-module module-remap-source master=ai_mic.monitor source_name=ai_virtual_mic source_properties=device.description="AI_Virtual_Microphone"
```

### 2. Endpoint Mapping
- **App Output Target**: `ai_mic` (virtual sink)
- **Zoom / Discord / Meet Mic Input**: **`AI_Virtual_Microphone`** (or `ai_virtual_mic` / `ai_mic.monitor`)

### 3. App Configuration
- In `.env`:
  ```ini
  VIRTUAL_MIC_DEVICE="ai_mic"
  ```
- In **Zoom / Discord / Google Meet / System Settings**:
  - Open Audio / Sound Settings $\rightarrow$ Input Device $\rightarrow$ choose **AI_Virtual_Microphone**.

---

## 🔍 Verification Snippet

To verify that your operating system detects the virtual device and check its index, run:

```bash
python -c "import sounddevice as sd; print(sd.query_devices())"
```

You can also use the built-in CLI command:

```bash
python main.py --list-devices
```

Confirm that:
1. Your physical input microphone appears with $> 0$ input channels.
2. Your virtual cable sink (`CABLE Input` / `BlackHole 2ch` / `aivmic` / `ai_mic`) appears with $> 0$ output channels.
