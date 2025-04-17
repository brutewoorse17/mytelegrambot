import os
import logging
import math
import time
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
QBITTORRENT_HOST = "http://localhost:8080"
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

# Initialize qBittorrent client
qb = Client(
    host=QBITTORRENT_HOST,
    username=QBITTORRENT_USER,
    password=QBITTORRENT_PASS
)

# Initialize Pyrogram client
app = PyroClient(
    "video_converter_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

async def download_torrent(torrent_content: Union[str, bytes], download_dir: str, message: Message) -> Optional[str]:
    """Download torrent using qBittorrent"""
    try:
        # Create download directory if it doesn't exist
        os.makedirs(download_dir, exist_ok=True)
        
        # Add torrent to qBittorrent
        if isinstance(torrent_content, str) and torrent_content.startswith('magnet:'):
            torrent = qb.torrents_add(torrent_content, save_path=download_dir)
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
        
        while True:
            # Check timeout
            if time.time() - start_time > TORRENT_DOWNLOAD_TIMEOUT:
                await status_msg.edit_text("⌛ Torrent timed out")
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
                    f"⏳ ETA: {torrent_info.eta // 60}m"
                )
            
            # Check for completion
            if progress >= 100:
                await status_msg.delete()
                return download_dir
            
            # Check for stalled download
            if speed < MIN_DOWNLOAD_SPEED and progress - last_progress < 1:
                await status_msg.edit_text("⚠️ Torrent stalled (low speed)")
                qb.torrents_delete(torrent_hash)
                return None
            
            last_progress = progress
            time.sleep(5)
    
    except Exception as e:
        logger.error(f"qBittorrent error: {e}")
        await message.reply_text(f"⚠️ Torrent failed: {str(e)}")
        return None

# [Keep all your existing functions like get_file_info, get_video_duration, 
# estimate_output_size, split_video, etc. from the previous code]

@app.on_message(filters.command("start"))
async def start_command(client: PyroClient, message: Message):
    """Enhanced start command with torrent info"""
    await message.reply_text(
        "🎥 **Video Converter Bot**\n\n"
        "Send me:\n"
        "- Video files (I'll convert to MP4)\n"
        "- Torrent files/magnet links (I'll download and process)\n\n"
        f"📁 Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}\n"
        f"🧲 Max torrent size: {MAX_TORRENT_SIZE//(1024**3)}GB\n"
        f"📏 Max file size: {MAX_SINGLE_FILE_SIZE//(1024**2)}MB\n"
        "⚡ Min download speed: 50KB/s (auto-cancels if slower)"
    )

async def handle_torrent(client: PyroClient, message: Message, torrent_content: Union[str, bytes]):
    """Process torrent files/links"""
    temp_dir = mkdtemp(prefix="torrent_")
    
    try:
        content_path = await download_torrent(torrent_content, temp_dir, message)
        if not content_path:
            return

        # Process downloaded files
        processed_files = 0
        for root, _, files in os.walk(content_path):
            for file in files:
                if os.path.splitext(file)[1].lower() in SUPPORTED_EXTENSIONS:
                    file_path = os.path.join(root, file)
                    if await process_video_file(client, message, file_path):
                        processed_files += 1

        if processed_files == 0:
            await message.reply_text("⚠️ No supported videos found in torrent")
    
    except Exception as e:
        logger.error(f"Torrent processing error: {e}")
        await message.reply_text(f"⚠️ Torrent failed: {str(e)}")
    finally:
        # Clean up
        for root, dirs, files in os.walk(temp_dir, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(temp_dir)

# [Keep the rest of your existing handlers and main block]

if __name__ == "__main__":
    # Verify qBittorrent connection
    try:
        qb.auth_log_in()
        logger.info("Connected to qBittorrent")
    except Exception as e:
        logger.error(f"Failed to connect to qBittorrent: {e}")
        exit(1)
    
    # Verify FFmpeg is available
    try:
        subprocess.run(['ffmpeg', '-version'], check=True)
    except Exception as e:
        logger.error(f"FFmpeg not found: {e}")
        exit(1)
    
    logger.info("Starting bot...")
    app.run()