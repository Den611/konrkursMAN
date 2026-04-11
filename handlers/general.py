from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import json
import io
import i18n
import database as db
import ai_manager
from keyboards import get_main_kb
from states import FeedbackState
from utils import ul, ulang, _update_progress
 
router = Router()

@router.message(Command("help"))
@router.message(F.text.in_(["Допомога ❓", "Pomoc ❓", "Help ❓"]))
async def cmd_help(message: types.Message):
    await message.answer(ul(message.from_user.id, "help.title"), parse_mode="HTML",
                         reply_markup=await get_main_kb(message.from_user.id))

@router.message(Command("exit"))
async def cmd_exit(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await db.update_last_active(uid)
    await state.clear()
    await message.answer(ul(uid, "exit.done", commands=ul(uid, "help.commands_list")),
                         reply_markup=await get_main_kb(uid))

@router.message(Command("feedback"))
@router.message(F.text.in_(["Відгук 💬", "Opinia 💬", "Feedback 💬"]))
async def cmd_feedback(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await state.set_state(FeedbackState.waiting_for_message)
    await message.answer(ul(uid, "feedback.prompt"), reply_markup=types.ReplyKeyboardRemove())
 
@router.message(FeedbackState.waiting_for_message)
async def process_feedback(message: types.Message, state: FSMContext):
    if not message.text:
        return
    uid = message.from_user.id
    if message.text == '/exit':
        return await cmd_exit(message, state)
    await db.save_feedback(uid, message.from_user.username or "Unknown", message.text)
    await state.clear()
    await message.answer(ul(uid, "feedback.thanks"), reply_markup=await get_main_kb(uid))

@router.callback_query(F.data.startswith("regen:"))
async def callback_regenerate(callback: types.CallbackQuery):
    uid = callback.from_user.id
    try:
        word_prefix = callback.data.split(":")[1]
        new_url     = await ai_manager.get_image_url(word_prefix, use_random=True)
        if new_url:
            await db.db_execute(
                "UPDATE user_words SET image_url=$1 WHERE user_id=$2 AND word LIKE $3",
                new_url, uid, f"{word_prefix}%")
            await callback.message.edit_media(
                media=types.InputMediaPhoto(media=new_url, caption=callback.message.caption,
                                            parse_mode="HTML"),
                reply_markup=callback.message.reply_markup)
            await callback.answer(ul(uid, "btn.photo_updated"))
        else:
            await callback.answer(ul(uid, "btn.no_photo"), show_alert=True)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"⚠️ Помилка регенерації: {e}")
        await callback.answer(ul(uid, "errors.regen_err"), show_alert=True)

@router.message(F.content_type == types.ContentType.WEB_APP_DATA)
async def process_web_app_data(message: types.Message):
    uid  = message.from_user.id
    data = json.loads(message.web_app_data.data)
    if data.get('type') == 'game_result':
        score = data.get('score', 0)
        for w in data.get('learned_words', []):
            await db.update_word_progress(uid, w, True)
        level, streak, _ = await _update_progress(uid)
        current_best = await db.get_best_score(uid)
        msg = ul(uid, "webapp.result", score=score, count=len(data.get('learned_words', [])))
        if score > current_best:
            await db.update_best_score(uid, score)
            msg += ul(uid, "webapp.new_record", old=current_best)
        await message.answer(msg, reply_markup=await get_main_kb(uid))
