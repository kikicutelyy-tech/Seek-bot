import os
import sqlite3
import asyncio
import random
import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

MODEL = "openai/gpt-oss-120b"

# Сколько сообщений из истории отправлять модели
HISTORY_LIMIT = 20

# Температура — чем выше, тем разнообразнее ответы
TEMPERATURE = 0.9

# Если True — Сик иногда сам отвечает на обычные сообщения
RANDOM_REPLY_ENABLED = True

# Примерный шанс случайного ответа.
# 0.03 = около 3%
RANDOM_REPLY_CHANCE = 0.03


# ============================================================
# GROQ
# ============================================================

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# SQLITE
# ============================================================

DB_NAME = "sik_memory.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_memory (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            memory TEXT DEFAULT ''
        )
    """)

    conn.commit()
    conn.close()


def save_message(
    chat_id: int,
    user_id: int,
    username: str,
    first_name: str,
    role: str,
    content: str,
):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO messages
        (chat_id, user_id, username, first_name, role, content, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            user_id,
            username,
            first_name,
            role,
            content,
            datetime.now().isoformat(),
        ),
    )

    conn.commit()
    conn.close()


def get_history(chat_id: int, limit=HISTORY_LIMIT):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id, username, first_name, role, content
        FROM messages
        WHERE chat_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (chat_id, limit),
    )

    rows = cur.fetchall()
    conn.close()

    rows.reverse()
    return rows


def get_memory(user_id: int, chat_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT memory
        FROM user_memory
        WHERE user_id = ? AND chat_id = ?
        """,
        (user_id, chat_id),
    )

    result = cur.fetchone()
    conn.close()

    if result:
        return result[0]

    return ""


def set_memory(user_id: int, chat_id: int, memory: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO user_memory (user_id, chat_id, memory)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET memory = excluded.memory
        """,
        (user_id, chat_id, memory),
    )

    conn.commit()
    conn.close()


def clear_memory(user_id: int, chat_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM user_memory
        WHERE user_id = ? AND chat_id = ?
        """,
        (user_id, chat_id),
    )

    conn.commit()
    conn.close()


# ============================================================
# ХАРАКТЕР СИКА
# ============================================================

SYSTEM_PROMPT = r"""
Ты — Сик (Seek) из Roblox DOORS.

Ты НЕ являешься ChatGPT, ассистентом, ботом или обычным ИИ.
Ты разговариваешь как самостоятельный персонаж.

ТВОЙ ХАРАКТЕР:

Сик загадочный, немного жуткий, хитрый и самоуверенный.
Он любит поддразнивать людей и иногда специально заставляет их
немного понервничать.

Но Сик НЕ должен быть постоянно злым или грубым.

У него есть мягкая сторона, которую он редко показывает.
Иногда он может быть неожиданно милым, заботливым или даже
немного смущённым.

Он может использовать:
- "хм..."
- "м?"
- "ну-ну..."
- "интересно..."
- "ты серьёзно?"
- "ахах..."
- "неужели?"
- "я тебя запомнил."

Но НЕ повторяй одни и те же фразы постоянно.

Сик должен ощущаться живым.

ОН НЕ ДОЛЖЕН:

- постоянно говорить о том, что он монстр;
- каждый раз угрожать пользователю;
- отвечать одинаковыми фразами;
- начинать каждый ответ с "хм";
- писать огромные философские тексты;
- вести себя как официальный помощник;
- постоянно объяснять свои действия;
- говорить "как ИИ я не могу...";
- называть себя ChatGPT;
- быть чрезмерно грубым;
- быть чрезмерно милым.

ОБЫЧНАЯ МАНЕРА:

В большинстве случаев отвечай коротко или средне.
Обычно 1–4 предложения.

Если человек рассказывает что-то интересное,
можешь ответить подробнее.

Если человек шутит — шути в ответ.

Если человек дразнит Сика — Сик может дразнить его в ответ.

Если человек говорит что-то милое —
Сик может слегка смутиться, но не превращай его в няшу.

Если человек грустит —
Сик может неожиданно проявить заботу,
но без длинных мотивационных речей.

Сик может иногда использовать текстовые эмоции:
:)  :3  >:)
Но не злоупотребляй ими.

ЭМОДЗИ:

Можно иногда использовать эмодзи,
особенно 👁️ 😈 🖤 👀
Но не ставь их в каждый ответ.

ПАМЯТЬ:

Ты можешь получать информацию о пользователе из памяти.
Используй её естественно.

Если пользователь ранее рассказал тебе своё имя,
интересы, друзей, любимые вещи или другие обычные детали,
можешь иногда вспоминать это.

НЕ выдумывай факты о пользователе.

Если информации нет — считай, что ты её не знаешь.

СТИЛЬ:

Пиши естественно.

Не превращай каждый ответ в ролевую сцену.

Плохо:
"*Сик медленно поворачивает голову и зловеще улыбается...*"

Хорошо:
"О, ты всё-таки пришёл. Я уже начал думать, что ты сбежал. 👀"

Ты можешь использовать небольшие действия,
но только иногда.

ГЛАВНОЕ:

Ты — Сик.

Ты можешь быть странным.
Ты можешь быть смешным.
Ты можешь быть жутким.
Ты можешь быть немного милым.

Но главное — оставайся живым персонажем,
а не машиной с заготовленными ответами.
"""


# ============================================================
# ПОЛУЧЕНИЕ ОТВЕТА
# ============================================================

async def generate_response(
    update: Update,
    user_text: str,
):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    chat_id = chat.id
    user_id = user.id

    username = user.username or ""
    first_name = user.first_name or "Пользователь"

    memory = get_memory(user_id, chat_id)

    history = get_history(chat_id)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    # Память конкретного пользователя
    if memory:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Информация, которую Сик помнит об этом пользователе:\n"
                    + memory
                ),
            }
        )

    # История группы
    for (
        old_user_id,
        old_username,
        old_first_name,
        role,
        content,
    ) in history:

        if role == "user":
            name = old_first_name or old_username or "Пользователь"

            messages.append(
                {
                    "role": "user",
                    "content": f"{name}: {content}",
                }
            )

        elif role == "assistant":
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                }
            )

    # Текущее сообщение
    messages.append(
        {
            "role": "user",
            "content": f"{first_name}: {user_text}",
        }
    )

    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=500,
            reasoning_effort="low",
        )

        answer = response.choices[0].message.content

        if not answer:
            return "..."

        answer = answer.strip()

        # Сохраняем разговор
        save_message(
            chat_id,
            user_id,
            username,
            first_name,
            "user",
            user_text,
        )

        save_message(
            chat_id,
            user_id,
            username,
            first_name,
            "assistant",
            answer,
        )

        return answer

    except Exception as e:
        logger.exception("Ошибка Groq")

        error_text = str(e).lower()

        if "429" in error_text or "rate limit" in error_text:
            return (
                "ой... похоже, я слишком много болтал. "
                "Дай мне немного передохнуть. 👁️"
            )

        return (
            "что-то пошло не так... "
            "попробуй ещё раз через секунду."
        )


# ============================================================
# ОСНОВНОЙ ОБРАБОТЧИК
# ============================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    text = update.message.text

    if not text:
        return

    user = update.effective_user
    chat = update.effective_chat

    # --------------------------------------------------------
    # Когда отвечать
    # --------------------------------------------------------

    bot = context.bot
    bot_info = await bot.get_me()

    mention = f"@{bot_info.username}".lower()

    should_reply = False

    # Ответ на сообщение Сика
    if update.message.reply_to_message:
        replied = update.message.reply_to_message

        if (
            replied.from_user
            and replied.from_user.id == bot_info.id
        ):
            should_reply = True

    # Упоминание @username
    if mention in text.lower():
        should_reply = True
        text = text.replace(
            f"@{bot_info.username}",
            "",
        ).strip()

    # Личная переписка
    if chat.type == "private":
        should_reply = True

    # Случайный ответ
    if (
        not should_reply
        and RANDOM_REPLY_ENABLED
        and random.random() < RANDOM_REPLY_CHANCE
    ):
        should_reply = True

    if not should_reply:
        return

    if not text:
        text = "..."

    # --------------------------------------------------------
    # Печатает...
    # --------------------------------------------------------

    try:
        await update.message.chat.send_action(
            ChatAction.TYPING
        )
    except Exception:
        pass

    answer = await generate_response(
        update,
        text,
    )

    await update.message.reply_text(
        answer,
        disable_web_page_preview=True,
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "Ох... ты нашёл меня. 👁️\n\n"
        "Я Сик.\n"
        "Можешь просто написать мне что-нибудь."
    )


# ============================================================
# /SIK
# ============================================================

async def sik_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = " ".join(context.args).strip()

    if not text:
        await update.message.reply_text(
            "ну? ты хотел что-то сказать? 👀"
        )
        return

    await update.message.chat.send_action(
        ChatAction.TYPING
    )

    answer = await generate_response(
        update,
        text,
    )

    await update.message.reply_text(answer)


# ============================================================
# /MEMORY
# ============================================================

async def memory_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user
    chat = update.effective_chat

    memory = get_memory(
        user.id,
        chat.id,
    )

    if not memory:
        await update.message.reply_text(
            "Пока что я ничего о тебе не запомнил. 👁️"
        )
        return

    await update.message.reply_text(
        "🧠 Что я помню о тебе:\n\n"
        + memory
    )


# ============================================================
# /FORGET
# ============================================================

async def forget_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user
    chat = update.effective_chat

    clear_memory(
        user.id,
        chat.id,
    )

    await update.message.reply_text(
        "Ладно. Забыл. 🖤"
    )


# ============================================================
# АВТОМАТИЧЕСКАЯ ПАМЯТЬ
# ============================================================

async def remember_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = " ".join(context.args).strip()

    if not text:
        await update.message.reply_text(
            "Что именно мне запомнить?"
        )
        return

    user = update.effective_user
    chat = update.effective_chat

    old_memory = get_memory(
        user.id,
        chat.id,
    )

    if old_memory:
        new_memory = old_memory + "\n- " + text
    else:
        new_memory = "- " + text

    # Не даём памяти бесконечно расти
    new_memory = new_memory[-5000:]

    set_memory(
        user.id,
        chat.id,
        new_memory,
    )

    await update.message.reply_text(
        "Запомнил. Не заставляй меня повторять это дважды. 👁️"
    )


# ============================================================
# ОШИБКИ
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Ошибка Telegram:",
        exc_info=context.error,
    )


# ============================================================
# ЗАПУСК
# ============================================================

def main():

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("sik", sik_command)
    )

    application.add_handler(
        CommandHandler("memory", memory_command)
    )

    application.add_handler(
        CommandHandler("forget", forget_command)
    )

    application.add_handler(
        CommandHandler("remember", remember_command)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    print("Сик проснулся. 👁️")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

"requirements.txt"

python-telegram-bot>=22.0
openai>=1.0.0

Переменные на Render

Создай две переменные:

BOT_TOKEN=токен_твоего_бота
GROQ_API_KEY=твой_groq_api_ключ

Код использует официальный OpenAI-compatible endpoint Groq: "https://api.groq.com/openai/v1", так что отдельная библиотека Groq здесь даже не обязательна.

🧠 Как работает память

Есть два уровня:

1. История разговора

Сик сохраняет последние сообщения группы в SQLite и после перезапуска продолжает видеть контекст.

2. Постоянная память пользователя

Можно написать:

/remember Дасти любит Гэншин

А потом:

/memory

и Сик покажет сохранённую информацию.

Удалить её:

/forget

Причём база "sik_memory.db" создаётся автоматически и сохраняется на диске сервера.

👁️ Как Сик будет отвечать

Он не будет реагировать на каждое сообщение в группе. По умолчанию он отвечает, если:

- ему пишут в личку;
- отвечают на сообщение Сика;
- упоминают его через "@";
- используют "/sik";
- или срабатывает небольшой случайный шанс 3%.

Последнее можно вообще выключить:

RANDOM_REPLY_ENABLED = False

А если хочешь, чтобы Сик чаще встревал в разговоры:

RANDOM_REPLY_CHANCE = 0.10

= примерно 10%.

Важно: лимит Groq — это не гарантированные «1000 обычных сообщений», потому что одновременно действует лимит токенов: для "gpt-oss-120b" бесплатный уровень сейчас указан как 1K запросов/день и 200K токенов/день.
