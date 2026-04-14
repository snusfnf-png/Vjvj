import asyncio
import gzip
import io
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time

import aiohttp
import numpy as np
from dotenv import load_dotenv
from PIL import Image

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, StickerFormat
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputSticker,
    Message,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ─────────────────────────── DATABASE ───────────────────────────

DB_PATH = "emoji_bot.db"


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            joined_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            src_pack    TEXT,
            color_hex   TEXT,
            new_pack    TEXT,
            emoji_count INTEGER,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()
    conn.close()


def upsert_user(user_id: int, username: str | None, first_name: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO users(user_id, username, first_name) VALUES (?,?,?)",
        (user_id, username, first_name),
    )
    conn.commit()
    conn.close()


def save_task(
    user_id: int,
    src_pack: str,
    color_hex: str,
    new_pack: str,
    emoji_count: int,
) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO tasks(user_id, src_pack, color_hex, new_pack, emoji_count) VALUES (?,?,?,?,?)",
        (user_id, src_pack, color_hex, new_pack, emoji_count),
    )
    conn.commit()
    conn.close()


# ─────────────────────────── IMAGE UTILS ───────────────────────────


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def recolor_webp(data: bytes, hex_color: str) -> bytes:
    """Tint static WebP/PNG emoji, fully preserving original alpha (no black bg)."""
    r, g, b = hex_to_rgb(hex_color)
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    arr = np.array(img, dtype=np.float32)

    alpha = arr[:, :, 3]  # (H, W)

    # Маска непрозрачных пикселей (alpha > 0)
    mask = alpha > 0  # (H, W)

    # Яркость только по видимым пикселям
    lum = (
        0.299 * arr[:, :, 0]
        + 0.587 * arr[:, :, 1]
        + 0.114 * arr[:, :, 2]
    ) / 255.0  # (H, W)

    # Нормализуем яркость: минимум → 0, максимум → 1 (только по видимым)
    # Это устраняет «серый» сдвиг и делает покраску чистой
    if mask.any():
        lum_visible = lum[mask]
        lum_min = lum_visible.min()
        lum_max = lum_visible.max()
        denom = lum_max - lum_min if lum_max > lum_min else 1.0
        lum = np.where(mask, (lum - lum_min) / denom, 0.0)

    # Применяем цвет только там, где есть пиксель (alpha > 0)
    arr[:, :, 0] = np.where(mask, np.clip(lum * r, 0, 255), 0)
    arr[:, :, 1] = np.where(mask, np.clip(lum * g, 0, 255), 0)
    arr[:, :, 2] = np.where(mask, np.clip(lum * b, 0, 255), 0)
    arr[:, :, 3] = alpha  # альфа строго оригинальная

    out = Image.fromarray(arr.astype(np.uint8), "RGBA")
    buf = io.BytesIO()
    out.save(buf, format="WEBP", lossless=True)
    return buf.getvalue()


def _apply_lottie_color(k_val: list, r: float, g: float, b: float) -> list:
    """Заменяет RGB компоненты в Lottie-цвете, сохраняя альфу."""
    if len(k_val) >= 3 and all(isinstance(x, (int, float)) for x in k_val[:3]):
        result = [r, g, b]
        if len(k_val) == 4:
            result.append(float(k_val[3]))
        return result
    return k_val


def _walk_lottie(node, r: float, g: float, b: float) -> None:
    """Рекурсивно перекрашивает ВСЕ цвета в Lottie JSON."""
    if isinstance(node, dict):
        for key, val in node.items():
            # Solid color shape (Fill / Stroke)
            if key == "c" and isinstance(val, dict) and "k" in val:
                k = val["k"]
                if isinstance(k, list):
                    # Static color
                    if len(k) >= 3 and all(isinstance(x, (int, float)) for x in k[:3]):
                        val["k"] = _apply_lottie_color(k, r, g, b)
                    else:
                        # Keyframed color
                        for kf in k:
                            if isinstance(kf, dict):
                                for field in ("s", "e"):
                                    if field in kf and isinstance(kf[field], list):
                                        kf[field] = _apply_lottie_color(kf[field], r, g, b)
            # Gradient colors array
            elif key == "g" and isinstance(val, dict) and "k" in val:
                gk = val["k"]
                if isinstance(gk, dict) and "k" in gk:
                    arr = gk["k"]
                    if isinstance(arr, list) and len(arr) > 4:
                        n_stops = val.get("p", 0)
                        if n_stops:
                            for i in range(n_stops):
                                idx = i * 4 + 1  # offset, r, g, b
                                if idx + 2 < len(arr):
                                    lum = (
                                        0.299 * arr[idx]
                                        + 0.587 * arr[idx + 1]
                                        + 0.114 * arr[idx + 2]
                                    )
                                    arr[idx]     = r * lum
                                    arr[idx + 1] = g * lum
                                    arr[idx + 2] = b * lum
            else:
                _walk_lottie(val, r, g, b)
    elif isinstance(node, list):
        for item in node:
            _walk_lottie(item, r, g, b)


def recolor_tgs(data: bytes, hex_color: str) -> bytes:
    """Перекрашивает анимированный TGS (Lottie JSON + gzip)."""
    r, g, b = hex_to_rgb(hex_color)
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    lottie = json.loads(gzip.decompress(data))
    _walk_lottie(lottie, rf, gf, bf)
    return gzip.compress(json.dumps(lottie, separators=(",", ":")).encode())


def recolor_video(data: bytes, hex_color: str) -> bytes:
    """Перекрашивает видео-эмодзи (WebM) через ffmpeg colorize."""
    r, g, b = hex_to_rgb(hex_color)
    rf, gf, bf = r / 255, g / 255, b / 255

    # colorchannelmixer: каждый выходной канал = взвешенная сумма входных
    # Для монохромного тонирования используем luminance веса
    wr = 0.299 * rf
    wg = 0.587 * gf
    wb = 0.114 * bf

    # rr=wr+wb+wg ... применяем одинаковую яркость ко всем трём каналам
    total = wr + wg + wb if (wr + wg + wb) > 0 else 1

    ccm = (
        f"colorchannelmixer="
        f"rr={rf * 0.299 / total if total else rf}:"
        f"rg={rf * 0.587 / total if total else 0}:"
        f"rb={rf * 0.114 / total if total else 0}:"
        f"gr={gf * 0.299 / total if total else 0}:"
        f"gg={gf * 0.587 / total if total else gf}:"
        f"gb={gf * 0.114 / total if total else 0}:"
        f"br={bf * 0.299 / total if total else 0}:"
        f"bg={bf * 0.587 / total if total else 0}:"
        f"bb={bf * 0.114 / total if total else bf}"
    )

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fin:
        fin.write(data)
        in_path = fin.name
    out_path = in_path.replace(".webm", "_out.webm")

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", in_path,
                "-vf", ccm,
                "-c:v", "libvpx-vp9",
                "-b:v", "0", "-crf", "30",
                "-pix_fmt", "yuva420p",
                "-an",
                out_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        with open(out_path, "rb") as f:
            return f.read()
    except Exception:
        return data  # fallback — вернуть оригинал
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass


# ─────────────────────────── FSM STATES ───────────────────────────


class States(StatesGroup):
    wait_link = State()
    wait_color = State()
    wait_custom_hex = State()


# ─────────────────────────── KEYBOARD ───────────────────────────

PALETTE = [
    ("🔴 Красный",   "#FF3333"),
    ("🟠 Оранжевый", "#FF8800"),
    ("🟡 Жёлтый",    "#FFE000"),
    ("🟢 Зелёный",   "#22CC44"),
    ("🔵 Синий",     "#1177FF"),
    ("🟣 Фиолетовый","#9922EE"),
    ("⚪ Белый",     "#FFFFFF"),
    ("⚫ Чёрный",    "#111111"),
    ("🎨 Свой HEX", "custom"),
]


def build_color_kb() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for name, val in PALETTE:
        row.append(InlineKeyboardButton(text=name, callback_data=f"clr:{val}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────── BOT HANDLERS ───────────────────────────

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())


@dp.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext) -> None:
    upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    await state.set_state(States.wait_link)
    await msg.answer(
        "<tg-emoji emoji-id=\"5258450450448915742\">🎨</tg-emoji> Отправь ссылку на пак <b>премиум эмодзи</b>:\n"
        "<code>https://t.me/addemoji/НазваниеПака</code>\n\n"
        "<tg-emoji emoji-id=\"6021618194228187816\">💬</tg-emoji> <i>Бот скачает каждый эмодзи из пака, перекрасит в выбранный цвет "
        "и создаст новый пак для тебя</i>"
    )

# ── Получить ссылку на пак ──

@dp.message(States.wait_link)
async def handle_link(msg: Message, state: FSMContext) -> None:
    text = msg.text or ""
    m = re.search(r"t\.me/addemoji/([A-Za-z0-9_]+)", text)
    if not m:
        await msg.answer(
            "<tg-emoji emoji-id=\"5774077015388852135\">❌</tg-emoji> <b>Не вижу ссылку на пак эмодзи</b>\n\n"
            "Формат: <code>https://t.me/addemoji/НазваниеПака</code>"
        )
        return

    pack_name = m.group(1)
    wait = await msg.answer("<tg-emoji emoji-id=\"5429571366384842791\">🔍</tg-emoji> <b>Проверяю пак...</b>")

    try:
        ss = await bot.get_sticker_set(pack_name)
    except Exception:
        await wait.edit_text(
            f"<tg-emoji emoji-id=\"5774077015388852135\">❌</tg-emoji> <b>Пак не найден:</b> <code>{pack_name}</code>\n"
            "Убедись что ссылка верна и пак существует."
        )
        return

    total = len(ss.stickers)
    await state.update_data(pack_name=pack_name, pack_title=ss.title, total=total)
    await state.set_state(States.wait_color)

    await wait.edit_text(
        f"<tg-emoji emoji-id=\"5350354474181343974\">🎨</tg-emoji> <b>Выбери цвет для покраски {total} шт эмодзи:</b>",
        reply_markup=build_color_kb(),
    )


# ── Выбор цвета из кнопок ──

@dp.callback_query(F.data.startswith("clr:"), States.wait_color)
async def handle_color_btn(cb: CallbackQuery, state: FSMContext) -> None:
    color = cb.data.split(":", 1)[1]
    if color == "custom":
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◁ Назад", callback_data="clr:back")]
        ])
        await cb.message.edit_text(
            "<tg-emoji emoji-id=\"5258450450448915742\">🎨</tg-emoji> <b>Введи HEX-цвет</b>\n\n"
            "Можно взять из htmlcolorcodes.com\n"
            "<i>Поддерживаются форматы: #RGB, #RRGGBB</i>",
            reply_markup=back_kb,
        )
        await state.set_state(States.wait_custom_hex)
        await cb.answer()
        return

    await cb.answer()
    await cb.message.edit_text("<tg-emoji emoji-id=\"5258419835922030550\">⏳</tg-emoji> <b>Начинаю покраску...</b>")
    await do_recolor(cb.message, state, color, cb.from_user.id)

@dp.callback_query(F.data == "clr:back", States.wait_custom_hex)
async def handle_color_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    total = data.get("total", 0)
    await cb.message.edit_text(
        f"<tg-emoji emoji-id=\"5350354474181343974\">🎨</tg-emoji> <b>Выбери цвет для покраски {total} шт эмодзи:</b>",
        reply_markup=build_color_kb(),
    )
    await state.set_state(States.wait_color)
    await cb.answer()

# ── Свой HEX ──

@dp.message(States.wait_custom_hex)
async def handle_custom_hex(msg: Message, state: FSMContext) -> None:
    raw = msg.text.strip().lstrip("#")
    # Поддержка краткой #RGB → #RRGGBB
    if re.match(r"^[0-9A-Fa-f]{3}$", raw):
        raw = "".join(c * 2 for c in raw)
    if not re.match(r"^[0-9A-Fa-f]{6}$", raw):
        await msg.answer(
            "<tg-emoji emoji-id=\"5774077015388852135\">❌</tg-emoji> <b>Неверный формат</b>\n"
            "Введи HEX-цвет: <code>#FF5733</code> или <code>FF5733</code>"
        )
        return
    hex_color = "#" + raw.upper()
    status = await msg.answer(f"<tg-emoji emoji-id=\"5258419835922030550\">⏳</tg-emoji> <b>Начинаю покраску цветом {hex_color}...</b>")
    await do_recolor(status, state, hex_color, msg.from_user.id)


# ─────────────────────────── CORE RECOLOR LOGIC ───────────────────────────

PROGRESS_BAR_LEN = 12


def _bar(done: int, total: int) -> str:
    filled = round(done / total * PROGRESS_BAR_LEN)
    return "█" * filled + "░" * (PROGRESS_BAR_LEN - filled)


async def do_recolor(
    status_msg: Message,
    state: FSMContext,
    hex_color: str,
    user_id: int,
) -> None:
    data = await state.get_data()
    pack_name: str = data["pack_name"]
    pack_title: str = data["pack_title"]

    try:
        ss = await bot.get_sticker_set(pack_name)
        stickers = ss.stickers
        total = len(stickers)

        bot_info = await bot.get_me()
        ts = int(time.time()) % 10_000_000
        color_tag = hex_color.lstrip("#").lower()
        new_pack_name = f"ec{color_tag[:4]}{ts}_by_{bot_info.username}"
        new_title = f"{pack_title[:50]} [{hex_color}]"

        input_stickers: list[InputSticker] = []
        errors = 0

        for i, sticker in enumerate(stickers):
            # Прогресс каждые 3 эмодзи
            if i % 3 == 0:
                try:
                    await status_msg.edit_text(
                        f"<tg-emoji emoji-id=\"5258450450448915742\">🎨</tg-emoji> <b>Крашу эмодзи...</b>\n\n"
                        f"<code>[{_bar(i, total)}]</code>  {i}/{total}\n"
                        f"Цвет: <code>{hex_color}</code>"
                    )
                except Exception:
                    pass

            try:
                file_obj = await bot.get_file(sticker.file_id)
                dl = await bot.download_file(file_obj.file_path)
                raw: bytes = dl.read()

                # aiogram 3.x: определяем формат через булевы поля
                if getattr(sticker, "is_animated", False):
                    processed = recolor_tgs(raw, hex_color)
                    ext, sfmt = "tgs", "animated"
                elif getattr(sticker, "is_video", False):
                    processed = recolor_video(raw, hex_color)
                    ext, sfmt = "webm", "video"
                else:
                    processed = recolor_webp(raw, hex_color)
                    ext, sfmt = "webp", "static"

                # emoji_list для InputSticker
                emoji_char = sticker.emoji or "🌟"
                if isinstance(emoji_char, str):
                    emoji_list = [emoji_char]
                else:
                    emoji_list = list(emoji_char)[:1]

                input_stickers.append(
                    InputSticker(
                        sticker=BufferedInputFile(processed, filename=f"e{i}.{ext}"),
                        emoji_list=emoji_list,
                        format=sfmt,
                    )
                )

            except Exception as e:
                errors += 1
                print(f"[WARN] sticker {i} error: {e}")
                continue

        if not input_stickers:
            await status_msg.edit_text(
                "<tg-emoji emoji-id=\"5774077015388852135\">❌</tg-emoji> <b>Не удалось обработать ни одного эмодзи</b>\n"
                "Попробуй другой пак или /start"
            )
            await state.set_state(States.wait_link)
            return

        await status_msg.edit_text(
            f"<tg-emoji emoji-id=\"6039573425268201570\">📤</tg-emoji> <b>Создаю новый пак ({len(input_stickers)} эмодзи)...</b>"
        )

        # Создаём пак с первым эмодзи
        await bot.create_new_sticker_set(
            user_id=user_id,
            name=new_pack_name,
            title=new_title[:64],
            stickers=[input_stickers[0]],
            sticker_type="custom_emoji",
        )

        # Добавляем остальные
        for idx, inp_s in enumerate(input_stickers[1:], 1):
            if idx % 5 == 0:
                try:
                    await status_msg.edit_text(
                        f"<tg-emoji emoji-id=\"6039573425268201570\">📤</tg-emoji> <b>Загружаю эмодзи в пак...</b>\n\n"
                        f"<code>[{_bar(idx, len(input_stickers))}]</code>  "
                        f"{idx}/{len(input_stickers)}"
                    )
                except Exception:
                    pass
            try:
                await bot.add_sticker_to_set(
                    user_id=user_id,
                    name=new_pack_name,
                    sticker=inp_s,
                )
            except Exception as e:
                print(f"[WARN] add sticker {idx}: {e}")

        save_task(user_id, pack_name, hex_color, new_pack_name, len(input_stickers))

        warn_txt = f"\n⚠️ <i>{errors} эмодзи пропущено из-за ошибок</i>" if errors else ""

        await status_msg.edit_text(
            f"<tg-emoji emoji-id=\"6039348811363520645\">📂</tg-emoji> <a href='https://t.me/addemoji/{new_pack_name}'>https://t.me/addemoji/{new_pack_name}</a>",
        )

    except Exception as e:
        await status_msg.edit_text(
            f"<tg-emoji emoji-id=\"5774077015388852135\">❌</tg-emoji> <b>Ошибка при обработке</b>\n\n"
            f"<code>{str(e)[:400]}</code>\n\n"
            f"/start — попробовать снова"
        )

    await state.set_state(States.wait_link)


# ─────────────────────────── ENTRY POINT ───────────────────────────


async def main() -> None:
    init_db()

    await bot.set_my_commands([
        BotCommand(command="start", description="ГАз"),
    ])

    banner = """
\033[35m
    ███████╗███╗   ███╗ ██████╗      ██╗██╗
    ██╔════╝████╗ ████║██╔═══██╗     ██║██║
    █████╗  ██╔████╔██║██║   ██║     ██║██║
    ██╔══╝  ██║╚██╔╝██║██║   ██║██   ██║██║
    ███████╗██║ ╚═╝ ██║╚██████╔╝╚█████╔╝██║
    ╚══════╝╚═╝     ╚═╝ ╚═════╝  ╚════╝ ╚═╝
\033[0m
    \033[36mСоздатель:\033[0m @wivvi
    \033[36mТелеграм: \033[0m t.me/StriverDev

    \033[32mСтатус: GAZ\033[0m
    """
    print(banner)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())