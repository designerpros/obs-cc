#!/bin/bash
# Startup script for OBS News Ticker Backend

echo "==================================="
echo "OBS News Ticker Backend"
echo "==================================="
echo ""

# Check if config.json exists
if [ ! -f "config.json" ]; then
    echo "❌ config.json not found!"
    echo ""
    echo "Please create config.json from config.example.json:"
    echo "  cp config.example.json config.json"
    echo ""
    echo "Then edit config.json and add your API keys:"
    echo "  - assemblyai_api_key"
    echo "  - anthropic_api_key"
    echo ""
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -q -r requirements.txt

echo ""
echo "✅ Starting backend service..."
echo "   URL: http://localhost:8765"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Run the backend
python -m backend.main
