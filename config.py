import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


SERVER_IP = os.getenv("SERVER_IP", "127.0.0.1")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "word_bot_db")
DB_PORT = os.getenv("DB_PORT", "5432")


NEON_URL = os.getenv("NEON_URL")


AI_URL = f"http://{SERVER_IP}:11434/api/generate"
raw_keys = os.getenv("GEMINI_API_KEYS", "")
GEMINI_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]

PIXABAY_KEY = os.getenv("PIXABAY_API_KEY")
WEB_APP_URL = os.getenv("WEB_APP_URL")