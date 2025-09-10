# advancing_query_bot.py
# Telegram бот за дневни списъци по ОФИС/Topic (форум нишки) – независим списък и напомняне за всеки Topic.
# Всяка сутрин ботът праща "Дата ..." + съобщение "Днешният списък:", което се ОБНОВЯВА при всяко добавяне.
# Добавен е интерактивен /edit уизард с бутони (Смени/Изтрий/Вмъкни/Премести). Потребителските съобщения се изтриват.
# Зависимости: python-telegram-bot[job-queue]==21.4, tzdata (за Windows)

import json
import os
from datetime import datetime, time
from typing import Dict, List, Optional

# Часова зона: Europe/Sofia (fallback към локалната, ако липсва tzdata на Windows)
try:
    from zoneinfo import ZoneInfo
    TIMEZONE = ZoneInfo("Europe/Sofia")
except Exception:
    TIMEZONE = datetime.now().astimezone().tzinfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

# ======================= НАСТРОЙКИ ==========================
import os
TOKEN = os.getenv("TOKEN", "").strip()
DAILY_HOUR = 10  # 10:00 местно време
DATA_FILE = "order_data.json"
SEP = "#"
# ============================================================

# ------------------ СЪХРАНЕНИЕ ------------------
def topic_key(chat_id: int, thread_id: Optional[int]) -> str:
    return f"{chat_id}{SEP}{thread_id if thread_id is not None else 0}"

def load_data() -> Dict:
    if not os.path.exists(DATA_FILE):
        return {"enabled_topics": [], "lists": {}, "list_msgs": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("enabled_topics", [])
            data.setdefault("lists", {})
            data.setdefault("list_msgs", {})
            return data
    except Exception:
        return {"enabled_topics": [], "lists": {}, "list_msgs": {}}

def save_data(data: Dict) -> None:
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)

def today_key() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")

def render_list_text(items: List[str]) -> str:
    header = "Днешният списък:"
    if not items:
        return header
    bullets = "\n".join("• " + x for x in items)
    return f"{header}\n{bullets}"

def append_item(k: str, item: str) -> None:
    data = load_data()
    lists = data.setdefault("lists", {})
    topic_lists = lists.setdefault(k, {})
    day = today_key()
    topic_lists.setdefault(day, [])
    if item.strip():
        topic_lists[day].append(item.strip())
    save_data(data)

def clear_today(k: str) -> None:
    data = load_data()
    lists = data.setdefault("lists", {})
    lists.setdefault(k, {})[today_key()] = []
    save_data(data)

def get_today(k: str) -> List[str]:
    data = load_data()
    return data.get("lists", {}).get(k, {}).get(today_key(), [])

def set_today_list(k: str, items: List[str]) -> None:
    data = load_data()
    lists = data.setdefault("lists", {})
    topic_lists = lists.setdefault(k, {})
    topic_lists[today_key()] = items
    save_data(data)

def set_list_message_id(k: str, message_id: int) -> None:
    data = load_data()
    lm = data.setdefault("list_msgs", {})
    per_topic = lm.setdefault(k, {})
    per_topic[today_key()] = int(message_id)
    save_data(data)

def get_list_message_id(k: str) -> Optional[int]:
    data = load_data()
    msg_id = data.get("list_msgs", {}).get(k, {}).get(today_key())
    return int(msg_id) if msg_id is not None else None

def enable_topic(chat_id: int, thread_id: Optional[int]) -> None:
    data = load_data()
    k = topic_key(chat_id, thread_id)
    if k not in data["enabled_topics"]:
        data["enabled_topics"].append(k)
        save_data(data)

def disable_topic(chat_id: int, thread_id: Optional[int]) -> None:
    data = load_data()
    k = topic_key(chat_id, thread_id)
    if k in data["enabled_topics"]:
        data["enabled_topics"].remove(k)
        save_data(data)

# ------------------ ПОМОЩНИЦИ ------------------
async def send_in_topic(context: ContextTypes.DEFAULT_TYPE, chat_id: int, thread_id: Optional[int], text: str):
    kwargs = {"chat_id": chat_id, "text": text}
    if thread_id is not None and thread_id != 0:
        kwargs["message_thread_id"] = thread_id
    return await context.bot.send_message(**kwargs)

async def ensure_list_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, thread_id: Optional[int], k: str) -> int:
    msg_id = get_list_message_id(k)
    if msg_id is None:
        sent = await send_in_topic(context, chat_id, thread_id, render_list_text(get_today(k)))
        set_list_message_id(k, sent.message_id)
        return sent.message_id
    return msg_id

async def update_list_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, thread_id: Optional[int], k: str) -> None:
    msg_id = get_list_message_id(k)
    text = render_list_text(get_today(k))
    if msg_id is None:
        sent = await send_in_topic(context, chat_id, thread_id, text)
        set_list_message_id(k, sent.message_id)
        return
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text)
    except Exception:
        sent = await send_in_topic(context, chat_id, thread_id, text)
        set_list_message_id(k, sent.message_id)

# ------------------ JOB ------------------
async def daily_prompt_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    job_data = context.job.data or {}
    chat_id = job_data.get("chat_id")
    thread_id = job_data.get("thread_id")
    k = topic_key(chat_id, thread_id)

    clear_today(k)
    today = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    header = f"Дата {today}, списък за изпращане :"
    try:
        await send_in_topic(context, chat_id, thread_id if thread_id != 0 else None, header)
        await ensure_list_message(context, chat_id, (thread_id if thread_id != 0 else None), k)
        await update_list_message(context, chat_id, (thread_id if thread_id != 0 else None), k)
    except Exception:
        pass

# ------------------ HANDЛЕРИ ------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    thread_id = getattr(update.effective_message, "message_thread_id", None)
    msg = """Здравей! Ботът работи по Topics.
Пусни /enable във ВСЕКИ офис-Topic, за да има отделен списък и дневно съобщение в 10:00.

Команди в текущия Topic:
• /enable – включва дневното съобщение и списък за тази нишка
• /disable – спира дневното съобщение за тази нишка
• /show – показва днешния списък за тази нишка
• /clear – изчиства днешния списък за тази нишка
• /edit – интерактивна редакция с бутони (Смени/Изтрий/Вмъкни/Премести)
• Пишете артикул като текст – ще бъде изтрит и добавен към списъка на тази нишка (и ще се редактира „Днешният списък:“).
"""
    await send_in_topic(context, update.effective_chat.id, thread_id, msg)

async def enable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.job_queue is None:
        await update.effective_message.reply_text(
            "Нужно е да инсталираш python-telegram-bot[job-queue], за да работи дневното напомняне.")
        return
    chat_id = update.effective_chat.id
    thread_id = getattr(update.effective_message, "message_thread_id", None)
    k = topic_key(chat_id, thread_id)
    enable_topic(chat_id, thread_id)
    job_name = f"daily_{chat_id}_{thread_id if thread_id is not None else 0}"
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    context.job_queue.run_daily(
        daily_prompt_job,
        time=time(hour=DAILY_HOUR, minute=0, tzinfo=TIMEZONE),
        name=job_name,
        data={"chat_id": chat_id, "thread_id": (thread_id if thread_id is not None else 0)},
    )
    await ensure_list_message(context, chat_id, thread_id, k)
    await send_in_topic(context, chat_id, thread_id, "Готово! Този Topic е активиран за 10:00 и има собствен списък.")

async def disable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    thread_id = getattr(update.effective_message, "message_thread_id", None)
    disable_topic(chat_id, thread_id)
    if context.job_queue is not None:
        job_name = f"daily_{chat_id}_{thread_id if thread_id is not None else 0}"
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()
    await send_in_topic(context, chat_id, thread_id, "Този Topic е деактивиран за дневното съобщение.")

async def show_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    thread_id = getattr(update.effective_message, "message_thread_id", None)
    k = topic_key(chat_id, thread_id)
    await ensure_list_message(context, chat_id, thread_id, k)
    await update_list_message(context, chat_id, thread_id, k)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    thread_id = getattr(update.effective_message, "message_thread_id", None)
    k = topic_key(chat_id, thread_id)
    clear_today(k)
    await update_list_message(context, chat_id, thread_id, k)

async def delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    msg_id = data.get("message_id")
    if chat_id and msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

async def capture_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    thread_id = getattr(msg, "message_thread_id", None)
    k = topic_key(chat.id, thread_id)

    # ❗ Обработваме текст САМО ако този Topic е активиран с /enable
    data = load_data()
    if k not in data.get("enabled_topics", []):
        return  # нищо не правим – без изтриване, без добавяне

    text_in = (msg.text or "").strip()
    if not text_in:
        return

    # Проверка дали чакаме текст за уизарда /edit (set/ins)
    state_key = f"edit:{k}"
    state = context.user_data.get(state_key)
    if state and state.get("await") == "text":
        mode = state.get("mode")
        idx = state.get("index")
        items = get_today(k)
        changed = False
        notice = None

        if mode == "set":
            if idx and 1 <= idx <= len(items):
                items[idx - 1] = text_in
                changed = True
                notice = f"✅ Ред {idx} е обновен."
        elif mode == "ins":
            if idx and 1 <= idx <= len(items) + 1:
                items.insert(idx - 1, text_in)
                changed = True
                notice = f"✅ Вмъкнато преди позиция {idx}."

        context.user_data.pop(state_key, None)

        try:
            await msg.delete()
        except Exception:
            pass

        if changed:
            set_today_list(k, items)
            await ensure_list_message(context, chat.id, thread_id, k)
            await update_list_message(context, chat.id, thread_id, k)
            try:
                sent = await send_in_topic(context, chat.id, thread_id, notice or "Обнових списъка.")
                if context.job_queue is not None:
                    context.job_queue.run_once(
                        delete_message_job, when=5, data={"chat_id": chat.id, "message_id": sent.message_id}
                    )
            except Exception:
                pass
        else:
            await send_in_topic(context, chat.id, thread_id, "Невалидна позиция за редакция.")
        return

    # Обичайното поведение: артикул за добавяне
    try:
        await msg.delete()
    except Exception:
        pass

    append_item(k, text_in)

    try:
        await ensure_list_message(context, chat.id, thread_id, k)
        await update_list_message(context, chat.id, thread_id, k)
    except Exception:
        pass

    try:
        sent = await send_in_topic(context, chat.id, thread_id, "Списъкът е обновен.")
        if context.job_queue is not None:
            context.job_queue.run_once(
                delete_message_job,
                when=3,
                data={"chat_id": chat.id, "message_id": sent.message_id},
            )
    except Exception:
        pass

# ---------- /edit уизард (бутони) ----------
async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Стартира интерактивен уизард за редакция с бутони в текущия Topic."""
    chat_id = update.effective_chat.id
    thread_id = getattr(update.effective_message, "message_thread_id", None)

    keyboard = [
        [
            InlineKeyboardButton("✏️ Смени", callback_data="edit_set"),
            InlineKeyboardButton("🗑️ Изтрий", callback_data="edit_del"),
        ],
        [
            InlineKeyboardButton("➕ Вмъкни", callback_data="edit_ins"),
            InlineKeyboardButton("↔️ Премести", callback_data="edit_move"),
        ],
        [InlineKeyboardButton("❌ Откажи", callback_data="edit_cancel")],
    ]
    markup = InlineKeyboardMarkup(keyboard)

    await send_in_topic(
        context,
        chat_id,
        thread_id,
        """Какво искаш да направиш със списъка?
(Избери действие от бутоните)""",
    )
    await update.effective_message.reply_text("Избери действие:", reply_markup=markup)


def _index_keyboard(n: int, prefix: str) -> InlineKeyboardMarkup:
    # Генерира клавиатура 1..n с бутони, по 5 на ред
    n = max(0, int(n))
    rows = []
    row = []
    for i in range(1, n + 1):
        row.append(InlineKeyboardButton(str(i), callback_data=f"{prefix}_{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Откажи", callback_data="edit_cancel")])
    return InlineKeyboardMarkup(rows)


async def on_edit_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data  # edit_set/del/ins/move/cancel

    chat = query.message.chat
    thread_id = getattr(query.message, "message_thread_id", None)
    k = topic_key(chat.id, thread_id)

    if data == "edit_cancel":
        context.user_data.pop(f"edit:{k}", None)
        await query.edit_message_text("❌ Отказано.")
        return

    mode = data.split("_", 1)[1]  # set/del/ins/move
    items = get_today(k)

    if mode in ("set", "del", "move") and not items:
        await query.edit_message_text("Няма редове за редакция. Използвай ➕ Вмъкни.")
        return

    context.user_data[f"edit:{k}"] = {"mode": mode}

    if mode == "set":
        await query.edit_message_text("Избери кой ред да сменя:", reply_markup=_index_keyboard(len(items), "pick"))
    elif mode == "del":
        await query.edit_message_text("Избери кой ред да изтрия:", reply_markup=_index_keyboard(len(items), "pick"))
    elif mode == "ins":
        await query.edit_message_text(
            "Избери ПРЕД коя позиция да вмъкна (или последната за накрая):",
            reply_markup=_index_keyboard(len(items) + 1, "pick"),
        )
    elif mode == "move":
        await query.edit_message_text("Избери кой ред да преместя:", reply_markup=_index_keyboard(len(items), "pick"))


async def on_pick_index(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data  # pick_N
    idx = int(data.split("_", 1)[1])
    chat = query.message.chat
    thread_id = getattr(query.message, "message_thread_id", None)
    k = topic_key(chat.id, thread_id)

    state_key = f"edit:{k}"
    state = context.user_data.get(state_key) or {}
    mode = state.get("mode")
    items = get_today(k)

    if mode == "del":
        if 1 <= idx <= len(items):
            removed = items.pop(idx - 1)
            set_today_list(k, items)
            await update_list_message(context, chat.id, thread_id, k)
            await query.edit_message_text(f"✅ Изтрих ред {idx}: {removed}")
        else:
            await query.edit_message_text("Невалиден индекс.")
        context.user_data.pop(state_key, None)
        return

    if mode == "set":
        context.user_data[state_key] = {"mode": "set", "await": "text", "index": idx}
        await query.edit_message_text(f"Изпрати новия текст за ред {idx} като съобщение.")
        return

    if mode == "ins":
        context.user_data[state_key] = {"mode": "ins", "await": "text", "index": idx}
        await query.edit_message_text(
            f"Изпрати текст за вмъкване ПРЕДИ позиция {idx} (или последна за накрая)."
        )
        return

    if mode == "move":
        context.user_data[state_key] = {"mode": "move", "await": "toindex", "from": idx}
        await query.edit_message_text("Избери НОВА позиция:", reply_markup=_index_keyboard(len(items), "pickto"))
        return

    await query.edit_message_text("Неподдържано действие.")


async def on_pick_to_index(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data  # pickto_N
    dst = int(data.split("_", 1)[1])
    chat = query.message.chat
    thread_id = getattr(query.message, "message_thread_id", None)
    k = topic_key(chat.id, thread_id)

    state_key = f"edit:{k}"
    state = context.user_data.get(state_key) or {}
    src = state.get("from")
    items = get_today(k)

    if not src or not (1 <= src <= len(items)) or not (1 <= dst <= len(items)):
        await query.edit_message_text("Невалидни позиции.")
        context.user_data.pop(state_key, None)
        return

    if src == dst:
        await query.edit_message_text("Позициите съвпадат – няма промяна.")
        context.user_data.pop(state_key, None)
        return

    itm = items.pop(src - 1)
    items.insert(dst - 1, itm)
    set_today_list(k, items)
    await update_list_message(context, chat.id, thread_id, k)

    await query.edit_message_text(f"✅ Преместих ред {src} → {dst}.")
    context.user_data.pop(state_key, None)

# ------------------ MAIN ------------------
def main() -> None:
    if not TOKEN or TOKEN == "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        raise RuntimeError("Моля, постави валиден TOKEN в променливата TOKEN в кода.")
    app = Application.builder().token(TOKEN).build()

    data = load_data()
    if app.job_queue is not None:
        for k in data.get("enabled_topics", []):
            try:
                chat_id_str, thread_str = k.split(SEP, 1)
                chat_id = int(chat_id_str)
                thread_id = int(thread_str)
            except Exception:
                continue
            job_name = f"daily_{chat_id}_{thread_id}"
            for job in app.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
            app.job_queue.run_daily(
                daily_prompt_job,
                time=time(hour=DAILY_HOUR, minute=0, tzinfo=TIMEZONE),
                name=job_name,
                data={"chat_id": chat_id, "thread_id": thread_id},
            )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("enable", enable_cmd))
    app.add_handler(CommandHandler("disable", disable_cmd))
    app.add_handler(CommandHandler("show", show_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("edit", edit_cmd))

    app.add_handler(CallbackQueryHandler(on_edit_action, pattern=r"^edit_(set|del|ins|move|cancel)$"))
    app.add_handler(CallbackQueryHandler(on_pick_index, pattern=r"^pick_([0-9]+)$"))
    app.add_handler(CallbackQueryHandler(on_pick_to_index, pattern=r"^pickto_([0-9]+)$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, capture_text))

    print("Bot is running… Press Ctrl+C to stop.")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
