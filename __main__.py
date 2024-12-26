import logging
import libtorrent as lt
import time
import os
import requests
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Initialize logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Telegram Bot Token
TOKEN = 'YOUR_BOT_TOKEN'  # Replace with your actual Telegram bot token

# Torrent download directory
DOWNLOAD_DIR = '/path/to/download'

# Bot Owner ID (replace with your actual Telegram user ID)
OWNER_ID = 123456789  # Replace with your own Telegram user ID

# Dictionary to track ongoing downloads
user_downloads = {}
# Dictionary to track already uploaded file IDs
uploaded_files = {}

# Function to check if the user is the owner
def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

# Function to start the bot
@Client.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text("Hello! Send me a torrent magnet link or a direct download link, and I will handle it.")

# Function to handle either torrent magnet links or direct download links
@Client.on_message(filters.text & ~filters.command())
async def handle_torrent_or_link(client, message):
    user_id = message.from_user.id
    input_link = message.text

    if "magnet:" in input_link:
        # Handle as a magnet link
        await handle_magnet_link(client, message, input_link)
    elif input_link.startswith("http://") or input_link.startswith("https://"):
        # Handle as a direct download link
        await handle_direct_link(client, message, input_link)
    else:
        await message.reply_text("Please send a valid magnet link or a direct download link.")

# Function to handle torrent magnet links
async def handle_magnet_link(client, message, magnet_link):
    user_id = message.from_user.id

    try:
        # Create a torrent session
        ses = lt.session()
        ses.listen_on(6881, 6891)

        # Add the torrent
        params = {
            'save_path': DOWNLOAD_DIR,
            'storage_mode': lt.storage_mode_t(2)  # Use file storage
        }
        handle = lt.add_magnet_uri(ses, magnet_link, params)

        # Store the download session in the user's download tracking dictionary
        user_downloads[user_id] = {'handle': handle, 'session': ses, 'message_id': message.message_id}

        await message.reply_text(f"Downloading metadata for {handle.name()}...")

        # Wait for the metadata to be downloaded
        while not handle.has_metadata():
            time.sleep(1)

        await message.reply_text(f"Torrent metadata downloaded. Starting download for {handle.name()}")

        # Start downloading the torrent
        while not handle.is_seed():
            status = handle.status()
            progress_msg = f"{status.state} {status.progress*100:.1f}% complete " \
                           f"(down: {status.download_rate / 1000:.1f} kB/s up: {status.upload_rate / 1000:.1f} kB/s " \
                           f"peers: {status.num_peers})"
            await message.reply_text(progress_msg)
            time.sleep(5)  # Update progress every 5 seconds

        # After the download is completed, offer the file type choices via inline buttons
        download_file_path = os.path.join(DOWNLOAD_DIR, handle.name())
        if os.path.exists(download_file_path):
            await message.reply_text(f"Download completed! Choose a file type to upload.", reply_markup=build_file_type_keyboard(download_file_path))
        else:
            await message.reply_text(f"Downloaded file {handle.name()} not found in the directory.")

    except Exception as e:
        logger.error(f"Error downloading torrent: {e}")
        await message.reply_text("There was an error processing your torrent link.")
        del user_downloads[user_id]  # Remove the download from tracking in case of error

# Function to handle direct download links
async def handle_direct_link(client, message, download_link):
    user_id = message.from_user.id

    try:
        # Extract the file name from the URL
        file_name = download_link.split("/")[-1]
        download_path = os.path.join(DOWNLOAD_DIR, file_name)

        # Download the file
        await message.reply_text(f"Downloading file: {file_name}...")

        # Stream the content and save it to the download directory
        with requests.get(download_link, stream=True) as r:
            if r.status_code == 200:
                with open(download_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

                await message.reply_text(f"Download of {file_name} completed! Choose a file type to upload.",
                                         reply_markup=build_file_type_keyboard(download_path))
            else:
                await message.reply_text("Failed to download the file. Please check the link and try again.")
    except Exception as e:
        logger.error(f"Error downloading direct link: {e}")
        await message.reply_text("There was an error processing your direct download link.")

# Build the inline keyboard with file type options
def build_file_type_keyboard(file_path: str):
    file_types = ['.mp4', '.zip', '.pdf', '.jpg', '.png']  # Define file types
    keyboard = []

    for file_type in file_types:
        keyboard.append([InlineKeyboardButton(f"Send {file_type} file", callback_data=f"send_{file_type}")])

    return InlineKeyboardMarkup(keyboard)

# Handle the button press and upload the selected file type
@Client.on_callback_query(filters.regex('^send_'))
async def handle_file_type_selection(client, query):
    selected_file_type = query.data.split('_')[1]
    user_id = query.from_user.id
    file_path = os.path.join(DOWNLOAD_DIR, user_downloads[user_id]['handle'].name())

    # Check if the file has already been uploaded
    if file_path in uploaded_files:
        await query.edit_message_text(text=f"The file has already been uploaded to Telegram.")
    else:
        # Filter the file to upload based on the selected type
        selected_file = get_file_of_type(file_path, selected_file_type)

        if selected_file:
            # Upload the file with progress tracking
            await query.edit_message_text(text=f"Uploading your {selected_file_type} file...")
            await upload_file_with_progress(client, query, selected_file)
        else:
            await query.edit_message_text(text=f"No {selected_file_type} file found in the downloaded torrent.")

# Helper function to get a file of the specified type
def get_file_of_type(file_path: str, file_type: str):
    # Check if the file exists and matches the type
    for root, dirs, files in os.walk(file_path):
        for file in files:
            if file.lower().endswith(file_type):
                return os.path.join(root, file)
    return None

# Upload file with progress tracking
async def upload_file_with_progress(client, query, file_path):
    total_size = os.path.getsize(file_path)  # Get the total size of the file
    with open(file_path, 'rb') as f:
        chunk_size = 1024 * 1024  # 1MB chunks
        bytes_uploaded = 0
        
        # Send the file in chunks, tracking the progress
        while chunk := f.read(chunk_size):
            await query.edit_message_text(text=f"Uploading... {bytes_uploaded / total_size * 100:.2f}%")
            # Upload the chunk
            await client.send_document(query.from_user.id, document=chunk)
            bytes_uploaded += len(chunk)

        # Once upload is complete, notify the user
        await query.edit_message_text(text=f"Upload completed for {file_path}!")

# Handle deleted messages to cancel download
@Client.on_deleted_messages()
async def handle_deleted_message(client, messages):
    for message in messages:
        user_id = message.from_user.id if message.from_user else None
        if user_id and user_id in user_downloads:
            # Stop the download if it's still in progress
            if user_downloads[user_id]['session']:
                user_downloads[user_id]['session'].pause()  # Pause the torrent download
            await client.send_message(user_id, "Your download has been canceled because you deleted the message.")
            del user_downloads[user_id]  # Remove the user's download from tracking

# Create and run the client
app = Client("torrent_bot", bot_token=TOKEN)

if __name__ == '__main__':
    app.run()
