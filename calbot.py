import os
import logging
import math
import time
import subprocess
from typing import Union, Tuple, Optional, List
from tempfile import mkdtemp, mkstemp
from qbittorrentapi import Client
from pyrogram import Client as PyroClient, filters
from pyrogram.types import Message
from pyrogram.errors import RPCError

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
API_ID = 1845829
API_HASH = "334d370d0c39a8039e6dfc53dd0f6d75"
BOT_TOKEN = "7633520700:AAHmBLBTV2oj-6li8E1txmIiS_zJOzquOxc"

# qBittorrent configuration
QBITTORRENT_HOST = "http://127.0.0.1:8080"
QBITTORRENT_USER = "admin"
QBITTORRENT_PASS = "adminadmin"

# Video settings
SUPPORTED_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.flv', '.webm', '.mpeg', '.mpg', '.wmv'}
MAX_SINGLE_FILE_SIZE = 1900 * 1024 * 1024  # 1900MB
MIN_SPLIT_DURATION = 30  # Minimum duration (seconds)

# Torrent settings
MAX_TORRENT_SIZE = 5 * 1024 * 1024 * 1024  # 5GB max
TORRENT_DOWNLOAD_TIMEOUT = 3600  # 1 hour timeout
MIN_DOWNLOAD_SPEED = 50 * 1024  # 50 KB/s minimum

def initialize_qbittorrent():
    """Initialize qBittorrent connection with automatic startup"""
    qb = Client(
        host=QBITTORRENT_HOST,
        username=QBITTORRENT_USER,
        password=QBITTORRENT_PASS
    )
    
    try:
        qb.auth_log_in()
        logger.info("Successfully connected to qBittorrent")
        return qb
    except Exception as e:
        logger.warning(f"First connection attempt failed: {e}")
        logger.info("Attempting to start qBittorrent automatically...")
        
        try:
            # Start qBittorrent in the background
            subprocess.Popen(
                ["qbittorrent-nox", "--webui-port=8080"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(5)  # Wait for startup
            
            # Retry connection
            for attempt in range(3):
                try:
                    qb.auth_log_in()
                    logger.info(f"Successfully connected on attempt {attempt + 1}")
                    return qb
                except Exception:
                    time.sleep(5)
            
            raise ConnectionError("Failed to connect after multiple attempts")
        except Exception as e:
            logger.error(f"Failed to start qBittorrent: {e}")
            raise

# Initialize qBittorrent client
try:
    qb = initialize_qbittorrent()
except Exception as e:
    logger.error(f"Critical qBittorrent initialization error: {e}")
    exit(1)

# Initialize Pyrogram client
app = PyroClient(
    "video_converter_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

async def download_torrent(torrent_content: Union[str, bytes], download_dir: str, message: Message) -> Optional[str]:
    """Download torrent using qBittorrent with enhanced error handling"""
    try:
        # Create download directory if it doesn't exist
        os.makedirs(download_dir, exist_ok=True)
        
        # Add torrent to qBittorrent
        if isinstance(torrent_content, str) and torrent_content.startswith('magnet:'):
            torrent = qb.torrents_add(urls=torrent_content, save_path=download_dir)
        else:
            # For torrent files
            if isinstance(torrent_content, str):
                with open(torrent_content, 'rb') as f:
                    torrent_file = f.read()
            else:
                torrent_file = torrent_content
            torrent = qb.torrents_add(torrent_files=torrent_file, save_path=download_dir)
        
        # Get torrent hash
        torrent_hash = torrent.info.hash
        status_msg = await message.reply_text("🔄 Starting download...")
        last_progress = 0
        start_time = time.time()
        last_speed_check = time.time()
        stalled_since = None
        
        while True:
            # Check timeout
            if time.time() - start_time > TORRENT_DOWNLOAD_TIMEOUT:
                await status_msg.edit_text("⌛ Torrent timed out (1 hour limit)")
                qb.torrents_delete(torrent_hash)
                return None
            
            # Get torrent status
            torrent_info = qb.torrents_info(torrent_hashes=torrent_hash)[0]
            progress = torrent_info.progress * 100
            speed = torrent_info.dlspeed / 1024  # KB/s
            
            # Update status every 15 seconds
            if int(time.time()) % 15 == 0:
                await status_msg.edit_text(
                    f"📥 Downloading: {progress:.1f}%\n"
                    f"⚡ Speed: {speed:.1f} KB/s\n"
                    f"👥 Seeds: {torrent_info.num_seeds}\n"
                    f"⏳ ETA: {format_eta(torrent_info.eta)}"
                )
            
            # Check for completion
            if progress >= 100:
                await status_msg.delete()
                return download_dir
            
            # Check for stalled download
            current_time = time.time()
            if speed < MIN_DOWNLOAD_SPEED:
                if stalled_since is None:
                    stalled_since = current_time
                elif current_time - stalled_since > STALLED_TIME_LIMIT:
                    await status_msg.edit_text("⚠️ Torrent stalled (low speed for too long)")
                    qb.torrents_delete(torrent_hash)
                    return None
            else:
                stalled_since = None
            
            last_progress = progress
            time.sleep(5)
    
    except Exception as e:
        logger.error(f"Torrent download error: {e}")
        await message.reply_text(f"⚠️ Torrent download failed: {str(e)}")
        return None

def format_eta(seconds: int) -> str:
    """Format ETA into human-readable format"""
    if seconds < 0:
        return "Unknown"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {seconds}s"

# [Keep all your existing functions like get_file_info, get_video_duration, 
# estimate_output_size, split_video, process_video_file, handle_torrent, etc.]

if __name__ == "__main__":
    # Verify FFmpeg is available
    try:
        subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True)
    except Exception as e:
        logger.error(f"FFmpeg not found: {e}")
        exit(1)
    
    logger.info("Starting bot with qBittorrent integration...")
    app.run()