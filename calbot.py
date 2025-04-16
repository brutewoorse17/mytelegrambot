import os
import logging
import math
import subprocess
import time
import libtorrent as lt
from typing import Union, Tuple, Optional, List
from tempfile import mkdtemp, mkstemp
from datetime import timedelta
from pyrogram import Client, filters
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

# Video settings
SUPPORTED_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.flv', '.webm', '.mpeg', '.mpg', '.wmv'}
MAX_SINGLE_FILE_SIZE = 1900 * 1024 * 1024  # 1900MB
MIN_SPLIT_DURATION = 30  # Minimum duration (seconds)

# Torrent settings
MAX_TORRENT_SIZE = 5 * 1024 * 1024 * 1024  # 5GB max
TORRENT_DOWNLOAD_TIMEOUT = 3600  # 1 hour timeout
MIN_DOWNLOAD_SPEED = 50 * 1024  # 50 KB/s minimum
STALLED_TIME_LIMIT = 300  # 5 minutes
SPEED_CHECK_INTERVAL = 30  # Check speed every 30 seconds

# Initialize Pyrogram client
app = Client(
    "video_converter_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def is_torrent_link(text: str) -> bool:
    """Check if text is a torrent/magnet link"""
    return text.startswith(('magnet:', 'http')) and any(
        text.endswith(ext) for ext in ('.torrent', '')
    )

async def download_torrent(torrent_link: str, download_dir: str, message: Message) -> Optional[str]:
    """Enhanced torrent download with stall/dead detection"""
    ses = lt.session()
    ses.listen_on(6881, 6891)

    params = {
        'save_path': download_dir,
        'storage_mode': lt.storage_mode_t(2),
        'paused': False,
        'auto_managed': True
    }

    try:
        if torrent_link.startswith('magnet:'):
            handle = lt.add_magnet_uri(ses, torrent_link, params)
        else:
            info = lt.torrent_info(lt.bdecode(open(torrent_link, 'rb').read()))
            handle = ses.add_torrent({'ti': info, 'save_path': download_dir})

        status_msg = await message.reply_text("🔄 Connecting to peers...")
        last_update = time.time()
        last_downloaded = 0
        stalled_since = None

        while not handle.is_seed():
            s = handle.status()
            current_time = time.time()

            # Calculate speed
            elapsed = max(1, current_time - last_update)
            speed = (s.total_download - last_downloaded) / elapsed
            last_downloaded = s.total_download
            last_update = current_time

            # Update status every 15 seconds
            if int(current_time) % 15 == 0:
                progress = s.progress * 100
                await status_msg.edit_text(
                    f"📥 Downloading: {progress:.1f}%\n"
                    f"⚡ Speed: {speed/1024:.1f} KB/s\n"
                    f"👥 Peers: {s.num_peers}\n"
                    f"⏳ Remaining: {format_time_left(s)}"
                )

            # Check for stalled download
            if speed < MIN_DOWNLOAD_SPEED:
                stalled_since = stalled_since or current_time
                if current_time - stalled_since > STALLED_TIME_LIMIT:
                    await status_msg.edit_text("⚠️ Torrent stalled (low speed)")
                    ses.remove_torrent(handle)
                    return None
            else:
                stalled_since = None

            # Check for dead torrent
            if s.num_peers == 0 and current_time - last_update > 120:
                await status_msg.edit_text("⚠️ Torrent dead (no peers)")
                ses.remove_torrent(handle)
                return None

            time.sleep(1)

        await status_msg.delete()
        return download_dir

    except Exception as e:
        logger.error(f"Torrent error: {e}")
        await message.reply_text(f"⚠️ Torrent failed: {str(e)}")
        return None

def format_time_left(status: lt.torrent_status) -> str:
    """Format remaining time estimate"""
    if status.download_rate > 0:
        seconds = (status.total_wanted - status.total_wanted_done) / status.download_rate
        return str(timedelta(seconds=int(seconds))).split(".")[0]
    return "Unknown"

async def safe_cleanup(directory: str):
    """Robust directory cleanup with retries"""
    for attempt in range(3):
        try:
            for root, dirs, files in os.walk(directory, topdown=False):
                for name in files:
                    os.unlink(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(directory)
            break
        except Exception as e:
            logger.warning(f"Cleanup attempt {attempt+1} failed: {e}")
            time.sleep(1)

def get_file_info(message: Message) -> Tuple[Union[None, object], Union[None, str]]:
    """Extract file information from message"""
    if message.document:
        file = message.document
        ext = os.path.splitext(file.file_name or "")[1].lower()
        if ext in SUPPORTED_EXTENSIONS:
            return file, None
        elif ext == '.torrent':
            return file, "torrent"
    elif message.video:
        return message.video, None
    elif message.text and is_torrent_link(message.text):
        return message.text, "torrent_link"
    
    return None, "Unsupported file type"

async def get_video_duration(input_path: str) -> float:
    """Get video duration using FFprobe"""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        input_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())

async def estimate_output_size(input_path: str) -> int:
    """Estimate output file size"""
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=bit_rate',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            input_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        bitrate = float(result.stdout.strip())
        duration = await get_video_duration(input_path)
        return int((bitrate * duration) / 8)
    except Exception as e:
        logger.error(f"Size estimation error: {e}")
        return 0

async def split_video(input_path: str, max_size: int) -> List[str]:
    """Split video into segments"""
    segments = []
    duration = await get_video_duration(input_path)
    estimated_size = await estimate_output_size(input_path)
    
    if estimated_size <= max_size:
        return [input_path]

    num_segments = math.ceil(estimated_size / max_size)
    segment_duration = duration / num_segments
    
    if segment_duration < MIN_SPLIT_DURATION:
        num_segments = math.floor(duration / MIN_SPLIT_DURATION)
        segment_duration = duration / num_segments
    
    for i in range(num_segments):
        start = i * segment_duration
        end = (i + 1) * segment_duration if i < num_segments - 1 else duration
        
        fd, segment_path = mkstemp(suffix=f"_part{i+1}.mp4")
        os.close(fd)
        
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-ss', str(start),
            '-to', str(end),
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'fast',
            '-y',
            segment_path
        ]
        
        subprocess.run(cmd, check=True)
        segments.append(segment_path)
    
    return segments

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
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

async def process_video_file(client: Client, message: Message, input_path: str):
    """Process and upload a video file"""
    status_msg = await message.reply_text("🔍 Analyzing video...")
    
    try:
        segments = await split_video(input_path, MAX_SINGLE_FILE_SIZE)
        
        if len(segments) > 1:
            await status_msg.edit_text(f"✂️ Splitting into {len(segments)} parts...")
        
        for i, segment_path in enumerate(segments):
            segment_num = f" (Part {i+1})" if len(segments) > 1 else ""
            await status_msg.edit_text(f"🔄 Processing{segment_num}...")
            
            if i == len(segments) - 1:
                await message.reply_video(
                    segment_path,
                    caption=f"Here's your video{segment_num}!",
                    progress=progress_callback,
                    progress_args=(status_msg, f"Uploading{segment_num}")
                )
            else:
                await client.send_video(
                    message.chat.id,
                    segment_path,
                    caption=f"Here's your video{segment_num}!",
                    progress=progress_callback,
                    progress_args=(status_msg, f"Uploading{segment_num}")
                )
        
        await status_msg.delete()
        return True
    
    except Exception as e:
        logger.error(f"Video processing error: {e}")
        await status_msg.edit_text(f"⚠️ Error: {str(e)}")
        return False
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)
        if 'segments' in locals():
            for path in segments:
                if os.path.exists(path):
                    os.remove(path)

async def handle_torrent(client: Client, message: Message, torrent_content: Union[str, object]):
    """Process torrent files/links"""
    temp_dir = mkdtemp(prefix="torrent_")
    start_time = time.time()
    
    try:
        if isinstance(torrent_content, str):
            content_path = await download_torrent(torrent_content, temp_dir, message)
        else:
            torrent_path = os.path.join(temp_dir, "temp.torrent")
            await client.download_media(message, file_name=torrent_path)
            content_path = await download_torrent(torrent_path, temp_dir, message)

        if not content_path or time.time() - start_time > TORRENT_DOWNLOAD_TIMEOUT:
            await message.reply_text("⌛ Torrent timed out")
            return

        # Find and process video files
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
        await safe_cleanup(temp_dir)

@app.on_message(filters.document | filters.video | filters.text)
async def handle_media(client: Client, message: Message):
    """Handle all incoming media"""
    content, content_type = get_file_info(message)
    
    if content is None:
        await message.reply_text(f"⚠️ {content_type}")
        return
    
    if content_type == "torrent_link":
        await handle_torrent(client, message, content)
    elif content_type == "torrent":
        await handle_torrent(client, message, content)
    else:
        temp_path = mkstemp(suffix='.temp')[1]
        try:
            await client.download_media(message, file_name=temp_path)
            await process_video_file(client, message, temp_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

async def progress_callback(current, total, status_msg, action):
    """Update progress during transfers"""
    percent = (current / total) * 100
    if int(percent) % 5 == 0:  # Update every 5%
        try:
            await status_msg.edit_text(f"{action}... {int(percent)}%")
        except RPCError:
            pass

if __name__ == "__main__":
    # Verify dependencies
    try:
        subprocess.run(['ffmpeg', '-version'], check=True)
        subprocess.run(['ffprobe', '-version'], check=True)
    except Exception as e:
        logger.error(f"Missing dependency: {e}")
        exit(1)
    
    logger.info("Starting bot...")
    app.run()