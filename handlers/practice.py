import random
import html
import fuzzy_ext
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import database as db
import i18n
from states import PracticeWord
from keyboards import get_main_kb
from utils import ulang, ul, _update_progress
from handlers.general import cmd_exit
 
router = Router()

@router.message(Command("practice"))
async def cmd_practice(message: types.Message, state: FSMContext):
    uid   = message.from_user.id
    words = await db.get_user_words(uid, for_review=True)
    if not words:
        return await message.answer(ul(uid, "practice.all_done"),
                                    reply_markup=await get_main_kb(uid))
    await state.update_data(all_practice_words=[dict(w) for w in words])
    all_langs_btn = ul(uid, "practice.all_langs_btn")
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in set(w['language'] for w in words)] +
                 [[types.KeyboardButton(text=all_langs_btn)],
                  [types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(PracticeWord.waiting_for_language)
    await message.answer(ul(uid, "practice.words_today", count=len(words)), reply_markup=lang_kb)
 
@router.message(PracticeWord.waiting_for_language)
async def practice_choose_lang(message: types.Message, state: FSMContext):
    if not message.text: return
    uid = message.from_user.id
    if message.text == '/exit':
        return await cmd_exit(message, state)
        
    target = (await state.get_data())['all_practice_words']
    all_langs = ul(uid, "practice.all_langs_btn")
    if message.text != all_langs:
        target = [w for w in target if w['language'] == message.text]
        
    if not target:
        return await message.answer(ul(uid, "practice.empty_lang"))
        
    random.shuffle(target)
    await state.update_data(plist=target[:10], pidx=0)
    await state.set_state(PracticeWord.waiting_for_answer)
    
    await send_practice_flashcard(message, uid, target[0])
 
async def send_practice_flashcard(message_obj: types.Message, uid: int, word_data: dict):
    q = ul(uid, "practice.question", translation=html.escape(word_data['translation']), lang=word_data['language'])
    q += "\n\n⌨️ <i>Напишіть переклад текстом, або натисніть кнопку, щоб здатися.</i>"
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="👀 Не знаю (Показати)", callback_data="pract_show")
    ]])
    
    if word_data['image_url']:
        await message_obj.answer_photo(word_data['image_url'], caption=q, reply_markup=kb, parse_mode="HTML")
    else:
        await message_obj.answer(q, reply_markup=kb, parse_mode="HTML")
 
@router.callback_query(F.data == "pract_show", PracticeWord.waiting_for_answer)
async def practice_show_answer(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    data = await state.get_data()
    w = data['plist'][data['pidx']]
    
    ans_text = (
        f"🎯 <b>Правильна відповідь:</b>\n\n"
        f"🇬🇧 Слово: <b>{html.escape(w['word'])}</b> {html.escape(w['transcription'] or '')}\n"
        f"🇺🇦 Переклад: {html.escape(w['translation'])}\n"
        f"💡 Асоціація: <i>{html.escape(w['association'] or '')}</i>\n\n"
        f"Оціни, наскільки добре ти пам'ятав це слово:"
    )
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🟢 Легко (5)", callback_data="pract_q:5"),
         types.InlineKeyboardButton(text="🟡 Нормально (4)", callback_data="pract_q:4")],
        [types.InlineKeyboardButton(text="🟠 Важко (3)", callback_data="pract_q:3"),
         types.InlineKeyboardButton(text="🔴 Забув (1)", callback_data="pract_q:1")]
    ])
    
    if callback.message.photo:
        await callback.message.edit_caption(caption=ans_text, reply_markup=kb, parse_mode="HTML")
    else:
        await callback.message.edit_text(text=ans_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()
 
@router.callback_query(F.data.startswith("pract_q:"), PracticeWord.waiting_for_answer)
async def process_practice_quality(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    quality = int(callback.data.split(":")[1])
    
    data = await state.get_data()
    w = data['plist'][data['pidx']]
    lang = ulang(uid)
    
    await db.update_word_progress_sm2(uid, w['word'], quality)
    level, streak, is_new_day = await _update_progress(uid)
    
    data['pidx'] += 1
    
    await callback.message.edit_reply_markup(reply_markup=None)
    
    if data['pidx'] >= len(data['plist']):
        summary = ul(uid, "practice.finished")
        summary += f"\n\n{i18n.t(lang, 'practice.summary_level', level=level)}"
        
        if is_new_day and streak > 1:
            summary += f"\n{i18n.t(lang, 'practice.streak_msg', streak=streak)}"
            if streak in (3, 7, 14, 30, 100):
                summary += f"\n{i18n.t(lang, 'practice.streak_achievement', streak=streak)}"
        elif streak > 0:
            summary += f"\n{i18n.t(lang, 'practice.summary_streak', streak=streak)}"
            
        await callback.message.answer(summary, parse_mode="HTML", reply_markup=await get_main_kb(uid))
        await state.clear()
    else:
        await state.update_data(pidx=data['pidx'])
        next_w = data['plist'][data['pidx']]
        await send_practice_flashcard(callback.message, uid, next_w)
        
    await callback.answer()
 
@router.message(PracticeWord.waiting_for_answer)
async def process_practice_text_input(message: types.Message, state: FSMContext):
    if not message.text:
        return
    if message.text == "/exit":
        return await cmd_exit(message, state)
 
    uid = message.from_user.id
    data = await state.get_data()
    w = data['plist'][data['pidx']]
    lang = ulang(uid)
 
    user_answer = message.text.strip().lower()
    correct_answer = w['word'].lower()
 
    sim = fuzzy_ext.similarity(user_answer, correct_answer)
 
    if sim == 1.0:
        quality = 5
        feedback = f"🎯 <b>Ідеально!</b> Це дійсно <b>{html.escape(w['word'])}</b>."
    elif sim >= 0.8:
        quality = 4
        feedback = f"🟡 <b>Майже правильно!</b> Опечатка.\nПравильно: <b>{html.escape(w['word'])}</b>"
    elif sim >= 0.6:
        quality = 3
        feedback = f"🟠 <b>Близько, але є помилки.</b>\nПравильно: <b>{html.escape(w['word'])}</b>"
    else:
        quality = 1
        feedback = f"🔴 <b>Неправильно.</b>\nПравильно: <b>{html.escape(w['word'])}</b>"
 
    await message.answer(feedback, parse_mode="HTML")
 
    await db.update_word_progress_sm2(uid, w['word'], quality)
    level, streak, is_new_day = await _update_progress(uid)
 
    data['pidx'] += 1
    
    if data['pidx'] >= len(data['plist']):
        summary = ul(uid, "practice.finished")
        summary += f"\n\n{i18n.t(lang, 'practice.summary_level', level=level)}"
        
        if is_new_day and streak > 1:
            summary += f"\n{i18n.t(lang, 'practice.streak_msg', streak=streak)}"
            if streak in (3, 7, 14, 30, 100):
                summary += f"\n{i18n.t(lang, 'practice.streak_achievement', streak=streak)}"
        elif streak > 0:
            summary += f"\n{i18n.t(lang, 'practice.summary_streak', streak=streak)}"
            
        await message.answer(summary, parse_mode="HTML", reply_markup=await get_main_kb(uid))
        await state.clear()
    else:
        await state.update_data(pidx=data['pidx'])
        next_w = data['plist'][data['pidx']]
        await send_practice_flashcard(message, uid, next_w)
