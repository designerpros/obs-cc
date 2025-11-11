"""
OBS News Ticker Plugin
Python script for OBS Studio that displays contextually relevant news headlines

Installation:
1. In OBS Studio, go to Tools > Scripts
2. Click the + button and select this script
3. Configure the backend URL in the script settings
4. The script will create a text source named "News Ticker"

Requirements:
- OBS Studio with Python scripting support
- Backend service running (see backend/main.py)
"""

import obspython as obs
import urllib.request
import urllib.error
import json
import time
from typing import List, Dict, Optional

# Global state
backend_url = "http://localhost:8765"
ticker_source_name = "News Ticker"
update_interval = 5000  # Check for updates every 5 seconds
current_headlines: List[Dict] = []
ticker_position = 0
last_update_time = 0
is_enabled = True

# Ticker settings
ticker_width = 3840
ticker_height = 100
ticker_font_size = 48
ticker_scroll_speed = 2.0
ticker_color = 0xFFFFFFFF  # White


def script_description():
    """OBS calls this to get script description"""
    return """<h2>Contextual News Ticker</h2>
    <p>Displays real-time news headlines that match your stream content.</p>
    <p><b>Features:</b></p>
    <ul>
        <li>AI-powered context matching using Claude</li>
        <li>Real-time transcription via AssemblyAI</li>
        <li>Multiple free news sources (RSS, HackerNews, etc.)</li>
        <li>Automatic refresh every 10 minutes</li>
    </ul>
    <p><b>Setup:</b></p>
    <ol>
        <li>Configure API keys in config.json</li>
        <li>Start the backend service: python -m backend.main</li>
        <li>Configure the backend URL below</li>
        <li>Click "Create Ticker Source" or "Update Headlines"</li>
    </ol>
    """


def script_properties():
    """OBS calls this to get script properties for the UI"""
    props = obs.obs_properties_create()

    # Backend URL
    obs.obs_properties_add_text(
        props,
        "backend_url",
        "Backend Service URL",
        obs.OBS_TEXT_DEFAULT
    )

    # Source name
    obs.obs_properties_add_text(
        props,
        "source_name",
        "Ticker Source Name",
        obs.OBS_TEXT_DEFAULT
    )

    # Enable/disable
    obs.obs_properties_add_bool(
        props,
        "enabled",
        "Enable Ticker Updates"
    )

    # Font size
    obs.obs_properties_add_int(
        props,
        "font_size",
        "Font Size",
        20,
        120,
        1
    )

    # Scroll speed
    obs.obs_properties_add_float(
        props,
        "scroll_speed",
        "Scroll Speed",
        0.5,
        10.0,
        0.1
    )

    # Update interval
    obs.obs_properties_add_int(
        props,
        "update_interval",
        "Update Check Interval (seconds)",
        1,
        60,
        1
    )

    # Manual update button
    obs.obs_properties_add_button(
        props,
        "update_now",
        "Update Headlines Now",
        update_headlines_button
    )

    # Create source button
    obs.obs_properties_add_button(
        props,
        "create_source",
        "Create Ticker Source",
        create_ticker_source_button
    )

    return props


def script_defaults(settings):
    """OBS calls this to set default values"""
    obs.obs_data_set_default_string(settings, "backend_url", "http://localhost:8765")
    obs.obs_data_set_default_string(settings, "source_name", "News Ticker")
    obs.obs_data_set_default_bool(settings, "enabled", True)
    obs.obs_data_set_default_int(settings, "font_size", 48)
    obs.obs_data_set_default_double(settings, "scroll_speed", 2.0)
    obs.obs_data_set_default_int(settings, "update_interval", 5)


def script_update(settings):
    """OBS calls this when settings are updated"""
    global backend_url, ticker_source_name, is_enabled
    global ticker_font_size, ticker_scroll_speed, update_interval

    backend_url = obs.obs_data_get_string(settings, "backend_url")
    ticker_source_name = obs.obs_data_get_string(settings, "source_name")
    is_enabled = obs.obs_data_get_bool(settings, "enabled")
    ticker_font_size = obs.obs_data_get_int(settings, "font_size")
    ticker_scroll_speed = obs.obs_data_get_double(settings, "scroll_speed")
    update_interval = obs.obs_data_get_int(settings, "update_interval") * 1000

    obs.script_log(obs.LOG_INFO, f"Settings updated: backend={backend_url}, source={ticker_source_name}")


def script_load(settings):
    """OBS calls this when the script is loaded"""
    obs.script_log(obs.LOG_INFO, "News Ticker script loaded")

    # Start the update timer
    obs.timer_add(update_ticker, update_interval)


def script_unload():
    """OBS calls this when the script is unloaded"""
    obs.script_log(obs.LOG_INFO, "News Ticker script unloaded")
    obs.timer_remove(update_ticker)


def fetch_headlines() -> Optional[List[Dict]]:
    """Fetch headlines from the backend service"""
    try:
        url = f"{backend_url}/headlines"
        req = urllib.request.Request(url)
        req.add_header('Content-Type', 'application/json')

        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            headlines = data.get('headlines', [])

            obs.script_log(obs.LOG_INFO, f"Fetched {len(headlines)} headlines from backend")
            return headlines

    except urllib.error.URLError as e:
        obs.script_log(obs.LOG_WARNING, f"Failed to fetch headlines: {e}")
        return None
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"Error fetching headlines: {e}")
        return None


def format_ticker_text(headlines: List[Dict]) -> str:
    """Format headlines into a ticker text string"""
    if not headlines:
        return "Waiting for contextual news headlines..."

    # Format: [Source] Title  •  [Source] Title  •  ...
    parts = []
    for h in headlines:
        source = h.get('source', 'News')
        title = h.get('title', 'No title')
        parts.append(f"[{source}] {title}")

    # Add spacing between repetitions for smooth scrolling
    ticker_text = "  •  ".join(parts)

    # Repeat for smooth infinite scroll
    return f"{ticker_text}  •  {ticker_text}  •  {ticker_text}"


def update_ticker():
    """Periodic update function called by OBS timer"""
    global current_headlines, last_update_time

    if not is_enabled:
        return

    # Fetch new headlines
    headlines = fetch_headlines()

    if headlines is not None:
        current_headlines = headlines
        last_update_time = time.time()

        # Update the ticker text
        update_ticker_source()


def update_ticker_source():
    """Update the OBS text source with current headlines"""
    global current_headlines

    # Get the source
    source = obs.obs_get_source_by_name(ticker_source_name)

    if source is None:
        obs.script_log(obs.LOG_WARNING, f"Source '{ticker_source_name}' not found")
        return

    try:
        # Format the ticker text
        ticker_text = format_ticker_text(current_headlines)

        # Update the text source
        settings = obs.obs_data_create()
        obs.obs_data_set_string(settings, "text", ticker_text)

        obs.obs_source_update(source, settings)
        obs.obs_data_release(settings)

        obs.script_log(obs.LOG_DEBUG, f"Updated ticker with {len(current_headlines)} headlines")

    finally:
        obs.obs_source_release(source)


def create_ticker_source_button(props, prop):
    """Button callback to create the ticker source"""
    create_ticker_source()
    return True


def create_ticker_source():
    """Create the news ticker text source in OBS"""
    global ticker_source_name, ticker_font_size, ticker_color

    # Check if source already exists
    existing_source = obs.obs_get_source_by_name(ticker_source_name)
    if existing_source is not None:
        obs.obs_source_release(existing_source)
        obs.script_log(obs.LOG_INFO, f"Source '{ticker_source_name}' already exists")
        return

    # Create text source settings
    settings = obs.obs_data_create()
    obs.obs_data_set_string(settings, "text", "Initializing news ticker...")

    # Font settings
    font_obj = obs.obs_data_create()
    obs.obs_data_set_string(font_obj, "face", "Arial")
    obs.obs_data_set_int(font_obj, "size", ticker_font_size)
    obs.obs_data_set_int(font_obj, "flags", 0)
    obs.obs_data_set_string(font_obj, "style", "Bold")
    obs.obs_data_set_obj(settings, "font", font_obj)

    # Color
    obs.obs_data_set_int(settings, "color", ticker_color)

    # Scrolling
    obs.obs_data_set_bool(settings, "read_from_file", False)

    # Create the source
    source = obs.obs_source_create("text_gdiplus", ticker_source_name, settings, None)

    if source is None:
        # Try text_ft2_source for Linux/Mac
        source = obs.obs_source_create("text_ft2_source", ticker_source_name, settings, None)

    if source is not None:
        # Add to current scene
        current_scene = obs.obs_frontend_get_current_scene()
        if current_scene is not None:
            scene = obs.obs_scene_from_source(current_scene)
            if scene is not None:
                scene_item = obs.obs_scene_add(scene, source)

                # Position at bottom of screen
                pos = obs.vec2()
                pos.x = 0
                pos.y = 1080 - ticker_height  # Assume 1080p, adjust as needed

                obs.obs_sceneitem_set_pos(scene_item, pos)

                obs.script_log(obs.LOG_INFO, f"Created ticker source '{ticker_source_name}'")

            obs.obs_source_release(current_scene)

        obs.obs_source_release(source)

    obs.obs_data_release(font_obj)
    obs.obs_data_release(settings)


def update_headlines_button(props, prop):
    """Button callback to manually update headlines"""
    obs.script_log(obs.LOG_INFO, "Manual headline update triggered")
    update_ticker()
    return True
