Here is the full code with the /realtime command, proper error handling, and the download and upload functionality:

import os
import subprocess
import json
import time
import asyncio
import logging
import shutil
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ParseMode
import aria2p
import validators

# Load environment variables
load_dotenv()

# Bot configuration
API_ID = 1845829
API_HASH = "334d370d0c39a8039e6dfc53dd0f6d75"
BOT_TOKEN = "7633520700:AAHmBLBTV2oj-6li8E1txmIiS_zJOzquOxc"

app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Aria2 setup
aria2 = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))

# Directory setup
os.makedirs("downloads", exist_ok=True)
os.makedirs("splits", exist_ok=True)

# User preferences
prefs_file = "prefs.json"
user_prefs = json.load(open(prefs_file)) if os.path.exists(prefs_file) else {}
SPLIT_DURATIONS = {"10min": 600, "20min": 1200, "30min": 1800}

def save_preferences():
    with open(prefs_file, "w") as f:
        json.dump(user_prefs, f)

def preferences_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Convert to MP4", callback_data="convert_mp4"),
         InlineKeyboardButton("Don't Convert", callback_data="no_convert")],
        [InlineKeyboardButton("Split: 10 min", callback_data="split_10min"),
         InlineKeyboardButton("Split: 20 min", callback_data="split_20min"),
         InlineKeyboardButton("Split: 30 min", callback_data="split_30min"),
         InlineKeyboardButton("No Split", callback_data="no_split")],
        [InlineKeyboardButton("Send as Video", callback_data="send_video"),
         InlineKeyboardButton("Send as Document", callback_data="send_document")],
        [InlineKeyboardButton("Delete after Upload", callback_data="delete_true"),
         InlineKeyboardButton("Keep Files", callback_data="delete_false")],
        [InlineKeyboardButton("Save as Default", callback_data="save_default"),
         InlineKeyboardButton("Start Download", callback_data="start_download")]
    ])

async def safe_edit(message, text, **kwargs):
    try:
        if message.text != text:
            await message.edit_text(text, **kwargs)
    except Exception as e:
        logger.warning(f"Safe edit failed: {e}")

@app.on_message(filters.document & filters.private)
async def handle_torrent(client, message):
    file = message.document
    if not file.file_name.endswith(".torrent"):
        return await message.reply("Please send a valid .torrent file.")
    path = f"downloads/{file.file_name}"
    await message.download(file_name=path)
    user_prefs[message.from_user.id] = {"torrent": path, "type": "torrent"}
    save_preferences()
    await message.reply("Select options:", reply_markup=preferences_keyboard())

@app.on_message(filters.text & filters.private)
async def handle_links(client, message):
    text = message.text.strip()
    user_id = message.from_user.id
    if text.startswith("magnet:?xt="):
        user_prefs[user_id] = {"magnet": text, "type": "magnet"}
    elif validators.url(text):
        user_prefs[user_id] = {"url": text, "type": "url"}
    else:
        return await message.reply("Send a valid magnet or direct link.")
    save_preferences()
    await message.reply("Select options:", reply_markup=preferences_keyboard())

@app.on_callback_query()
async def on_button(client, query):
    user_id = query.from_user.id
    data = query.data
    msg = query.message

    if data.startswith("convert_"):
        user_prefs[user_id]["convert"] = (data == "convert_mp4")
    elif data.startswith("split_"):
        user_prefs[user_id]["split"] = SPLIT_DURATIONS.get(data, None)
    elif data.startswith("send_"):
        user_prefs[user_id]["upload_as"] = data.split("_")[1]
    elif data.startswith("delete_"):
        user_prefs[user_id]["delete"] = (data == "delete_true")
    elif data == "save_default":
        await msg.reply("Preferences saved.")
    elif data == "start_download":
        await safe_edit(msg, "Starting download...", reply_markup=None)
        await start_download(user_id, msg)
    save_preferences()
    await query.answer()

@app.on_message(filters.command("realtime") & filters.private)
async def realtime_stats(client, message):
    active_downloads = aria2.get_downloads()
    if not active_downloads:
        return await message.reply("No active downloads currently.")
    
    stats_message = "Real-time download stats:\n"
    for download in active_downloads:
        progress = (download.completed_length / (download.total_length or 1)) * 100
        speed = download.download_speed / 1024  # Convert bytes to KB
        stats_message += f"**{download.name}:**\n"
        stats_message += f"Progress: {progress:.2f}%\nSpeed: {speed:.2f} KB/s\n\n"
    
    await message.reply(stats_message, parse_mode=ParseMode.MARKDOWN)

def cleanup_slow_downloads(threshold_kbps=5, timeout=60):
    for download in aria2.get_downloads():
        speed = int(download.download_speed or 0) / 1024
        if download.is_active and speed < threshold_kbps:
            if not hasattr(download, "slow_start"):
                download.slow_start = time.time()
            elif time.time() - download.slow_start > timeout:
                logger.info(f"Removing slow: {download.name}")
                aria2.remove([download], force=True, files=True)
        else:
            download.slow_start = time.time()

last_update_time = {}

async def start_download(user_id, message):
    settings = user_prefs[user_id]
    download = None
    try:
        if settings["type"] == "torrent":
            download = aria2.add_torrent(settings["torrent"])
        elif settings["type"] == "magnet":
            download = aria2.add_magnet(settings["magnet"])
        elif settings["type"] == "url":
            download = aria2.add_uris([settings["url"]])

        msg = await message.reply("Downloading...")

        while not download.is_complete:
            download.update()
            cleanup_slow_downloads()
            now = time.time()
            if user_id not in last_update_time or now - last_update_time[user_id] >= 5:
                progress = (download.completed_length / (download.total_length or 1)) * 100
                speed = download.download_speed / 1024
                await safe_edit(
                    msg,
                    f"**Downloading:** `{download.name}`\n**Progress:** {progress:.2f}%\n**Speed:** {speed:.2f} KB/s",
                    parse_mode=ParseMode.MARKDOWN
                )
                last_update_time[user_id] = now
            await asyncio.sleep(5)

        await safe_edit(msg, "Download complete! Uploading...", parse_mode=ParseMode.MARKDOWN)
        for file in download.files:
            await process_video(message, file.path, msg)

    except Exception as e:
        logger.exception("Download error: %s", e)
        await message.reply("Download failed.")

async def process_video(message, path, progress_msg):
    settings = user_prefs[message.from_user.id]
    max_size = 2 * 1024 * 1024 * 1024
    filename = os.path.basename(path)

    if settings.get("convert"):
        mp4_path = path.rsplit(".", 1)[0] + ".mp4"
        subprocess.run(["ffmpeg", "-i", path, "-c:v", "libx264", "-c:a", "aac", mp4_path])
        path, filename = mp4_path, os.path.basename(mp4_path)

    if os.path.getsize(path) > max_size and settings.get("split"):
        pattern = f"splits/{filename}_%03d.mp4"
        subprocess.run([
            "ffmpeg", "-i", path, "-c", "copy", "-map", "0", "-f", "segment",
            "-segment_time", str(settings["split"]), pattern
        ])
        for part in sorted(os.listdir("splits")):
            await upload_file(message, f"splits/{part}", progress_msg)
        shutil.rmtree("splits")
    else:
        await upload_file(message, path, progress_msg)

    if settings.get("delete"):
        os.remove(path)

async def upload_file(message, path, progress_msg):
    settings = user_prefs[message.from_user.id]
    try:
        async def progress(current, total):
            percent = (current / total) * 100
            await safe_edit(
                progress_msg,
                f"**Uploading:** `{os.path.basename(path)}`\n**Progress:** {percent:.2f}%",
                parse_mode=ParseMode.MARKDOWN
            )

        if settings.get("upload_as") == "document":
            await message.reply_document(
                document=path,
                caption=f"Uploaded: `{os.path.basename(path)}`",
                parse_mode=ParseMode.MARKDOWN,
                progress=progress
            )
        else:
            await message.reply_video(
                video=path,
                caption=f"Uploaded: `{os.path.basename(path)}`",
                parse_mode=ParseMode.MARKDOWN,
                progress=progress
            )

    except Exception as e:
        logger.exception(f"Error during upload: {e}")
        await message.reply("Upload failed.")

# Run the bot
if __name__ == "__main__":
    app.run()

Key Changes:

1. /realtime Command: Fetches and displays the real-time download stats for active downloads.


2. aria2 Integration: Handles torrent, magnet, and URL downloads with status updates.


3. Video Processing: Converts and splits videos if necessary before uploading.


4. Preferences Handling: Allows the user to customize download/upload behavior.



Now, the bot should be capable of handling torrent downloads, providing real-time stats with /realtime, and managing download preferences for each user.

Let me know if you need further modifications!

