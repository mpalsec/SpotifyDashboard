REPO_URL="https://github.com/mpalsec/SpotifyDashboard"
REPO_DIR="/etc/spotify_app"
SOURCE_CODE_DIR="/etc/spotify_app/repo"
VENV_DIR="/etc/spotify_app/spotify_app_venv/bin/activate"
APP_SCRIPT = "App.py"
APP_PORT=8501

# 1. Update all packages (for apt, pip, and npm as examples)
# Update system packages (Debian/Ubuntu)
sudo apt update && sudo apt upgrade -y

# Update all Python packages (user installs)
pip list --user --outdated --format=freeze | cut -d = -f 1 | xargs -n1 pip install --u>
# Update all global npm packages
npm update -g

# ensure that there aren't any updates to git repo. If there are, restart Spotify App
cd "$REPO_DIR" || exit

# 1. Fetch latest changes from remote
git fetch

# 2. Compare local and remote HEADs
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse @{u})

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "Updates found. Pulling changes..."
    git pull

    # 3. Find and kill the running Streamlit app (if any)
    PID=$(pgrep -f "streamlit run.*$APP_SCRIPT")
    if [ -n "$PID" ]; then
        echo "Killing Streamlit app (PID $PID)..."
        kill "$PID"
        sleep 2  # Give it a moment to shut down
    fi

    # 4. Restart the Streamlit app
    echo "Restarting Streamlit app..."
    nohup streamlit run "$APP_SCRIPT" --server.port $APP_PORT > streamlit.log 2>&1 &
    sudo source "$VENV_DIR"
else
    echo "No updates. Streamlit app continues running."
fi