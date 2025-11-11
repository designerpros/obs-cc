# OBS Contextual News Ticker Plugin

AI-powered OBS plugin that displays real-time news headlines matched to your streaming content using AssemblyAI transcription and Claude AI.

## Features

- **Real-time transcription** of your stream audio via AssemblyAI
- **AI-powered context matching** using Claude Haiku/Sonnet to find relevant news
- **Multiple free news sources**: RSS feeds, Hacker News, NewsAPI, GNews
- **Customizable ticker** (3840x100px default) with scrolling headlines
- **Automatic updates** every 10 minutes
- **Top 4 contextually relevant** headlines displayed

## Architecture

```
┌─────────────────┐
│   OBS Studio    │
│                 │
│  ┌───────────┐  │     ┌──────────────────┐
│  │  Python   │◄─┼────►│ Backend Service  │
│  │  Script   │  │     │   (FastAPI)      │
│  └───────────┘  │     └──────────────────┘
│                 │              │
│  ┌───────────┐  │              ├─► AssemblyAI (Transcription)
│  │   News    │  │              ├─► News APIs (RSS, HN, etc.)
│  │  Ticker   │  │              └─► Claude AI (Context Matching)
│  └───────────┘  │
└─────────────────┘
```

### Components

1. **Backend Service** (`backend/`)
   - FastAPI server handling transcription, news fetching, and AI matching
   - Maintains 5-minute rolling context buffer
   - Fetches news from multiple free sources
   - Uses Claude AI to match news with content

2. **OBS Plugin** (`obs-plugin/`)
   - Python script for OBS Studio
   - Creates and updates ticker text source
   - Polls backend for updated headlines

## Installation

### Prerequisites

- **Python 3.8+** installed
- **OBS Studio 28+** with Python scripting support
- **API Keys**:
  - [AssemblyAI API Key](https://www.assemblyai.com/) (required for transcription)
  - [Anthropic API Key](https://console.anthropic.com/) (required for Claude AI)
  - [NewsAPI Key](https://newsapi.org/) (optional, free tier available)
  - [GNews API Key](https://gnews.io/) (optional, free tier available)

### Step 1: Clone Repository

```bash
git clone https://github.com/designerpros/obs-cc.git
cd obs-cc
```

### Step 2: Install Python Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Configure API Keys

1. Copy the example config:
   ```bash
   cp config.example.json config.json
   ```

2. Edit `config.json` and add your API keys:
   ```json
   {
     "assemblyai_api_key": "your_assemblyai_key_here",
     "anthropic_api_key": "your_anthropic_key_here",
     "news_apis": {
       "newsapi_key": "optional_newsapi_key",
       "gnews_key": "optional_gnews_key"
     }
   }
   ```

### Step 4: Start Backend Service

```bash
python -m backend.main
```

The backend will start on `http://localhost:8765` by default.

**Verify it's running:**
```bash
curl http://localhost:8765
```

You should see:
```json
{"status": "running", "last_update": null, "headlines_count": 0}
```

### Step 5: Install OBS Plugin

1. Open **OBS Studio**
2. Go to **Tools → Scripts**
3. Click the **+** button
4. Navigate to `obs-plugin/news_ticker.py` and select it
5. The script will appear in the "Loaded Scripts" list

### Step 6: Configure OBS Plugin

In the Scripts panel:

1. **Backend Service URL**: `http://localhost:8765` (or your backend URL)
2. **Ticker Source Name**: `News Ticker` (default)
3. **Enable Ticker Updates**: ✓ (checked)
4. **Font Size**: `48` (adjust as needed)
5. **Scroll Speed**: `2.0` (adjust as needed)
6. **Update Check Interval**: `5` seconds

### Step 7: Create Ticker Source

1. In the Scripts panel, click **"Create Ticker Source"**
2. This will add a text source named "News Ticker" to your current scene
3. Position it at the bottom of your scene (default: 1080 - 100px)

### Step 8: Test

1. Start streaming or recording in OBS
2. Speak about a topic (e.g., "artificial intelligence", "space exploration")
3. Wait 10-15 seconds for transcription to process
4. Click **"Update Headlines Now"** in the Scripts panel
5. The ticker should update with contextually relevant news

## How Context Works

The system uses a smart **context persistence** approach:

### First Stream (Cold Start)
- **0-30 seconds**: Shows generic top news (no context yet)
- **30+ seconds**: Transcription builds up, headlines become contextually relevant
- **On stream end**: Context is saved to `.context_cache.json`

### Subsequent Streams (Warm Start)
- **Stream start**: Previous stream's context is loaded automatically
- **Initial headlines**: Immediately relevant to your previous stream topic
- **0-5 minutes**: New transcription gradually replaces old context (rolling buffer)
- **5+ minutes**: All context is fresh from current stream
- **10 minutes**: First automatic headline refresh with fully fresh context

This means:
✅ **No cold start problem** - Headlines are always relevant from the first update
✅ **Smooth transitions** - If you stream similar topics, context carries over naturally
✅ **Auto-refresh** - Old context is automatically purged after 5 minutes
✅ **Privacy-friendly** - Context is stored locally in `.context_cache.json`

**Example:** If you were streaming about AI yesterday and start a new stream about AI today, the initial headlines will already be AI-focused until new transcription takes over.

## Configuration

### Backend Configuration (`config.json`)

```json
{
  "assemblyai_api_key": "...",
  "anthropic_api_key": "...",

  "backend": {
    "host": "localhost",
    "port": 8765
  },

  "transcription": {
    "buffer_duration_seconds": 300,  // 5 minutes
    "language": "en"
  },

  "news": {
    "refresh_interval_minutes": 10,  // Update every 10 minutes
    "max_headlines": 50,             // Total headlines to fetch
    "categories": ["general", "technology", "business", "science"]
  },

  "matching": {
    "model": "claude-3-5-haiku-20241022",  // or "claude-3-5-sonnet-20241022"
    "top_results": 4                       // Number of headlines to display
  },

  "ticker": {
    "width": 3840,
    "height": 100,
    "scroll_speed": 2.0,
    "font_size": 48
  }
}
```

### Ticker Customization

In OBS:

1. Select the "News Ticker" source
2. Right-click → **Filters**
3. Add **Scroll** filter for smooth scrolling animation
4. Adjust **Transform** to position/resize

### News Sources

The plugin fetches from these **free** sources:

- **RSS Feeds**: CNN, BBC, Reuters, NY Times, Ars Technica, Guardian, TechCrunch, Wired
- **Hacker News API**: Top tech/startup stories (no key required)
- **NewsAPI.org**: Top headlines (free tier: 100 requests/day, optional)
- **GNews API**: Global news (free tier: 100 requests/day, optional)

## API Costs

- **AssemblyAI**: $0.00025/second (~$0.015/minute)
  - 5-minute buffer = ~$0.075 per refresh
  - Hourly cost: ~$0.45/hour of streaming

- **Claude Haiku**: ~$0.25 per million input tokens
  - ~3000 tokens per match (context + articles)
  - Cost per refresh: ~$0.00075
  - Hourly cost: ~$0.0045/hour (negligible)

- **Claude Sonnet**: ~$3.00 per million input tokens (12x more expensive)
  - Cost per refresh: ~$0.009
  - Hourly cost: ~$0.054/hour

**Recommended**: Use Claude Haiku for cost-effectiveness. Total cost: **~$0.50/hour of streaming**

## Usage

### Basic Workflow

1. Start backend: `python -m backend.main`
2. Start OBS and load the script
3. Begin streaming/recording
4. The ticker automatically:
   - Transcribes your audio in real-time
   - Fetches fresh news every 10 minutes
   - Matches news with your content using AI
   - Displays top 4 relevant headlines

### Manual Controls

- **Update Headlines Now**: Force immediate update
- **Enable/Disable**: Toggle automatic updates
- **Adjust scroll speed**: Change ticker animation speed

### API Endpoints

The backend exposes these endpoints:

- `GET /`: Health check
- `GET /headlines`: Get current matched headlines
- `GET /context`: Get current transcription context (debug)
- `POST /trigger-update`: Manually trigger news update
- `WebSocket /ws/audio`: Audio streaming endpoint (future use)

**Example:**
```bash
# Get current headlines
curl http://localhost:8765/headlines

# Trigger manual update
curl -X POST http://localhost:8765/trigger-update
```

## Troubleshooting

### "Source not found" error

- Click **"Create Ticker Source"** in the Scripts panel
- Ensure the source name matches the configured name

### "Failed to fetch headlines"

- Verify backend is running: `curl http://localhost:8765`
- Check backend URL in OBS script settings
- Review backend logs for errors

### No context / headlines not relevant

- Speak clearly and wait 10-15 seconds for transcription
- Check transcription context: `curl http://localhost:8765/context`
- Verify AssemblyAI API key is correct

### Headlines not updating

- Check "Enable Ticker Updates" is enabled
- Verify update interval is reasonable (5+ seconds)
- Check backend logs for errors
- Click "Update Headlines Now" to force update

### Backend crashes on startup

- Verify all API keys in `config.json`
- Check Python dependencies: `pip install -r requirements.txt`
- Review error logs

## Development

### Running in Development Mode

```bash
# Backend with auto-reload
cd backend
uvicorn main:app --reload --host localhost --port 8765

# View logs
python -m backend.main
```

### Testing Without OBS

```bash
# Test news fetching
curl http://localhost:8765/trigger-update

# View current headlines
curl http://localhost:8765/headlines | jq

# Check transcription context
curl http://localhost:8765/context
```

### Adding News Sources

Edit `backend/news_fetcher.py`:

```python
async def _fetch_custom_source(self) -> List[Dict]:
    """Add your custom news source"""
    articles = []
    # Fetch from your API
    return articles
```

Then add to `fetch_all()`:
```python
tasks = [
    self._fetch_rss_feeds(),
    self._fetch_custom_source(),  # Add here
    ...
]
```

## Architecture Details

### Transcription Flow

```
OBS Audio → AssemblyAI Real-time API → Context Buffer (5 min rolling)
```

### News Matching Flow

```
News APIs → Fetch Articles → Claude AI Matching ← Transcription Context
                                     ↓
                            Top 4 Matched Headlines
```

### Context Matching Prompt

Claude receives:
- Last 5 minutes of transcribed speech
- 50+ news articles from various sources
- Request to return top 4 most contextually relevant

Claude analyzes semantic similarity, topic overlap, and thematic relevance.

## License

MIT License - see LICENSE file

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

- **Issues**: [GitHub Issues](https://github.com/designerpros/obs-cc/issues)
- **Discussions**: [GitHub Discussions](https://github.com/designerpros/obs-cc/discussions)

## Credits

- Built with [OBS Studio](https://obsproject.com/)
- Powered by [AssemblyAI](https://www.assemblyai.com/)
- AI matching by [Anthropic Claude](https://www.anthropic.com/)
- News from various free APIs and RSS feeds

---

**Note**: This is a plugin for content creators. Ensure you comply with news source terms of service and properly attribute sources when displaying headlines on stream.
