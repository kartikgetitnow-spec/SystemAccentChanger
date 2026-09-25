#!/usr/bin/env bash
set -e

echo "Setting up AI Virtual Microphone on Linux (PulseAudio / PipeWire)..."

# 1. Check and load null sink (where our app writes audio)
if pactl list short sinks | grep -q "ai_mic"; then
    echo "✔ Sink 'ai_mic' is already active."
else
    echo "Loading module-null-sink 'ai_mic'..."
    pactl load-module module-null-sink sink_name=ai_mic sink_properties=device.description=ai_mic
    echo "✔ Sink 'ai_mic' loaded."
fi

# 2. Check and load remapped source (so it appears as a real microphone in Zoom/Discord/Settings)
if pactl list short sources | grep -q "ai_virtual_mic"; then
    echo "✔ Microphone 'AI_Virtual_Microphone' is already active."
else
    echo "Loading module-remap-source 'ai_virtual_mic'..."
    pactl load-module module-remap-source \
        master=ai_mic.monitor \
        source_name=ai_virtual_mic \
        source_properties="device.description=AI_Virtual_Microphone"
    echo "✔ Microphone 'AI_Virtual_Microphone' created."
fi

echo ""
echo "================================================================="
echo "✅ SUCCESS: Virtual microphone is ready!"
echo "• Our application writes to sink: 'ai_mic'"
echo "• In Zoom, Discord, Google Meet, or Chrome:"
echo "  Choose 'AI_Virtual_Microphone' (or 'ai_virtual_mic') as your mic!"
echo "================================================================="
