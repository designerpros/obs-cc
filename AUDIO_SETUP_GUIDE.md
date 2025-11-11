# Audio Capture Setup Guide

## The Problem

**IMPORTANT**: The OBS Python plugin cannot directly capture audio from OBS due to API limitations. To enable contextual news matching, you need to set up audio transcription separately.

## Initial Context Behavior

When you first start streaming **without audio capture configured**:

1. ❌ **No transcription context** - The system has nothing to match against
2. 📰 **Generic news** - Returns top headlines from news sources (not contextually matched)
3. ⚠️ **Warning in logs** - Backend will show: "Audio capture disabled - context matching will use generic news!"

After ~30 seconds with audio capture enabled:
1. ✅ **Transcription builds up** - AssemblyAI transcribes your speech
2. 🎯 **Context matching activates** - Claude matches news to your content
3. 📊 **Relevant headlines** - Top 4 contextually relevant news items appear

## Audio Capture Options

Choose one of these methods to enable transcription:

### Option 1: System Audio Capture (Recommended - Best Quality)

Captures audio directly from your system audio device.

**Pros:**
- ✅ Real-time, lowest latency
- ✅ Best audio quality
- ✅ No file I/O overhead
- ✅ Works with any streaming software

**Cons:**
- ❌ Requires sounddevice library
- ❌ Need to configure correct audio device

**Setup:**

1. Install audio library:
   ```bash
   pip install sounddevice
   ```

2. Find your audio device:
   ```bash
   python -m backend.audio_capture
   ```

   This will list all audio devices, for example:
   ```
   0: Microphone (Realtek High Definition Audio)
   1: Stereo Mix (Realtek High Definition Audio)  ← Use this for desktop audio
   2: Line In (Realtek High Definition Audio)
   ```

3. Update `config.json`:
   ```json
   {
     "audio_capture": {
       "mode": "system",
       "device": 1,          // Use device number from list (or null for default)
       "sample_rate": 48000,
       "channels": 2
     }
   }
   ```

4. Start backend:
   ```bash
   ./run_backend.sh
   ```

**Troubleshooting:**

- **"No input device found"**: Enable "Stereo Mix" or similar in Windows Sound Settings
- **No audio captured**: Try different device numbers from the list
- **MacOS**: Use BlackHole or Loopback for virtual audio routing
- **Linux**: Use PulseAudio monitor device

---

### Option 2: File-Based Monitoring (Easiest - No Extra Software)

OBS writes audio to a file, backend monitors and transcribes it.

**Pros:**
- ✅ Simple setup
- ✅ No additional libraries
- ✅ Works on all platforms

**Cons:**
- ❌ ~1-2 second latency
- ❌ Requires OBS audio output configuration
- ❌ File I/O overhead

**Setup:**

1. **In OBS Studio:**
   - Go to **Settings → Audio**
   - Under **Advanced**, set a **Monitoring Device**
   - Right-click your **Desktop Audio** source
   - Select **Advanced Audio Properties**
   - Set **Audio Monitoring** to "Monitor and Output"

2. **Install OBS Audio Sync plugin** (Optional but recommended):
   - Download: [OBS Audio Sync](https://obsproject.com/forum/resources/audio-sync-fix.1157/)
   - Or use built-in audio output to file

3. **Configure OBS to output audio to file:**

   **Method A: Using Audio Output Capture**
   - Add **Source → Audio Output Capture**
   - Select your audio device
   - In source properties, enable "Capture audio to file"
   - Set output path: `/path/to/obs-cc/obs_audio.wav`

   **Method B: Using FFmpeg Recording**
   - Add a separate recording output
   - Format: WAV, 48kHz, Stereo
   - Configure to continuously overwrite the same file

4. **Update config.json:**
   ```json
   {
     "audio_capture": {
       "mode": "file",
       "audio_file_path": "./obs_audio.wav"  // Path to OBS audio output
     }
   }
   ```

5. Start backend:
   ```bash
   ./run_backend.sh
   ```

**Troubleshooting:**

- **File not found**: Check the path in config.json matches OBS output
- **Permission denied**: Ensure the backend has read access to the file
- **High latency**: Use RAW/WAV format, not compressed formats

---

### Option 3: Manual Test Mode (For Testing Without Streaming)

Disable audio capture and manually provide context via API.

**Use case:** Testing the system without actually streaming

**Setup:**

1. Update `config.json`:
   ```json
   {
     "audio_capture": {
       "mode": "none"
     }
   }
   ```

2. Start backend:
   ```bash
   ./run_backend.sh
   ```

3. The system will show **generic top news** (not contextually matched)

4. To test context matching manually:
   ```bash
   # Send test context via API
   curl -X POST http://localhost:8765/test-context \
     -H "Content-Type: application/json" \
     -d '{"context": "I am discussing artificial intelligence and machine learning breakthroughs"}'
   ```

---

### Option 4: WebSocket (Advanced - For Custom Integrations)

Stream audio via WebSocket from custom sources.

**Use case:** Custom audio routing, external capture tools

**Setup:**

1. Update `config.json`:
   ```json
   {
     "audio_capture": {
       "mode": "websocket"
     }
   }
   ```

2. Connect to WebSocket endpoint: `ws://localhost:8765/ws/audio`

3. Send raw audio data (PCM, 48kHz, 16-bit, stereo):
   ```python
   import websocket
   import wave

   ws = websocket.WebSocket()
   ws.connect("ws://localhost:8765/ws/audio")

   with wave.open("audio.wav", "rb") as wf:
       while True:
           data = wf.readframes(4800)  # 100ms chunks
           if not data:
               break
           ws.send_binary(data)
   ```

---

## Recommended Setup by Use Case

### 🎮 Gaming Streams
**Use: System Audio Capture (Option 1)**
- Low latency is critical
- Desktop audio + microphone mix

### 🎙️ Podcast/Talk Shows
**Use: System Audio Capture (Option 1)**
- Best audio quality
- Real-time transcription

### 💻 Coding/Tutorial Streams
**Use: File-Based Monitoring (Option 2)**
- Simpler setup
- Less critical latency

### 🧪 Testing/Development
**Use: Manual Test Mode (Option 3)**
- No streaming needed
- Quick testing

---

## Verifying Audio Capture

After setup, verify audio is being captured:

1. Start the backend
2. Check logs for:
   ```
   INFO: Starting system audio capture...
   INFO: Audio capture started (device: 1, 48000Hz)
   ```

3. Make some noise / speak

4. Check transcription context:
   ```bash
   curl http://localhost:8765/context
   ```

   You should see your transcribed speech:
   ```json
   {
     "context": "Hello this is a test of the audio capture system...",
     "length": 156,
     "buffer_duration": 300
   }
   ```

5. If context is empty after 10+ seconds:
   - Audio capture is not working
   - Check device configuration
   - Check backend logs for errors

---

## Cost Considerations

Remember, transcription costs money:

- **AssemblyAI**: ~$0.45/hour of streaming
- **Only transcribe when streaming**: Stop backend when not streaming
- **Consider speech-to-text alternatives**: Whisper API, Google STT (cheaper)

---

## Alternative: Use Pre-made Context

If you don't want real-time transcription, you can manually set context:

1. Set `audio_capture.mode` to `"none"`
2. Before streaming, set your stream topic:
   ```bash
   # Example: Gaming stream about space games
   curl -X POST http://localhost:8765/set-context \
     -H "Content-Type: application/json" \
     -d '{
       "context": "Playing space exploration games, discussing NASA missions, Mars colonization, SpaceX launches"
     }'
   ```

3. News will be matched to this static context

---

## Troubleshooting

### "No audio device found"
- **Windows**: Enable "Stereo Mix" in Recording Devices
- **Mac**: Install [BlackHole](https://github.com/ExistentialAudio/BlackHole)
- **Linux**: Use PulseAudio monitor

### "Permission denied" (Linux)
```bash
sudo usermod -a -G audio $USER
```

### High CPU usage
- Lower `sample_rate` to 44100 or 24000 in config
- Use `file` mode instead of `system` mode

### Transcription is empty
- Speak louder / check microphone levels
- Verify audio device is correct
- Check AssemblyAI API key is valid
- Look for errors in backend logs

### Headlines not contextually relevant
- Wait 30+ seconds for transcription to build up
- Speak clearly about specific topics
- Check context length: `curl http://localhost:8765/context`
- If context is too short, it won't match well

---

## Next Steps

1. Choose an audio capture method from above
2. Follow the setup instructions
3. Start the backend: `./run_backend.sh`
4. Start streaming and speak for 30+ seconds
5. Click "Update Headlines Now" in OBS
6. Verify headlines are contextually relevant

---

## Support

If you're still having issues:

1. Check backend logs for errors
2. Verify API keys are correct in `config.json`
3. Test transcription context: `curl http://localhost:8765/context`
4. Open an issue on GitHub with logs
