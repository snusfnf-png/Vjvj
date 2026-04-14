import asyncio
import logging
import os
import sys
import aiohttp
import aiosqlite
import hashlib
from aiogram import Bot, Dispatcher, types, F, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_NAME = "free_ai_images.db"

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

if not TOKEN:
    logger.error("Ошибка: TELEGRAM_TOKEN не найден в .env")
    sys.exit(1)

# --- МОДЕЛИ ---
AI_MODELS = {
    # === GEMINI МОДЕЛИ ===
    "gemini_2.5_flash": {
        "name": "✨ Gemini 2.5 Flash",
        "description": "Новейшая модель Google",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=gemini-2.5-flash-image-preview&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "gemini",
        "speed": "10-20 сек"
    },

    # === GPT-IMAGE МОДЕЛИ ===
    "gpt_image_1.5": {
        "name": "🧠 GPT-Image 1.5",
        "description": "Продвинутая GPT генерация",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=gpt-image-1.5&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "gpt",
        "speed": "15-25 сек"
    },
    "gpt_image_1": {
        "name": "🧠 GPT-Image 1.0",
        "description": "Базовая GPT генерация",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=gpt-image-1&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "gpt",
        "speed": "10-20 сек"
    },
    "gpt_image_mini": {
        "name": "⚡ GPT-Image Mini",
        "description": "Быстрая GPT модель",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=gpt-image-1-mini&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "gpt",
        "speed": "5-15 сек"
    },

    # === DALL-E МОДЕЛИ ===
    "dalle_3": {
        "name": "🎨 DALL-E 3",
        "description": "Топовая модель OpenAI",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=dall-e-3&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "dalle",
        "speed": "20-30 сек"
    },
    "dalle_2": {
        "name": "🎨 DALL-E 2",
        "description": "Классика от OpenAI",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=dall-e-2&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "dalle",
        "speed": "15-25 сек"
    },

    # === FLUX МОДЕЛИ (Black Forest Labs) ===
    "flux_1.1_pro": {
        "name": "⚡ FLUX 1.1 Pro",
        "description": "Новейший FLUX Pro",
        "hf_model": "black-forest-labs/FLUX.1.1-pro",
        "category": "flux",
        "speed": "20-35 сек"
    },
    "flux_pro": {
        "name": "🎨 FLUX Pro",
        "description": "Профессиональное качество",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=flux-pro&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "flux",
        "speed": "20-30 сек"
    },
    "flux_schnell": {
        "name": "⚡ FLUX Schnell",
        "description": "Очень быстро",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=flux&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "flux",
        "speed": "5-15 сек"
    },
    "flux_schnell_free": {
        "name": "🆓 FLUX Schnell Free",
        "description": "Бесплатная версия",
        "hf_model": "black-forest-labs/FLUX.1-schnell-Free",
        "category": "flux",
        "speed": "5-15 сек"
    },
    "flux_dev": {
        "name": "🔧 FLUX Dev",
        "description": "Экспериментальная версия",
        "hf_model": "black-forest-labs/FLUX.1-dev",
        "category": "flux",
        "speed": "15-25 сек"
    },
    "flux_dev_lora": {
        "name": "🎯 FLUX Dev LoRA",
        "description": "С дообучением",
        "hf_model": "black-forest-labs/FLUX.1-dev-lora",
        "category": "flux",
        "speed": "15-25 сек"
    },
    "flux_canny_pro": {
        "name": "🖼️ FLUX Canny Pro",
        "description": "Контроль границ",
        "hf_model": "black-forest-labs/FLUX.1-Canny-pro",
        "category": "flux",
        "speed": "20-30 сек"
    },
    "flux_kontext_pro": {
        "name": "🎭 FLUX Kontext Pro",
        "description": "Контекстное понимание Pro",
        "hf_model": "black-forest-labs/FLUX.1-kontext-pro",
        "category": "flux",
        "speed": "20-30 сек"
    },
    "flux_kontext_max": {
        "name": "💎 FLUX Kontext Max",
        "description": "Максимальный контекст",
        "hf_model": "black-forest-labs/FLUX.1-kontext-max",
        "category": "flux",
        "speed": "25-35 сек"
    },
    "flux_kontext_dev": {
        "name": "🔧 FLUX Kontext Dev",
        "description": "Контекстное понимание Dev",
        "hf_model": "black-forest-labs/FLUX.1-kontext-dev",
        "category": "flux",
        "speed": "15-25 сек"
    },
    "flux_krea_dev": {
        "name": "🎨 FLUX Krea Dev",
        "description": "Креативная генерация",
        "hf_model": "black-forest-labs/FLUX.1-krea-dev",
        "category": "flux",
        "speed": "15-25 сек"
    },
    "flux_realism": {
        "name": "📸 FLUX Realism",
        "description": "Фотореалистичные изображения",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=flux-realism&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "flux",
        "speed": "20-30 сек"
    },
    "flux_anime": {
        "name": "🌸 FLUX Anime",
        "description": "Аниме стиль",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=flux-anime&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "flux",
        "speed": "15-25 сек"
    },
    "flux_3d": {
        "name": "🎮 FLUX 3D",
        "description": "3D рендеры",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=flux-3d&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "flux",
        "speed": "20-30 сек"
    },

    # === GOOGLE IMAGEN МОДЕЛИ ===
    "imagen_4_ultra": {
        "name": "💎 Imagen 4.0 Ultra",
        "description": "Топовая модель Google",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=google/imagen-4.0-ultra&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "google",
        "speed": "25-35 сек"
    },
    "imagen_4_preview": {
        "name": "🔍 Imagen 4.0 Preview",
        "description": "Превью версия",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=google/imagen-4.0-preview&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "google",
        "speed": "20-30 сек"
    },
    "imagen_4_fast": {
        "name": "⚡ Imagen 4.0 Fast",
        "description": "Быстрая версия",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=google/imagen-4.0-fast&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "google",
        "speed": "10-20 сек"
    },
    "flash_image_2.5": {
        "name": "⚡ Flash Image 2.5",
        "description": "Супербыстрая генерация",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=google/flash-image-2.5&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "google",
        "speed": "5-15 сек"
    },

    # === STABLE DIFFUSION МОДЕЛИ ===
    "sd_3_medium": {
        "name": "🎭 SD 3 Medium",
        "description": "Stable Diffusion 3",
        "hf_model": "stabilityai/stable-diffusion-3-medium",
        "category": "stable",
        "speed": "15-25 сек"
    },
    "sdxl": {
        "name": "🎨 SDXL Base",
        "description": "Классика от Stability AI",
        "hf_model": "stabilityai/stable-diffusion-xl-base-1.0",
        "category": "stable",
        "speed": "15-30 сек"
    },

    # === DREAMSHAPER & PLAYGROUND ===
    "dreamshaper": {
        "name": "✨ DreamShaper",
        "description": "Стилизованные арты",
        "hf_model": "Lykon/DreamShaper",
        "category": "creative",
        "speed": "10-20 сек"
    },
    "playground": {
        "name": "🎪 Playground v2.5",
        "description": "Универсальная модель",
        "hf_model": "playgroundai/playground-v2.5-1024px-aesthetic",
        "category": "creative",
        "speed": "15-25 сек"
    },

    # === JUGGERNAUT МОДЕЛИ ===
    "juggernaut_pro_flux": {
        "name": "⚡ Juggernaut Pro FLUX",
        "description": "Про версия с FLUX",
        "hf_model": "RunDiffusion/Juggernaut-pro-flux",
        "category": "juggernaut",
        "speed": "20-30 сек"
    },
    "juggernaut_lightning": {
        "name": "⚡ Juggernaut Lightning",
        "description": "Молниеносная генерация",
        "hf_model": "Rundiffusion/Juggernaut-Lightning-Flux",
        "category": "juggernaut",
        "speed": "10-20 сек"
    },

    # === SEEDREAM МОДЕЛИ ===
    "seedream_4.0": {
        "name": "🌱 Seedream 4.0",
        "description": "Новейшая версия",
        "hf_model": "ByteDance-Seed/Seedream-4.0",
        "category": "seedream",
        "speed": "20-30 сек"
    },
    "seedream_3.0": {
        "name": "🌱 Seedream 3.0",
        "description": "Стабильная версия",
        "hf_model": "ByteDance-Seed/Seedream-3.0",
        "category": "seedream",
        "speed": "15-25 сек"
    },

    # === HIDREAM МОДЕЛИ ===
    "hidream_full": {
        "name": "💎 HiDream I1 Full",
        "description": "Полная версия",
        "hf_model": "HiDream-ai/HiDream-I1-Full",
        "category": "hidream",
        "speed": "25-35 сек"
    },
    "hidream_dev": {
        "name": "🔧 HiDream I1 Dev",
        "description": "Экспериментальная",
        "hf_model": "HiDream-ai/HiDream-I1-Dev",
        "category": "hidream",
        "speed": "15-25 сек"
    },
    "hidream_fast": {
        "name": "⚡ HiDream I1 Fast",
        "description": "Быстрая версия",
        "hf_model": "HiDream-ai/HiDream-I1-Fast",
        "category": "hidream",
        "speed": "10-20 сек"
    },

    # === ДРУГИЕ МОДЕЛИ ===
    "ideogram_3.0": {
        "name": "🎯 Ideogram 3.0",
        "description": "Точная генерация текста",
        "hf_model": "ideogram/ideogram-3.0",
        "category": "other",
        "speed": "15-25 сек"
    },
    "qwen_image": {
        "name": "🇨🇳 Qwen Image",
        "description": "Китайская модель",
        "hf_model": "Qwen/Qwen-Image",
        "category": "other",
        "speed": "15-25 сек"
    },
    "turbo": {
        "name": "🚀 Turbo",
        "description": "Максимальная скорость",
        "url": "https://image.pollinations.ai/prompt/{prompt}?model=turbo&width=1024&height=1024&seed={seed}&nologo=true",
        "category": "other",
        "speed": "3-8 сек"
    }
}

# Категории моделей
MODEL_CATEGORIES = {
    "gemini": "✨ GEMINI",
    "gpt": "🧠 GPT-IMAGE",
    "dalle": "🎨 DALL-E",
    "flux": "⚡ FLUX",
    "google": "🔮 GOOGLE IMAGEN",
    "stable": "🎭 STABLE DIFFUSION",
    "creative": "🎪 CREATIVE",
    "juggernaut": "💪 JUGGERNAUT",
    "seedream": "🌱 SEEDREAM",
    "hidream": "💎 HIDREAM",
    "other": "🔥 ДРУГИЕ"
}


# --- FSM ---
class GenerateImage(StatesGroup):
    choosing_category = State()
    choosing_model = State()
    waiting_for_prompt = State()


# --- БД ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prompt TEXT,
                model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def add_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        await db.commit()


async def save_generation(user_id: int, prompt: str, model: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO generations (user_id, prompt, model) VALUES (?, ?, ?)",
            (user_id, prompt, model)
        )
        await db.commit()


async def get_user_stats(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM generations WHERE user_id = ?",
            (user_id,)
        )
        count = await cursor.fetchone()
        return count[0] if count else 0


# --- IMAGE GENERATOR ---
class ImageGenerator:
    def __init__(self):
        self.session = None

    def _generate_seed(self, prompt: str, user_id: int) -> int:
        hash_input = f"{prompt}_{user_id}_{asyncio.get_event_loop().time()}"
        return int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)

    async def _ensure_session(self):
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=120)
            connector = aiohttp.TCPConnector(ssl=False)
            self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)

    async def generate_pollinations(self, prompt: str, model_key: str, user_id: int):
        await self._ensure_session()
        model = AI_MODELS[model_key]
        seed = self._generate_seed(prompt, user_id)
        url = model["url"].format(prompt=prompt, seed=seed)

        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    logger.error(f"Pollinations error: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Pollinations exception: {e}")
            return None

    async def generate_huggingface(self, prompt: str, model_key: str):
        await self._ensure_session()
        model = AI_MODELS[model_key]
        api_url = f"https://api-inference.huggingface.co/models/{model['hf_model']}"
        payload = {"inputs": prompt}

        try:
            async with self.session.post(api_url, json=payload) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    logger.error(f"HuggingFace error: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"HuggingFace exception: {e}")
            return None

    async def generate(self, prompt: str, model_key: str, user_id: int):
        model = AI_MODELS.get(model_key)
        if not model:
            return None

        if "url" in model:
            return await self.generate_pollinations(prompt, model_key, user_id)
        elif "hf_model" in model:
            return await self.generate_huggingface(prompt, model_key)

        return None

    async def close(self):
        if self.session:
            await self.session.close()


generator = ImageGenerator()

# --- БОТ ---
storage = MemoryStorage()
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=storage)


# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    """Основная клавиатура бота"""
    keyboard = [
        [KeyboardButton(text="➕ Создать изображение"), KeyboardButton(text="🤖 Модели")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="💡 Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_category_keyboard():
    """Клавиатура выбора категории моделей"""
    buttons = []
    for cat_key, cat_name in MODEL_CATEGORIES.items():
        buttons.append([InlineKeyboardButton(
            text=cat_name,
            callback_data=f"cat_{cat_key}"
        )])
    buttons.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_models_keyboard(category: str, page: int = 0):
    """Клавиатура моделей в категории с пагинацией"""
    models = [(k, v) for k, v in AI_MODELS.items() if v.get("category") == category]

    buttons = []
    items_per_page = 8
    start = page * items_per_page
    end = start + items_per_page
    page_models = models[start:end]

    for key, model in page_models:
        buttons.append([InlineKeyboardButton(
            text=f"{model['name']}",
            callback_data=f"model_{key}"
        )])

    # Пагинация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"page_{category}_{page - 1}"))
    if end < len(models):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"page_{category}_{page + 1}"))

    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([InlineKeyboardButton(text="🔙 К категориям", callback_data="back_to_categories")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ОБРАБОТЧИКИ ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await add_user(message.from_user.id, message.from_user.username or "Anonymous")

    total_models = len(AI_MODELS)
    await message.answer(
        f"👋 Привет, <b>{html.quote(message.from_user.full_name)}</b>!\n\n"
        f"🎨 <b>Бот для бесплатной генерации изображений!</b>\n\n"
        f"✨ <b>{total_models} AI моделей:</b>\n"
        f"• Gemini 2.5 Flash\n"
        f"• GPT-Image 1.5/1.0/Mini\n"
        f"• DALL-E 3/2\n"
        f"• FLUX (15+ вариантов)\n"
        f"• Google Imagen 4.0\n"
        f"• Stable Diffusion 3/XL\n"
        f"• Juggernaut Pro/Lightning\n"
        f"• Seedream 3.0/4.0\n"
        f"• HiDream I1\n"
        f"• Ideogram 3.0\n"
        f"• И многое другое!\n\n"
        f"💎 <b>Особенности:</b>\n"
        f"✔️ Полностью бесплатно\n"
        f"✔️ Без лимитов\n"
        f"✔️ Быстрая генерация (3-35 сек)\n"
        f"✔️ Высокое качество\n\n"
        f"Используй кнопки ниже для навигации! 👇",
        reply_markup=get_main_keyboard()
    )
    await state.clear()


@dp.message(F.text == "💡 Помощь")
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "<b>📖 Как пользоваться:</b>\n\n"
        "1️⃣ Нажми <b>➕ Создать изображение</b>\n"
        "2️⃣ Выбери категорию моделей\n"
        "3️⃣ Выбери конкретную модель\n"
        "4️⃣ Отправь описание изображения\n"
        "5️⃣ Получи результат!\n\n"
        "<b>💡 Советы для крутых результатов:</b>\n\n"
        "🔹 <b>Пиши на английском</b>\n"
        "AI модели лучше понимают English\n\n"
        "🔹 <b>Будь конкретным</b>\n"
        "Вместо: <code>cat</code>\n"
        "Лучше: <code>fluffy orange cat on windowsill, sunlight</code>\n\n"
        "🔹 <b>Указывай стиль</b>\n"
        "<code>realistic, anime, cartoon, oil painting, cyberpunk, fantasy</code>\n\n"
        "🔹 <b>Добавляй детали</b>\n"
        "• Освещение: golden hour, neon lights, soft\n"
        "• Настроение: peaceful, dramatic, mysterious\n"
        "• Качество: highly detailed, 4k, cinematic\n\n"
        "<b>📝 Пример:</b>\n"
        "<code>A majestic lion on a mountain peak at sunset, golden hour lighting, epic scale, highly detailed, fantasy art style</code>",
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == "🤖 Модели")
@dp.message(Command("models"))
async def cmd_models(message: types.Message):
    await show_models_page(message, 0)


async def show_models_page(message: types.Message, page: int = 0):
    """Показывает страницу со всеми моделями"""
    all_models = list(AI_MODELS.items())
    items_per_page = 20
    start = page * items_per_page
    end = start + items_per_page
    page_models = all_models[start:end]

    text = f"<b>🤖 Доступно {len(AI_MODELS)} AI моделей:</b>\n\n"

    current_category = None
    for key, model in page_models:
        category = model.get("category")
        if category != current_category:
            cat_name = MODEL_CATEGORIES.get(category, "Другие")
            text += f"\n<b>{cat_name}:</b>\n"
            current_category = category
        text += f"• {model['name']}\n"

    # Создаем кнопки навигации
    buttons = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"models_page_{page - 1}"))
    if end < len(all_models):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"models_page_{page + 1}"))

    if nav_buttons:
        buttons.append(nav_buttons)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    text += f"\n\nИспользуй <b>➕ Создать изображение</b> для генерации!"

    await message.answer(text, reply_markup=keyboard or get_main_keyboard())


@dp.callback_query(F.data.startswith("models_page_"))
async def models_page_callback(callback: types.CallbackQuery):
    page = int(callback.data.split("_")[-1])

    all_models = list(AI_MODELS.items())
    items_per_page = 20
    start = page * items_per_page
    end = start + items_per_page
    page_models = all_models[start:end]

    text = f"<b>🤖 Доступно {len(AI_MODELS)} AI моделей:</b>\n\n"

    current_category = None
    for key, model in page_models:
        category = model.get("category")
        if category != current_category:
            cat_name = MODEL_CATEGORIES.get(category, "Другие")
            text += f"\n<b>{cat_name}:</b>\n"
            current_category = category
        text += f"• {model['name']}\n"

    # Создаем кнопки навигации
    buttons = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"models_page_{page - 1}"))
    if end < len(all_models):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"models_page_{page + 1}"))

    if nav_buttons:
        buttons.append(nav_buttons)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    text += f"\n\nИспользуй <b>➕ Создать изображение</b> для генерации!"

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.message(F.text == "📊 Статистика")
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    count = await get_user_stats(message.from_user.id)
    await message.answer(
        f"📊 <b>Твоя статистика:</b>\n\n"
        f"🖼️ Создано изображений: <b>{count}</b>\n"
        f"🤖 Доступно моделей: <b>{len(AI_MODELS)}</b>\n"
        f"💰 Потрачено: <b>0₽</b> (бесплатно!)\n\n"
        f"Продолжай творить! 🎨",
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == "➕ Создать изображение")
@dp.message(Command("generate"))
async def cmd_generate(message: types.Message, state: FSMContext):
    await message.answer(
        "🎨 <b>Выбери категорию AI моделей:</b>\n\n"
        "Каждая категория содержит уникальные модели! 👇",
        reply_markup=get_category_keyboard()
    )
    await state.set_state(GenerateImage.choosing_category)


@dp.callback_query(F.data == "ignore")
async def ignore_callback(callback: types.CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.startswith("cat_"))
async def select_category(callback: types.CallbackQuery, state: FSMContext):
    category = callback.data.split("_", 1)[1]
    cat_name = MODEL_CATEGORIES.get(category, "Модели")

    # Сохраняем категорию
    await state.update_data(category=category)

    await callback.message.edit_text(
        f"<b>{cat_name}</b>\n\n"
        f"Выбери модель для генерации:",
        reply_markup=get_models_keyboard(category, 0)
    )
    await state.set_state(GenerateImage.choosing_model)
    await callback.answer()


@dp.callback_query(F.data.startswith("page_"))
async def change_page(callback: types.CallbackQuery, state: FSMContext):
    _, category, page = callback.data.split("_")
    page = int(page)
    cat_name = MODEL_CATEGORIES.get(category, "Модели")

    # Сохраняем категорию
    await state.update_data(category=category)

    await callback.message.edit_text(
        f"<b>{cat_name}</b>\n\n"
        f"Выбери модель для генерации:",
        reply_markup=get_models_keyboard(category, page)
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_categories")
async def back_to_categories(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎨 <b>Выбери категорию AI моделей:</b>\n\n"
        "Каждая категория содержит уникальные модели! 👇",
        reply_markup=get_category_keyboard()
    )
    await state.set_state(GenerateImage.choosing_category)
    await callback.answer()


@dp.callback_query(F.data.startswith("model_"))
async def select_model(callback: types.CallbackQuery, state: FSMContext):
    model_key = callback.data.split("_", 1)[1]
    model = AI_MODELS[model_key]

    # Сохраняем категорию для возможности вернуться назад
    data = await state.get_data()
    category = data.get("category")
    await state.update_data(model_key=model_key, category=category)

    # Создаем кнопку отмены
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel_prompt")]
    ])

    await callback.message.edit_text(
        f"✔️ <b>Выбрана модель:</b> {model['name']}\n\n"
        f"📝 {model['description']}\n"
        f"⚡ Скорость генерации: {model['speed']}\n\n"
        f"Теперь отправь мне описание изображения!\n\n"
        f"💡 <b>Пример:</b>\n"
        f"<code>Cyberpunk city at night, neon lights, rain, flying cars, cinematic lighting, highly detailed</code>",
        reply_markup=cancel_kb
    )

    await state.set_state(GenerateImage.waiting_for_prompt)
    await callback.answer()


@dp.callback_query(F.data == "cancel_prompt")
async def cancel_prompt(callback: types.CallbackQuery, state: FSMContext):
    """Отмена ввода промпта - возврат к выбору модели"""
    data = await state.get_data()
    category = data.get("category")

    if category:
        cat_name = MODEL_CATEGORIES.get(category, "Модели")
        await callback.message.edit_text(
            f"<b>{cat_name}</b>\n\n"
            f"Выбери модель для генерации:",
            reply_markup=get_models_keyboard(category, 0)
        )
        await state.set_state(GenerateImage.choosing_model)
    else:
        # Если категория не сохранена, возвращаемся к выбору категории
        await callback.message.edit_text(
            "🎨 <b>Выбери категорию AI моделей:</b>\n\n"
            "Каждая категория содержит уникальные модели! 👇",
            reply_markup=get_category_keyboard()
        )
        await state.set_state(GenerateImage.choosing_category)

    await callback.answer("✖️ Отменено")


@dp.callback_query(F.data == "cancel")
async def cancel_generation(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("✖️ Отменено")


@dp.message(GenerateImage.waiting_for_prompt)
async def process_prompt(message: types.Message, state: FSMContext):
    prompt = message.text

    if len(prompt) > 1000:
        await message.answer(
            "✖️ Промпт слишком длинный! Максимум 1000 символов.",
            reply_markup=get_main_keyboard()
        )
        return

    data = await state.get_data()
    model_key = data.get("model_key")

    if not model_key:
        await message.answer(
            "✖️ Модель не выбрана. Используй <b>➕ Создать изображение</b>",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return

    model = AI_MODELS[model_key]

    msg = await message.answer(
        f"🎨 <b>Генерирую изображение...</b>\n\n"
        f"🤖 Модель: {model['name']}\n"
        f"📝 Промпт: <i>{html.quote(prompt[:100])}...</i>\n\n"
        f"⏳ Ожидай {model['speed']}...\n\n"
        f"<i>Бесплатная генерация через Pollinations.ai & HuggingFace</i> 🚀"
    )

    try:
        image_data = await generator.generate(
            prompt=prompt,
            model_key=model_key,
            user_id=message.from_user.id
        )

        if image_data:
            await save_generation(message.from_user.id, prompt, model_key)

            photo = types.BufferedInputFile(image_data, filename="ai_generated.png")

            caption = (
                f"✔️ <b>Готово!</b>\n\n"
                f"🤖 {model['name']}\n"
                f"📝 <i>{html.quote(prompt[:180])}</i>\n\n"
                f"💎 Бесплатная AI генерация"
            )

            await message.answer_photo(photo=photo, caption=caption)
            await msg.delete()

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать еще", callback_data="generate_more")],
                [InlineKeyboardButton(text="🔁 Эта же модель", callback_data=f"again_{model_key}")]
            ])
            await message.answer("Что дальше?", reply_markup=kb)
        else:
            await msg.delete()
            await message.answer(
                "✖️ <b>Ошибка генерации</b>\n\n"
                "Возможные причины:\n"
                "• Сервер перегружен (попробуй другую модель)\n"
                "• Промпт содержит запрещенные слова\n"
                "• Временная ошибка API\n\n"
                "<b>Попробуй:</b>\n"
                "1. Упростить промпт\n"
                "2. Выбрать модель 🚀 Turbo\n"
                "3. Подождать минуту\n\n"
                "Используй <b>➕ Создать изображение</b> для новой попытки",
                reply_markup=get_main_keyboard()
            )

    except Exception as e:
        logger.error(f"Generation error: {e}", exc_info=True)
        await msg.delete()
        await message.answer(
            f"✖️ <b>Произошла ошибка:</b>\n"
            f"<code>{str(e)[:200]}</code>\n\n"
            f"Попробуй <b>➕ Создать изображение</b> снова",
            reply_markup=get_main_keyboard()
        )

    await state.clear()


@dp.callback_query(F.data == "generate_more")
async def generate_more(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Отлично! Создадим еще! 🎨")

    # Имитируем сообщение для вызова cmd_generate
    fake_msg = callback.message
    await cmd_generate(fake_msg, state)
    await callback.answer()


@dp.callback_query(F.data.startswith("again_"))
async def generate_again(callback: types.CallbackQuery, state: FSMContext):
    model_key = callback.data.split("_", 1)[1]
    model = AI_MODELS[model_key]

    # Определяем категорию модели
    category = model.get("category")

    await state.update_data(model_key=model_key, category=category)
    await state.set_state(GenerateImage.waiting_for_prompt)

    # Создаем кнопку отмены
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel_prompt")]
    ])

    await callback.message.edit_text(
        f"✔️ Используем <b>{model['name']}</b>\n\n"
        f"Отправь новый промпт!",
        reply_markup=cancel_kb
    )
    await callback.answer()


@dp.message(F.text)
async def handle_text(message: types.Message):
    await message.answer(
        "🤔 Не понимаю команду.\n\n"
        "Используй кнопки ниже или команды:\n"
        "/start - Начать\n"
        "/generate - Создать изображение\n"
        "/help - Помощь",
        reply_markup=get_main_keyboard()
    )


# --- ЗАПУСК ---
async def main():
    await init_db()
    logger.info(f"🚀 Free AI Image Bot запущен!")
    logger.info(f"💎 {len(AI_MODELS)} моделей | Бесплатно | Без лимитов")
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        await generator.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
        