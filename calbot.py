import os
import logging
import math
from typing import Union, Tuple
from tempfile import mkstemp
from moviepy.editor import VideoFileClip
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
API_ID = 1845829  # Your API ID from my.telegram.org
API_HASH = "334d370d0c39a8039e6dfc53dd0f6d75"  # Your API Hash
BOT_TOKEN = "7633520700:AAHmBLBTV2oj-6li8E1txmIiS_zJOzquOxc"  # Your bot token from @BotFather

# Video settings
SUPPORTED_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.flv', '.webm', '.mpeg', '.mpg', '.wmv'}
MAX_SINGLE_FILE_SIZE = 1900 * 1024 * 1024  # 1900MB (slightly under 2GB for safety)
MIN_SPLIT_DURATION = 30  # Minimum duration (seconds) for a split segment

# Initialize Pyrogram client
app = Client(
    "video_converter_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def get_file_info(message: Message) -> Tuple[Union[None, object], Union[None, str]]:
    """Extract file information from message and validate it"""
    if message.document:
        file = message.document
        ext = os.path.splitext(file.file_name or "")[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return None, "Unsupported file format"
        return file, None
    
    if message.video:
        return message.video, None
    
    return None, "No supported file found"

async def estimate_output_size(input_path: str) -> int:
    """Estimate output file size in bytes"""
    try:
        clip = VideoFileClip(input_path)
        duration = clip.duration
        fps = clip.fps
        width, height = clip.size
        
        # Rough estimation formula (bytes)
        # This is simplified - actual size depends on compression, content, etc.
        estimated_size = (width * height * fps * duration * 0.07)  # 0.07 is a compression factor
        
        clip.close()
        return int(estimated_size)
    except Exception as e:
        logger.error(f"Size estimation error: {e}")
        return 0

async def split_video(input_path: str, output_prefix: str, max_size: int) -> list:
    """Split video into segments that fit under max_size"""
    segments = []
    clip = VideoFileClip(input_path)
    total_duration = clip.duration
    estimated_total_size = await estimate_output_size(input_path)
    
    if estimated_total_size <= max_size:
        clip.close()
        return [input_path]  # No splitting needed
    
    # Calculate needed segments
    num_segments = math.ceil(estimated_total_size / max_size)
    segment_duration = total_duration / num_segments
    
    # Ensure segments aren't too short
    if segment_duration < MIN_SPLIT_DURATION:
        num_segments = math.floor(total_duration / MIN_SPLIT_DURATION)
        if num_segments < 1:
            num_segments = 1
        segment_duration = total_duration / num_segments
    
    # Split the video
    for i in range(num_segments):
        start_time = i * segment_duration
        end_time = (i + 1) * segment_duration if i < num_segments - 1 else total_duration
        
        fd, segment_path = mkstemp(suffix=f"_part{i+1}.mp4")
        os.close(fd)
        
        subclip = clip.subclip(start_time, end_time)
        subclip.write_videofile(
            segment_path,
            codec='libx264',
            audio_codec='aac',
            threads=4,
            preset='fast'
        )
        subclip.close()
        segments.append(segment_path)
    
    clip.close()
    return segments

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    """Handle /start command"""
    await message.reply_text(
        "🎥 **Video Converter Bot**\n\n"
        "Send me a video file and I'll convert it to MP4 format.\n"
        "Large videos will be automatically split into parts.\n\n"
        f"📁 **Supported formats**: {', '.join(SUPPORTED_EXTENSIONS)}\n"
        f"📏 **Max single file size**: {MAX_SINGLE_FILE_SIZE // (1024 * 1024)}MB\n\n"
        "Use /help for more info."
    )

@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    """Handle /help command"""
    await message.reply_text(
        "🛠 **How to use this bot:**\n\n"
        "1. Send me any video file (document or video message)\n"
        "2. I'll automatically convert it to MP4 format\n"
        "3. If the file is too large, I'll split it into parts\n"
        "4. You'll receive all converted parts\n\n"
        "⚙️ **Commands:**\n"
        "/start - Show welcome message\n"
        "/help - Show this help message\n\n"
        "⚠️ Note: Processing time depends on video length and size."
    )

@app.on_message(filters.document | filters.video)
async def handle_video(client: Client, message: Message):
    """Handle incoming video files"""
    # Get file info
    file, error = get_file_info(message)
    if error:
        await message.reply_text(f"⚠️ {error}")
        return
    
    # Create temp files
    fd, input_path = mkstemp(suffix='.temp')
    os.close(fd)
    
    status_msg = await message.reply_text("📥 Downloading file...")
    
    try:
        # Download the file
        await client.download_media(
            message,
            file_name=input_path,
            progress=progress_callback,
            progress_args=(status_msg, "Downloading")
        )
        
        await status_msg.edit_text("🔍 Analyzing video...")
        
        # Check if splitting is needed
        segments = await split_video(input_path, "output", MAX_SINGLE_FILE_SIZE)
        
        if len(segments) > 1:
            await status_msg.edit_text(f"✂️ Splitting video into {len(segments)} parts...")
        
        # Process each segment
        for i, segment_path in enumerate(segments):
            segment_num = f" (Part {i+1})" if len(segments) > 1 else ""
            await status_msg.edit_text(f"🔄 Converting{segment_num}...")
            
            # For single file or last segment, use reply_video
            if i == len(segments) - 1:
                await message.reply_video(
                    segment_path,
                    caption=f"Here's your converted video{segment_num}!",
                    progress=progress_callback,
                    progress_args=(status_msg, f"Uploading{segment_num}")
                )
            else:
                await client.send_video(
                    message.chat.id,
                    segment_path,
                    caption=f"Here's your converted video{segment_num}!",
                    progress=progress_callback,
                    progress_args=(status_msg, f"Uploading{segment_num}")
                )
        
        await status_msg.delete()
        
    except RPCError as e:
        logger.error(f"RPCError: {e}")
        await status_msg.edit_text("⚠️ Error processing file. Please try again.")
    except Exception as e:
        logger.error(f"Conversion error: {e}", exc_info=True)
        await status_msg.edit_text("⚠️ Error during video processing.")
    finally:
        # Clean up all temp files
        temp_files = [input_path]
        if 'segments' in locals():
            temp_files.extend(segments)
        
        for path in temp_files:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                logger.error(f"Error cleaning up {path}: {e}")

async def progress_callback(current, total, status_msg, action):
    """Update progress during download/upload"""
    percent = (current / total) * 100
    if int(percent) % 10 == 0:  # Update every 10% to avoid spamming
        try:
            await status_msg.edit_text(f"{action}... {int(percent)}%")
        except RPCError:
            pass  # Don't fail if we can't update the progress

if __name__ == "__main__":
    logger.info("Starting video converter bot...")
    app.run()
    