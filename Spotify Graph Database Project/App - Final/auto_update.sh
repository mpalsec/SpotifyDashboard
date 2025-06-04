#!/bin/bash

# CONFIGURATION
REPO_URL="https://github.com/mpalsec/SpotifyDashboard"
LOCAL_DIR="/etc/Spotify-App/Source"
VENV_DIR="/etc/Spotify-App/spotify_app_venv"
STREAMLIT_APP="App.py"  # Change to your Streamlit main file
PORT=8501

# Function to stop the Streamlit app
stop_streamlit() {
    echo "Stopping existing Streamlit app..."
    pkill -f streamlit
}

# Function to start the Streamlit app
start_streamlit() {
    echo "Starting Streamlit app..."
    cd "$LOCAL_DIR" || exit
    nohup streamlit run "$STREAMLIT_APP" --server.port "$PORT" > /var/log/streamlit.log 2>&1 &
}

# Function to check for repo updates
check_for_updates() {
    echo "Checking for updates..."
    cd "$LOCAL_DIR" || exit
    git fetch origin main
    LOCAL_HASH=$(git rev-parse HEAD)
    REMOTE_HASH=$(git rev-parse origin/main)

    if [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
        echo "Updates found."
        return 0
    else
        echo "No updates. Exiting."
        return 1
    fi
}

# SCRIPT LOGIC
echo "Starting update check..."

if [ -d "$LOCAL_DIR/.git" ]; then
    if check_for_updates; then
        stop_streamlit
        git pull origin main
        python3 venv -m "$VENV_DIR"
        pip3 install -r "$LOCAL_DIR"/requirements.txt
        start_streamlit
        echo "Streamlit app updated."
    fi
else
    echo "Repo doesn't exist. Cloning..."
    git clone "$REPO_URL" "$LOCAL_DIR"
    python3 venv -m "$VENV_DIR"
    pip3 install -r "$LOCAL_DIR"/requirements.txt
    start_streamlit
fi
