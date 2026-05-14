from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import database as db
import ai_manager
import i18n
import html
from deep_translator import GoogleTranslator
from states import AIHelper, WordOfDayState
from keyboards import get_main_kb, _study_lang_keyboard
from utils import ulang, ul, study_langs, ai_lang_name, get_user_level_info
from handlers.general import cmd_exit
 
router = Router()

@router.message(Command("word_of_day"))
async def cmd_word_of_day(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await state.set_state(WordOfDayState.waiting_for_language)
    await message.answer(ul(uid, "word_of_day.choose_lang"),
                         reply_markup=_study_lang_keyboard(uid))
 
@router.message(WordOfDayState.waiting_for_language)
async def process_wod_lang(message: types.Message, state: FSMContext):
    if not message.text:
        return
    uid        = message.from_user.id
    lang_learn = message.text.strip()
    
    if lang_learn == '/exit':
        return await cmd_exit(message, state)
        
    if lang_learn not in study_langs(uid):
        await message.answer(ul(uid, "word_of_day.choose_lang"),
                             reply_markup=_study_lang_keyboard(uid))
        return
        
    await message.answer(ul(uid, "word_of_day.generating", lang=lang_learn))
    
    lvl, _, _ = await get_user_level_info(uid)
    diff      = "A1" if lvl <= 3 else "B1" if lvl <= 8 else "C1"
    native    = ai_lang_name(uid)
    
    prompt    = (f"Generate exactly ONE word in {lang_learn} for level {diff} "
                 f"with {native} translation. Format strictly: Apple - Яблуко")
    result    = await ai_manager.generate_content_safe(prompt, uid=uid)
    
    w, t_word = None, None
    for line in result.split('\n'):
        line = line.strip().replace("*", "")
        if " - " in line and "Слово" not in line and "Word" not in line:
            parts = line.split(" - ", 1)
            w, t_word = parts[0].strip(), parts[1].strip()
            break
            
    if w and t_word:
        info   = await ai_manager.get_full_word_info(w, t_word, lang_learn, response_lang=native, uid=uid)
        transc = info["transcription"]
        assoc  = info["association"]
        
        try:
            w_en = GoogleTranslator(source='auto', target='en').translate(w)
        except:
            w_en = w
            
        img = (await ai_manager.get_image_url(info["image_query"]) or
               await ai_manager.get_image_url(w_en) or
               await ai_manager.get_image_url(w))
               
        await state.update_data(new_word=w, translation=t_word, lang=lang_learn,
                                image_url=img, association=assoc, transcription=transc)
                                
        msg_text = ul(uid, "word_of_day.result",
                      word=html.escape(w), transcription=html.escape(transc or ""),
                      translation=html.escape(t_word))
                      
        lbl_assoc = ul(uid, "word_of_day.association_lbl")
        lbl_ex = ul(uid, "word_of_day.examples_lbl")
        
        if lbl_assoc == "word_of_day.association_lbl": lbl_assoc = "Асоціація"
        if lbl_ex == "word_of_day.examples_lbl": lbl_ex = "Приклади"

        if assoc:
            msg_text += f"\n\n💡 <b>{lbl_assoc}:</b> <i>{html.escape(assoc)}</i>"
            
        examples = info.get("examples", [])
        if examples:
            msg_text += f"\n\n📝 <b>{lbl_ex}:</b>\n"
            for ex in examples:
                msg_text += f"🔸 <i>{html.escape(ex)}</i>\n"

        if len(msg_text) > 1024:
            msg_text = msg_text[:1020] + "..."

        inline = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text=ul(uid, "btn.regen_photo"),
                                       callback_data=f"regen:{w[:20]}")]])
                                       
        wod_kb = types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text=ul(uid, "word_of_day.add_btn"))],
                      [types.KeyboardButton(text=ul(uid, "btn.exit_menu"))]],
            resize_keyboard=True)
            
        try:
            if img:
                await message.answer_photo(img, caption=msg_text, reply_markup=inline, parse_mode="HTML")
            else:
                await message.answer(msg_text, reply_markup=inline, parse_mode="HTML")
        except:
            await message.answer(f"🌟 {w} {transc}\n {t_word}", reply_markup=inline)
            
        await message.answer(ul(uid, "word_of_day.actions"), reply_markup=wod_kb)
        await state.set_state(WordOfDayState.waiting_for_action)
    else:
        await message.answer(ul(uid, "word_of_day.ai_confused", result=result),
                             reply_markup=await get_main_kb(uid))
        await state.clear()


@router.message(WordOfDayState.waiting_for_action)
async def process_wod_action(message: types.Message, state: FSMContext):
    if not message.text:
        return
    uid = message.from_user.id
    if message.text == ul(uid, "btn.exit_menu"):
        return await cmd_exit(message, state)
    if message.text == ul(uid, "word_of_day.add_btn"):
        data  = await state.get_data()
        added = await db.add_word_to_db(uid, data['new_word'], data['translation'],
                                        data['lang'], data['image_url'],
                                        data['association'], data['transcription'])
        await message.answer(
            ul(uid, "word_of_day.added", association=data['association']) if added
            else ul(uid, "word_of_day.already"),
            reply_markup=await get_main_kb(uid)
        )
        await state.clear()
 
@router.message(Command("AI"))
@router.message(F.text == "🤖 /AI")
async def cmd_ai(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer(ul(uid, "ai.enter_word"), reply_markup=await get_main_kb(uid))
 
@router.message(AIHelper.waiting_for_prompt)
async def process_ai_prompt(message: types.Message, state: FSMContext):
    if not message.text:
        return
    if message.text == '/exit':
        return await cmd_exit(message, state)
    uid   = message.from_user.id
    word  = message.text.strip()
    hobby = await db.get_user_hobby(uid) or "everyday life"
    await message.answer(ul(uid, "ai.thinking"))
    txt = ul(uid, "errors.gen_error")
    try:
        try:
            word_en = GoogleTranslator(source='auto', target='en').translate(word)
        except:
            word_en = word
 
        import asyncio
        txt, img = await asyncio.gather(
            ai_manager.get_ai_explanation_text(word, "auto", hobby,
                                               response_lang=ai_lang_name(uid)),
            ai_manager.get_image_url(word_en)
        )
        if not img:
            img = await ai_manager.get_image_url(word)
 
        inline = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text=ul(uid, "btn.regen_photo"),
                                       callback_data=f"regen:{word_en[:20]}")]])
        prefix = ul(uid, "ai.result_prefix")
        if img:
            await message.answer_photo(img, caption=f"{prefix}{txt}"[:1024],
                                       reply_markup=inline, parse_mode="HTML")
        else:
            await message.answer(f"{prefix}{txt}", reply_markup=inline, parse_mode="HTML")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"⚠️ AI error: {e}")
        await message.answer(f"🤖\n\n{txt}", parse_mode=None)
    await message.answer(ul(uid, "ai.ask_next"))
