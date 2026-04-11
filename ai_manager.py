import aiohttp
import asyncio
import random
import re
import urllib.parse
import logging
from config import AI_URL, GEMINI_KEYS, PIXABAY_KEY
 
logger = logging.getLogger(__name__)
 
FORCE_GEMINI = False
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
 
 
class KeyManager:
    def __init__(self, keys):
        self.keys          = [k for k in keys if k.strip()]
        self.current_index = 0
 
    def get_key(self):
        if not self.keys: return None
        return self.keys[self.current_index]
 
    def rotate_key(self):
        if self.keys:
            self.current_index = (self.current_index + 1) % len(self.keys)
            logger.info(f"🔄 [AI] Перемикання на ключ Gemini №{self.current_index + 1}")
 
 
key_manager = KeyManager(GEMINI_KEYS)
 
 
async def generate_content_safe(prompt: str, model_name: str = "gemma:2b") -> str:
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
 
    if not key_manager.keys:
        return "😅 ШІ зараз перевантажений! Спробуй пізніше."
 
    attempts, max_attempts = 0, len(key_manager.keys) + 1
    while attempts < max_attempts:
        api_key    = key_manager.get_key()
        gemini_url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                      f"gemini-2.5-flash:generateContent?key={api_key}")
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
                            logger.info("✅ [AI] Gemini відповіла!")
                            return text
                        except (KeyError, IndexError):
                            logger.warning("⚠️ [AI] Фільтр безпеки Google.")
                            key_manager.rotate_key()
                            attempts += 1
                            await asyncio.sleep(1)
                            continue
                    else:
                        if response.status in [429, 403, 404, 500, 503]:
                            key_manager.rotate_key()
                            attempts += 1
                            await asyncio.sleep(1)
                        else:
                            break
        except Exception as e:
            logger.error(f"⚠️ [AI] Збій Gemini: {type(e).__name__}")
            attempts += 1
 
    return "😅 ШІ зараз обробляє багато запитів. Спробуй через хвилину!"
 
 
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
                             response_lang: str = "Ukrainian") -> dict:
    """
    Один запит — повертає dict з усіма даними.
    response_lang — мова на якій писати асоціацію і приклади.
    """
    prompt = (
        f"Analyze the word '{word}' ({lang}, translation: '{translation}'). "
        f"Write EVERYTHING in {response_lang}. NO markdown, NO asterisks. "
        f"Output EXACTLY this format, each field on a new line:\n"
        f"TRANSCRIPTION: [æpl]\n"
        f"ASSOCIATION: short funny memorable association\n"
        f"EXAMPLE1: first example sentence\n"
        f"EXAMPLE2: second example sentence\n"
        f"EXAMPLE3: third example sentence\n"
        f"IMAGE: 2-3 word english search query\n"
        f"Do not write anything else."
    )
    text = await generate_content_safe(prompt)
 
    result = {
        "transcription": "",
        "association":   translation,
        "examples":      [],
        "image_query":   word,
    }
 
    if "😅" in text:
        return result
 
    for line in text.splitlines():
        line = (line.strip()
                    .replace("**", "")
                    .replace("__", "")
                    .replace("*", "")
                    .replace("`", "")
                    .replace("<", "")
                    .replace(">", ""))
        if line.startswith("TRANSCRIPTION:"):
            result["transcription"] = line.split(":", 1)[1].strip()
        elif line.startswith("ASSOCIATION:"):
            result["association"] = line.split(":", 1)[1].strip()
        elif line.startswith("EXAMPLE1:"):
            result["examples"].append(line.split(":", 1)[1].strip())
        elif line.startswith("EXAMPLE2:"):
            result["examples"].append(line.split(":", 1)[1].strip())
        elif line.startswith("EXAMPLE3:"):
            result["examples"].append(line.split(":", 1)[1].strip())
        elif line.startswith("IMAGE:"):
            result["image_query"] = line.split(":", 1)[1].strip()
 
    return result
 
 
async def get_ai_explanation_text(content: str, language_of_word: str,
                                  hobby: str = "everyday life",
                                  response_lang: str = "Ukrainian") -> str:
    """
    Пояснює слово на мові response_lang з прикладом через хобі.
    language_of_word = 'auto' → AI сам визначить мову слова.
    Повертає текст з HTML-форматуванням (bold через <b>).
    """
    if language_of_word == "auto":
        lang_hint = "Detect the language of the word automatically."
    else:
        lang_hint = f"The word is in {language_of_word}."
 
    prompt = (
        f"Explain the word '{content}'. {lang_hint} "
        f"The student is interested in: '{hobby}'. "
        f"Write the ENTIRE explanation ONLY in {response_lang}. "
        f"Use **word** for bold text. NO other markdown. "
        f"Structure:\n"
        f"1. **{content}** — [transcription] — translation\n"
        f"2. Meaning: short definition\n"
        f"3. Association: funny memorable association to remember this word\n"
        f"4. Example: one sentence related to {hobby}"
    )
    result = await generate_content_safe(prompt)
 
    # Конвертуємо **bold** → <b>bold</b>
    result = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', result)
 
    # Чистимо решту markdown
    result = result.replace("__", "").replace("`", "").replace("*", "")
 
    # Безпечна заміна < > (крім наших <b> тегів)
    result = result.replace("<b>", "«B»").replace("</b>", "«/B»")
    result = result.replace("<", "").replace(">", "")
    result = result.replace("«B»", "<b>").replace("«/B»", "</b>")
 
    return result