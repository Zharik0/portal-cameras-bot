from typing import Any, cast
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, Message
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import BadRequest, TelegramError, TimedOut, NetworkError
from telegram.request import HTTPXRequest
import os
import sys
import json
import logging
from pathlib import Path
import re

# Типы для конфигурации
ConfigDict = dict[str, Any]

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Добавить родительскую директорию в path для правильного импорта пакета
sys.path.insert(0, str(Path(__file__).parent.parent))

# Загрузка конфигурации из config.json
config_path = Path(__file__).parent / "config.json"
with open(config_path, "r", encoding="utf-8") as f:
    config: ConfigDict = json.load(f)

TOKEN: str = config["telegram"]["token"]
ALLOWED_USERS: list[int] = config["telegram"]["allowed_users"]
CAMERAS_FOLDER: str = config["paths"]["cameras_dir"]
VIDEOS_FOLDER: str = config["paths"]["videos_dir"]

# Список камер из конфигурации
_cameras_list: list[dict[str, Any]] = config["cameras"]

# Построение словаря CAMERAS: {название_камеры: photo_file}
CAMERAS: dict[str, str] = {
    str(camera["name"]): str(camera["photo_file"]) for camera in _cameras_list
}
# Построение словаря для получения ID камеры по названию
CAMERA_ID_MAP: dict[str, str] = {
    str(camera["name"]): str(camera["photo_file"]).split("_")[1].split(".")[0]
    for camera in _cameras_list
}

# Построение словаря команд: {"camera_1": "Улица 1", ...}
CAMERA_COMMANDS: dict[str, str] = {
    f"camera_{i + 1}": str(camera["name"]) for i, camera in enumerate(_cameras_list)
}


# Функция для получения списка видео для камеры
def get_videos_for_camera(camera_name: str) -> list[dict[str, str]]:
    """Получить список видео для конкретной камеры, отсортированный по дате"""
    camera_id: str | None = None
    for cam in _cameras_list:
        if str(cam["name"]) == camera_name:
            photo_file: str = str(cam["photo_file"])
            camera_id = photo_file.split("_")[1].split(".")[0]
            break

    if not camera_id:
        return []

    videos: list[dict[str, str]] = []
    if not os.path.exists(VIDEOS_FOLDER):
        return videos

    # Паттерн 1 (новый): camera_{id}_YYYY-MM-DD_HH-MM_HH-MM.mp4
    pattern_new = rf"camera_{camera_id}_(\d{{4}}-\d{{2}}-\d{{2}})_(\d{{2}}-\d{{2}}_\d{{2}}-\d{{2}})\.mp4"
    # Паттерн 2 (старый): camera_{id}_YYYY-MM-DD_HH-HH.mp4
    pattern_old = rf"camera_{camera_id}_(\d{{4}}-\d{{2}}-\d{{2}})_(\d{{2}}-\d{{2}})\.mp4"

    try:
        filenames: list[str] = os.listdir(VIDEOS_FOLDER)
    except FileNotFoundError:
        return videos

    for filename in filenames:
        if filename.endswith(".mp4"):
            match = re.match(pattern_new, filename)
            if match:
                date_str = match.group(1)  # YYYY-MM-DD
                time_str = match.group(2)  # HH-MM_HH-MM
                videos.append(
                    {
                        "filename": filename,
                        "date": date_str,
                        "time": time_str,
                        "display_name": format_video_time(time_str),
                    }
                )
                continue
            match = re.match(pattern_old, filename)
            if match:
                date_str = match.group(1)  # YYYY-MM-DD
                time_str = match.group(2)  # HH-HH
                videos.append(
                    {
                        "filename": filename,
                        "date": date_str,
                        "time": time_str,
                        "display_name": format_video_time(time_str),
                    }
                )

    # Сортируем по дате и времени (новые первыми)
    videos.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    return videos


def format_video_time(time_str: str) -> str:
    """Форматирует время для кнопки видео"""
    try:
        if "_" in time_str:
            # Новый формат: HH-MM_HH-MM -> HH:MM-HH:MM
            start_time, end_time = time_str.split("_")
            start_formatted = start_time.replace("-", ":")
            end_formatted = end_time.replace("-", ":")
            return f"{start_formatted}-{end_formatted}"
        else:
            # Старый формат: HH-HH -> HH:00-HH:00
            parts = time_str.split("-")
            start_hour = int(parts[0])
            end_hour = int(parts[1])
            return f"{start_hour:02d}:00-{end_hour:02d}:00"
    except Exception:
        return time_str


# Функция для удаления предыдущих сообщений
async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удалить текущее сообщение"""
    if update.callback_query and update.callback_query.message:
        message = update.callback_query.message
        if isinstance(message, Message):
            try:
                await message.delete()
            except BadRequest:
                pass


async def setup_bot_commands(application: Application[Any, ContextTypes.DEFAULT_TYPE, Any, Any, Any, Any]) -> None:
    """Установка команд бота для бокового меню"""
    commands = [BotCommand("start", "Главное меню")]

    # Добавляем команды для каждой камеры
    for cmd, camera_name in CAMERA_COMMANDS.items():
        commands.append(BotCommand(cmd, camera_name))

    await application.bot.set_my_commands(commands)
    logger.info(f"Установлено {len(commands)} команд в меню бота")


async def camera_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Универсальный обработчик команд /camera_X"""
    if update.effective_user is None or update.message is None or update.message.text is None:
        return

    user_id = update.effective_user.id

    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ У вас нет доступа к этому боту.")
        return

    # Получаем команду без слеша
    command = update.message.text.split()[0][1:]  # убираем /

    camera_name = CAMERA_COMMANDS.get(command)
    if not camera_name:
        await update.message.reply_text("❌ Камера не найдена.")
        return

    # Удаляем предыдущие сообщения бота
    if context.user_data is not None and "all_message_ids" in context.user_data:
        all_msg_ids = cast(list[int], context.user_data.get("all_message_ids", []))
        for msg_id in all_msg_ids:
            try:
                await context.bot.delete_message(
                    chat_id=update.message.chat_id,
                    message_id=msg_id,
                )
            except (BadRequest, TelegramError):
                pass

    if context.user_data is not None:
        context.user_data["all_message_ids"] = []

    # Показываем фото камеры
    await show_camera_photo_from_command(update, context, camera_name)


async def show_camera_photo_from_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, camera_name: str
) -> None:
    """Отправить фото камеры при вызове через команду /camera_X"""
    if update.message is None:
        return

    file_name = CAMERAS.get(camera_name)
    if not file_name:
        await update.message.reply_text("Камера не найдена!")
        return

    file_path = os.path.join(CAMERAS_FOLDER, file_name)

    if not os.path.exists(file_path):
        await update.message.reply_text(f"Файл не найден: {file_name}")
        return

    # Создаём клавиатуру
    keyboard = [
        [InlineKeyboardButton("🎥 Видео", callback_data=f"show_videos_{camera_name}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cameras")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        with open(file_path, "rb") as photo:
            msg = await update.message.reply_photo(
                photo=photo,
                caption=f"{camera_name}",
                reply_markup=reply_markup,
            )
            # Сохраняем message_id
            if context.user_data is not None:
                if "all_message_ids" not in context.user_data:
                    context.user_data["all_message_ids"] = []
                all_message_ids = cast(list[int], context.user_data["all_message_ids"])
                all_message_ids.append(msg.message_id)
    except (TimedOut, NetworkError, TelegramError) as e:
        logger.error(f"Ошибка при отправке фото: {e}")
        await update.message.reply_text("Ошибка при загрузке фото камеры")


# Команда /start - показать кнопки с камерами
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id

    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ У вас нет доступа к этому боту.")
        return

    # Удаляем все сообщения бота из истории
    if context.user_data is not None and "all_message_ids" in context.user_data:
        all_msg_ids = cast(list[int], context.user_data.get("all_message_ids", []))
        for msg_id in all_msg_ids:
            try:
                await context.bot.delete_message(
                    chat_id=update.message.chat_id,
                    message_id=msg_id,
                )
            except (BadRequest, TelegramError):
                pass  # Сообщение уже удалено или истекло время

    # Очищаем список message_id'ов
    if context.user_data is not None:
        context.user_data["all_message_ids"] = []

    keyboard: list[list[InlineKeyboardButton]] = []
    for cam_name in CAMERAS.keys():
        keyboard.append(
            [
                InlineKeyboardButton(
                    cam_name, callback_data=f"show_photo_{cam_name}"
                )
            ]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    message = await update.message.reply_text(
        "Выберите камеру:", reply_markup=reply_markup
    )
    # Сохраняем message_id для последующего удаления
    if context.user_data is not None:
        if "all_message_ids" not in context.user_data:
            context.user_data["all_message_ids"] = []
        all_msg_ids = cast(list[int], context.user_data["all_message_ids"])
        all_msg_ids.append(message.message_id)


# Обработка нажатия на кнопку
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    if update.effective_user is None:
        return

    user_id = update.effective_user.id

    if user_id not in ALLOWED_USERS:
        await query.answer("У вас нет доступа!", show_alert=True)
        return

    # Сначала отвечаем на callback
    try:
        await query.answer()
    except BadRequest as e:
        if "too old" in str(e).lower() or "invalid" in str(e).lower():
            logger.warning(f"Callback query устарел: {e}")
            return
        raise

    data = query.data
    if data is None:
        return

    # Определяем тип действия из callback_data
    if data.startswith("show_photo_"):
        # Показать фото камеры с кнопками "Видео" и "Назад"
        camera_name = data.replace("show_photo_", "")
        await show_camera_photo(update, context, camera_name)

    elif data.startswith("show_videos_"):
        # Показать список видео для камеры
        camera_name = data.replace("show_videos_", "")
        await show_videos_list(update, context, camera_name)

    elif data.startswith("play_video_"):
        # Показать выбранное видео
        video_filename = data.replace("play_video_", "")
        await show_video(update, context, video_filename)

    elif data == "back_to_cameras":
        # Вернуться в меню выбора камер
        await back_to_cameras(update, context)

    elif data.startswith("back_to_camera_"):
        # Вернуться к фото камеры
        camera_name = data.replace("back_to_camera_", "")
        await show_camera_photo(update, context, camera_name)

    elif data.startswith("back_to_videos_"):
        # Вернуться к списку видео
        camera_name = data.replace("back_to_videos_", "")
        await show_videos_list(update, context, camera_name)

    else:
        # Это выбор камеры из начального меню
        camera_name = data
        if camera_name in CAMERAS:
            await show_camera_photo(update, context, camera_name)


async def show_camera_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE, camera_name: str
) -> None:
    """Отправить фото камеры с кнопками для выбора видео и возврата"""
    query = update.callback_query
    if query is None or query.message is None or not isinstance(query.message, Message):
        return

    file_name: str | None = CAMERAS.get(camera_name)
    if not file_name:
        await send_new_message(update, context, "Камера не найдена!")
        return

    file_path: str = os.path.join(CAMERAS_FOLDER, file_name)

    if not os.path.exists(file_path):
        await send_new_message(update, context, f"Файл не найден: {file_name}")
        return

    # Удаляем старое сообщение
    await delete_message(update, context)

    # Создаём клавиатуру
    keyboard = [
        [InlineKeyboardButton("🎥 Видео", callback_data=f"show_videos_{camera_name}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cameras")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        with open(file_path, "rb") as photo:
            msg = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=photo,
                caption=f"{camera_name}",
                reply_markup=reply_markup,
            )
            # Сохраняем message_id
            if context.user_data is not None:
                if "all_message_ids" not in context.user_data:
                    context.user_data["all_message_ids"] = []
                all_msg_ids = cast(list[int], context.user_data["all_message_ids"])
                all_msg_ids.append(msg.message_id)
    except (TimedOut, NetworkError, TelegramError) as e:
        logger.error(f"Ошибка при отправке фото: {e}")
        await send_new_message(update, context, "Ошибка при загрузке фото камеры")


async def show_videos_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, camera_name: str
) -> None:
    """Отправить список видео для выбранной камеры в 3 ряда"""
    query = update.callback_query
    if query is None or query.message is None or not isinstance(query.message, Message):
        return

    videos = get_videos_for_camera(camera_name)

    if not videos:
        await send_new_message(update, context, f"Видео для '{camera_name}' не найдены")
        return

    # Удаляем старое сообщение
    await delete_message(update, context)

    # Создаём клавиатуру со списком видео в 3 ряда
    # Видео отсортированы от новых к старым, нужно расположить так:
    # старые слева сверху, новые справа снизу
    # Разворачиваем список (новые в конце станут справа снизу)
    videos_reversed = list(reversed(videos))

    keyboard: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(videos_reversed), 3):
        row: list[InlineKeyboardButton] = []
        for j in range(3):
            if i + j < len(videos_reversed):
                video = videos_reversed[i + j]
                button_text = video["display_name"]
                row.append(
                    InlineKeyboardButton(
                        button_text, callback_data=f"play_video_{video['filename']}"
                    )
                )
        if row:
            keyboard.append(row)

    # Добавляем кнопку "Назад"
    back_button: list[InlineKeyboardButton] = [InlineKeyboardButton("⬅️ Назад", callback_data=f"back_to_camera_{camera_name}")]
    keyboard.append(back_button)

    reply_markup = InlineKeyboardMarkup(keyboard)

    message_text = "Выберите временной интервал"

    try:
        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=message_text,
            reply_markup=reply_markup,
        )
        # Сохраняем message_id
        if context.user_data is not None:
            if "all_message_ids" not in context.user_data:
                context.user_data["all_message_ids"] = []
            all_msg_ids = cast(list[int], context.user_data["all_message_ids"])
            all_msg_ids.append(msg.message_id)
    except (TimedOut, NetworkError, TelegramError) as e:
        logger.error(f"Ошибка при отправке списка видео: {e}")


async def show_video(
    update: Update, context: ContextTypes.DEFAULT_TYPE, video_filename: str
) -> None:
    """Отправить выбранное видео"""
    query = update.callback_query
    if query is None or query.message is None or not isinstance(query.message, Message):
        return

    video_path = os.path.join(VIDEOS_FOLDER, video_filename)

    if not os.path.exists(video_path):
        await send_new_message(update, context, f"Видео не найдено: {video_filename}")
        return

    # Извлекаем информацию из названия файла
    match = re.match(r"camera_(\d+)_(.+)\.mp4", video_filename)
    if not match:
        await send_new_message(update, context, "Ошибка при обработке видео")
        return

    camera_id = match.group(1)
    found_camera_name: str | None = None
    for name, photo_file in CAMERAS.items():
        if photo_file.startswith(f"camera_{camera_id}"):
            found_camera_name = name
            break

    camera_name: str = found_camera_name if found_camera_name else f"Камера {camera_id}"

    # Удаляем старое сообщение
    await delete_message(update, context)

    # Создаём клавиатуру с двумя кнопками
    keyboard = [
        [
            InlineKeyboardButton(
                "🎥 Выбрать другой интервал",
                callback_data=f"back_to_videos_{camera_name}",
            )
        ],
        [
            InlineKeyboardButton(
                "📷 Перейти в меню камер", callback_data="back_to_cameras"
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        with open(video_path, "rb") as video:
            msg = await context.bot.send_video(
                chat_id=query.message.chat_id,
                video=video,
                caption=f"{camera_name}",
                reply_markup=reply_markup,
                write_timeout=300,
                read_timeout=300,
            )
            # Сохраняем message_id
            if context.user_data is not None:
                if "all_message_ids" not in context.user_data:
                    context.user_data["all_message_ids"] = []
                all_msg_ids = cast(list[int], context.user_data["all_message_ids"])
                all_msg_ids.append(msg.message_id)
    except (TimedOut, NetworkError, TelegramError) as e:
        logger.error(f"Ошибка при отправке видео: {e}")
        await send_new_message(update, context, "Ошибка при загрузке видео")


async def back_to_cameras(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вернуться в меню выбора камер"""
    query = update.callback_query
    if query is None or query.message is None or not isinstance(query.message, Message):
        return

    # Удаляем старое сообщение
    await delete_message(update, context)

    keyboard: list[list[InlineKeyboardButton]] = []
    for cam_name in CAMERAS.keys():
        keyboard.append(
            [
                InlineKeyboardButton(
                    cam_name, callback_data=f"show_photo_{cam_name}"
                )
            ]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Выберите камеру:",
            reply_markup=reply_markup,
        )
        # Сохраняем message_id
        if context.user_data is not None:
            if "all_message_ids" not in context.user_data:
                context.user_data["all_message_ids"] = []
            all_msg_ids = cast(list[int], context.user_data["all_message_ids"])
            all_msg_ids.append(msg.message_id)
    except (TimedOut, NetworkError, TelegramError) as e:
        logger.error(f"Ошибка при отправке меню камер: {e}")


async def send_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Отправить новое сообщение об ошибке"""
    query = update.callback_query
    if query is None or query.message is None or not isinstance(query.message, Message):
        return

    # Удаляем старое сообщение
    await delete_message(update, context)

    try:
        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"{text}",
        )
        # Сохраняем message_id
        if context.user_data is not None:
            if "all_message_ids" not in context.user_data:
                context.user_data["all_message_ids"] = []
            all_msg_ids = cast(list[int], context.user_data["all_message_ids"])
            all_msg_ids.append(msg.message_id)
    except (TimedOut, NetworkError, TelegramError) as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")


# Глобальный обработчик ошибок
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception while handling an update: {context.error}")


# Запуск бота
def main() -> None:
    # Увеличиваем таймауты для медленных соединений
    request = HTTPXRequest(
        connect_timeout=10.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )

    app = Application.builder().token(TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))

    # Регистрируем обработчики для всех команд камер
    for cmd in CAMERA_COMMANDS.keys():
        app.add_handler(CommandHandler(cmd, camera_command))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)

    # Устанавливаем команды меню при запуске
    app.post_init = setup_bot_commands

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
