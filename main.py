import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, CallbackContext,
    MessageHandler, filters, JobQueue
)
import datetime
import sqlite3
from typing import Optional, List, Tuple

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы
DB_NAME = 'boardgames.db'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DISPLAY_DATE_FORMAT = '%d.%m.%Y %H:%M'
SHORT_DATE_FORMAT = '%d.%m %H:%M'


class DatabaseManager:
    """Класс для управления взаимодействием с базой данных"""

    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        self._init_db()

    def _init_db(self) -> None:
        """Инициализация структуры базы данных"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Таблица игр
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name TEXT NOT NULL,
                game_date DATETIME NOT NULL,
                creator_id INTEGER NOT NULL,
                max_players INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                photo_id TEXT DEFAULT NULL
            )
            ''')

            # Таблица игроков
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS players (
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                join_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (game_id, user_id),
                FOREIGN KEY (game_id) REFERENCES games (id)
            )
            ''')

            # Таблица напоминаний
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reminded BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (game_id) REFERENCES games (id)
            )
            ''')
            conn.commit()

    def _get_connection(self):
        """Возвращает соединение с базой данных"""
        return sqlite3.connect(self.db_name)

    def execute_query(self, query: str, params: tuple = (), fetch_one: bool = False):
        """Выполняет SQL-запрос и возвращает результат"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone() if fetch_one else cursor.fetchall()

    def execute_update(self, query: str, params: tuple = ()) -> int:
        """Выполняет SQL-запрос на обновление и возвращает ID последней записи"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid


db = DatabaseManager()


# Вспомогательные функции
def format_date(date_str: str, output_format: str = DISPLAY_DATE_FORMAT) -> str:
    """Форматирует дату из строки в указанный формат"""
    return datetime.datetime.strptime(date_str, DATE_FORMAT).strftime(output_format)


def get_game_info(game_id: int) -> Optional[Tuple]:
    """Возвращает информацию об игре по ID"""
    query = '''
    SELECT g.game_name, g.game_date, g.max_players, g.description, g.photo_id,
           (SELECT COUNT(*) FROM players WHERE game_id = g.id) as current_players,
           u.username as creator_name
    FROM games g
    LEFT JOIN players u ON g.creator_id = u.user_id AND g.id = u.game_id
    WHERE g.id = ?
    '''
    return db.execute_query(query, (game_id,), fetch_one=True)


def get_game_participants(game_id: int) -> List[Tuple]:
    """Возвращает список участников игры"""
    return db.execute_query('SELECT user_id, username FROM players WHERE game_id = ?', (game_id,))


def is_user_creator(game_id: int, user_id: int) -> bool:
    """Проверяет, является ли пользователь создателем игры"""
    query = 'SELECT creator_id FROM games WHERE id = ?'
    result = db.execute_query(query, (game_id,), fetch_one=True)
    return result and result[0] == user_id


def is_user_joined(game_id: int, user_id: int) -> bool:
    """Проверяет, записан ли пользователь на игру"""
    query = 'SELECT 1 FROM players WHERE game_id = ? AND user_id = ?'
    return bool(db.execute_query(query, (game_id, user_id), fetch_one=True))


# Обработчики команд
async def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}!\n"
        "Я бот для записи на настольные игры. Используй /menu для выбора действий."
    )


async def menu(update: Update, context: CallbackContext) -> None:
    """Отображает главное меню"""
    keyboard = [
        [InlineKeyboardButton("Предстоящие игры", callback_data='upcoming_games')],
        [InlineKeyboardButton("Создать игру", callback_data='create_game')],
        [InlineKeyboardButton("Мои записи", callback_data='my_bookings')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text('Выберите действие:', reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text('Выберите действие:', reply_markup=reply_markup)


async def show_upcoming_games(update: Update, context: CallbackContext) -> None:
    """Показывает список предстоящих игр"""
    query = update.callback_query
    await query.answer()

    games = db.execute_query('''
    SELECT id, game_name, game_date, max_players, 
           (SELECT COUNT(*) FROM players WHERE game_id = games.id) as current_players
    FROM games
    WHERE game_date >= datetime('now')
    ORDER BY game_date
    ''')

    if not games:
        await query.edit_message_text(text="Нет запланированных игр. Хотите создать свою?")
        return

    keyboard = []
    for game in games:
        game_id, game_name, game_date, max_players, current_players = game
        date_str = format_date(game_date, SHORT_DATE_FORMAT)

        btn_text = f"{game_name} - {date_str} ({current_players}/{max_players})" if max_players > 0 \
            else f"{game_name} - {date_str} ({current_players}+)"

        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'game_{game_id}')])

    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_menu')])
    await query.edit_message_text(
        text="Предстоящие игры:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def create_game_step1(update: Update, context: CallbackContext) -> None:
    """Начинает процесс создания игры"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Введите название игры:")
    context.user_data['action'] = 'creating_game'


async def handle_message(update: Update, context: CallbackContext) -> None:
    """Обрабатывает текстовые сообщения в процессе создания игры"""
    action = context.user_data.get('action')
    text = update.message.text.lower()

    if action == 'creating_game':
        context.user_data['game_name'] = update.message.text
        context.user_data['action'] = 'setting_game_date'
        await update.message.reply_text("Введите дату и время игры в формате ДД.ММ.ГГГГ ЧЧ:ММ")

    elif action == 'setting_game_date':
        try:
            game_date = datetime.datetime.strptime(update.message.text, '%d.%m.%Y %H:%M')
            context.user_data['game_date'] = game_date
            context.user_data['action'] = 'setting_max_players'
            await update.message.reply_text("Введите максимальное количество игроков (0 - без ограничений):")
        except ValueError:
            await update.message.reply_text("Неверный формат даты. Попробуйте еще раз.")

    elif action == 'setting_max_players':
        try:
            max_players = int(update.message.text)
            if max_players < 0:
                raise ValueError

            context.user_data['max_players'] = max_players
            context.user_data['action'] = 'setting_description'
            await update.message.reply_text("Введите описание игры (или /skip чтобы пропустить):")
        except ValueError:
            await update.message.reply_text("Пожалуйста, введите число (0 - без ограничений):")

    elif action == 'setting_description' and text != '/skip':
        context.user_data['description'] = update.message.text
        context.user_data['action'] = 'setting_photo'
        await update.message.reply_text("Пришлите фото для игры (или /skip чтобы пропустить):")

    elif action == 'setting_photo' and update.message.photo and text != '/skip':
        context.user_data['photo_id'] = update.message.photo[-1].file_id
        await create_game_in_db(update, context)

    elif action == 'setting_photo' and text != '/skip':
        await update.message.reply_text("Пожалуйста, пришлите фото или /skip чтобы пропустить")
    else:
        await create_game_in_db(update, context)


async def create_game_in_db(update: Update, context: CallbackContext) -> None:
    """Создает игру в базе данных на основе данных из context.user_data"""
    user_data = context.user_data
    user = update.message.from_user

    try:
        game_id = db.execute_update('''
        INSERT INTO games (game_name, game_date, creator_id, max_players, description, photo_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            user_data['game_name'],
            user_data['game_date'].strftime(DATE_FORMAT),
            user.id,
            user_data.get('max_players', 0),
            user_data.get('description', ''),
            user_data.get('photo_id', None))
                                    )

        # Добавляем создателя в список игроков
        db.execute_update('''
        INSERT INTO players (game_id, user_id, username)
        VALUES (?, ?, ?)
        ''', (game_id, user.id, user.username))

        # Планируем напоминания
        if context.job_queue:
            schedule_reminders(game_id, context.job_queue)

        await update.message.reply_text(f"🎉 Игра создана! ID: {game_id}")

    except Exception as e:
        logger.error(f"Ошибка при создании игры: {e}")
        await update.message.reply_text("Произошла ошибка при создании игры.")
    finally:
        context.user_data.clear()
        await menu(update, context)


async def game_details(update: Update, context: CallbackContext) -> None:
    """Показывает детали игры и кнопки управления"""
    query = update.callback_query
    await query.answer()

    game_id = int(query.data.split('_')[1])
    game = get_game_info(game_id)

    if not game:
        await query.edit_message_text(text="Игра не найдена.")
        return

    (game_name, game_date, max_players, description, photo_id,
     current_players, creator_name) = game

    players = get_game_participants(game_id)
    date_str = format_date(game_date)
    players_list = "\n".join([f"👤 @{username}" for (_, username) in players])

    text = (
        f"🎲 <b>{game_name}</b>\n"
        f"📅 <b>Дата:</b> {date_str}\n"
        f"👑 <b>Создатель:</b> @{creator_name}\n"
        f"👥 <b>Игроки:</b> {current_players}{f'/{max_players}' if max_players > 0 else '+'}\n"
    )

    if description:
        text += f"\n📝 <b>Описание:</b>\n{description}\n"

    # Создаем клавиатуру в зависимости от роли пользователя
    keyboard = []
    user_id = query.from_user.id

    if is_user_creator(game_id, user_id):
        keyboard.append([InlineKeyboardButton("❌ Удалить игру", callback_data=f'delete_game_{game_id}')])
    elif is_user_joined(game_id, user_id):
        keyboard.append([InlineKeyboardButton("❌ Отменить запись", callback_data=f'cancel_booking_{game_id}')])
    elif max_players == 0 or current_players < max_players:
        keyboard.append([InlineKeyboardButton("✅ Записаться", callback_data=f'book_{game_id}')])
    else:
        text += "\n⚠️ <b>Мест нет!</b>"

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='upcoming_games')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем сообщение с фото или без
    if photo_id:
        try:
            await query.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=photo_id,
                caption=text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            await query.delete_message()
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}")
            await query.edit_message_text(text=text, parse_mode='HTML', reply_markup=reply_markup)
    else:
        await query.edit_message_text(text=text, parse_mode='HTML', reply_markup=reply_markup)


async def book_game(update: Update, context: CallbackContext) -> None:
    """Обрабатывает запись пользователя на игру"""
    query = update.callback_query
    await query.answer()

    game_id = int(query.data.split('_')[1])
    user_id = query.from_user.id
    username = query.from_user.username

    try:
        # Проверяем наличие мест
        game = get_game_info(game_id)
        if not game:
            await query.answer("Игра не найдена!")
            return

        max_players, current_players = game[2], game[5]

        if max_players > 0 and current_players >= max_players:
            await query.answer("К сожалению, все места уже заняты!")
            return

        if is_user_joined(game_id, user_id):
            await query.answer("Вы уже записаны на эту игру!")
            return

        # Записываем пользователя
        db.execute_update('''
        INSERT INTO players (game_id, user_id, username)
        VALUES (?, ?, ?)
        ''', (game_id, user_id, username))

        # Отправляем уведомление создателю
        try:
            await query.bot.send_message(
                chat_id=game[6],  # creator_id
                text=f"🎉 Новый участник на игру {game[0]}!\n"
                     f"Пользователь @{username} записался на вашу игру.\n"
                     f"Теперь участников: {current_players + 1}{f'/{max_players}' if max_players > 0 else ''}"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление создателю: {e}")

        await query.answer("Вы успешно записались на игру!")
        await game_details(update, context)

    except Exception as e:
        logger.error(f"Ошибка при записи на игру: {e}")
        await query.answer("Произошла ошибка при записи на игру.")


async def cancel_booking(update: Update, context: CallbackContext) -> None:
    """Обрабатывает отмену записи на игру"""
    query = update.callback_query
    await query.answer()

    game_id = int(query.data.split('_')[2])
    user_id = query.from_user.id

    try:
        if not is_user_joined(game_id, user_id):
            await query.answer("Вы не записаны на эту игру!")
            return

        # Удаляем запись
        db.execute_update('DELETE FROM players WHERE game_id = ? AND user_id = ?', (game_id, user_id))

        # Отправляем уведомление создателю
        game = get_game_info(game_id)
        if game:
            try:
                await query.bot.send_message(
                    chat_id=game[6],  # creator_id
                    text=f"😕 Участник отменил запись на игру {game[0]}.\n"
                         f"Пользователь @{query.from_user.username} больше не участвует."
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление создателю: {e}")

        await query.answer("Вы отменили запись на игру.")
        await game_details(update, context)

    except Exception as e:
        logger.error(f"Ошибка при отмене записи: {e}")
        await query.answer("Произошла ошибка при отмене записи.")


async def delete_game(update: Update, context: CallbackContext) -> None:
    """Обрабатывает удаление игры"""
    query = update.callback_query
    await query.answer()

    game_id = int(query.data.split('_')[2])
    user_id = query.from_user.id

    try:
        if not is_user_creator(game_id, user_id):
            await query.answer("Вы не можете удалить эту игру!")
            return

        # Получаем информацию об игре перед удалением
        game = get_game_info(game_id)
        if not game:
            await query.answer("Игра не найдена!")
            return

        # Получаем список участников
        participants = db.execute_query('SELECT user_id FROM players WHERE game_id = ?', (game_id,))

        # Удаляем игру
        db.execute_update('DELETE FROM games WHERE id = ?', (game_id,))
        db.execute_update('DELETE FROM players WHERE game_id = ?', (game_id,))
        db.execute_update('DELETE FROM reminders WHERE game_id = ?', (game_id,))

        await query.answer("Игра удалена.")

        # Уведомляем участников
        for participant_id in participants:
            if participant_id[0] != user_id:  # Не уведомляем создателя
                try:
                    await query.bot.send_message(
                        chat_id=participant_id[0],
                        text=f"❌ Игра {game[0]}, на которую вы записались, была отменена создателем."
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление участнику {participant_id[0]}: {e}")

        await show_upcoming_games(update, context)

    except Exception as e:
        logger.error(f"Ошибка при удалении игры: {e}")
        await query.answer("Произошла ошибка при удалении игры.")


async def show_my_bookings(update: Update, context: CallbackContext) -> None:
    """Показывает игры, на которые записан пользователь"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    games = db.execute_query('''
    SELECT g.id, g.game_name, g.game_date, g.max_players, 
           (SELECT COUNT(*) FROM players p WHERE p.game_id = g.id) as current_players,
           CASE WHEN g.creator_id = ? THEN 1 ELSE 0 END as is_creator
    FROM games g
    LEFT JOIN players p ON g.id = p.game_id
    WHERE p.user_id = ? AND g.game_date >= datetime('now')
    ORDER BY g.game_date
    ''', (user_id, user_id))

    if not games:
        text = "У вас нет активных записей на игры."
    else:
        text = "🎲 <b>Ваши записи:</b>\n\n"
        for game in games:
            game_id, game_name, game_date, max_players, current_players, is_creator = game
            date_str = format_date(game_date, SHORT_DATE_FORMAT)
            role = "👑 Создатель" if is_creator else "👤 Участник"
            players_str = f"{current_players}/{max_players}" if max_players > 0 else f"{current_players}+"

            text += f"{role}: <b>{game_name}</b> - {date_str} ({players_str})\n"
            text += f"<a href='tg://btn/{game_id}'>Просмотреть</a>\n\n"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')]]
    await query.edit_message_text(
        text=text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def schedule_reminders(game_id: int, job_queue: JobQueue) -> None:
    """Планирует напоминания об игре"""
    game = get_game_info(game_id)
    if not game:
        return

    game_date = datetime.datetime.strptime(game[1], DATE_FORMAT)
    now = datetime.datetime.now()

    # Напоминание за 1 день
    reminder_day = game_date - datetime.timedelta(days=1)
    if reminder_day > now:
        job_queue.run_once(
            callback=send_reminder,
            when=reminder_day,
            data=game_id,
            name=f"reminder_1day_{game_id}"
        )

    # Напоминание за 1 час
    reminder_hour = game_date - datetime.timedelta(hours=1)
    if reminder_hour > now:
        job_queue.run_once(
            callback=send_reminder,
            when=reminder_hour,
            data=game_id,
            name=f"reminder_1hour_{game_id}"
        )


async def send_reminder(context: CallbackContext) -> None:
    """Отправляет напоминание об игре"""
    game_id = context.job.data
    game = get_game_info(game_id)

    if not game:
        return

    game_name, game_date_str = game[0], game[1]
    game_date = datetime.datetime.strptime(game_date_str, DATE_FORMAT)
    now = datetime.datetime.now()

    time_left = "1 день" if now < game_date - datetime.timedelta(hours=23) else "1 час"
    date_str = format_date(game_date_str)

    text = (
        f"⏰ <b>Напоминание о игре {game_name}</b>\n"
        f"Игра начнется через {time_left}!\n"
        f"Дата: {date_str}"
    )

    participants = get_game_participants(game_id)
    for user_id, _ in participants:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание пользователю {user_id}: {e}")


async def button(update: Update, context: CallbackContext) -> None:
    """Обрабатывает нажатия кнопок"""
    query = update.callback_query
    await query.answer()

    handlers = {
        'upcoming_games': show_upcoming_games,
        'create_game': create_game_step1,
        'game_': game_details,
        'book_': book_game,
        'cancel_booking_': cancel_booking,
        'delete_game_': delete_game,
        'my_bookings': show_my_bookings,
        'back_to_menu': menu
    }

    for prefix, handler in handlers.items():
        if query.data.startswith(prefix):
            if prefix.endswith('_'):
                await handler(update, context)
            else:
                await handler(update, context)
            break


def main() -> None:
    """Запускает бота"""
    # Создаем Application и передаем токен бота
    application = Application.builder().token("8103700421:AAGBGKeZ5UHXOYCGT3PU2tmW9XnS-xOxlhY").build()

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))

    # Запускаем бота
    application.run_polling()


if __name__ == '__main__':
    main()