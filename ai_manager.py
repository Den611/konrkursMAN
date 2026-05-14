import aiohttp
import asyncio
import random
import re
import urllib.parse
import logging
from utils import ul
from config import AI_URL, GEMINI_KEYS, PIXABAY_KEY
 
logger = logging.getLogger(__name__)
 
FORCE_GEMINI = True
_session: aiohttp.ClientSession = None
 
async def init_ai_session():
    global _session
    if not _session:
        _session = aiohttp.ClientSession()

async def close_ai_session():
    global _session
    if _session:
        await _session.close()
        _session = None
 
def set_ai_mode(force_gemini: bool):
    global FORCE_GEMINI
    FORCE_GEMINI = force_gemini
 
 
class RotationManager:
    def __init__(self, keys):
        self.keys = [k for k in keys if k.strip()]
        self.models = [
            "gemini-2.5-flash-lite", 
            "gemma-3-27b-it",    
            "gemini-2.5-flash",
            "gemini-1.5-flash"     
            "gemini-3.1-flash-lite"
        ]
        self.current_key_idx = 0
        self.current_model_idx = 0

    def get_current(self):
        if not self.keys: return None, None
        return self.keys[self.current_key_idx], self.models[self.current_model_idx]

    def rotate(self):
        if not self.keys: return
        self.current_model_idx += 1
        if self.current_model_idx >= len(self.models):
            self.current_model_idx = 0
            self.current_key_idx = (self.current_key_idx + 1) % len(self.keys)
            logger.info(f"🔑 [AI] Ліміти моделей вичерпано. Перемикаємо на ключ №{self.current_key_idx + 1}")
        logger.info(f"🔄 [AI] Використовуємо модель: {self.models[self.current_model_idx]}")

rotation_manager = RotationManager(GEMINI_KEYS)



async def generate_content_safe(prompt: str, uid: int = 0, model_name: str = "gemma:2b") -> str:
    if not FORCE_GEMINI:
        logger.info(f"⏳ [AI] Запит до Ollama ({model_name})...")
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            payload = {"model": model_name, "prompt": prompt, "stream": False}
            async with _session.post(AI_URL, json=payload, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info("✅ [AI] Ollama відповіла!")
                        return data.get("response", "").strip()
                    logger.warning(f"⚠️ [AI] Ollama: {response.status}")
        except asyncio.TimeoutError:
            logger.warning("⚠️ [AI] Ollama timeout. Перемикаємось на Gemini!")
        except Exception as e:
            logger.error(f"⚠️ [AI] Ollama недоступна ({type(e).__name__}).")

    if FORCE_GEMINI:
        logger.info("⚡ [AI] Режим Gemini API.")
    else:
        logger.info("☁️ [AI] Gemini резерв...")

    if not rotation_manager.keys:
        return ul(uid, "errors.ai_busy") if uid else "😅 ШІ зараз перевантажений! Спробуй пізніше."

    attempts, max_attempts = 0, len(rotation_manager.keys) * len(rotation_manager.models) + 1
    while attempts < max_attempts:
        api_key, current_model = rotation_manager.get_current()
        gemini_url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                      f"{current_model}:generateContent?key={api_key}")
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with _session.post(
                gemini_url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=timeout
            ) as response:
                    if response.status == 200:
                        data = await response.json()
                        try:
                            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                            logger.info(f"✅ [AI] {current_model} відповіла успішно!")
                            return text
                        except (KeyError, IndexError):
                            logger.warning(f"⚠️ [AI] Фільтр безпеки на {current_model}.")
                            rotation_manager.rotate()
                            attempts += 1
                            await asyncio.sleep(1)
                            continue
                    else:
                        error_text = await response.text()
                        logger.error(f"⚠️ [AI] Помилка {response.status} від {current_model}: {error_text}")
                        if response.status in [429, 403, 404, 500, 503]:
                            rotation_manager.rotate()
                            attempts += 1
                            await asyncio.sleep(1)
                        else:
                            break
        except Exception as e:
            logger.error(f"⚠️ [AI] Збій підключення до {current_model}: {type(e).__name__}")
            rotation_manager.rotate()
            attempts += 1

    return ul(uid, "errors.ai_busy") if uid else "😅 ШІ зараз обробляє багато запитів. Спробуй через хвилину!"

#РЕПЕТИТОР (МАЙБУТНЄ)
async def generate_premium_content_safe(prompt: str, uid: int = 0) -> str:
    if not rotation_manager.keys:
        return ul(uid, "errors.ai_busy") if uid else "😅 ШІ зараз перевантажений! Спробуй пізніше."

    attempts, max_attempts = 0, len(rotation_manager.keys) + 1
    while attempts < max_attempts:
        api_key, _ = rotation_manager.get_current()
        
        gemini_url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                      f"gemini-3-flash:generateContent?key={api_key}")
        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with _session.post(
                gemini_url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=timeout
            ) as response:
                    if response.status == 200:
                        data = await response.json()
                        try:
                            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                            logger.info("💎 [AI] Преміум модель Gemini 3 Flash відповіла!")
                            return text
                        except (KeyError, IndexError):
                            rotation_manager.rotate()
                            attempts += 1
                            await asyncio.sleep(1)
                            continue
                    else:
                        error_text = await response.text()
                        logger.error(f"⚠️ [AI PREMIUM] Помилка: {response.status} - {error_text}")
                        if response.status in [429, 403, 404, 500, 503]:
                            rotation_manager.rotate()
                            attempts += 1
                            await asyncio.sleep(1)
                        else:
                            break
        except Exception as e:
            logger.error(f"⚠️ [AI PREMIUM] Збій: {type(e).__name__}")
            attempts += 1

    return ul(uid, "errors.ai_premium_busy") if uid else "😅 Розумна нейромережа зараз зайнята. Спробуй пізніше!"


async def get_image_url(query, use_random=False):
    if not query or not PIXABAY_KEY: return None
    try:
        params = {
            "key": PIXABAY_KEY,
            "q": query,
            "image_type": "photo",
            "orientation": "horizontal",
            "safesearch": "true",
            "per_page": 20 if use_random else 3
        }
        timeout = aiohttp.ClientTimeout(total=10)
        async with _session.get("https://pixabay.com/api/", params=params, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('hits'):
                        return (random.choice(data['hits'])['webformatURL'] if use_random
                                else data['hits'][0]['webformatURL'])
    except Exception:
        pass
    return None


async def get_full_word_info(word: str, translation: str, lang: str,
                             response_lang: str = "Ukrainian", uid: int = 0) -> dict:
    prompt = (
        f"Analyze the word '{word}' ({lang}, translation: '{translation}'). "
        f"Provide the explanation in {response_lang}. "
        f"CRITICAL: Start every line with the English prefix. No intro, no markdown. "
        f"Format strictly:\n"
        f"TRANSCRIPTION: [æpl]\n"
        f"ASSOCIATION: funny memory trick\n"
        f"EXAMPLE 1: first sentence\n"
        f"EXAMPLE 2: second sentence\n"
        f"EXAMPLE 3: third sentence\n"
        f"IMAGE: search keywords"
    )
    
    text = await generate_content_safe(prompt, uid=uid)

    result = {
        "transcription": "",
        "association":   translation,
        "examples":      [],
        "image_query":   word,
    }

    if "😅" in text or not text:
        return result

    for line in text.splitlines():
        line = line.strip().replace("*", "").replace("`", "")
        if not line or ":" not in line:
            continue
            
        key_part, value_part = line.split(":", 1)
        key = key_part.strip().upper()
        value = value_part.strip()

        if "TRANSCRIPTION" in key:
            result["transcription"] = value
        elif "ASSOCIATION" in key:
            result["association"] = value
        elif "EXAMPLE" in key:
            result["examples"].append(value)
        elif "IMAGE" in key:
            result["image_query"] = value

    if result["association"] == translation:
        for line in text.splitlines():
            if "асоціація" in line.lower() or "skojarzenie" in line.lower() or "association" in line.lower():
                if ":" in line:
                    result["association"] = line.split(":", 1)[1].strip()

    return result


async def get_ai_explanation_text(content: str, language_of_word: str,
                                  hobby: str = "everyday life",
                                  response_lang: str = "Ukrainian", uid: int = 0) -> str:
    if language_of_word == "auto":
        lang_hint = "Detect the language of the word automatically."
    else:
        lang_hint = f"The word is in {language_of_word}."

    prompt = (
        f"Explain the word '{content}'. {lang_hint} "
        f"The student is interested in: '{hobby}'. "
        f"Write the ENTIRE text ONLY in {response_lang}, including ALL headings! "
        f"Use **word** for bold text. NO other markdown. "
        f"Structure:\n"
        f"1. **{content}** — [transcription] — translation\n"
        f"2. [Translate 'Meaning' to {response_lang}]: short definition\n"
        f"3. [Translate 'Association' to {response_lang}]: funny memorable association to remember this word\n"
        f"4. [Translate 'Example' to {response_lang}]: one sentence related to {hobby}"
    )

    result = await generate_content_safe(prompt, uid=uid)
    result = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', result)
    result = result.replace("__", "").replace("`", "").replace("*", "")
    result = result.replace("<b>", "«B»").replace("</b>", "«/B»")
    result = result.replace("<", "").replace(">", "")
    result = result.replace("«B»", "<b>").replace("«/B»", "</b>")

    return result