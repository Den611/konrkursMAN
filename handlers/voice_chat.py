import io
import wave
import logging
from aiogram import Router, F, types
from aiogram.filters import StateFilter
from pydub import AudioSegment
from google import genai
from google.genai import types as genai_types

import database as db
from config import GEMINI_KEYS

router = Router()

client = genai.Client(api_key=GEMINI_KEYS[0] if GEMINI_KEYS else "")
MODEL = "gemini-2.5-flash"
VOICE_NAME = "Eneladus"


def make_config(instruction: str, with_input_transcription: bool = True):
    return genai_types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=genai_types.SpeechConfig(
            voice_config=genai_types.VoiceConfig(
                prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                    voice_name=VOICE_NAME
                )
            )
        ),
        output_audio_transcription=genai_types.AudioTranscriptionConfig(),
        input_audio_transcription=genai_types.AudioTranscriptionConfig()
        if with_input_transcription
        else None,
        system_instruction={"parts": [{"text": instruction}]},
    )


def ogg_to_pcm(ogg_bytes: bytes) -> bytes:
    audio = AudioSegment.from_ogg(io.BytesIO(ogg_bytes))
    audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)
    return audio.raw_data


def pcm_to_ogg(pcm_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm_bytes)
    buf.seek(0)
    out = io.BytesIO()
    AudioSegment.from_wav(buf).export(out, format="ogg", codec="libopus")
    return out.getvalue()


async def collect_response(session):
    pcm_chunks, input_transcript, output_transcript = [], "", ""
    async for msg in session.receive():
        sc = msg.server_content
        if not sc:
            continue
        if sc.model_turn and sc.model_turn.parts:
            for part in sc.model_turn.parts:
                if part.inline_data:
                    pcm_chunks.append(part.inline_data.data)
        if sc.input_transcription and sc.input_transcription.text:
            input_transcript += sc.input_transcription.text
        if sc.output_transcription and sc.output_transcription.text:
            output_transcript += sc.output_transcription.text
        if sc.turn_complete:
            break
    return pcm_chunks, input_transcript, output_transcript


@router.message(F.voice, StateFilter(None))
async def on_voice(message: types.Message):
    uid = message.from_user.id
    logging.info(f"[VoiceChat] Отримано голосове від {uid}")

    await message.bot.send_chat_action(message.chat.id, "record_voice")

    try:
        target_lang = await db.get_target_lang(uid) or "English"
        hobby = await db.get_user_hobby(uid) or "daily life"
    except Exception as e:
        target_lang, hobby = "English", "general topics"

    instruction = (
        f"You are an expert AI language tutor. The user is learning {target_lang} "
        f"and their main interest is '{hobby}'.\n"
        f"STRICT INSTRUCTIONS:\n"
        f"1. You MUST communicate EXCLUSIVELY in {target_lang}.\n"
        f"2. Carefully evaluate the user's speech. If they make a mistake, politely correct them.\n"
        f"3. Keep your responses concise (1-3 sentences max).\n"
        f"4. DO NOT hallucinate. Do not invent fake facts or words.\n"
        f"5. Never break your persona."
    )

    try:
        file = await message.bot.get_file(message.voice.file_id)
        downloaded = await message.bot.download_file(file.file_path)
        pcm = ogg_to_pcm(downloaded.read())

        config = make_config(instruction)

        async with client.aio.live.connect(model=MODEL, config=config) as session:
            await session.send_realtime_input(
                audio=genai_types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
            )
            await session.send_realtime_input(audio_stream_end=True)
            pcm_chunks, input_transcript, output_transcript = await collect_response(
                session
            )

        if input_transcript:
            await message.reply(
                f"🎤 Ти сказав: <i>{input_transcript}</i>", parse_mode="HTML"
            )

        caption = (
            f"📝 {output_transcript}\n\n🎙 <b>{VOICE_NAME}</b>"
            if output_transcript
            else f"🎙 <b>{VOICE_NAME}</b>"
        )

        if pcm_chunks:
            ogg = pcm_to_ogg(b"".join(pcm_chunks))
            await message.reply_voice(
                types.BufferedInputFile(ogg, "reply.ogg"),
                caption=caption,
                parse_mode="HTML",
            )
        else:
            await message.reply("❌ Помилка генерації аудіо. Порожня відповідь.")

    except Exception as e:
        logging.error(f"[VoiceChat] Критична помилка для {uid}: {e}", exc_info=True)
        await message.reply("❌ Сталася технічна помилка.")
