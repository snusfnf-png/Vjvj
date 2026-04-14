#!/usr/bin/env python3
"""
🎨 Sticker & Emoji Recolor Bot
Перекрашивает WebP-стикеры, TGS-анимации (Lottie) и премиум кастомные эмодзи.
"""

import os
import io
import re
import gzip
import json
import time
import zipfile
import sqlite3
import asyncio
import numpy as np
from PIL import Image
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
DB_PATH:   str = "bot.db"

# ══════════════════════════════════════════════════════════════
#  PALETTE  (12 базовых цветов)
# ══════════════════════════════════════════════════════════════

PALETTE: list[tuple[str, str]] = [
    ("🔴 Красный",    "#FF3333"),
    ("🟠 Оранжевый",  "#FF8C00"),
    ("🟡 Жёлтый",     "#FFD700"),
    ("🟢 Зелёный",    "#00C853"),
    ("🔵 Синий",      "#1565C0"),
    ("🟣 Фиолетовый", "#7B1FA2"),
    ("🩷 Розовый",    "#FF4081"),
    ("🩵 Голубой",    "#00B4D8"),
    ("⚪ Белый",      "#FFFFFF"),
    ("⚫ Чёрный",     "#1A1A1A"),
    ("🟤 Коричневый", "#795548"),
    ("🩶 Серый",      "#9E9E9E"),
]

# ══════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY,
                username   TEXT,
                full_name  TEXT,
                last_color TEXT    DEFAULT '#FF3333',
                processed  INTEGER DEFAULT 0,
                joined_at  INTEGER DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS history (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                uid     INTEGER,
                kind    TEXT,
                color   TEXT,
                pack    TEXT,
                ts      INTEGER DEFAULT (strftime('%s','now'))
            );
        """)


def db_touch(uid: int, username: str, full_name: str) -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """INSERT INTO users(id, username, full_name) VALUES (?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   username=excluded.username, full_name=excluded.full_name""",
            (uid, username, full_name),
        )


def db_last_color(uid: int) -> str:
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT last_color FROM users WHERE id=?", (uid,)).fetchone()
    return row[0] if row else "#FF3333"


def db_set_color(uid: int, color: str) -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE users SET last_color=? WHERE id=?", (color, uid))


def db_add(uid: int, n: int = 1) -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE users SET processed=processed+? WHERE id=?", (n, uid))


def db_log(uid: int, kind: str, color: str, pack: str | None = None) -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT INTO history(uid,kind,color,pack) VALUES(?,?,?,?)",
            (uid, kind, color, pack),
        )


def db_stats(uid: int):
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT processed, last_color, joined_at FROM users WHERE id=?", (uid,)
        ).fetchone()

# ══════════════════════════════════════════════════════════════
#  COLOR UTILS
# ══════════════════════════════════════════════════════════════

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def valid_hex(s: str) -> bool:
    return bool(re.fullmatch(r"#?[0-9A-Fa-f]{6}", s.strip()))


def norm_hex(s: str) -> str:
    return "#" + s.strip().lstrip("#").upper()

# ══════════════════════════════════════════════════════════════
#  RECOLOR ENGINES
# ══════════════════════════════════════════════════════════════

# ── Static WebP ───────────────────────────────────────────────

def recolor_webp(raw: bytes, hex_color: str) -> bytes:
    """
    Luminance-preserving recolor:
    каждый пиксель окрашивается в целевой цвет,
    яркость сохраняется из оригинала.
    """
    tr, tg, tb = hex_to_rgb(hex_color)
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    arr = np.array(img, dtype=np.float64)

    # Яркость исходного пикселя (0-1)
    lum = (arr[:, :, 0] * 0.299
           + arr[:, :, 1] * 0.587
           + arr[:, :, 2] * 0.114) / 255.0

    arr[:, :, 0] = np.clip(tr * lum, 0, 255)
    arr[:, :, 1] = np.clip(tg * lum, 0, 255)
    arr[:, :, 2] = np.clip(tb * lum, 0, 255)
    # Alpha-канал не трогаем

    out = Image.fromarray(arr.astype(np.uint8), "RGBA")
    buf = io.BytesIO()
    out.save(buf, "WebP", quality=90)
    return buf.getvalue()


# ── Lottie/TGS helpers ───────────────────────────────────────

def _is_color(v) -> bool:
    """Проверяет, похоже ли значение на цветовой массив Lottie [r,g,b] или [r,g,b,a] в диапазоне 0-1."""
    return (
        isinstance(v, list)
        and 3 <= len(v) <= 4
        and all(isinstance(x, (int, float)) and -0.01 <= x <= 1.01 for x in v[:3])
    )


def _map_color(arr: list, tr: float, tg: float, tb: float) -> list:
    """Применяет целевой цвет, сохраняя яркость оригинала."""
    L = 0.299 * arr[0] + 0.587 * arr[1] + 0.114 * arr[2]
    result = [round(tr * L, 6), round(tg * L, 6), round(tb * L, 6)]
    if len(arr) >= 4:
        result.append(arr[3])   # сохраняем alpha
    return result


def _patch_animated_val(obj: dict, tr: float, tg: float, tb: float) -> None:
    """Обрабатывает объект анимированного значения {a:0/1, k:[...]}."""
    k = obj.get("k")
    if _is_color(k):
        obj["k"] = _map_color(k, tr, tg, tb)
    elif isinstance(k, list):
        for kf in k:
            if isinstance(kf, dict):
                for p in ("s", "e"):
                    if p in kf and _is_color(kf[p]):
                        kf[p] = _map_color(kf[p], tr, tg, tb)


def _patch_gradient(obj: dict, tr: float, tg: float, tb: float) -> None:
    """Обрабатывает объект градиента Lottie (g.k = [pos,r,g,b, pos,r,g,b, ...])."""
    k = obj.get("k")
    # Статичный градиент: плоский массив [pos, r, g, b, ...]
    if isinstance(k, list) and k and isinstance(k[0], (int, float)):
        # Каждые 4 элемента: позиция, r, g, b
        new_k = []
        i = 0
        while i < len(k):
            if i + 3 < len(k):
                pos = k[i]
                r, g, b = float(k[i+1]), float(k[i+2]), float(k[i+3])
                L = 0.299 * r + 0.587 * g + 0.114 * b
                new_k.extend([pos, round(tr*L,6), round(tg*L,6), round(tb*L,6)])
                i += 4
            else:
                new_k.append(k[i]); i += 1
        obj["k"] = new_k
    # Анимированный градиент
    elif isinstance(k, list):
        for kf in k:
            if isinstance(kf, dict):
                for p in ("s", "e"):
                    if p in kf and isinstance(kf[p], list):
                        arr = kf[p]
                        new_arr = []
                        i = 0
                        while i < len(arr):
                            if i + 3 < len(arr) and all(isinstance(arr[i+j], (int,float)) for j in range(4)):
                                pos = arr[i]
                                r, g, b = float(arr[i+1]), float(arr[i+2]), float(arr[i+3])
                                L = 0.299*r + 0.587*g + 0.114*b
                                new_arr.extend([pos, round(tr*L,6), round(tg*L,6), round(tb*L,6)])
                                i += 4
                            else:
                                new_arr.append(arr[i]); i += 1
                        kf[p] = new_arr


def _walk(node, tr: float, tg: float, tb: float) -> None:
    """Рекурсивно обходит Lottie JSON и перекрашивает все цвета."""
    if isinstance(node, dict):
        ty = node.get("ty")

        for key, val in node.items():
            # Обычные свойства цвета (fill / stroke)
            if key in ("c", "sc", "fc") and isinstance(val, dict) and "k" in val:
                _patch_animated_val(val, tr, tg, tb)
            # Градиентные цвета
            elif key == "g" and ty in ("gf", "gs") and isinstance(val, dict):
                _patch_gradient(val, tr, tg, tb)
            else:
                _walk(val, tr, tg, tb)

    elif isinstance(node, list):
        for item in node:
            _walk(item, tr, tg, tb)


def recolor_tgs(raw: bytes, hex_color: str) -> bytes:
    """
    Перекрашивает TGS-стикер (gzip-сжатый Lottie JSON).
    Возвращает корректный TGS, готовый к отправке в Telegram.
    """
    r, g, b = hex_to_rgb(hex_color)
    tr, tg, tb = r / 255.0, g / 255.0, b / 255.0

    lottie = json.loads(gzip.decompress(raw))
    _walk(lottie, tr, tg, tb)

    return gzip.compress(
        json.dumps(lottie, separators=(",", ":")).encode("utf-8"),
        compresslevel=9,
    )

# ══════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════

def kb_colors(last: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row:  list[InlineKeyboardButton] = []

    for name, hx in PALETTE:
        mark = "✓ " if hx == last else ""
        row.append(InlineKeyboardButton(
            text=f"{mark}{name}",
            callback_data=f"clr:{hx}",
        ))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="🎨 Свой HEX цвет", callback_data="clr:custom")])
    rows.append([InlineKeyboardButton(text="◁ Отмена",         callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_scope() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🖼 Один стикер", callback_data="scope:single"),
            InlineKeyboardButton(text="📦 Весь пак",    callback_data="scope:pack"),
        ],
        [InlineKeyboardButton(text="◁ Отмена", callback_data="cancel")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◁ Назад", callback_data="back_colors")]
    ])

# ══════════════════════════════════════════════════════════════
#  FSM STATES
# ══════════════════════════════════════════════════════════════

class S(StatesGroup):
    scope     = State()   # выбор: один / весь пак
    color     = State()   # палитра
    hex_input = State()   # ввод произвольного HEX

# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

async def _dl(bot: Bot, file_id: str) -> bytes:
    """Скачивает файл по file_id и возвращает bytes."""
    f = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(f.file_path, buf)
    return buf.getvalue()


def _chat(ctx) -> int:
    return ctx.message.chat.id if isinstance(ctx, CallbackQuery) else ctx.chat.id


def _uid(ctx) -> int:
    return ctx.from_user.id


# ── Обработка одного стикера ─────────────────────────────────

async def do_single(bot: Bot, ctx, state: FSMContext, hex_color: str) -> None:
    data    = await state.get_data()
    chat_id = _chat(ctx)
    uid     = _uid(ctx)

    status = await bot.send_message(
        chat_id,
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        '<b>Перекрашиваю стикер...</b>',
        parse_mode=ParseMode.HTML,
    )
    try:
        raw = await _dl(bot, data["file_id"])

        if data["is_animated"]:
            result = recolor_tgs(raw, hex_color)
            fname  = f"sticker_{hex_color.lstrip('#')}.tgs"
            kind   = "tgs"
        else:
            result = recolor_webp(raw, hex_color)
            fname  = f"sticker_{hex_color.lstrip('#')}.webp"
            kind   = "webp"

        await bot.delete_message(chat_id, status.message_id)
        await bot.send_sticker(chat_id, BufferedInputFile(result, filename=fname))

        db_set_color(uid, hex_color)
        db_add(uid, 1)
        db_log(uid, kind, hex_color, data.get("pack_name") or None)

    except Exception as exc:
        await bot.edit_message_text(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            f'Ошибка: <code>{exc}</code>',
            chat_id=chat_id,
            message_id=status.message_id,
            parse_mode=ParseMode.HTML,
        )
    finally:
        await state.clear()


# ── Обработка целого пака ────────────────────────────────────

async def do_pack(bot: Bot, ctx, state: FSMContext, hex_color: str) -> None:
    data      = await state.get_data()
    pack_name = data.get("pack_name", "")
    chat_id   = _chat(ctx)
    uid       = _uid(ctx)

    status = await bot.send_message(
        chat_id,
        f'<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        f'<b>Загружаю пак <code>{pack_name}</code>...</b>',
        parse_mode=ParseMode.HTML,
    )
    try:
        pack     = await bot.get_sticker_set(pack_name)
        stickers = pack.stickers
        total    = len(stickers)

        await bot.edit_message_text(
            f'<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
            f'<b>Перекрашиваю {total} стикеров...</b>',
            chat_id=chat_id,
            message_id=status.message_id,
            parse_mode=ParseMode.HTML,
        )

        zbuf = io.BytesIO()
        ok   = 0

        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_STORED) as zf:
            for i, s in enumerate(stickers):
                if s.is_video:
                    continue
                try:
                    raw  = await _dl(bot, s.file_id)
                    if s.is_animated:
                        data_out = recolor_tgs(raw, hex_color)
                        ext = "tgs"
                    else:
                        data_out = recolor_webp(raw, hex_color)
                        ext = "webp"

                    safe_emoji = re.sub(r"[^\w]", "_", s.emoji or "s")
                    zf.writestr(f"{i+1:03d}_{safe_emoji}.{ext}", data_out)
                    ok += 1
                except Exception:
                    pass

                # Прогресс каждые 10 стикеров
                if (i + 1) % 10 == 0:
                    try:
                        await bot.edit_message_text(
                            f'<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
                            f'<b>{i+1}/{total}</b> обработано...',
                            chat_id=chat_id,
                            message_id=status.message_id,
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass

                await asyncio.sleep(0.05)   # лёгкий rate-limit

        zbuf.seek(0)
        await bot.delete_message(chat_id, status.message_id)
        await bot.send_document(
            chat_id,
            BufferedInputFile(
                zbuf.getvalue(),
                filename=f"{pack_name}_{hex_color.lstrip('#')}.zip",
            ),
            caption=(
                f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
                f'<b>Готово!</b> {ok}/{total} стикеров перекрашено\n'
                f'<tg-emoji emoji-id="6050679691004612757">🖌</tg-emoji> '
                f'Цвет: <code>{hex_color}</code>\n\n'
                f'<i>Видео-стикеры (WEBM) пропущены — не поддерживаются.</i>'
            ),
            parse_mode=ParseMode.HTML,
        )

        db_set_color(uid, hex_color)
        db_add(uid, ok)
        db_log(uid, "pack", hex_color, pack_name)

    except Exception as exc:
        await bot.edit_message_text(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            f'Ошибка: <code>{exc}</code>',
            chat_id=chat_id,
            message_id=status.message_id,
            parse_mode=ParseMode.HTML,
        )
    finally:
        await state.clear()


# ── Спросить цвет для пака ───────────────────────────────────

async def _start_pack(msg: Message, state: FSMContext, pack_name: str) -> None:
    await state.clear()
    await state.set_data({"mode": "pack", "pack_name": pack_name})
    await state.set_state(S.color)
    last = db_last_color(msg.from_user.id)
    await msg.answer(
        f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> '
        f'Пак: <code>{pack_name}</code></b>\n\nВыбери цвет для покраски всего пака:',
        parse_mode=ParseMode.HTML,
        reply_markup=kb_colors(last),
    )


# ── Кастомное эмодзи ─────────────────────────────────────────

async def _start_custom_emoji(
    msg: Message, state: FSMContext, bot: Bot, ids: list[str]
) -> None:
    try:
        stickers = await bot.get_custom_emoji_stickers(ids[:1])
        if not stickers:
            raise ValueError("Стикер не найден")
        s = stickers[0]

        await state.clear()
        await state.set_data({
            "file_id":     s.file_id,
            "is_animated": s.is_animated,
            "is_video":    False,
            "pack_name":   s.set_name or "",
            "emoji":       s.emoji or "⭐",
        })

        kind = "анимированное" if s.is_animated else "статичное"
        last = db_last_color(msg.from_user.id)

        if s.set_name:
            await state.set_state(S.scope)
            await msg.answer(
                f'<b><tg-emoji emoji-id="6030400221232501136">🤖</tg-emoji> '
                f'Кастомное эмодзи</b> <i>({kind})</i>\n\n'
                f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> '
                f'Пак: <code>{s.set_name}</code>\n\nКрасить только это эмодзи или весь пак?',
                parse_mode=ParseMode.HTML,
                reply_markup=kb_scope(),
            )
        else:
            await state.set_state(S.color)
            await msg.answer(
                f'<b><tg-emoji emoji-id="6030400221232501136">🤖</tg-emoji> '
                f'Кастомное эмодзи</b> <i>({kind})</i>\n\nВыбери цвет:',
                parse_mode=ParseMode.HTML,
                reply_markup=kb_colors(last),
            )

    except Exception as exc:
        await msg.answer(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            f'Не удалось получить эмодзи: <code>{exc}</code>',
            parse_mode=ParseMode.HTML,
        )

# ══════════════════════════════════════════════════════════════
#  DISPATCHER & HANDLERS
# ══════════════════════════════════════════════════════════════

init_db()
dp = Dispatcher(storage=MemoryStorage())


# ── /start ────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    db_touch(msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name)
    await msg.answer(
        f'<b><tg-emoji emoji-id="6050679691004612757">🖌</tg-emoji> '
        f'Привет, {msg.from_user.first_name}!</b>\n\n'
        f'Я умею перекрашивать стикеры и кастомные эмодзи в любой цвет.\n\n'
        f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Поддерживаю:</b>\n'
        f'• Статичные стикеры <code>.webp</code>\n'
        f'• Анимированные стикеры <code>.tgs</code> (Lottie)\n'
        f'• Премиум кастомные эмодзи\n'
        f'• Целые стикер-паки и эмодзи-паки → <code>.zip</code>\n\n'
        f'<b><tg-emoji emoji-id="5870772616305839506">👥</tg-emoji> Как пользоваться:</b>\n'
        f'1. Пришли стикер или сообщение с эмодзи\n'
        f'2. Или ссылку <code>t.me/addstickers/NAME</code>\n'
        f'3. Или ссылку <code>t.me/addemoji/NAME</code>\n'
        f'4. Выбери цвет → получи результат!\n\n'
        f'<i>/help — справка · /stats — статистика</i>',
        parse_mode=ParseMode.HTML,
    )


# ── /help ────────────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        '<b><tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> Справка</b>\n\n'
        '<b>Команды:</b>\n'
        '/start — главное меню\n'
        '/help — эта страница\n'
        '/stats — моя статистика\n\n'
        '<b>Форматы на вход:</b>\n'
        '• Стикер напрямую (WebP или TGS)\n'
        '• Сообщение с кастомным эмодзи 🔥\n'
        '• <code>t.me/addstickers/PackName</code>\n'
        '• <code>t.me/addemoji/EmojiPackName</code>\n'
        '• Имя пака: <code>PackName</code>\n\n'
        '<b>Цвет:</b> 12 пресетов + произвольный HEX\n'
        'Пример: <code>#FF5500</code>\n\n'
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        '<i>Видео-стикеры (WEBM) не поддерживаются.</i>',
        parse_mode=ParseMode.HTML,
    )


# ── /stats ───────────────────────────────────────────────────

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    db_touch(msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name)
    row = db_stats(msg.from_user.id)
    if not row:
        return await msg.answer("Нет данных.")
    proc, last_color, joined = row
    dt = time.strftime("%d.%m.%Y", time.localtime(joined))
    await msg.answer(
        f'<b><tg-emoji emoji-id="5870921681735781843">📊</tg-emoji> Статистика</b>\n\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        f'Обработано стикеров: <b>{proc}</b>\n'
        f'<tg-emoji emoji-id="6050679691004612757">🖌</tg-emoji> '
        f'Последний цвет: <code>{last_color}</code>\n'
        f'<tg-emoji emoji-id="5890937706803894250">📅</tg-emoji> '
        f'В боте с: <b>{dt}</b>',
        parse_mode=ParseMode.HTML,
    )


# ── Стикер ───────────────────────────────────────────────────

@dp.message(F.sticker)
async def on_sticker(msg: Message, state: FSMContext):
    await state.clear()
    db_touch(msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name)
    s = msg.sticker

    if s.is_video:
        return await msg.answer(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            '<b>Видео-стикеры (WEBM) не поддерживаются.</b>\n'
            'Пришли WebP или TGS стикер.',
            parse_mode=ParseMode.HTML,
        )

    await state.set_data({
        "file_id":     s.file_id,
        "is_animated": s.is_animated,
        "is_video":    s.is_video,
        "pack_name":   s.set_name or "",
        "emoji":       s.emoji or "⭐",
    })

    kind = "анимированный TGS" if s.is_animated else "статичный WebP"
    last = db_last_color(msg.from_user.id)
    intro = (
        f'<b><tg-emoji emoji-id="6050679691004612757">🖌</tg-emoji> '
        f'Стикер получен</b> <i>({kind})</i>\n\n'
    )

    if s.set_name:
        await state.set_state(S.scope)
        await msg.answer(
            intro
            + f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> '
              f'Пак: <code>{s.set_name}</code>\n\n'
              f'Красить только этот стикер или весь пак?',
            parse_mode=ParseMode.HTML,
            reply_markup=kb_scope(),
        )
    else:
        await state.set_state(S.color)
        await msg.answer(
            intro + "Выбери цвет:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_colors(last),
        )


# ── HEX ввод (приоритет над общим text-обработчиком) ─────────

@dp.message(S.hex_input, F.text)
async def on_hex_input(msg: Message, state: FSMContext, bot: Bot):
    val = msg.text.strip()
    if not valid_hex(val):
        return await msg.answer(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            'Неверный формат. Пример: <code>#FF3333</code> или <code>FF3333</code>',
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back(),
        )
    val  = norm_hex(val)
    data = await state.get_data()
    if data.get("mode") == "pack":
        await do_pack(bot, msg, state, val)
    else:
        await do_single(bot, msg, state, val)


# ── Текст (ссылки, имена паков, эмодзи) ──────────────────────

@dp.message(F.text)
async def on_text(msg: Message, state: FSMContext, bot: Bot):
    text = (msg.text or "").strip()
    db_touch(msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name)

    # Ссылка на эмодзи-пак
    m = re.search(r"t\.me/addemoji/([A-Za-z0-9_]+)", text)
    if m:
        return await _start_pack(msg, state, m.group(1))

    # Ссылка на стикерпак
    m = re.search(r"t\.me/addstickers/([A-Za-z0-9_]+)", text)
    if m:
        return await _start_pack(msg, state, m.group(1))

    # Кастомные эмодзи в тексте сообщения
    if msg.entities:
        emoji_ids = [
            e.custom_emoji_id
            for e in msg.entities
            if e.type == "custom_emoji" and e.custom_emoji_id
        ]
        if emoji_ids:
            return await _start_custom_emoji(msg, state, bot, emoji_ids)

    # Имя пака напрямую (должны быть символ подчёркивания или длина ≥ 8 и только ascii)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,63}", text) and ("_" in text or len(text) >= 8):
        return await _start_pack(msg, state, text)

    await msg.answer(
        '<tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> '
        'Пришли стикер, сообщение с кастомным эмодзи или ссылку на пак.\n'
        '<i>/help — подробнее</i>',
        parse_mode=ParseMode.HTML,
    )


# ── Callbacks ────────────────────────────────────────────────

@dp.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Отменено.',
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@dp.callback_query(F.data == "back_colors")
async def cb_back(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S.color)
    last = db_last_color(cb.from_user.id)
    await cb.message.edit_text(
        '<tg-emoji emoji-id="6050679691004612757">🖌</tg-emoji> <b>Выбери цвет:</b>',
        parse_mode=ParseMode.HTML,
        reply_markup=kb_colors(last),
    )
    await cb.answer()


@dp.callback_query(S.scope, F.data.startswith("scope:"))
async def cb_scope(cb: CallbackQuery, state: FSMContext):
    mode = cb.data.split(":", 1)[1]   # "single" | "pack"
    await state.update_data(mode=mode)
    await state.set_state(S.color)
    last = db_last_color(cb.from_user.id)
    await cb.message.edit_text(
        '<tg-emoji emoji-id="6050679691004612757">🖌</tg-emoji> <b>Выбери цвет:</b>',
        parse_mode=ParseMode.HTML,
        reply_markup=kb_colors(last),
    )
    await cb.answer()


@dp.callback_query(S.color, F.data.startswith("clr:"))
async def cb_color(cb: CallbackQuery, state: FSMContext, bot: Bot):
    val = cb.data.split(":", 1)[1]
    await cb.answer()

    if val == "custom":
        await state.set_state(S.hex_input)
        await cb.message.edit_text(
            '<tg-emoji emoji-id="6050679691004612757">🖌</tg-emoji> '
            '<b>Введи HEX цвет</b>\n\n'
            'Пример: <code>#FF3333</code> или <code>1565C0</code>',
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back(),
        )
        return

    data = await state.get_data()
    try:
        await cb.message.delete()
    except Exception:
        pass

    if data.get("mode") == "pack":
        await do_pack(bot, cb, state, val)
    else:
        await do_single(bot, cb, state, val)


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════

async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    print("✅ Бот запущен")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())