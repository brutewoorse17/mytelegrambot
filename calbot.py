import os import subprocess import json import time import asyncio import logging from pyrogram import Client, filters from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup from dotenv import load_dotenv import aria2p import shutil import validators

Setup logging

logging.basicConfig( level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', handlers=[ logging.FileHandler("bot.log"), logging.StreamHandler() ] ) logger = logging.getLogger(name)

load_dotenv()

api_id = int(os.getenv("API_ID")) api_hash = os.getenv("API_HASH") chat_id = int(os.getenv("CHAT_ID"))

app = Client("my_userbot", api_id=api_id, api_hash=api_hash)

try: aria2 = aria2p.API( aria2p.Client(host="http://localhost", port=6800, secret="") ) except Exception as e: logger.error("Failed to connect to aria2 RPC: %s", e) raise

os.makedirs("downloads", exist_ok=True) os.makedirs("splits", exist_ok=True)

prefs_file = "prefs.json"

if os.path.exists(prefs_file): with open(prefs_file, "r") as f: user_prefs = json.load(f) else: user_prefs = {}

SPLIT_DURATIONS = { "10min": 600, "20min": 1200, "30min": 1800 }

def save_preferences(): with open(prefs_file, "w") as f: json.dump(user_prefs, f)

def preferences_keyboard(): return InlineKeyboardMarkup([ [ InlineKeyboardButton("Convert to MP4", callback_data="convert_mp4"), InlineKeyboardButton("Don't Convert", callback_data="no_convert") ], [ InlineKeyboardButton("Split: 10 min", callback_data="split_10min"), InlineKeyboardButton("Split: 20 min", callback_data="split_20min"), InlineKeyboardButton("Split: 30 min", callback_data="split_30min"), InlineKeyboardButton("No Split", callback_data="no_split") ], [ InlineKeyboardButton("Send as Video", callback_data="send_video"), InlineKeyboardButton("Send as Document", callback_data="send_document") ], [ InlineKeyboardButton("Delete after Upload", callback_data="delete_true"), InlineKeyboardButton("Keep Files", callback_data="delete_false") ], [ InlineKeyboardButton("Save as Default", callback_data="save_default"), InlineKeyboardButton("Start Download", callback_data="start_download") ] ])

@app.on_message(filters.document & filters.private) async def handle_torrent(client, message): try: file = message.document if not file.file_name.endswith(".torrent"): await message.reply("Please send a valid .torrent file.") return

torrent_path = f"downloads/{file.file_name}"
    await message.download(file_name=torrent_path)

    await message.reply("Please select options for this download:", reply_markup=preferences_keyboard())
    user_prefs[message.from_user.id] = {
        "torrent": torrent_path,
        "type": "torrent"
    }
    save_preferences()
except Exception as e:
    logger.exception("Error handling torrent: %s", e)
    await message.reply("An error occurred while processing the torrent.")

@app.on_message(filters.text & filters.private) async def handle_links(client, message): try: text = message.text.strip() user_id = message.from_user.id

if text.startswith("magnet:?xt="):
        user_prefs[user_id] = {"magnet": text, "type": "magnet"}
    elif validators.url(text):
        user_prefs[user_id] = {"url": text, "type": "url"}
    else:
        await message.reply("Please send a valid magnet link or direct download link.")
        return

    await message.reply("Please select options for this download:", reply_markup=preferences_keyboard())
    save_preferences()
except Exception as e:
    logger.exception("Error handling link: %s", e)
    await message.reply("An error occurred while processing your link.")

@app.on_callback_query() async def on_button(client, query): try: user_id = query.from_user.id data = query.data message = query.message

if data.startswith("convert_"):
        user_prefs[user_id]["convert"] = (data == "convert_mp4")
    elif data.startswith("split_"):
        if data != "no_split":
            user_prefs[user_id]["split"] = SPLIT_DURATIONS[data]
        else:
            user_prefs[user_id]["split"] = None
    elif data.startswith("send_"):
        user_prefs[user_id]["upload_as"] = data.split("_")[1]
    elif data.startswith("delete_"):
        user_prefs[user_id]["delete"] = (data == "delete_true")
    elif data == "save_default":
        await message.reply("Preferences saved as default.")
        save_preferences()
    elif data == "start_download":
        await message.reply("Starting download...")
        await start_download(user_id, message)

    save_preferences()
    await query.answer()
except Exception as e:
    logger.exception("Error in button callback: %s", e)
    await query.message.reply("An error occurred while processing your request.")

def cleanup_slow_downloads(threshold_speed=5, timeout=60): for download in aria2.get_downloads(): if download.is_active: speed_kb = int(download.download_speed or 0) / 1024 if speed_kb < threshold_speed: if not hasattr(download, "slow_start"): download.slow_start = time.time() elif time.time() - download.slow_start > timeout: logger.info("Removing slow download: %s", download.name) aria2.remove([download], force=True, files=True) else: download.slow_start = time.time()

async def start_download(user_id, message): try: user_settings = user_prefs[user_id] download = None

if user_settings["type"] == "torrent":
        download = aria2.add_torrent(user_settings["torrent"], download_dir="downloads")
    elif user_settings["type"] == "magnet":
        download = aria2.add_magnet(user_settings["magnet"], download_dir="downloads")
    elif user_settings["type"] == "url":
        download = aria2.add_uris([user_settings["url"]], download_dir="downloads")

    progress_msg = await message.reply("Downloading...")

    while not download.is_complete:
        download.update()
        cleanup_slow_downloads(threshold_speed=5, timeout=60)

        total_length = download.total_length or 1
        completed_length = download.completed_length
        progress_percent = (completed_length / total_length) * 100
        speed = download.download_speed / 1024

        await progress_msg.edit(
            f"**Downloading:** {download.name}\n"
            f"**Progress:** {progress_percent:.2f}%\n"
            f"**Speed:** {speed:.2f} KB/s"
        )

        await asyncio.sleep(5)

    await progress_msg.edit("Download complete!")
    for file in download.files:
        await process_video(message, file.path)
except Exception as e:
    logger.exception("Error during download: %s", e)
    await message.reply("Download failed or encountered an error.")

async def process_video(message, filepath): try: user_settings = user_prefs[message.from_user.id] max_size = 2 * 1024 * 1024 * 1024 filename = os.path.basename(filepath)

if user_settings.get("convert", False):
        new_path = filepath.replace(".mkv", ".mp4").replace(".avi", ".mp4")
        subprocess.run(["ffmpeg", "-i", filepath, "-c:v", "libx264", "-c:a", "aac", new_path])
        filepath = new_path
        filename = os.path.basename(filepath)

    file_size = os.path.getsize(filepath)

    if file_size > max_size and user_settings["split"]:
        split_duration = user_settings["split"]
        split_pattern = f"splits/{filename}_%03d.mp4"
        subprocess.run([
            "ffmpeg", "-i", filepath, "-c", "copy", "-map", "0",
            "-f", "segment", "-segment_time", str(split_duration), split_pattern
        ])

        for part in sorted(os.listdir("splits")):
            await upload_file(message, f"splits/{part}")
        shutil.rmtree("splits")
    else:
        await upload_file(message, filepath)

    if user_settings.get("delete", False):
        os.remove(filepath)
except Exception as e:
    logger.exception("Error during processing video: %s", e)
    await message.reply("Failed to process the video.")

async def upload_file(message, filepath): try: user_settings = user_prefs[message.from_user.id]

if user_settings["upload_as"] == "video":
        await message.reply_video(filepath)
    else:
        await message.reply_document(filepath)
except Exception as e:
    logger.exception("Error uploading file: %s", e)
    await message.reply("Failed to upload the file.")

if name == "main": logger.info("[+] Starting bot... Make sure aria2c is running!") app.run()

