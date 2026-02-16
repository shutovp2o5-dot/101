#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram бот для управления расписанием
"""

import json
import os
import asyncio
import random
import logging
import fcntl
import sys
import signal
import shutil
import time
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from functools import wraps
from zoneinfo import ZoneInfo

# Импорты для определения часового пояса по городу (опциональные)
try:
    from geopy.geocoders import Nominatim
    from timezonefinder import TimezoneFinder
    GEOCODING_AVAILABLE = True
except ImportError as e:
    GEOCODING_AVAILABLE = False
    # logger будет определен позже, поэтому используем print для предупреждения
    import sys
    print(f"⚠️  Библиотеки geopy и timezonefinder не установлены: {e}", file=sys.stderr)
    print("⚠️  Автоматическое определение часового пояса будет недоступно.", file=sys.stderr)
    print("⚠️  Для установки выполните: pip install geopy timezonefinder", file=sys.stderr)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from telegram.error import Conflict, RetryAfter, TimedOut, NetworkError, TelegramError

# Состояния для ConversationHandler
(WAITING_TITLE, WAITING_DATE, WAITING_TIME, WAITING_DESCRIPTION, 
 WAITING_CATEGORY, WAITING_REPEAT, WAITING_REMINDER_1, WAITING_EDIT_CHOICE, WAITING_EDIT_VALUE,
 WAITING_CATEGORY_NAME, WAITING_CATEGORY_EDIT_NAME, WAITING_CATEGORY_DELETE_CONFIRM, WAITING_CITY) = range(13)

# Файл для хранения данных
DATA_FILE = 'schedule_data.json'
MESSAGES_FILE = 'user_messages.json'  # Файл для хранения ID сообщений бота
USER_MESSAGES_FILE = 'user_sent_messages.json'  # Файл для хранения ID сообщений пользователя
CATEGORIES_FILE = 'user_categories.json'  # Файл для хранения категорий пользователей
USER_SETTINGS_FILE = 'user_settings.json'  # Файл для хранения настроек пользователей (город, часовой пояс)
LOCK_FILE = 'bot.lock'  # Файл блокировки для предотвращения множественных запусков

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Логируем статус библиотек для диагностики
if GEOCODING_AVAILABLE:
    logger.info("✅ Библиотеки geopy и timezonefinder успешно загружены. Автоматическое определение часового пояса доступно.")
else:
    logger.warning("⚠️  Библиотеки geopy и timezonefinder не установлены. Автоматическое определение часового пояса недоступно.")
    logger.warning("⚠️  Попытка автоматической установки...")
    
    # Попытка автоматической установки библиотек
    try:
        import subprocess
        import sys
        
        # Пытаемся установить библиотеки
        logger.info("📦 Установка geopy...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "geopy==2.4.1"], 
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        logger.info("📦 Установка timezonefinder...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "timezonefinder==6.2.0"], 
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Пытаемся импортировать снова
        try:
            from geopy.geocoders import Nominatim
            from timezonefinder import TimezoneFinder
            GEOCODING_AVAILABLE = True
            logger.info("✅ Библиотеки успешно установлены и загружены!")
        except ImportError:
            logger.warning("⚠️  Не удалось загрузить библиотеки после установки. Требуется перезапуск бота.")
    except Exception as e:
        logger.warning(f"⚠️  Не удалось автоматически установить библиотеки: {e}")
        logger.warning("⚠️  Для установки выполните: pip install geopy timezonefinder")

# Глобальная переменная для graceful shutdown
shutdown_requested = False
application_instance = None


def signal_handler(signum, frame):
    """Обработчик сигналов для graceful shutdown"""
    global shutdown_requested, application_instance
    logger.info(f"📶 Получен сигнал {signum}. Начинаем graceful shutdown...")
    shutdown_requested = True
    
    if application_instance:
        try:
            # Останавливаем бота корректно
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(application_instance.stop())
            loop.run_until_complete(application_instance.shutdown())
            loop.close()
            logger.info("✅ Бот корректно остановлен")
        except Exception as e:
            logger.error(f"❌ Ошибка при остановке бота: {e}")
    
    sys.exit(0)


def retry_on_error(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Декоратор для повторных попыток при ошибках"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (RetryAfter, TimedOut, NetworkError) as e:
                    last_exception = e
                    if isinstance(e, RetryAfter):
                        wait_time = e.retry_after
                    else:
                        wait_time = current_delay
                    logger.warning(f"⚠️  Попытка {attempt + 1}/{max_retries} не удалась: {e}. Повтор через {wait_time}с")
                    await asyncio.sleep(wait_time)
                    current_delay *= backoff
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"⚠️  Попытка {attempt + 1}/{max_retries} не удалась: {e}. Повтор через {current_delay}с")
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            if last_exception:
                raise last_exception
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"⚠️  Попытка {attempt + 1}/{max_retries} не удалась: {e}. Повтор через {current_delay}с")
                    time.sleep(current_delay)
                    current_delay *= backoff
            if last_exception:
                raise last_exception
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    return decorator


def atomic_write(file_path: str, data: Any, backup: bool = True):
    """Атомарная запись в файл с созданием backup"""
    temp_path = f"{file_path}.tmp"
    backup_path = f"{file_path}.bak"
    
    try:
        # Создаем backup существующего файла
        if backup and os.path.exists(file_path):
            shutil.copy2(file_path, backup_path)
        
        # Записываем во временный файл
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Принудительная запись на диск
        
        # Атомарно заменяем оригинальный файл
        os.replace(temp_path, file_path)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при записи файла {file_path}: {e}")
        # Восстанавливаем из backup при ошибке
        if backup and os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, file_path)
                logger.info(f"✅ Восстановлен backup для {file_path}")
            except:
                pass
        # Удаляем временный файл при ошибке
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise

# Стандартные категории по умолчанию (используются только при первом запуске)
DEFAULT_CATEGORIES = {
    'other': 'остальное'
}

# Дни недели на русском
WEEKDAYS = {
    0: 'Понедельник',
    1: 'Вторник',
    2: 'Среда',
    3: 'Четверг',
    4: 'Пятница',
    5: 'Суббота',
    6: 'Воскресенье'
}

WEEKDAYS_SHORT = {
    0: 'Пн',
    1: 'Вт',
    2: 'Ср',
    3: 'Чт',
    4: 'Пт',
    5: 'Сб',
    6: 'Вс'
}

# Месяцы на русском
MONTHS_RU = {
    'января': 1, 'январь': 1,
    'февраля': 2, 'февраль': 2,
    'марта': 3, 'март': 3,
    'апреля': 4, 'апрель': 4,
    'мая': 5, 'май': 5,
    'июня': 6, 'июнь': 6,
    'июля': 7, 'июль': 7,
    'августа': 8, 'август': 8,
    'сентября': 9, 'сентябрь': 9,
    'октября': 10, 'октябрь': 10,
    'ноября': 11, 'ноябрь': 11,
    'декабря': 12, 'декабрь': 12
}

# Относительные даты
RELATIVE_DATES = {
    'сегодня': 0,
    'завтра': 1,
    'послезавтра': 2,
    'через день': 2,
    'через 2 дня': 2,
    'через 3 дня': 3,
    'через неделю': 7,
    # Добавляем варианты с заглавной буквой и другими возможными написаниями
    'Сегодня': 0,
    'Завтра': 1,
    'Послезавтра': 2,
    'СЕГОДНЯ': 0,
    'ЗАВТРА': 1,
    'ПОСЛЕЗАВТРА': 2
}

# Дни недели для парсинга
WEEKDAYS_PARSE = {
    'понедельник': 0,
    'пн': 0,
    'вторник': 1,
    'вт': 1,
    'среда': 2,
    'ср': 2,
    'четверг': 3,
    'чт': 3,
    'пятница': 4,
    'пт': 4,
    'суббота': 5,
    'сб': 5,
    'воскресенье': 6,
    'вс': 6
}


def get_weekday(date_obj: datetime) -> str:
    """Получение дня недели на русском"""
    return WEEKDAYS[date_obj.weekday()]


def get_weekday_short(date_obj: datetime) -> str:
    """Получение сокращенного дня недели на русском"""
    return WEEKDAYS_SHORT[date_obj.weekday()]


def get_main_keyboard():
    """Создание основной клавиатуры с командами"""
    keyboard = [
        [
            KeyboardButton("➕"),
            KeyboardButton("моё расписание")
        ],
        [
            KeyboardButton("что сегодня?"),
            KeyboardButton("что завтра?")
        ],
        [
            KeyboardButton("✏️"),
            KeyboardButton("🙈")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def format_date_natural(date_obj: datetime) -> str:
    """Форматирование даты в естественном формате (например, "18 января")"""
    day = date_obj.day
    month_names = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    month = month_names[date_obj.month]
    return f"{day} {month}"


def parse_natural_date(date_str: str, user_timezone: Optional[str] = None) -> Optional[datetime]:
    """Парсинг естественных формулировок дат на русском языке
    Учитывает часовой пояс пользователя для относительных дат"""
    date_str = date_str.strip().lower()
    
    # Получаем текущее время в часовом поясе пользователя
    if user_timezone:
        try:
            tz = ZoneInfo(user_timezone)
            now = datetime.now(tz)
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        except:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    today_weekday = today.weekday()  # 0 = понедельник, 6 = воскресенье
    
    # Проверка относительных дат (сегодня, завтра, послезавтра)
    if date_str in RELATIVE_DATES:
        days_offset = RELATIVE_DATES[date_str]
        result = today + timedelta(days=days_offset)
        # Убираем timezone info для возврата naive datetime
        if result.tzinfo is not None:
            result = result.replace(tzinfo=None)
        return result
    
    # Проверка дней недели (понедельник, вторник и т.д.)
    if date_str in WEEKDAYS_PARSE:
        target_weekday = WEEKDAYS_PARSE[date_str]
        # Вычисляем количество дней до ближайшего дня недели
        days_ahead = target_weekday - today_weekday
        # Если день недели уже прошел на этой неделе, берем следующий
        if days_ahead < 0:
            days_ahead += 7
        # Если сегодня нужный день недели, возвращаем сегодня
        if days_ahead == 0:
            result = today
        else:
            result = today + timedelta(days=days_ahead)
        # Убираем timezone info для возврата naive datetime
        if result.tzinfo is not None:
            result = result.replace(tzinfo=None)
        return result
    
    # Проверка формата "DD MM" (например, "19 01", "25 12")
    parts = date_str.split()
    if len(parts) == 2:
        try:
            day = int(parts[0])
            month = int(parts[1])
            
            # Проверяем, что это два числа (день и месяц)
            if 1 <= month <= 12 and 1 <= day <= 31:
                current_year = today.year
                
                # Создаем дату
                try:
                    date_obj = datetime(current_year, month, day)
                    # Если дата уже прошла в этом году, берем следующий год
                    if date_obj < today:
                        date_obj = datetime(current_year + 1, month, day)
                    return date_obj
                except ValueError:
                    return None
        except ValueError:
            pass
    
    # Проверка формата "число месяц" (например, "17 января", "18 января")
    if len(parts) == 2:
        try:
            day = int(parts[0])
            month_name = parts[1].lower()
            
            if month_name in MONTHS_RU:
                month = MONTHS_RU[month_name]
                current_year = today.year
                
                # Создаем дату
                try:
                    date_obj = datetime(current_year, month, day)
                    # Сравниваем с naive datetime
                    today_naive = today.replace(tzinfo=None) if today.tzinfo else today
                    # Если дата уже прошла в этом году, берем следующий год
                    if date_obj < today_naive:
                        date_obj = datetime(current_year + 1, month, day)
                    return date_obj
                except ValueError:
                    return None
        except ValueError:
            pass
    
    # Проверка формата "через N дней" или "через неделю"
    if date_str.startswith('через'):
        parts = date_str.split()
        if len(parts) >= 2:
            if parts[1] == 'неделю' or parts[1] == 'недели':
                result = today + timedelta(days=7)
            elif parts[1].isdigit():
                if len(parts) >= 3 and (parts[2] == 'дня' or parts[2] == 'дней' or parts[2] == 'день'):
                    days = int(parts[1])
                    result = today + timedelta(days=days)
                elif len(parts) >= 3 and (parts[2] == 'недели' or parts[2] == 'недель' or parts[2] == 'неделю'):
                    weeks = int(parts[1])
                    result = today + timedelta(days=weeks * 7)
                else:
                    return None
            else:
                return None
            # Убираем timezone info для возврата naive datetime
            if result.tzinfo is not None:
                result = result.replace(tzinfo=None)
            return result
    
    return None


def validate_event(event: Dict) -> bool:
    """Валидация события перед сохранением"""
    required_fields = ['id', 'title', 'date', 'time', 'category']
    for field in required_fields:
        if field not in event:
            logger.error(f"❌ Событие не содержит обязательное поле: {field}")
            return False
    
    # Проверка формата даты
    try:
        datetime.strptime(event['date'], '%Y-%m-%d')
    except ValueError:
        logger.error(f"❌ Неверный формат даты: {event['date']}")
        return False
    
    # Проверка формата времени
    try:
        datetime.strptime(event['time'], '%H:%M')
    except ValueError:
        logger.error(f"❌ Неверный формат времени: {event['time']}")
        return False
    
    return True


@retry_on_error(max_retries=3, delay=0.5)
def load_data() -> Dict[str, List[Dict]]:
    """Загрузка данных из файла с retry механизмом"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Валидация загруженных данных
                if isinstance(data, dict):
                    for user_id, events in data.items():
                        if isinstance(events, list):
                            data[user_id] = [e for e in events if validate_event(e)]
                    return data
                return {}
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON в {DATA_FILE}: {e}")
            # Пытаемся восстановить из backup
            backup_path = f"{DATA_FILE}.bak"
            if os.path.exists(backup_path):
                try:
                    logger.info(f"🔄 Попытка восстановления из backup: {backup_path}")
                    shutil.copy2(backup_path, DATA_FILE)
                    with open(DATA_FILE, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except:
                    pass
            return {}
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке данных: {e}")
            return {}
    return {}


@retry_on_error(max_retries=3, delay=0.5)
def save_data(data: Dict[str, List[Dict]]):
    """Сохранение данных в файл с атомарной записью и backup"""
    # Валидация данных перед сохранением
    validated_data = {}
    for user_id, events in data.items():
        if isinstance(events, list):
            validated_data[user_id] = [e for e in events if validate_event(e)]
        else:
            validated_data[user_id] = []
    
    atomic_write(DATA_FILE, validated_data, backup=True)


@retry_on_error(max_retries=3, delay=0.5)
def load_messages() -> Dict[str, List[int]]:
    """Загрузка ID сообщений пользователей с retry механизмом"""
    if os.path.exists(MESSAGES_FILE):
        try:
            with open(MESSAGES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON в {MESSAGES_FILE}: {e}")
            backup_path = f"{MESSAGES_FILE}.bak"
            if os.path.exists(backup_path):
                try:
                    shutil.copy2(backup_path, MESSAGES_FILE)
                    with open(MESSAGES_FILE, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except:
                    pass
            return {}
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке сообщений: {e}")
            return {}
    return {}


@retry_on_error(max_retries=3, delay=0.5)
def save_messages(messages_data: Dict[str, List[int]]):
    """Сохранение ID сообщений с атомарной записью"""
    atomic_write(MESSAGES_FILE, messages_data, backup=True)


def add_message_id(user_id: str, message_id: int):
    """Добавление ID сообщения бота для пользователя"""
    messages_data = load_messages()
    user_id_str = str(user_id)
    if user_id_str not in messages_data:
        messages_data[user_id_str] = []
    messages_data[user_id_str].append(message_id)
    # Храним только последние 50 сообщений для каждого пользователя
    messages_data[user_id_str] = messages_data[user_id_str][-50:]
    save_messages(messages_data)


@retry_on_error(max_retries=3, delay=0.5)
def load_user_sent_messages() -> Dict[str, List[int]]:
    """Загрузка ID сообщений пользователя с retry механизмом"""
    if os.path.exists(USER_MESSAGES_FILE):
        try:
            with open(USER_MESSAGES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON в {USER_MESSAGES_FILE}: {e}")
            backup_path = f"{USER_MESSAGES_FILE}.bak"
            if os.path.exists(backup_path):
                try:
                    shutil.copy2(backup_path, USER_MESSAGES_FILE)
                    with open(USER_MESSAGES_FILE, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except:
                    pass
            return {}
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке сообщений пользователя: {e}")
            return {}
    return {}


@retry_on_error(max_retries=3, delay=0.5)
def save_user_sent_messages(messages_data: Dict[str, List[int]]):
    """Сохранение ID сообщений пользователя с атомарной записью"""
    atomic_write(USER_MESSAGES_FILE, messages_data, backup=True)


def add_user_message_id(user_id: str, message_id: int):
    """Добавление ID сообщения пользователя"""
    messages_data = load_user_sent_messages()
    user_id_str = str(user_id)
    if user_id_str not in messages_data:
        messages_data[user_id_str] = []
    messages_data[user_id_str].append(message_id)
    # Храним только последние 50 сообщений для каждого пользователя
    messages_data[user_id_str] = messages_data[user_id_str][-50:]
    save_user_sent_messages(messages_data)


@retry_on_error(max_retries=3, delay=0.5)
def load_user_categories() -> Dict[str, Dict[str, str]]:
    """Загрузка категорий пользователей с retry механизмом"""
    if os.path.exists(CATEGORIES_FILE):
        try:
            with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON в {CATEGORIES_FILE}: {e}")
            return {}
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке категорий: {e}")
            return {}
    return {}


@retry_on_error(max_retries=3, delay=0.5)
def save_user_categories(categories_data: Dict[str, Dict[str, str]]):
    """Сохранение категорий пользователей с атомарной записью"""
    atomic_write(CATEGORIES_FILE, categories_data, backup=True)


def get_user_categories(user_id: str) -> Dict[str, str]:
    """Получение категорий пользователя"""
    categories_data = load_user_categories()
    user_id_str = str(user_id)
    
    # Если у пользователя нет категорий, создаем стандартную
    if user_id_str not in categories_data or not categories_data[user_id_str]:
        categories_data[user_id_str] = DEFAULT_CATEGORIES.copy()
        save_user_categories(categories_data)
    
    return categories_data.get(user_id_str, DEFAULT_CATEGORIES.copy())


def add_user_category(user_id: str, category_id: str, category_name: str):
    """Добавление категории пользователю"""
    categories_data = load_user_categories()
    user_id_str = str(user_id)
    
    if user_id_str not in categories_data:
        categories_data[user_id_str] = DEFAULT_CATEGORIES.copy()
    
    categories_data[user_id_str][category_id] = category_name
    save_user_categories(categories_data)


def delete_user_category(user_id: str, category_id: str) -> bool:
    """Удаление категории пользователя"""
    categories_data = load_user_categories()
    user_id_str = str(user_id)
    
    if user_id_str not in categories_data:
        return False
    
    if category_id in categories_data[user_id_str]:
        # Нельзя удалить последнюю категорию
        if len(categories_data[user_id_str]) <= 1:
            return False
        del categories_data[user_id_str][category_id]
        save_user_categories(categories_data)
        return True
    
    return False


def update_user_category(user_id: str, category_id: str, new_name: str):
    """Обновление названия категории"""
    categories_data = load_user_categories()
    user_id_str = str(user_id)
    
    if user_id_str not in categories_data:
        categories_data[user_id_str] = DEFAULT_CATEGORIES.copy()
    
    if category_id in categories_data[user_id_str]:
        categories_data[user_id_str][category_id] = new_name
        save_user_categories(categories_data)
        return True
    
    return False


def generate_category_id() -> str:
    """Генерация уникального ID для категории"""
    return f"cat_{int(time.time() * 1000)}"


# Функции для работы с настройками пользователя (город и часовой пояс)
@retry_on_error(max_retries=3, delay=0.5)
def load_user_settings() -> Dict[str, Dict[str, str]]:
    """Загрузка настроек пользователей с retry механизмом"""
    if os.path.exists(USER_SETTINGS_FILE):
        try:
            with open(USER_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON в {USER_SETTINGS_FILE}: {e}")
            backup_path = f"{USER_SETTINGS_FILE}.bak"
            if os.path.exists(backup_path):
                try:
                    shutil.copy2(backup_path, USER_SETTINGS_FILE)
                    with open(USER_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except:
                    pass
            return {}
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке настроек: {e}")
            return {}
    return {}


@retry_on_error(max_retries=3, delay=0.5)
def save_user_settings(settings_data: Dict[str, Dict[str, str]]):
    """Сохранение настроек пользователей с атомарной записью"""
    atomic_write(USER_SETTINGS_FILE, settings_data, backup=True)


def get_user_settings(user_id: str) -> Dict[str, str]:
    """Получение настроек пользователя"""
    settings_data = load_user_settings()
    user_id_str = str(user_id)
    return settings_data.get(user_id_str, {})


def set_user_city(user_id: str, city: str):
    """Установка города пользователя"""
    settings_data = load_user_settings()
    user_id_str = str(user_id)
    if user_id_str not in settings_data:
        settings_data[user_id_str] = {}
    settings_data[user_id_str]['city'] = city
    save_user_settings(settings_data)


def set_user_timezone(user_id: str, timezone: str):
    """Установка часового пояса пользователя"""
    settings_data = load_user_settings()
    user_id_str = str(user_id)
    if user_id_str not in settings_data:
        settings_data[user_id_str] = {}
    settings_data[user_id_str]['timezone'] = timezone
    save_user_settings(settings_data)


def get_user_timezone(user_id: str) -> Optional[str]:
    """Получение часового пояса пользователя"""
    settings = get_user_settings(user_id)
    return settings.get('timezone')


# Популярные часовые пояса для выбора
TIMEZONES = {
    'Europe/Moscow': 'Москва (UTC+3)',
    'Europe/Kiev': 'Киев (UTC+2)',
    'Europe/Minsk': 'Минск (UTC+3)',
    'Europe/Warsaw': 'Варшава (UTC+1)',
    'Europe/Berlin': 'Берлин (UTC+1)',
    'Europe/London': 'Лондон (UTC+0)',
    'Europe/Paris': 'Париж (UTC+1)',
    'Europe/Rome': 'Рим (UTC+1)',
    'Europe/Madrid': 'Мадрид (UTC+1)',
    'Europe/Athens': 'Афины (UTC+2)',
    'Europe/Istanbul': 'Стамбул (UTC+3)',
    'Asia/Dubai': 'Дубай (UTC+4)',
    'Asia/Yerevan': 'Ереван (UTC+4)',
    'Asia/Tbilisi': 'Тбилиси (UTC+4)',
    'Asia/Baku': 'Баку (UTC+4)',
    'Asia/Almaty': 'Алматы (UTC+6)',
    'Asia/Tashkent': 'Ташкент (UTC+5)',
    'Asia/Bishkek': 'Бишкек (UTC+6)',
    'Asia/Dushanbe': 'Душанбе (UTC+5)',
    'Asia/Ashgabat': 'Ашхабад (UTC+5)',
    'Asia/Krasnoyarsk': 'Красноярск (UTC+7)',
    'Asia/Irkutsk': 'Иркутск (UTC+8)',
    'Asia/Yakutsk': 'Якутск (UTC+9)',
    'Asia/Vladivostok': 'Владивосток (UTC+10)',
    'Asia/Magadan': 'Магадан (UTC+11)',
    'Asia/Kamchatka': 'Петропавловск-Камчатский (UTC+12)',
    'America/New_York': 'Нью-Йорк (UTC-5)',
    'America/Chicago': 'Чикаго (UTC-6)',
    'America/Denver': 'Денвер (UTC-7)',
    'America/Los_Angeles': 'Лос-Анджелес (UTC-8)',
    'America/Toronto': 'Торонто (UTC-5)',
    'America/Mexico_City': 'Мехико (UTC-6)',
    'America/Sao_Paulo': 'Сан-Паулу (UTC-3)',
    'America/Buenos_Aires': 'Буэнос-Айрес (UTC-3)',
    'Asia/Shanghai': 'Шанхай (UTC+8)',
    'Asia/Tokyo': 'Токио (UTC+9)',
    'Asia/Seoul': 'Сеул (UTC+9)',
    'Asia/Hong_Kong': 'Гонконг (UTC+8)',
    'Asia/Singapore': 'Сингапур (UTC+8)',
    'Asia/Bangkok': 'Бангкок (UTC+7)',
    'Asia/Jakarta': 'Джакарта (UTC+7)',
    'Asia/Kolkata': 'Мумбаи (UTC+5:30)',
    'Australia/Sydney': 'Сидней (UTC+10)',
    'Australia/Melbourne': 'Мельбурн (UTC+10)',
    'Pacific/Auckland': 'Окленд (UTC+12)',
}


def get_timezone_keyboard() -> InlineKeyboardMarkup:
    """Создание клавиатуры для выбора часового пояса"""
    keyboard = []
    # Группируем по 2 кнопки в ряд
    timezone_items = list(TIMEZONES.items())
    for i in range(0, len(timezone_items), 2):
        row = []
        for j in range(2):
            if i + j < len(timezone_items):
                tz_id, tz_name = timezone_items[i + j]
                row.append(InlineKeyboardButton(tz_name, callback_data=f'tz_{tz_id}'))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


def get_timezone_by_city(city_name: str) -> Optional[str]:
    """Определение часового пояса по названию города"""
    if not GEOCODING_AVAILABLE:
        logger.warning("⚠️  Библиотеки для определения часового пояса не установлены")
        return None
    
    if not city_name or not city_name.strip():
        return None
    
    try:
        # Используем geopy для получения координат города
        geolocator = Nominatim(user_agent="telegram_schedule_bot")
        location = geolocator.geocode(city_name.strip(), timeout=10, language='ru')
        
        if location and hasattr(location, 'latitude') and hasattr(location, 'longitude'):
            # Используем timezonefinder для определения часового пояса по координатам
            tf = TimezoneFinder()
            timezone_name = tf.timezone_at(lat=location.latitude, lng=location.longitude)
            
            if timezone_name:
                logger.info(f"✅ Определен часовой пояс {timezone_name} для города {city_name}")
                return timezone_name
        logger.warning(f"⚠️  Не удалось найти координаты для города {city_name}")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка при определении часового пояса для города {city_name}: {e}")
        return None


def is_likely_city(text: str) -> bool:
    """Проверка, похоже ли сообщение на название города"""
    text = text.strip()
    # Простая эвристика: если текст состоит из букв и не является командой или кнопкой
    if len(text) < 2 or len(text) > 50:
        return False
    
    # Исключаем команды и кнопки
    excluded = ['что завтра?', 'завтра', 'что сегодня?', 'сегодня', 'моё расписание', 
                '➕', '✏️', '🙈', 'skip', '/skip', 'отмена', 'cancel']
    if text.lower() in excluded:
        return False
    
    # Проверяем, что это не число и не дата
    if text.isdigit():
        return False
    
    # Проверяем, что это не время (формат HH:MM)
    if ':' in text and len(text.split(':')) == 2:
        try:
            parts = text.split(':')
            if len(parts[0]) <= 2 and len(parts[1]) <= 2:
                int(parts[0])
                int(parts[1])
                return False
        except:
            pass
    
    # Если текст состоит в основном из букв (кириллица или латиница), возможно это город
    if any(c.isalpha() for c in text):
        return True
    
    return False


async def delete_user_sent_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int, pinned_message_id: Optional[int] = None) -> int:
    """Попытка удаления сообщений пользователя (работает только в группах, если бот - админ) с улучшенной обработкой ошибок"""
    try:
        messages_data = load_user_sent_messages()
        user_id_str = str(user_id)
        deleted_count = 0
        
        if user_id_str in messages_data:
            message_ids = messages_data[user_id_str]
            # Пытаемся удалить сообщения пользователя
            # ВАЖНО: Это работает только в группах, если бот является администратором
            # В личных чатах это не сработает из-за ограничений Telegram Bot API
            remaining_messages = []
            for msg_id in message_ids:
                # Пропускаем закрепленное сообщение
                if pinned_message_id and msg_id == pinned_message_id:
                    remaining_messages.append(msg_id)
                    continue
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    deleted_count += 1
                    # Небольшая задержка между удалениями (50ms)
                    await asyncio.sleep(0.05)
                except RetryAfter as e:
                    logger.warning(f"⚠️  Rate limit при удалении сообщений пользователя. Ждем {e.retry_after}с")
                    await asyncio.sleep(e.retry_after)
                except (TimedOut, NetworkError) as e:
                    logger.warning(f"⚠️  Сетевая ошибка при удалении сообщения пользователя {msg_id}: {e}")
                    continue
                except Exception:
                    # Игнорируем ошибки (сообщение уже удалено, недоступно или нет прав)
                    pass
            
            # Сохраняем только закрепленное сообщение (если есть)
            messages_data[user_id_str] = remaining_messages
            save_user_sent_messages(messages_data)
        
        return deleted_count
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении сообщений пользователя {user_id}: {e}", exc_info=True)
        return 0


async def show_week_schedule(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int):
    """Показать расписание на неделю после операций"""
    try:
        # Удаляем прошедшие события перед показом расписания
        delete_past_events(user_id)
        events = get_user_events(user_id)
        if events:
            text = format_events_list(events, 'week', str(user_id))
        else:
            text = "Может устроить день дурака?"
        
        # Всегда показываем основную клавиатуру
        main_keyboard = get_main_keyboard()
        msg = await send_message_safe(
            context.bot,
            chat_id=chat_id, 
            text=text, 
            reply_markup=main_keyboard, 
            parse_mode='HTML'
        )
        add_message_id(user_id, msg.message_id)
    except Exception as e:
        logger.error(f"❌ Ошибка при показе расписания пользователю {user_id}: {e}", exc_info=True)


async def delete_user_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int) -> int:
    """Удаление всех сообщений бота и пользователя для пользователя с улучшенной обработкой ошибок"""
    try:
        messages_data = load_messages()
        user_id_str = str(user_id)
        deleted_count = 0
        
        # Удаляем сообщения бота
        if user_id_str in messages_data:
            message_ids = messages_data[user_id_str]
            # Удаляем сообщения с небольшой задержкой, чтобы избежать rate limiting
            for msg_id in message_ids:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    deleted_count += 1
                    # Небольшая задержка между удалениями (50ms)
                    await asyncio.sleep(0.05)
                except RetryAfter as e:
                    # Если rate limit, ждем и продолжаем
                    logger.warning(f"⚠️  Rate limit при удалении сообщений. Ждем {e.retry_after}с")
                    await asyncio.sleep(e.retry_after)
                except (TimedOut, NetworkError) as e:
                    logger.warning(f"⚠️  Сетевая ошибка при удалении сообщения {msg_id}: {e}")
                    continue
                except Exception as e:
                    # Игнорируем ошибки (сообщение уже удалено или недоступно)
                    pass
            
            # Очищаем список сообщений бота
            messages_data[user_id_str] = []
            save_messages(messages_data)
        
        # Пытаемся удалить сообщения пользователя
        # ВАЖНО: Это работает только в группах, если бот является администратором
        # В личных чатах это не сработает из-за ограничений Telegram Bot API
        user_deleted_count = await delete_user_sent_messages(context, user_id, chat_id)
        deleted_count += user_deleted_count
        
        return deleted_count
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении сообщений пользователя {user_id}: {e}", exc_info=True)
        return 0


def get_user_events(user_id: str) -> List[Dict]:
    """Получение событий пользователя"""
    data = load_data()
    return data.get(str(user_id), [])


def save_user_event(user_id: str, event: Dict):
    """Сохранение события пользователя с валидацией"""
    # Валидация события перед сохранением
    if not validate_event(event):
        logger.error(f"❌ Попытка сохранить невалидное событие: {event}")
        return False
    
    try:
        data = load_data()
        user_id_str = str(user_id)
        if user_id_str not in data:
            data[user_id_str] = []
        data[user_id_str].append(event)
        save_data(data)
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении события: {e}", exc_info=True)
        return False


def update_user_event(user_id: str, event_id: str, updated_event: Dict):
    """Обновление события пользователя с валидацией"""
    # Валидация события перед обновлением
    if not validate_event(updated_event):
        logger.error(f"❌ Попытка обновить невалидное событие: {updated_event}")
        return False
    
    try:
        data = load_data()
        user_id_str = str(user_id)
        if user_id_str in data:
            for i, event in enumerate(data[user_id_str]):
                if event.get('id') == event_id:
                    # Сохраняем источник из оригинального события, если он не указан в обновлении
                    if 'source' not in updated_event and 'source' in event:
                        updated_event['source'] = event['source']
                    # Если источника нет ни в оригинале, ни в обновлении, устанавливаем по умолчанию
                    if 'source' not in updated_event:
                        updated_event['source'] = 'schedule'
                    data[user_id_str][i] = updated_event
                    save_data(data)
                    return True
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении события: {e}", exc_info=True)
        return False


def delete_user_event(user_id: str, event_id: str):
    """Удаление события пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data:
        data[user_id_str] = [e for e in data[user_id_str] if e.get('id') != event_id]
        save_data(data)
        return True
    return False


def delete_all_user_events(user_id: str):
    """Удаление всех событий пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data:
        data[user_id_str] = []
        save_data(data)
        return True
    return False


def delete_past_events(user_id: str = None):
    """Удаление прошедших событий для пользователя или всех пользователей
    Учитывает часовой пояс каждого пользователя"""
    data = load_data()
    deleted_count = 0
    
    user_ids_to_check = [str(user_id)] if user_id else list(data.keys())
    
    for user_id_str in user_ids_to_check:
        if user_id_str not in data:
            continue
        
        # Получаем текущее время в часовом поясе пользователя
        user_timezone = get_user_timezone(user_id_str)
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
                now = datetime.now(tz)
            except Exception as e:
                logger.warning(f"⚠️  Ошибка при получении часового пояса {user_timezone} для пользователя {user_id_str}: {e}")
                now = datetime.now()
        else:
            now = datetime.now()
        
        # Получаем сегодняшнюю дату (как naive для сравнения)
        if now.tzinfo is not None:
            today_date = now.date()
        else:
            today_date = now.replace(hour=0, minute=0, second=0, microsecond=0).date()
        
        events = data[user_id_str]
        remaining_events = []
        
        for event in events:
            try:
                # Проверяем дату события (события хранятся как naive даты)
                event_date = datetime.strptime(event['date'], '%Y-%m-%d')
                
                # Удаляем событие только если дата события уже прошла (меньше сегодняшней даты) - вчера и раньше
                # События сегодняшнего дня не удаляем, даже если их время уже прошло
                if event_date.date() < today_date:
                    # Событие было в прошлом дне (вчера и раньше) - удаляем
                    deleted_count += 1
                    logger.debug(f"🗑️  Удаление прошедшего события: {event.get('title', 'N/A')} на {event['date']}")
                    continue
                
                remaining_events.append(event)
            except (ValueError, KeyError) as e:
                # Если не удалось распарсить дату/время, оставляем событие
                logger.warning(f"⚠️  Не удалось обработать событие {event.get('id', 'unknown')}: {e}")
                remaining_events.append(event)
        
        data[user_id_str] = remaining_events
    
    if deleted_count > 0:
        save_data(data)
        logger.info(f"🗑️  Удалено прошедших событий: {deleted_count}")
    
    return deleted_count


def get_events_for_reminder() -> List[tuple]:
    """Получение событий, для которых нужно отправить напоминание
    Возвращает список кортежей (user_id, event, reminder_minutes)
    Учитывает часовой пояс каждого пользователя"""
    data = load_data()
    events_to_remind = []
    
    for user_id_str, events in data.items():
        # Получаем часовой пояс пользователя
        user_timezone = get_user_timezone(user_id_str)
        
        # Определяем текущее время в часовом поясе пользователя
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
                now = datetime.now(tz)
            except Exception as e:
                logger.warning(f"⚠️  Ошибка при получении часового пояса {user_timezone} для пользователя {user_id_str}: {e}")
                # Используем UTC как fallback
                now = datetime.now(ZoneInfo('UTC'))
        else:
            # Если часовой пояс не установлен, используем UTC
            now = datetime.now(ZoneInfo('UTC'))
        
        for event in events:
            # Поддержка старого формата (reminder_minutes) и нового (reminders)
            reminders = event.get('reminders', [])
            if not reminders:
                # Проверяем старый формат для обратной совместимости
                old_reminder = event.get('reminder_minutes')
                if old_reminder is not None:
                    reminders = [old_reminder]
                else:
                    continue
            
            # Пропускаем события без даты или времени
            if not event.get('date') or not event.get('time'):
                continue
            
            try:
                # Парсим дату и время события (время интерпретируется в часовом поясе пользователя)
                event_date = datetime.strptime(event['date'], '%Y-%m-%d')
                event_time = datetime.strptime(event['time'], '%H:%M').time()
                
                # Создаем datetime в часовом поясе пользователя
                if user_timezone:
                    try:
                        tz = ZoneInfo(user_timezone)
                        # Создаем naive datetime и затем делаем его aware
                        event_datetime_naive = datetime.combine(event_date.date(), event_time)
                        event_datetime = event_datetime_naive.replace(tzinfo=tz)
                    except Exception as e:
                        logger.warning(f"⚠️  Ошибка при создании datetime для часового пояса {user_timezone}: {e}")
                        event_datetime = datetime.combine(event_date.date(), event_time)
                else:
                    # Если часовой пояс не установлен, используем naive datetime
                    event_datetime = datetime.combine(event_date.date(), event_time)
                
                # Проверяем каждое напоминание
                reminder_sent = event.get('reminder_sent', [])
                if not isinstance(reminder_sent, list):
                    # Старый формат - преобразуем в список
                    reminder_sent = []
                
                for reminder_minutes in reminders:
                    # Вычисляем время напоминания
                    reminder_datetime = event_datetime - timedelta(minutes=reminder_minutes)
                    
                    # Убеждаемся, что оба datetime в одном формате (aware или naive)
                    # Всегда работаем с aware datetime для точности
                    if reminder_datetime.tzinfo is None:
                        # Если reminder_datetime naive, делаем его aware в часовом поясе пользователя
                        if user_timezone:
                            try:
                                tz = ZoneInfo(user_timezone)
                                reminder_datetime = reminder_datetime.replace(tzinfo=tz)
                            except Exception as e:
                                logger.warning(f"⚠️  Ошибка при установке часового пояса для напоминания: {e}")
                                # Используем UTC как fallback
                                reminder_datetime = reminder_datetime.replace(tzinfo=ZoneInfo('UTC'))
                        else:
                            # Если часовой пояс не установлен, используем UTC
                            reminder_datetime = reminder_datetime.replace(tzinfo=ZoneInfo('UTC'))
                    
                    # Убеждаемся, что now тоже aware (должно быть уже установлено выше, но проверяем на всякий случай)
                    if now.tzinfo is None:
                        if user_timezone:
                            try:
                                tz = ZoneInfo(user_timezone)
                                now = datetime.now(tz)
                            except Exception as e:
                                logger.warning(f"⚠️  Ошибка при установке часового пояса для now: {e}")
                                now = datetime.now(ZoneInfo('UTC'))
                        else:
                            now = datetime.now(ZoneInfo('UTC'))
                    
                    # Теперь оба datetime должны быть aware - вычисляем разницу
                    time_diff = (reminder_datetime - now).total_seconds()
                    
                    # Отправляем напоминание, если время пришло (в пределах 1 минуты до и после)
                    # Расширяем окно до 2 минут для надежности
                    if -60 <= time_diff < 60:
                        # Проверяем, не было ли уже отправлено это напоминание
                        if reminder_minutes not in reminder_sent:
                            logger.debug(f"🔔 Найдено событие для напоминания: пользователь {user_id_str}, событие {event.get('title', 'N/A')}, напоминание за {reminder_minutes} мин, время события {event_datetime}, время напоминания {reminder_datetime}, сейчас {now}, разница {time_diff}с")
                            events_to_remind.append((user_id_str, event, reminder_minutes))
            except (ValueError, KeyError) as e:
                logger.warning(f"⚠️  Ошибка при обработке события для напоминания: {e}")
                continue
    
    return events_to_remind


@retry_on_error(max_retries=3, delay=2.0)
async def send_message_safe(bot, chat_id: int, text: str, **kwargs):
    """Безопасная отправка сообщения с retry механизмом"""
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except RetryAfter as e:
        logger.warning(f"⚠️  Rate limit при отправке сообщения. Ждем {e.retry_after}с")
        await asyncio.sleep(e.retry_after)
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except (TimedOut, NetworkError) as e:
        logger.warning(f"⚠️  Сетевая ошибка при отправке сообщения: {e}")
        raise
    except TelegramError as e:
        logger.error(f"❌ Ошибка Telegram API при отправке сообщения: {e}")
        raise


async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача для отправки напоминаний с улучшенной обработкой ошибок"""
    try:
        events_to_remind = get_events_for_reminder()
        
        if events_to_remind:
            logger.info(f"🔔 Найдено {len(events_to_remind)} напоминаний для отправки")
        
        for user_id_str, event, reminder_minutes in events_to_remind:
            try:
                user_id = int(user_id_str)
                event_date = datetime.strptime(event['date'], '%Y-%m-%d')
                date_str = event_date.strftime('%d.%m.%Y')
                weekday = get_weekday(event_date)
                
                # Формируем текст о времени до события
                if reminder_minutes < 60:
                    time_text = f"через {reminder_minutes} мин"
                elif reminder_minutes < 1440:
                    hours = reminder_minutes // 60
                    time_text = f"через {hours} ч"
                else:
                    days = reminder_minutes // 1440
                    time_text = f"через {days} дн"
                
                reminder_text = f"🔔 <b>Напоминание</b> ({time_text})\n\n"
                reminder_text += f"📅 {date_str} ({weekday})\n"
                reminder_text += f"{event['time']}\n"
                reminder_text += f"📝 {event['title']}\n"
                
                if event.get('description'):
                    reminder_text += f"\n{event['description']}\n"
                
                # Отправляем напоминание с retry механизмом
                logger.info(f"🔔 Отправка напоминания пользователю {user_id} для события '{event.get('title', 'N/A')}' за {reminder_minutes} минут")
                await send_message_safe(
                    context.bot,
                    chat_id=user_id,
                    text=reminder_text,
                    parse_mode='HTML'
                )
                logger.info(f"✅ Напоминание успешно отправлено пользователю {user_id}")
                
                # Помечаем, что это напоминание отправлено
                reminder_sent = event.get('reminder_sent', [])
                if not isinstance(reminder_sent, list):
                    reminder_sent = []
                if reminder_minutes not in reminder_sent:
                    reminder_sent.append(reminder_minutes)
                event['reminder_sent'] = reminder_sent
                update_user_event(user_id_str, event['id'], event)
                logger.debug(f"✅ Напоминание помечено как отправленное для события {event.get('id', 'N/A')}")
                
            except (RetryAfter, TimedOut, NetworkError) as e:
                logger.warning(f"⚠️  Временная ошибка при отправке напоминания пользователю {user_id_str}: {e}")
                continue
            except TelegramError as e:
                logger.error(f"❌ Ошибка Telegram API при отправке напоминания пользователю {user_id_str}: {e}")
                continue
            except Exception as e:
                logger.error(f"❌ Неожиданная ошибка при отправке напоминания пользователю {user_id_str}: {e}", exc_info=True)
                continue
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в функции send_reminders: {e}", exc_info=True)


def format_event(event: Dict, user_id: str = None) -> str:
    """Форматирование события для отображения"""
    date_obj = datetime.strptime(event['date'], '%Y-%m-%d')
    date_str = date_obj.strftime('%d.%m.%Y')
    weekday = get_weekday(date_obj)
    
    # Получаем категории пользователя
    if user_id:
        user_categories = get_user_categories(user_id)
        category_name = user_categories.get(event.get('category', 'other'), 'остальное')
    else:
        category_name = DEFAULT_CATEGORIES.get(event.get('category', 'other'), 'остальное')
    
    text = f"<b>{event['title']}</b>\n"
    text += f"дата: {date_str} ({weekday})\n"
    text += f"время: {event['time']}\n"
    text += f"название: {event['title']}\n"
    text += f"категория ({category_name})\n"
    
    if event.get('description'):
        text += f"описание: {event['description']}\n"
    
    return text


def format_events_list(events: List[Dict], filter_type: str = 'all', user_id: str = None) -> str:
    """Форматирование списка событий с группировкой по датам
    Учитывает часовой пояс пользователя"""
    if not events:
        return "Может устроить день дурака?"
    
    # Получаем текущее время в часовом поясе пользователя
    if user_id:
        user_timezone = get_user_timezone(user_id)
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
                now = datetime.now(tz)
            except:
                now = datetime.now()
        else:
            now = datetime.now()
    else:
        now = datetime.now()
    
    # Получаем сегодняшнюю дату в часовом поясе пользователя (как naive для сравнения)
    if now.tzinfo is not None:
        today_naive = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        today_date = now.date()
    else:
        today_naive = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_date = today_naive.date()
    
    # Фильтрация событий по датам (сравниваем naive даты)
    if filter_type == 'today':
        events = [e for e in events if datetime.strptime(e['date'], '%Y-%m-%d').date() == today_date]
    elif filter_type == 'tomorrow':
        tomorrow_date = today_date + timedelta(days=1)
        events = [e for e in events if datetime.strptime(e['date'], '%Y-%m-%d').date() == tomorrow_date]
    elif filter_type == 'week':
        week_end_date = today_date + timedelta(days=7)
        events = [e for e in events if today_date <= datetime.strptime(e['date'], '%Y-%m-%d').date() <= week_end_date]
    
    if not events:
        if filter_type == 'today':
            return "Может устроить день дурака?"
        elif filter_type == 'tomorrow':
            return "Может устроить день дурака?"
        return "Может устроить день дурака?"
    
    # Получаем категории пользователя
    if user_id:
        user_categories = get_user_categories(user_id)
    else:
        user_categories = DEFAULT_CATEGORIES.copy()
    
    # Сортировка по дате и времени (хронологический порядок)
    events.sort(key=lambda x: (x['date'], x['time']))
    
    # Группировка событий по датам
    events_by_date = {}
    for event in events:
        date_key = event['date']
        if date_key not in events_by_date:
            events_by_date[date_key] = []
        events_by_date[date_key].append(event)
    
    text = ""
    
    # Отображаем события по датам (в хронологическом порядке)
    for date_key in sorted(events_by_date.keys()):
        date_obj = datetime.strptime(date_key, '%Y-%m-%d')
        
        # Форматируем дату
        date_str = format_date_natural(date_obj)
        weekday = get_weekday_short(date_obj).lower()  # Сокращенный день недели с маленькой буквы
        
        # Выводим дату жирным, день недели обычным
        text += f"<b>{date_str}</b>, {weekday}\n"
        
        # Добавляем пустую строку после даты для фильтров "сегодня" и "завтра"
        if filter_type == 'today' or filter_type == 'tomorrow':
            text += "\n"
        
        # Выводим события этого дня (уже отсортированные по времени)
        day_events = events_by_date[date_key]
        # Дополнительная сортировка по времени для гарантии хронологического порядка
        day_events.sort(key=lambda x: x['time'])
        for i, event in enumerate(day_events):
            # Время
            time_str = event['time']
            
            # Название
            title_str = event['title']
            
            # Категория
            category_name = user_categories.get(event.get('category', 'other'), 'остальное')
            
            # Формируем строку события в новом формате
            # Время подчеркнутое и курсивом
            text += f"<u><i>{time_str}</i></u>\n"
            # Название
            text += f"{title_str}\n"
            # Категория с |
            text += f"|{category_name}\n"
            # Описание (если есть)
            if event.get('description'):
                text += f"{event['description']}\n"
            
            # Добавляем пустую строку между событиями (но не после последнего события дня)
            if i < len(day_events) - 1:
                text += "\n"
        
        text += "\n"  # Пустая строка между датами
    
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    
    # Проверяем, установлен ли часовой пояс у пользователя
    user_timezone = get_user_timezone(user_id)
    
    # Если часовой пояс не установлен, спрашиваем город
    if not user_timezone:
        if not GEOCODING_AVAILABLE:
            # Если библиотеки недоступны, сообщаем об этом и предлагаем установить часовой пояс вручную через UTC offset
            welcome_text = (
                '⚽️\n\n'
                'Привет!\n'
                'Это arkTime-бот, написанный Славой Шутовым\n\n'
                '⚠️ Автоматическое определение часового пояса временно недоступно.\n\n'
                'Бот попытался установить необходимые библиотеки при запуске.\n'
                'Пожалуйста, попробуйте указать ваш город - возможно, определение всё же сработает:\n'
                'Например: Москва, Санкт-Петербург, Нью-Йорк'
            )
            msg = await update.message.reply_text(
                welcome_text,
                parse_mode='HTML'
            )
            add_message_id(user_id, msg.message_id)
            return WAITING_CITY
        else:
            welcome_text = (
                '⚽️\n\n'
                'Привет!\n'
                'Это arkTime-бот, написанный Славой Шутовым\n\n'
                'Для начала работы укажите ваш город:\n'
                'Например: Москва, Санкт-Петербург, Нью-Йорк'
            )
            msg = await update.message.reply_text(
                welcome_text,
                parse_mode='HTML'
            )
            add_message_id(user_id, msg.message_id)
            return WAITING_CITY
    
    # Если часовой пояс уже установлен, показываем обычное приветствие
    user_categories = get_user_categories(user_id)
    
    welcome_text = '⚽️'
    
    # Если у пользователя нет категорий или только стандартная "остальное", предлагаем создать
    if not user_categories or (len(user_categories) == 1 and 'other' in user_categories):
        welcome_text += '\n\nПривет!\nЭто arkTime-бот, написанный Славой Шутовым\n\nНачни с добавления категорий. Они помогут для более удобной организации событий.\n\nПример категорий:\nУчеба\nРабота\nСпорт\nи тд\n\nНажмите "✏️" → "управление категориями" для настройки.'
    
    keyboard = get_main_keyboard()
    # Убеждаемся, что клавиатура отправляется
    msg = await update.message.reply_text(
        welcome_text, 
        parse_mode='HTML',
        reply_markup=keyboard
    )
    add_message_id(user_id, msg.message_id)
    return ConversationHandler.END


async def city_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода города с автоматическим определением часового пояса"""
    user_id = update.effective_user.id
    
    # Проверяем, что сообщение содержит текст
    if not update.message:
        return WAITING_CITY
    
    if not update.message.text:
        error_msg = await update.message.reply_text(
            "❌ Пожалуйста, укажите название города текстом.",
            parse_mode='HTML'
        )
        add_message_id(user_id, error_msg.message_id)
        return WAITING_CITY
    
    city_name = update.message.text.strip()
    
    # Проверяем, что город не пустой
    if not city_name:
        error_msg = await update.message.reply_text(
            "❌ Пожалуйста, укажите название города.",
            parse_mode='HTML'
        )
        add_message_id(user_id, error_msg.message_id)
        return WAITING_CITY
    
    # Сохраняем ID сообщения пользователя
    add_user_message_id(user_id, update.message.message_id)
    
    # Показываем, что обрабатываем запрос
    processing_msg = await update.message.reply_text(
        f"🔍 Определяю часовой пояс для города <b>{city_name}</b>...",
        parse_mode='HTML'
    )
    add_message_id(user_id, processing_msg.message_id)
    
    # Определяем часовой пояс по городу
    timezone_name = get_timezone_by_city(city_name)
    
    if timezone_name:
        # Сохраняем город и часовой пояс
        set_user_city(user_id, city_name)
        set_user_timezone(user_id, timezone_name)
        
        # Получаем информацию о часовом поясе для отображения
        try:
            tz = ZoneInfo(timezone_name)
            now = datetime.now(tz)
            offset = now.strftime('%z')
            offset_formatted = f"{offset[:3]}:{offset[3:]}" if len(offset) >= 5 else offset
        except:
            offset_formatted = ""
        
        # Удаляем сообщение об обработке
        try:
            await processing_msg.delete()
        except:
            pass
        
        # Показываем успешное сообщение
        success_text = (
            f"✅ Часовой пояс определен!\n\n"
            f"Город: <b>{city_name}</b>\n"
            f"Часовой пояс: <b>{timezone_name}</b>"
        )
        if offset_formatted:
            success_text += f"\nСмещение: UTC{offset_formatted}"
        
        msg = await update.message.reply_text(
            success_text,
            parse_mode='HTML'
        )
        add_message_id(user_id, msg.message_id)
        
        # Проверяем категории пользователя
        user_categories = get_user_categories(user_id)
        
        welcome_text = ''
        # Если у пользователя нет категорий или только стандартная "остальное", предлагаем создать
        if not user_categories or (len(user_categories) == 1 and 'other' in user_categories):
            welcome_text = (
                '\n\nНачни с добавления категорий. Они помогут для более удобной организации событий.\n\n'
                'Пример категорий:\n'
                'Учеба\n'
                'Работа\n'
                'Спорт\n'
                'и тд\n\n'
                'Нажмите "✏️" → "управление категориями" для настройки.'
            )
        
        # Показываем основную клавиатуру
        keyboard = get_main_keyboard()
        msg = await update.message.reply_text(
            welcome_text if welcome_text else "Выберите действие:",
            parse_mode='HTML',
            reply_markup=keyboard
        )
        add_message_id(user_id, msg.message_id)
        
        return ConversationHandler.END
    else:
        # Если не удалось определить часовой пояс
        try:
            await processing_msg.delete()
        except:
            pass
        
        if not GEOCODING_AVAILABLE:
            # Если библиотеки недоступны
            error_msg = await update.message.reply_text(
                f"⚠️ Автоматическое определение часового пояса временно недоступно.\n\n"
                "Библиотеки geopy и timezonefinder не установлены в системе.\n\n"
                "Бот попытался установить их автоматически при запуске.\n"
                "Если вы видите это сообщение, пожалуйста:\n"
                "1. Обратитесь к администратору\n"
                "2. Или перезапустите бота - библиотеки могут установиться автоматически\n\n"
                "Вы можете попробовать указать город ещё раз - возможно, определение всё же сработает.",
                parse_mode='HTML'
            )
            add_message_id(user_id, error_msg.message_id)
            return WAITING_CITY
        else:
            # Если библиотеки доступны, но определение не удалось
            error_msg = await update.message.reply_text(
                f"❌ Не удалось определить часовой пояс для города <b>{city_name}</b>.\n\n"
                "Пожалуйста, попробуйте указать город более точно:\n"
                "• Москва\n"
                "• Санкт-Петербург\n"
                "• New York\n"
                "• London\n\n"
                "Или попробуйте указать другой город.",
                parse_mode='HTML'
            )
            add_message_id(user_id, error_msg.message_id)
            return WAITING_CITY


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /timezone для изменения часового пояса"""
    user_id = update.effective_user.id
    
    if not GEOCODING_AVAILABLE:
        welcome_text = (
            '⚠️ Автоматическое определение часового пояса временно недоступно.\n\n'
            'Бот попытался установить необходимые библиотеки при запуске.\n'
            'Пожалуйста, попробуйте указать ваш город - возможно, определение всё же сработает:\n'
            'Например: Москва, Санкт-Петербург, Нью-Йорк'
        )
    else:
        welcome_text = (
            'Для изменения часового пояса укажите ваш город:\n'
            'Например: Москва, Санкт-Петербург, Нью-Йорк'
        )
    
    msg = await update.message.reply_text(
        welcome_text,
        parse_mode='HTML'
    )
    add_message_id(user_id, msg.message_id)
    
    # Устанавливаем состояние для ввода города
    return WAITING_CITY


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📖 <b>Справка по использованию бота:</b>

<b>Основные кнопки:</b>
➕ - Добавить новое событие
моё расписание - Показать все события
что сегодня? - События на сегодня
что завтра? - События на завтра
✏️ - Редактировать события и управлять категориями
🙈 - Очистить чат

<b>Добавление события:</b>
1. Нажмите ➕
2. Введите название события
3. Введите дату:
   • завтра, послезавтра
   • понедельник, вторник, среда и т.д.
   • 17 января, 19 01, 25.12.2024
4. Введите время:
   • Только часы: 12, 13, 9
   • С минутами: 12:30, 14:45
5. Введите описание (или /skip)
6. Выберите категорию (или создайте свою)
7. Выберите повторяемость
8. Выберите напоминание

<b>Категории:</b>
Каждый пользователь создает свои категории.
Нажмите ✏️ → "управление категориями" для настройки.

<b>Редактирование:</b>
Нажмите ✏️ и выберите событие для редактирования.

<b>Удаление:</b>
В меню редактирования можно удалить отдельное событие или всё расписание.

<b>Совет:</b> События автоматически сортируются по дате и времени!
    """
    keyboard = get_main_keyboard()
    msg = await update.message.reply_text(
        help_text, 
        parse_mode='HTML',
        reply_markup=keyboard
    )
    add_message_id(update.effective_user.id, msg.message_id)


async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса добавления события"""
    keyboard = get_main_keyboard()
    msg = await update.message.reply_text(
        "Введи <b>название события</b>",
        parse_mode='HTML',
        reply_markup=keyboard
    )
    add_message_id(update.effective_user.id, msg.message_id)
    return WAITING_TITLE


async def add_event_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение названия события"""
    # Сохраняем ID сообщения пользователя
    add_user_message_id(update.effective_user.id, update.message.message_id)
    
    context.user_data['new_event'] = {'title': update.message.text}
    msg = await update.message.reply_text(
        "Введите дату события:\n\n"
        "Примеры:\n"
        "• сегодня\n"
        "• завтра\n"
        "• послезавтра\n"
        "• понедельник\n"
        "• 17 января\n"
        "• 19 01\n"
        "• 25.12.2024",
        parse_mode='HTML'
    )
    add_message_id(update.effective_user.id, msg.message_id)
    return WAITING_DATE


async def add_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение даты события с учетом часового пояса пользователя"""
    # Сохраняем ID сообщения пользователя
    add_user_message_id(update.effective_user.id, update.message.message_id)
    
    try:
        user_id = update.effective_user.id
        # Получаем часовой пояс пользователя
        user_timezone = get_user_timezone(str(user_id))
        
        # Получаем текущее время в часовом поясе пользователя
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
                now = datetime.now(tz)
                today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            except Exception as e:
                logger.warning(f"⚠️  Ошибка при получении времени в часовом поясе {user_timezone}: {e}")
                today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Сохраняем naive версию today для сравнения
        today_naive = today.replace(tzinfo=None) if today.tzinfo else today
        
        date_str = update.message.text.strip().lower()
        date_obj = None
        
        logger.info(f"📅 Обработка даты: '{date_str}', часовой пояс пользователя: {user_timezone}")
        logger.info(f"📅 RELATIVE_DATES содержит: {list(RELATIVE_DATES.keys())}")
        logger.info(f"📅 Проверка: '{date_str}' in RELATIVE_DATES = {date_str in RELATIVE_DATES}")
        
        # Проверяем относительные даты (сегодня, завтра, послезавтра)
        if date_str in RELATIVE_DATES:
            days_offset = RELATIVE_DATES[date_str]
            logger.info(f"✅ Найдена относительная дата: '{date_str}', смещение: {days_offset} дней")
            date_obj = today + timedelta(days=days_offset)
            # Убираем timezone info для дальнейшей обработки
            if date_obj.tzinfo is not None:
                date_obj = date_obj.replace(tzinfo=None)
            logger.info(f"✅ Результат: {date_obj.date()}")
        else:
            # Сначала пробуем парсить естественные формулировки с учетом часового пояса
            date_obj = parse_natural_date(date_str, user_timezone)
            
            # Если не получилось, пробуем формат DD MM (без года)
            if date_obj is None:
                parts = date_str.split()
                if len(parts) == 2:
                    try:
                        day = int(parts[0])
                        month = int(parts[1])
                        if 1 <= month <= 12 and 1 <= day <= 31:
                            current_year = today_naive.year
                            date_obj = datetime(current_year, month, day)
                            if date_obj < today_naive:
                                date_obj = datetime(current_year + 1, month, day)
                    except ValueError:
                        pass
            
            # Если все еще не получилось, пробуем стандартные форматы
            if date_obj is None:
                # Пробуем различные форматы дат
                date_formats = [
                    '%d.%m.%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', 
                    '%d.%m.%y', '%d/%m/%y', '%d.%m', '%d/%m',
                    '%d-%m', '%Y.%m.%d', '%Y/%m/%d'
                ]
                for fmt in date_formats:
                    try:
                        date_obj = datetime.strptime(date_str, fmt)
                        # Если формат без года, добавляем текущий год
                        if fmt in ['%d.%m', '%d/%m', '%d-%m']:
                            date_obj = date_obj.replace(year=today_naive.year)
                            if date_obj < today_naive:
                                date_obj = date_obj.replace(year=today_naive.year + 1)
                        break
                    except ValueError:
                        continue
        
        if date_obj is None:
            logger.warning(f"⚠️  Не удалось распарсить дату: {date_str}")
            raise ValueError("Неверный формат даты")
        
        # Убираем timezone info из date_obj для сравнения и сохранения, если он есть
        if date_obj.tzinfo is not None:
            date_obj = date_obj.replace(tzinfo=None)
        
        # Убираем timezone info из today для сравнения, если он есть
        if today.tzinfo is not None:
            today_naive = today.replace(tzinfo=None)
        else:
            today_naive = today
        
        logger.debug(f"📅 Распарсена дата: {date_str} -> {date_obj.date()}, сегодня: {today_naive.date()}")
        
        # Проверяем, что дата не в прошлом (кроме сегодня)
        if date_obj.date() < today_naive.date():
            msg = await update.message.reply_text(
                "Нельзя выбрать прошедшую дату. Попробуйте снова.\n\n"
                "Примеры:\n"
                "• Завтра\n"
                "• Послезавтра\n"
                "• Понедельник\n"
                "• Вторник\n"
                "• 17 января\n"
                "• 19 01\n"
                "• 25.12.2024",
                parse_mode='HTML'
            )
            add_message_id(update.effective_user.id, msg.message_id)
            return WAITING_DATE
        
        context.user_data['new_event']['date'] = date_obj.strftime('%Y-%m-%d')
        msg = await update.message.reply_text(
            "во сколько?\n\nМожно ввести время:\n"
            "• Только часы: 12, 13, 9\n"
            "• С минутами: 12:30, 14:45\n"
            "• Или: через час, через 2 часа, через полтора часа",
            parse_mode='HTML'
        )
        add_message_id(update.effective_user.id, msg.message_id)
        return WAITING_TIME
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке даты '{update.message.text}': {e}", exc_info=True)
        msg = await update.message.reply_text(
            "Неверный формат даты. Попробуйте снова.\n\n"
            "<b>Примеры форматов:</b>\n"
            "• сегодня\n"
            "• завтра\n"
            "• послезавтра\n"
            "• понедельник\n"
            "• вторник\n"
            "• среда\n"
            "• четверг\n"
            "• пятница\n"
            "• суббота\n"
            "• воскресенье\n"
            "• через неделю\n"
            "• 17 января\n"
            "• 19 01\n"
            "• 25.12.2024\n"
            "• 25/12/2024",
            parse_mode='HTML'
        )
        add_message_id(update.effective_user.id, msg.message_id)
        return WAITING_DATE


async def add_event_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение времени события"""
    # Сохраняем ID сообщения пользователя
    add_user_message_id(update.effective_user.id, update.message.message_id)
    
    try:
        time_str = update.message.text.strip().lower()
        
        # Проверяем относительные временные выражения
        now = datetime.now()
        hours_to_add = None
        
        if time_str == "через час":
            hours_to_add = 1
        elif time_str == "через полтора часа":
            hours_to_add = 1.5
        elif time_str.startswith("через ") and time_str.endswith(" часа"):
            try:
                hours_str = time_str.replace("через ", "").replace(" часа", "").strip()
                hours_to_add = float(hours_str)
            except:
                pass
        
        if hours_to_add is not None:
            # Вычисляем время через указанное количество часов
            future_time = now + timedelta(hours=hours_to_add)
            time_str = future_time.strftime('%H:%M')
            context.user_data['new_event']['time'] = time_str
            
            # Переходим сразу к шагу описания
            msg = await update.message.reply_text(
                f"Время установлено: {time_str}\n\nВведите описание или /skip",
                parse_mode='HTML'
            )
            add_message_id(update.effective_user.id, msg.message_id)
            return WAITING_DESCRIPTION
        
        # Проверяем, является ли ввод только числом (часы без минут)
        if time_str.isdigit():
            hours = int(time_str)
            if 0 <= hours <= 23:
                # Автоматически добавляем ":00" для минут
                time_str = f"{hours:02d}:00"
                context.user_data['new_event']['time'] = time_str
                
                # Переходим сразу к шагу описания
                msg = await update.message.reply_text(
                    f"Время установлено: {time_str}\n\nВведите описание или /skip",
                    parse_mode='HTML'
                )
                add_message_id(update.effective_user.id, msg.message_id)
                return WAITING_DESCRIPTION
            else:
                raise ValueError("Часы должны быть от 0 до 23")
        
        # Поддержка формата "чч мм" (с пробелом)
        if ' ' in time_str and ':' not in time_str:
            time_str = time_str.replace(' ', ':')
        
        # Проверяем оба формата: ЧЧ:ММ и ЧЧ ММ
        datetime.strptime(time_str, '%H:%M')
        # Сохраняем в стандартном формате ЧЧ:ММ
        context.user_data['new_event']['time'] = time_str
        
        # Переходим сразу к шагу описания
        msg = await update.message.reply_text(
            "Введите описание или /skip",
            parse_mode='HTML'
        )
        add_message_id(update.effective_user.id, msg.message_id)
        return WAITING_DESCRIPTION
    except:
        msg = await update.message.reply_text(
            "Неверный формат времени. Попробуйте снова:\n"
            "• Только часы: 12, 13, 9\n"
            "• С минутами: 12:30, 14:45\n"
            "• Или: через час, через 2 часа, через полтора часа"
        )
        add_message_id(update.effective_user.id, msg.message_id)
        return WAITING_TIME


async def add_event_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение описания события"""
    # Сохраняем ID сообщения пользователя (если это не команда /skip)
    if update.message.text.lower() != '/skip':
        add_user_message_id(update.effective_user.id, update.message.message_id)
        context.user_data['new_event']['description'] = update.message.text
    else:
        context.user_data['new_event']['description'] = ''
    
    # Получаем категории пользователя
    user_id = update.effective_user.id
    user_categories = get_user_categories(user_id)
    
    # Проверяем, есть ли категории у пользователя
    if not user_categories or len(user_categories) == 0:
        # Если категорий нет, предлагаем создать их
        keyboard = [
            [InlineKeyboardButton("Создать категории", callback_data="manage_categories")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text(
            "У вас пока нет категорий. Создайте их, чтобы продолжить добавление события.",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        add_message_id(user_id, msg.message_id)
        return WAITING_CATEGORY
    
    # Создаём клавиатуру с категориями пользователя
    keyboard = []
    for key, value in user_categories.items():
        keyboard.append([InlineKeyboardButton(value, callback_data=f"category_{key}")])
    
    # Добавляем кнопку для управления категориями
    keyboard.append([InlineKeyboardButton("управление категориями", callback_data="manage_categories")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = await update.message.reply_text(
        "Выберите <b>категорию</b> события:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    add_message_id(user_id, msg.message_id)
    return WAITING_CATEGORY


async def add_event_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение категории события"""
    query = update.callback_query
    await query.answer()
    
    category = query.data.replace('category_', '')
    context.user_data['new_event']['category'] = category
    
    # Создаём клавиатуру для выбора типа повторения
    keyboard = [
        [InlineKeyboardButton("Одноразовое", callback_data="repeat_once")],
        [InlineKeyboardButton("Ежедневное", callback_data="repeat_daily")],
        [InlineKeyboardButton("Еженедельное", callback_data="repeat_weekly")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = await query.edit_message_text(
        "Выберите тип повторения:",
        reply_markup=reply_markup
    )
    add_message_id(query.from_user.id, msg.message_id)
    return WAITING_REPEAT


async def add_event_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение типа повторения события"""
    query = update.callback_query
    await query.answer()
    
    repeat_type = query.data.replace('repeat_', '')
    context.user_data['new_event']['repeat_type'] = repeat_type
    
    # Переходим к выбору напоминания (от самого короткого к самому долгому)
    keyboard = [
        [InlineKeyboardButton("Без напоминания", callback_data="reminder_none")],
        [InlineKeyboardButton("За 5 минут", callback_data="reminder_5")],
        [InlineKeyboardButton("За 15 минут", callback_data="reminder_15")],
        [InlineKeyboardButton("За 30 минут", callback_data="reminder_30")],
        [InlineKeyboardButton("За 1 час", callback_data="reminder_60")],
        [InlineKeyboardButton("За 3 часа", callback_data="reminder_180")],
        [InlineKeyboardButton("За 1 день", callback_data="reminder_1440")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Инициализируем список напоминаний
    context.user_data['new_event']['reminders'] = []
    
    msg = await query.edit_message_text(
        "Выберите <b>напоминание</b>:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    add_message_id(query.from_user.id, msg.message_id)
    return WAITING_REMINDER_1


async def add_event_reminder_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение напоминания события"""
    query = update.callback_query
    await query.answer()
    
    reminder_data = query.data.replace('reminder_', '')
    
    if reminder_data == 'none':
        # Если выбрано "Без напоминания", сохраняем пустой список и завершаем
        context.user_data['new_event']['reminders'] = []
    else:
        # Сохраняем напоминание
        reminder_minutes = int(reminder_data)
        if 'reminders' not in context.user_data['new_event']:
            context.user_data['new_event']['reminders'] = []
        context.user_data['new_event']['reminders'] = [reminder_minutes]
    
    # Завершаем создание события
    return await finish_event_creation(query, context)


async def finish_event_creation(query, context: ContextTypes.DEFAULT_TYPE):
    """Завершение создания события и сохранение"""
    # Сохранение события(ий)
    base_event = context.user_data['new_event']
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    repeat_type = base_event.get('repeat_type', 'once')
    
    # Добавляем эмодзи к названию события, если его еще нет
    event_title = base_event.get('title', '')
    if event_title and not event_title.startswith('🕰'):
        base_event['title'] = '🕰 ' + event_title
    
    # Создаём события на основе типа повторения
    if repeat_type == 'once':
        # Одноразовое событие
        event = base_event.copy()
        event['id'] = str(datetime.now().timestamp())
        event['created_at'] = datetime.now().isoformat()
        # Добавляем источник создания события
        event['source'] = 'schedule'
        # Инициализируем reminder_sent как пустой список
        if 'reminder_sent' not in event:
            event['reminder_sent'] = []
        save_user_event(user_id, event)
    elif repeat_type == 'daily':
        # Ежедневное событие - создаём события на неделю вперед
        base_date = datetime.strptime(base_event['date'], '%Y-%m-%d')
        for i in range(7):  # 7 дней = 1 неделя
            event = base_event.copy()
            event_date = base_date + timedelta(days=i)
            event['date'] = event_date.strftime('%Y-%m-%d')
            event['id'] = f"{datetime.now().timestamp()}_{i}"
            event['created_at'] = datetime.now().isoformat()
            event['repeat_type'] = 'daily'
            event['base_date'] = base_event['date']
            # Добавляем источник создания события
            event['source'] = 'schedule'
            # Инициализируем reminder_sent как пустой список
            if 'reminder_sent' not in event:
                event['reminder_sent'] = []
            save_user_event(user_id, event)
    elif repeat_type == 'weekly':
        # Еженедельное событие - создаём события на 4 недели вперед
        base_date = datetime.strptime(base_event['date'], '%Y-%m-%d')
        for i in range(4):  # 4 недели
            event = base_event.copy()
            event_date = base_date + timedelta(weeks=i)
            event['date'] = event_date.strftime('%Y-%m-%d')
            event['id'] = f"{datetime.now().timestamp()}_{i}"
            event['created_at'] = datetime.now().isoformat()
            event['repeat_type'] = 'weekly'
            event['base_date'] = base_event['date']
            # Добавляем источник создания события
            event['source'] = 'schedule'
            # Инициализируем reminder_sent как пустой список
            if 'reminder_sent' not in event:
                event['reminder_sent'] = []
            save_user_event(user_id, event)
    
    # Удаляем все сообщения бота перед показом расписания
    try:
        await query.message.delete()
    except:
        pass
    
    deleted_count = await delete_user_messages(context, user_id, chat_id)
    
    # Удаляем прошедшие события перед показом расписания
    delete_past_events(user_id)
    
    # Показываем расписание на неделю
    events = get_user_events(user_id)
    if events:
        text = format_events_list(events, 'week', str(user_id))
    else:
        text = "Может устроить день дурака?"
    
    # Всегда показываем основную клавиатуру
    main_keyboard = get_main_keyboard()
    msg = await context.bot.send_message(
        chat_id=chat_id, 
        text=text, 
        reply_markup=main_keyboard, 
        parse_mode='HTML'
    )
    add_message_id(user_id, msg.message_id)
    
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена операции"""
    # Сохраняем ID сообщения пользователя
    add_user_message_id(update.effective_user.id, update.message.message_id)
    
    context.user_data.clear()
    msg = await update.message.reply_text("Операция отменена.")
    add_message_id(update.effective_user.id, msg.message_id)
    return ConversationHandler.END


async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все события"""
    user_id = update.effective_user.id
    # Удаляем прошедшие события перед показом
    delete_past_events(user_id)
    events = get_user_events(user_id)
    
    if not events:
        keyboard = get_main_keyboard()
        msg = await update.message.reply_text(
            "Может устроить день дурака?",
            reply_markup=keyboard
        )
        add_message_id(user_id, msg.message_id)
        return
    
    text = format_events_list(events, 'all', str(user_id))
    keyboard = get_main_keyboard()
    msg = await update.message.reply_text(
        text, 
        reply_markup=keyboard, 
        parse_mode='HTML'
    )
    add_message_id(user_id, msg.message_id)


async def today_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать события на сегодня"""
    # Проверяем, не находится ли пользователь в процессе добавления события
    # Если да, то "сегодня" должно обрабатываться как дата, а не как команда
    if 'new_event' in context.user_data:
        # Пользователь в процессе добавления события - пропускаем обработку
        # чтобы ConversationHandler обработал это как дату
        logger.debug("⏭️  Пропуск обработки 'сегодня' - пользователь добавляет событие")
        return
    
    # Также проверяем, не находится ли пользователь в процессе редактирования
    if 'editing_event_id' in context.user_data:
        logger.debug("⏭️  Пропуск обработки 'сегодня' - пользователь редактирует событие")
        return
    
    user_id = update.effective_user.id
    # Удаляем прошедшие события перед показом
    delete_past_events(user_id)
    events = get_user_events(user_id)
    text = format_events_list(events, 'today', str(user_id))
    keyboard = get_main_keyboard()
    msg = await update.message.reply_text(
        text, 
        parse_mode='HTML',
        reply_markup=keyboard
    )
    add_message_id(user_id, msg.message_id)


async def week_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все события (полное расписание)"""
    user_id = update.effective_user.id
    # Удаляем прошедшие события перед показом (принудительно)
    deleted = delete_past_events(str(user_id))
    if deleted > 0:
        logger.info(f"🗑️  Удалено {deleted} прошедших событий для пользователя {user_id} перед показом общего расписания")
    events = get_user_events(user_id)
    text = format_events_list(events, 'all', str(user_id))
    keyboard = get_main_keyboard()
    msg = await update.message.reply_text(
        text, 
        parse_mode='HTML',
        reply_markup=keyboard
    )
    add_message_id(user_id, msg.message_id)


async def tomorrow_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать события на завтра"""
    # Проверяем, не находится ли пользователь в процессе добавления события
    # Если да, то "завтра" должно обрабатываться как дата, а не как команда
    if 'new_event' in context.user_data:
        # Пользователь в процессе добавления события - пропускаем обработку
        # чтобы ConversationHandler обработал это как дату
        logger.debug("⏭️  Пропуск обработки 'завтра' - пользователь добавляет событие")
        return
    
    # Также проверяем, не находится ли пользователь в процессе редактирования
    if 'editing_event_id' in context.user_data:
        logger.debug("⏭️  Пропуск обработки 'завтра' - пользователь редактирует событие")
        return
    
    user_id = update.effective_user.id
    # Удаляем прошедшие события перед показом
    delete_past_events(user_id)
    events = get_user_events(user_id)
    
    # Получаем текущее время в часовом поясе пользователя
    user_timezone = get_user_timezone(str(user_id))
    if user_timezone:
        try:
            tz = ZoneInfo(user_timezone)
            now = datetime.now(tz)
        except Exception as e:
            logger.warning(f"⚠️  Ошибка при получении часового пояса {user_timezone}: {e}")
            now = datetime.now()
    else:
        now = datetime.now()
    
    # Получаем дату завтра в часовом поясе пользователя
    if now.tzinfo is not None:
        tomorrow_date = (now + timedelta(days=1)).date()
    else:
        tomorrow_date = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).date()
    
    # Фильтруем события на завтра (сравниваем naive даты)
    tomorrow_events_list = [
        e for e in events 
        if datetime.strptime(e['date'], '%Y-%m-%d').date() == tomorrow_date
    ]
    
    if tomorrow_events_list:
        # Используем форматирование для одного дня
        text = format_events_list(tomorrow_events_list, 'tomorrow', str(user_id))
    else:
        text = "Может устроить день дурака?"
    
    keyboard = get_main_keyboard()
    msg = await update.message.reply_text(
        text, 
        parse_mode='HTML',
        reply_markup=keyboard
    )
    add_message_id(user_id, msg.message_id)


async def delete_all_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос подтверждения удаления расписания"""
    user_id = update.effective_user.id
    
    # Создаём клавиатуру с подтверждением
    keyboard = [
        [InlineKeyboardButton("Да", callback_data="confirm_delete_yes")],
        [InlineKeyboardButton("Нет", callback_data="confirm_delete_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = await update.message.reply_text(
        "Точно удалить расписание?",
        reply_markup=reply_markup
    )
    add_message_id(user_id, msg.message_id)


async def confirm_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления расписания"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    # Удаляем все события пользователя
    if delete_all_user_events(user_id):
        keyboard = get_main_keyboard()
        await query.edit_message_text("Расписание удалено.")
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=" ",
            reply_markup=keyboard
        )
        add_message_id(user_id, msg.message_id)
    else:
        keyboard = get_main_keyboard()
        await query.edit_message_text("Ошибка при удалении расписания.")
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=" ",
            reply_markup=keyboard
        )
        add_message_id(user_id, msg.message_id)


async def confirm_delete_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена удаления расписания"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    events = get_user_events(user_id)
    
    # Возвращаемся к меню редактирования
    keyboard = []
    if events:
        events_sorted = sorted(events, key=lambda x: (x['date'], x['time']))[:20]
        for event in events_sorted:
            date_obj = datetime.strptime(event['date'], '%Y-%m-%d')
            date_str = date_obj.strftime('%d.%m')
            title_short = event['title'][:25] + '...' if len(event['title']) > 25 else event['title']
            button_text = f"{date_str} {event['time']} - {title_short}"
            keyboard.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"event_{event['id']}"
                )
            ])
    
    keyboard.append([InlineKeyboardButton("управление категориями", callback_data="manage_categories")])
    keyboard.append([InlineKeyboardButton("удалить расписание", callback_data="confirm_delete_start")])
    keyboard.append([InlineKeyboardButton("инструкция", callback_data="show_help")])
    keyboard.append([InlineKeyboardButton("назад", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "Удаление отменено.\n\nВыберите событие для редактирования:",
        reply_markup=reply_markup
    )


async def confirm_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос подтверждения удаления расписания из меню редактирования"""
    query = update.callback_query
    await query.answer()
    
    # Создаём клавиатуру с подтверждением
    keyboard = [
        [InlineKeyboardButton("Да", callback_data="confirm_delete_yes")],
        [InlineKeyboardButton("Нет", callback_data="confirm_delete_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "Точно удалить расписание?",
        reply_markup=reply_markup
    )


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать инструкцию из меню редактирования"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    events = get_user_events(user_id)
    
    help_text = """
📖 <b>Справка по использованию бота:</b>

<b>Основные кнопки:</b>
➕ - Добавить новое событие
моё расписание - Показать все события
что сегодня? - События на сегодня
что завтра? - События на завтра
✏️ - Редактировать события и управлять категориями
🙈 - Очистить чат

<b>Добавление события:</b>
1. Нажмите ➕
2. Введите название события
3. Введите дату:
   • завтра, послезавтра
   • понедельник, вторник, среда и т.д.
   • 17 января, 19 01, 25.12.2024
4. Введите время:
   • Только часы: 12, 13, 9
   • С минутами: 12:30, 14:45
5. Введите описание (или /skip)
6. Выберите категорию (или создайте свою)
7. Выберите повторяемость
8. Выберите напоминание

<b>Категории:</b>
Каждый пользователь создает свои категории.
Нажмите ✏️ → "управление категориями" для настройки.

<b>Редактирование:</b>
Нажмите ✏️ и выберите событие для редактирования.

<b>Удаление:</b>
В меню редактирования можно удалить отдельное событие или всё расписание.
"""
    
    # Создаём клавиатуру для возврата
    keyboard = []
    if events:
        events_sorted = sorted(events, key=lambda x: (x['date'], x['time']))[:20]
        for event in events_sorted:
            date_obj = datetime.strptime(event['date'], '%Y-%m-%d')
            date_str = date_obj.strftime('%d.%m')
            title_short = event['title'][:25] + '...' if len(event['title']) > 25 else event['title']
            button_text = f"{date_str} {event['time']} - {title_short}"
            keyboard.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"event_{event['id']}"
                )
            ])
    
    keyboard.append([InlineKeyboardButton("управление категориями", callback_data="manage_categories")])
    keyboard.append([InlineKeyboardButton("удалить расписание", callback_data="confirm_delete_start")])
    keyboard.append([InlineKeyboardButton("инструкция", callback_data="show_help")])
    keyboard.append([InlineKeyboardButton("назад", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='HTML')


async def clear_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очистка истории сообщений - удаляет все сообщения выше (кроме закрепленного)"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    current_message_id = update.message.message_id
    
    # Получаем информацию о чате для проверки закрепленного сообщения
    pinned_message_id = None
    try:
        chat = await context.bot.get_chat(chat_id)
        if hasattr(chat, 'pinned_message') and chat.pinned_message:
            pinned_message_id = chat.pinned_message.message_id
    except:
        # Если не удалось получить информацию о чате, продолжаем без проверки закрепленного
        pass
    
    # Функция для параллельного удаления сообщений
    async def delete_message_safe(msg_id: int) -> bool:
        """Безопасное удаление сообщения с обработкой ошибок"""
        if pinned_message_id and msg_id == pinned_message_id:
            return False
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            return True
        except Exception:
            return False
    
    # Собираем все ID сообщений для удаления
    messages_to_delete = []
    messages_data = load_user_sent_messages()
    user_id_str = str(user_id)
    
    if user_id_str in messages_data:
        message_ids = messages_data[user_id_str]
        remaining_messages = []
        for msg_id in message_ids:
            if pinned_message_id and msg_id == pinned_message_id:
                remaining_messages.append(msg_id)
                continue
            if msg_id < current_message_id:
                messages_to_delete.append(msg_id)
            else:
                remaining_messages.append(msg_id)
        
        messages_data[user_id_str] = remaining_messages
        save_user_sent_messages(messages_data)
    
    # Удаляем все сообщения бота выше текущего
    bot_messages_data = load_messages()
    
    if user_id_str in bot_messages_data:
        message_ids = bot_messages_data[user_id_str]
        remaining_bot_messages = []
        for msg_id in message_ids:
            if msg_id < current_message_id:
                if msg_id not in messages_to_delete:
                    messages_to_delete.append(msg_id)
            else:
                remaining_bot_messages.append(msg_id)
        
        bot_messages_data[user_id_str] = remaining_bot_messages
        save_messages(bot_messages_data)
    
    # Добавляем дополнительные сообщения для удаления (массовое удаление)
    start_id = max(1, current_message_id - 100)
    for msg_id in range(start_id, current_message_id):
        if msg_id not in messages_to_delete:
            messages_to_delete.append(msg_id)
    
    # Параллельное удаление батчами по 10 сообщений
    deleted_count = 0
    batch_size = 10
    for i in range(0, len(messages_to_delete), batch_size):
        batch = messages_to_delete[i:i + batch_size]
        tasks = [delete_message_safe(msg_id) for msg_id in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        deleted_count += sum(1 for r in results if r is True)
        # Небольшая задержка между батчами для избежания rate limit
        if i + batch_size < len(messages_to_delete):
            await asyncio.sleep(0.01)
    
    # Пытаемся удалить сообщение команды /clear (если бот имеет права)
    try:
        await update.message.delete()
        deleted_count += 1
    except:
        # Если не удалось удалить сообщение команды, продолжаем
        pass
    
    # Отправляем команду /start вместо расписания
    await start(update, context)


async def edit_events_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список событий для редактирования"""
    user_id = update.effective_user.id
    events = get_user_events(user_id)
    
    if not events:
        # Если нет событий, показываем меню с кнопкой управления категориями
        keyboard = [
            [InlineKeyboardButton("управление категориями", callback_data="manage_categories")],
            [InlineKeyboardButton("назад", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text(
            "Нет событий для редактирования.",
            reply_markup=reply_markup
        )
        add_message_id(user_id, msg.message_id)
        return
    
    # Создаём клавиатуру с событиями для редактирования
    keyboard = []
    events_sorted = sorted(events, key=lambda x: (x['date'], x['time']))[:20]  # Максимум 20
    
    for event in events_sorted:
        date_obj = datetime.strptime(event['date'], '%Y-%m-%d')
        date_str = date_obj.strftime('%d.%m')
        title_short = event['title'][:25] + '...' if len(event['title']) > 25 else event['title']
        button_text = f"{date_str} {event['time']} - {title_short}"
        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"event_{event['id']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("управление категориями", callback_data="manage_categories")])
    keyboard.append([InlineKeyboardButton("удалить расписание", callback_data="confirm_delete_start")])
    keyboard.append([InlineKeyboardButton("инструкция", callback_data="show_help")])
    keyboard.append([InlineKeyboardButton("назад", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = await update.message.reply_text(
        "Выберите событие для редактирования:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    add_message_id(user_id, msg.message_id)


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к главному меню"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    events = get_user_events(user_id)
    
    text = format_events_list(events, 'all', str(user_id)) if events else "Может устроить день дурака?"
    keyboard = get_main_keyboard()
    
    await query.edit_message_text(text, parse_mode='HTML')
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=" ",
        reply_markup=keyboard
    )


async def clear_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки очистки чата - удаляет все сообщения выше (кроме закрепленного), как кнопка 🙈"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    current_message_id = query.message.message_id
    
    # Получаем информацию о чате для проверки закрепленного сообщения
    pinned_message_id = None
    try:
        chat = await context.bot.get_chat(chat_id)
        if hasattr(chat, 'pinned_message') and chat.pinned_message:
            pinned_message_id = chat.pinned_message.message_id
    except:
        # Если не удалось получить информацию о чате, продолжаем без проверки закрепленного
        pass
    
    # Функция для параллельного удаления сообщений
    async def delete_message_safe(msg_id: int) -> bool:
        """Безопасное удаление сообщения с обработкой ошибок"""
        if pinned_message_id and msg_id == pinned_message_id:
            return False
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            return True
        except Exception:
            return False
    
    # Собираем все ID сообщений для удаления
    messages_to_delete = []
    messages_data = load_user_sent_messages()
    user_id_str = str(user_id)
    
    if user_id_str in messages_data:
        message_ids = messages_data[user_id_str]
        remaining_messages = []
        for msg_id in message_ids:
            if pinned_message_id and msg_id == pinned_message_id:
                remaining_messages.append(msg_id)
                continue
            if msg_id < current_message_id:
                messages_to_delete.append(msg_id)
            else:
                remaining_messages.append(msg_id)
        
        messages_data[user_id_str] = remaining_messages
        save_user_sent_messages(messages_data)
    
    # Удаляем все сообщения бота выше текущего
    bot_messages_data = load_messages()
    
    if user_id_str in bot_messages_data:
        message_ids = bot_messages_data[user_id_str]
        remaining_bot_messages = []
        for msg_id in message_ids:
            if msg_id < current_message_id:
                if msg_id not in messages_to_delete:
                    messages_to_delete.append(msg_id)
            else:
                remaining_bot_messages.append(msg_id)
        
        bot_messages_data[user_id_str] = remaining_bot_messages
        save_messages(bot_messages_data)
    
    # Добавляем дополнительные сообщения для удаления (массовое удаление)
    start_id = max(1, current_message_id - 100)
    for msg_id in range(start_id, current_message_id):
        if msg_id not in messages_to_delete:
            messages_to_delete.append(msg_id)
    
    # Параллельное удаление батчами по 10 сообщений
    deleted_count = 0
    batch_size = 10
    for i in range(0, len(messages_to_delete), batch_size):
        batch = messages_to_delete[i:i + batch_size]
        tasks = [delete_message_safe(msg_id) for msg_id in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        deleted_count += sum(1 for r in results if r is True)
        # Небольшая задержка между батчами для избежания rate limit
        if i + batch_size < len(messages_to_delete):
            await asyncio.sleep(0.01)
    
    # Пытаемся удалить текущее сообщение с кнопкой (если бот имеет права)
    try:
        await query.message.delete()
        deleted_count += 1
    except:
        # Если не удалось удалить сообщение, продолжаем
        pass
    
    # Показываем только актуальное расписание (без дополнительных сообщений о количестве удаленных)
    events = get_user_events(user_id)
    if events:
        text = format_events_list(events, 'all', str(user_id))
    else:
        text = "Может устроить день дурака?"
    
    # Всегда показываем основную клавиатуру
    main_keyboard = get_main_keyboard()
    msg = await context.bot.send_message(
        chat_id=chat_id, 
        text=text, 
        reply_markup=main_keyboard, 
        parse_mode='HTML'
    )
    add_message_id(user_id, msg.message_id)


async def event_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора события"""
    query = update.callback_query
    await query.answer()
    
    event_id = query.data.replace('event_', '')
    user_id = query.from_user.id
    events = get_user_events(user_id)
    
    event = next((e for e in events if e.get('id') == event_id), None)
    
    if not event:
        await query.edit_message_text("Событие не найдено.")
        return
    
    # Клавиатура для управления событием
    keyboard = [
        [
            InlineKeyboardButton("Редактировать", callback_data=f"edit_{event_id}"),
            InlineKeyboardButton("Удалить", callback_data=f"delete_{event_id}")
        ],
        [InlineKeyboardButton("назад к списку", callback_data="back_to_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = format_event(event, str(user_id))
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')


async def delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление события"""
    query = update.callback_query
    await query.answer()
    
    event_id = query.data.replace('delete_', '')
    user_id = query.from_user.id
    
    if delete_user_event(user_id, event_id):
        keyboard = get_main_keyboard()
        await query.edit_message_text("Событие успешно удалено!")
        # Отправляем клавиатуру отдельным сообщением после редактирования
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=" ",
            reply_markup=keyboard
        )
    else:
        keyboard = get_main_keyboard()
        await query.edit_message_text("Ошибка при удалении события.")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=" ",
            reply_markup=keyboard
        )


async def edit_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало редактирования события"""
    query = update.callback_query
    await query.answer()
    
    event_id = query.data.replace('edit_', '')
    user_id = query.from_user.id
    events = get_user_events(user_id)
    
    event = next((e for e in events if e.get('id') == event_id), None)
    
    if not event:
        await query.edit_message_text("Событие не найдено.")
        return
    
    context.user_data['editing_event_id'] = event_id
    context.user_data['editing_event'] = event
    
    # Клавиатура для выбора поля для редактирования
    keyboard = [
        [InlineKeyboardButton("Название", callback_data="edit_field_title")],
        [InlineKeyboardButton("Дата", callback_data="edit_field_date")],
        [InlineKeyboardButton("Время", callback_data="edit_field_time")],
        [InlineKeyboardButton("Описание", callback_data="edit_field_description")],
        [InlineKeyboardButton("Категория", callback_data="edit_field_category")],
        [InlineKeyboardButton("Отмена", callback_data="back_to_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"Что вы хотите изменить?\n\n{format_event(event, str(user_id))}"
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
    return WAITING_EDIT_CHOICE


async def edit_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор поля для редактирования"""
    query = update.callback_query
    await query.answer()
    
    field = query.data.replace('edit_field_', '')
    context.user_data['editing_field'] = field
    
    prompts = {
        'title': 'Введите новое название события:',
        'date': 'Введите новую дату (например: завтра, понедельник, 17 января, 19 01, 25.12.2024):',
        'time': 'Введите новое время (например: 12, 13:30, 9):',
        'description': 'Введите новое описание (или /skip чтобы удалить):',
        'category': 'Выберите новую категорию:'
    }
    
    if field == 'category':
        # Получаем категории пользователя
        user_id = query.from_user.id
        user_categories = get_user_categories(user_id)
        
        keyboard = []
        for key, value in user_categories.items():
            keyboard.append([InlineKeyboardButton(value, callback_data=f"cat_{key}")])
        
        # Добавляем кнопку для управления категориями
        keyboard.append([InlineKeyboardButton("управление категориями", callback_data="manage_categories")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"{prompts[field]}",
            reply_markup=reply_markup
        )
        return WAITING_EDIT_VALUE
    else:
        await query.edit_message_text(f"{prompts[field]}")
        return WAITING_EDIT_VALUE


async def edit_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение нового значения поля"""
    # Сохраняем ID сообщения пользователя
    add_user_message_id(update.effective_user.id, update.message.message_id)
    
    field = context.user_data.get('editing_field')
    event_id = context.user_data.get('editing_event_id')
    user_id = update.effective_user.id
    
    events = get_user_events(user_id)
    event = next((e for e in events if e.get('id') == event_id), None)
    
    if not event:
        await update.message.reply_text("Событие не найдено.")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Обработка разных полей
    if field == 'title':
        title = update.message.text
        # Добавляем эмодзи к названию события, если его еще нет
        if title and not title.startswith('🕰'):
            title = '🕰 ' + title
        event['title'] = title
    elif field == 'date':
        try:
            date_str = update.message.text.strip()
            date_obj = None
            
            # Сначала пробуем парсить естественные формулировки
            date_obj = parse_natural_date(date_str)
            
            # Если не получилось, пробуем формат DD MM (без года)
            if date_obj is None:
                parts = date_str.split()
                if len(parts) == 2:
                    try:
                        day = int(parts[0])
                        month = int(parts[1])
                        if 1 <= month <= 12 and 1 <= day <= 31:
                            current_year = datetime.now().year
                            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                            date_obj = datetime(current_year, month, day)
                            if date_obj < today:
                                date_obj = datetime(current_year + 1, month, day)
                    except ValueError:
                        pass
            
            # Если все еще не получилось, пробуем стандартные форматы
            if date_obj is None:
                for fmt in ['%d.%m.%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d.%m.%y', '%d/%m/%y']:
                    try:
                        date_obj = datetime.strptime(date_str, fmt)
                        break
                    except ValueError:
                        continue
            
            if date_obj is None:
                msg = await update.message.reply_text(
                    "Неверный формат даты. Попробуйте снова.\n\n"
                    "Примеры: Завтра, Послезавтра, Понедельник, Вторник, 17 января, 19 01, 25.12.2024",
                    parse_mode='HTML'
                )
                add_message_id(user_id, msg.message_id)
                return WAITING_EDIT_VALUE
            
            # Проверяем, что дата не в прошлом
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if date_obj.date() < today.date():
                msg = await update.message.reply_text(
                    "Нельзя выбрать прошедшую дату. Попробуйте снова.",
                    parse_mode='HTML'
                )
                add_message_id(user_id, msg.message_id)
                return WAITING_EDIT_VALUE
            
            event['date'] = date_obj.strftime('%Y-%m-%d')
        except Exception as e:
            msg = await update.message.reply_text("Ошибка при обработке даты.")
            add_message_id(user_id, msg.message_id)
            return WAITING_EDIT_VALUE
    elif field == 'time':
        try:
            time_str = update.message.text.strip()
            
            # Проверяем, является ли ввод только числом (часы без минут)
            if time_str.isdigit():
                hours = int(time_str)
                if 0 <= hours <= 23:
                    # Автоматически добавляем ":00" для минут
                    event['time'] = f"{hours:02d}:00"
                else:
                    raise ValueError("Часы должны быть от 0 до 23")
            else:
                # Поддержка формата "чч мм" (с пробелом)
                if ' ' in time_str and ':' not in time_str:
                    time_str = time_str.replace(' ', ':')
                
                # Проверяем формат времени
                datetime.strptime(time_str, '%H:%M')
                # Сохраняем в стандартном формате ЧЧ:ММ
                event['time'] = time_str
        except:
            await update.message.reply_text(
                "Неверный формат времени. Попробуйте снова:\n"
                "• Только часы: 12, 13, 9\n"
                "• С минутами: 12:30, 14:45"
            )
            return WAITING_EDIT_VALUE
    elif field == 'description':
        if update.message.text.lower() == '/skip':
            event['description'] = ''
        else:
            event['description'] = update.message.text
    
    update_user_event(user_id, event_id, event)
    
    # Удаляем все сообщения бота перед показом расписания
    chat_id = update.effective_chat.id
    deleted_count = await delete_user_messages(context, user_id, chat_id)
    
    # Показываем расписание на неделю
    await show_week_schedule(context, user_id, chat_id)
    
    context.user_data.clear()
    return ConversationHandler.END


async def edit_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора категории при редактировании"""
    query = update.callback_query
    await query.answer()
    
    category = query.data.replace('cat_', '')
    event_id = context.user_data.get('editing_event_id')
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    events = get_user_events(user_id)
    event = next((e for e in events if e.get('id') == event_id), None)
    
    if event:
        event['category'] = category
        update_user_event(user_id, event_id, event)
        
        # Удаляем все сообщения бота перед показом расписания
        try:
            await query.message.delete()
        except:
            pass
        
        deleted_count = await delete_user_messages(context, user_id, chat_id)
        
        # Показываем расписание на неделю
        await show_week_schedule(context, user_id, chat_id)
    
    context.user_data.clear()
    return ConversationHandler.END


async def back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к списку событий"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    events = get_user_events(user_id)
    
    text = format_events_list(events, 'all', str(user_id))
    
    await query.edit_message_text(text, parse_mode='HTML')
    
    # Всегда показываем основную клавиатуру после редактирования сообщения
    main_keyboard = get_main_keyboard()
    try:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=" ",
            reply_markup=main_keyboard
        )
    except:
        pass
    
    context.user_data.clear()
    return ConversationHandler.END


async def manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления категориями"""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id
    
    user_categories = get_user_categories(user_id)
    
    # Формируем список категорий
    categories_text = "📋 <b>Ваши категории:</b>\n\n"
    if user_categories:
        for cat_id, cat_name in user_categories.items():
            categories_text += f"• {cat_name}\n"
    else:
        categories_text += "Категорий пока нет.\n"
    
    categories_text += "\nВыберите действие:"
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить категорию", callback_data="category_add")],
        [InlineKeyboardButton("✏️ Редактировать категорию", callback_data="category_edit_list")],
        [InlineKeyboardButton("🗑️ Удалить категорию", callback_data="category_delete_list")],
    ]
    
    # Если это callback из процесса добавления события, добавляем кнопку "Дальше" и "Назад"
    if 'new_event' in context.user_data:
        keyboard.append([InlineKeyboardButton("➡️ Дальше", callback_data="categories_done")])
        keyboard.append([InlineKeyboardButton("◀️ Назад к выбору категории", callback_data="back_to_category_selection")])
    else:
        keyboard.append([InlineKeyboardButton("➡️ Дальше", callback_data="categories_done")])
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(categories_text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        msg = await update.message.reply_text(categories_text, reply_markup=reply_markup, parse_mode='HTML')
        add_message_id(user_id, msg.message_id)
    
    # Возвращаем None, чтобы ConversationHandler оставался активным
    return None


async def category_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления категории"""
    query = update.callback_query
    await query.answer()
    
    msg = await query.edit_message_text(
        "Введите название новой категории:",
        parse_mode='HTML'
    )
    add_message_id(query.from_user.id, msg.message_id)
    return WAITING_CATEGORY_NAME


async def category_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение названия новой категории"""
    user_id = update.effective_user.id
    category_name = update.message.text.strip() if update.message.text else ""
    
    logger.info(f"📝 Получено название категории от пользователя {user_id}: '{category_name}'")
    
    if not category_name:
        msg = await update.message.reply_text("Название категории не может быть пустым. Попробуйте снова:")
        add_message_id(user_id, msg.message_id)
        return WAITING_CATEGORY_NAME
    
    # Генерируем уникальный ID для категории
    category_id = generate_category_id()
    
    # Добавляем категорию
    add_user_category(user_id, category_id, category_name)
    
    # Сохраняем ID сообщения пользователя
    add_user_message_id(user_id, update.message.message_id)
    
    # Удаляем сообщение пользователя
    try:
        await update.message.delete()
    except:
        pass
    
    # Проверяем, откуда пришли (из добавления события или из меню)
    if 'new_event' in context.user_data:
        # Возвращаемся к выбору категории для события
        # Получаем категории пользователя
        user_categories = get_user_categories(user_id)
        
        # Создаём клавиатуру с категориями пользователя
        keyboard = []
        for key, value in user_categories.items():
            keyboard.append([InlineKeyboardButton(value, callback_data=f"category_{key}")])
        
        # Добавляем кнопку для управления категориями
        keyboard.append([InlineKeyboardButton("управление категориями", callback_data="manage_categories")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Категория <b>«{category_name}»</b> добавлена!\n\nВыберите <b>категорию</b> события:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        add_message_id(user_id, msg.message_id)
        return WAITING_CATEGORY
    else:
        # Возвращаемся в меню управления категориями
        # Создаем временное сообщение для обновления
        user_categories = get_user_categories(user_id)
        
        categories_text = f"✅ Категория <b>«{category_name}»</b> добавлена!\n\n📋 <b>Ваши категории:</b>\n\n"
        for cat_id, cat_name in user_categories.items():
            categories_text += f"• {cat_name}\n"
        
        categories_text += "\nВыберите действие:"
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить категорию", callback_data="category_add")],
            [InlineKeyboardButton("✏️ Редактировать категорию", callback_data="category_edit_list")],
            [InlineKeyboardButton("🗑️ Удалить категорию", callback_data="category_delete_list")],
            [InlineKeyboardButton("➡️ Дальше", callback_data="categories_done")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=categories_text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        add_message_id(user_id, msg.message_id)
        return ConversationHandler.END


async def category_edit_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список категорий для редактирования"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_categories = get_user_categories(user_id)
    
    if not user_categories:
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="manage_categories")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "У вас нет категорий для редактирования.",
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    keyboard = []
    for cat_id, cat_name in user_categories.items():
        keyboard.append([InlineKeyboardButton(cat_name, callback_data=f"category_edit_{cat_id}")])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="manage_categories")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "Выберите категорию для редактирования:",
        reply_markup=reply_markup
    )
    return WAITING_CATEGORY_EDIT_NAME


async def category_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор категории для редактирования"""
    query = update.callback_query
    await query.answer()
    
    category_id = query.data.replace('category_edit_', '')
    context.user_data['editing_category_id'] = category_id
    
    user_id = query.from_user.id
    user_categories = get_user_categories(user_id)
    old_name = user_categories.get(category_id, '')
    
    msg = await query.edit_message_text(
        f"Текущее название: <b>{old_name}</b>\n\nВведите новое название категории:",
        parse_mode='HTML'
    )
    add_message_id(user_id, msg.message_id)
    return WAITING_CATEGORY_EDIT_NAME


async def category_edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение нового названия категории"""
    user_id = update.effective_user.id
    category_id = context.user_data.get('editing_category_id')
    new_name = update.message.text.strip()
    
    if not new_name:
        msg = await update.message.reply_text("Название категории не может быть пустым. Попробуйте снова:")
        add_message_id(user_id, msg.message_id)
        return WAITING_CATEGORY_EDIT_NAME
    
    # Обновляем категорию
    update_user_category(user_id, category_id, new_name)
    
    # Сохраняем ID сообщения пользователя
    add_user_message_id(user_id, update.message.message_id)
    
    # Возвращаемся в меню управления категориями
    await manage_categories(update, context)
    context.user_data.pop('editing_category_id', None)
    return ConversationHandler.END


async def category_delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список категорий для удаления"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_categories = get_user_categories(user_id)
    
    if not user_categories or len(user_categories) <= 1:
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="manage_categories")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Нельзя удалить последнюю категорию. Должна остаться хотя бы одна категория.",
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    keyboard = []
    for cat_id, cat_name in user_categories.items():
        keyboard.append([InlineKeyboardButton(cat_name, callback_data=f"category_delete_{cat_id}")])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="manage_categories")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "Выберите категорию для удаления:\n\n⚠️ Все события с этой категорией будут переведены в категорию 'остальное'.",
        reply_markup=reply_markup
    )
    return WAITING_CATEGORY_DELETE_CONFIRM


async def category_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления категории"""
    query = update.callback_query
    await query.answer()
    
    category_id = query.data.replace('category_delete_', '')
    user_id = query.from_user.id
    user_categories = get_user_categories(user_id)
    category_name = user_categories.get(category_id, '')
    
    context.user_data['deleting_category_id'] = category_id
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"category_delete_yes_{category_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="category_delete_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"Точно удалить категорию <b>{category_name}</b>?\n\nВсе события с этой категорией будут переведены в категорию 'остальное'.",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    return WAITING_CATEGORY_DELETE_CONFIRM


async def category_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление категории"""
    query = update.callback_query
    await query.answer()
    
    category_id = query.data.replace('category_delete_yes_', '')
    user_id = query.from_user.id
    
    # Получаем категорию "остальное" или создаем её
    user_categories = get_user_categories(user_id)
    default_category_id = None
    for cat_id, cat_name in user_categories.items():
        if cat_name == 'остальное':
            default_category_id = cat_id
            break
    
    if not default_category_id:
        default_category_id = 'other'
        add_user_category(user_id, default_category_id, 'остальное')
    
    # Переводим все события с удаляемой категорией в "остальное"
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data:
        for event in data[user_id_str]:
            if event.get('category') == category_id:
                event['category'] = default_category_id
        save_data(data)
    
    # Удаляем категорию
    delete_user_category(user_id, category_id)
    
    # Возвращаемся в меню управления категориями
    await manage_categories(update, context)
    context.user_data.pop('deleting_category_id', None)
    return ConversationHandler.END


async def categories_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки 'Дальше' после работы с категориями"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Проверяем, откуда пришли (из добавления события или из меню)
    if 'new_event' in context.user_data:
        # Возвращаемся к выбору категории для события
        user_categories = get_user_categories(user_id)
        
        if not user_categories or len(user_categories) == 0:
            keyboard = [
                [InlineKeyboardButton("Создать категории", callback_data="manage_categories")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "У вас пока нет категорий. Создайте их, чтобы продолжить добавление события.",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return WAITING_CATEGORY
        
        # Создаём клавиатуру с категориями пользователя
        keyboard = []
        for key, value in user_categories.items():
            keyboard.append([InlineKeyboardButton(value, callback_data=f"category_{key}")])
        
        # Добавляем кнопку для управления категориями
        keyboard.append([InlineKeyboardButton("управление категориями", callback_data="manage_categories")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Выберите <b>категорию</b> события:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        return WAITING_CATEGORY
    else:
        # Возвращаемся в главное меню
        return await back_to_main(update, context)


async def back_to_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к выбору категории при добавлении события"""
    query = update.callback_query
    await query.answer()
    
    # Создаем временное сообщение для возврата к add_event_description
    # Используем существующее сообщение пользователя из контекста
    user_id = query.from_user.id
    
    # Получаем категории пользователя
    user_categories = get_user_categories(user_id)
    
    if not user_categories or len(user_categories) == 0:
        keyboard = [
            [InlineKeyboardButton("Создать категории", callback_data="manage_categories")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "У вас пока нет категорий. Создайте их, чтобы продолжить добавление события.",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        return WAITING_CATEGORY
    
    # Создаём клавиатуру с категориями пользователя
    keyboard = []
    for key, value in user_categories.items():
        keyboard.append([InlineKeyboardButton(value, callback_data=f"category_{key}")])
    
    # Добавляем кнопку для управления категориями
    keyboard.append([InlineKeyboardButton("управление категориями", callback_data="manage_categories")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "Выберите <b>категорию</b> события:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    return WAITING_CATEGORY


def check_lock():
    """Проверка блокировки для предотвращения множественных запусков"""
    lock_path = os.path.join(os.path.dirname(__file__), LOCK_FILE)
    
    try:
        # Пытаемся создать lock файл
        lock_file = open(lock_path, 'w')
        try:
            # Пытаемся заблокировать файл (работает только на Unix системах)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Записываем PID процесса
            lock_file.write(str(os.getpid()))
            lock_file.flush()
            return lock_file
        except (IOError, OSError):
            # Файл уже заблокирован другим процессом
            lock_file.close()
            logger.error("❌ Бот уже запущен! Проверьте другие процессы.")
            return None
    except Exception as e:
        logger.warning(f"⚠️  Не удалось создать lock файл (возможно, Windows): {e}")
        # На Windows fcntl не работает, используем простую проверку PID
        if os.path.exists(lock_path):
            try:
                with open(lock_path, 'r') as f:
                    pid = int(f.read().strip())
                    # Проверяем, существует ли процесс с этим PID
                    try:
                        os.kill(pid, 0)  # Проверка существования процесса
                        logger.error(f"❌ Бот уже запущен (PID: {pid})!")
                        return None
                    except OSError:
                        # Процесс не существует, удаляем старый lock файл
                        os.remove(lock_path)
            except:
                pass
        # Создаем новый lock файл
        try:
            lock_file = open(lock_path, 'w')
            lock_file.write(str(os.getpid()))
            lock_file.flush()
            return lock_file
        except:
            return None


def cleanup_lock(lock_file):
    """Очистка lock файла при завершении"""
    if lock_file:
        try:
            lock_file.close()
            lock_path = os.path.join(os.path.dirname(__file__), LOCK_FILE)
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except:
            pass


def main():
    """Основная функция запуска бота"""
    # Проверка блокировки
    lock_file = check_lock()
    if lock_file is None:
        sys.exit(1)
    
    try:
        # Получение токена из переменной окружения или .env файла
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        
        # Если токен не найден в переменных окружения, пытаемся загрузить из .env
        if not token:
            try:
                env_file = os.path.join(os.path.dirname(__file__), '.env')
                if os.path.exists(env_file):
                    with open(env_file, 'r') as f:
                        for line in f:
                            if line.startswith('TELEGRAM_BOT_TOKEN='):
                                token = line.split('=', 1)[1].strip().strip("'\"")
                                break
            except:
                pass
        
        if not token:
            logger.error("Ошибка: Не указан TELEGRAM_BOT_TOKEN!")
            logger.error("Создайте бота через @BotFather и установите токен:")
            logger.error("export TELEGRAM_BOT_TOKEN='ваш_токен'")
            logger.error("Или создайте файл .env с содержимым: TELEGRAM_BOT_TOKEN=ваш_токен")
            cleanup_lock(lock_file)
            return
        
        # Создание приложения
        async def post_init(app: Application) -> None:
            """Инициализация после создания приложения"""
            commands = [
                BotCommand("start", "Начать работу с ботом"),
                BotCommand("add", "Добавить событие"),
                BotCommand("list", "Показать все события"),
                BotCommand("today", "События на сегодня"),
                BotCommand("week", "События на неделю"),
                BotCommand("clear", "Очистить чат"),
                BotCommand("help", "Справка по использованию")
            ]
            await app.bot.set_my_commands(commands)
            
            # Настройка updater для обработки ошибок на низком уровне
            if app.updater:
                # Увеличиваем задержку между повторными попытками
                app.updater._network_loop_retry_delay = 2.0
                logger.info("✅ Updater настроен с улучшенной обработкой ошибок")
        
        application = Application.builder().token(token).post_init(post_init).build()
        global application_instance
        application_instance = application
        
        # Регистрация обработчиков сигналов для graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # ConversationHandler для добавления события (работает с командой /add и кнопкой)
        # Важно: фильтр для кнопки должен исключать её из обычных текстовых сообщений
        add_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('add', add_event_start),
                MessageHandler(filters.Regex('^➕$') & ~filters.COMMAND, add_event_start)
            ],
            states={
                WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_title)],
                WAITING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date)],
                WAITING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_time)],
                WAITING_DESCRIPTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_description),
                    CommandHandler('skip', add_event_description)
                ],
                WAITING_CATEGORY: [
                    CallbackQueryHandler(add_event_category, pattern='^category_'),
                    CallbackQueryHandler(manage_categories, pattern='^manage_categories$'),
                    CallbackQueryHandler(back_to_category_selection, pattern='^back_to_category_selection$')
                ],
                WAITING_REPEAT: [CallbackQueryHandler(add_event_repeat, pattern='^repeat_')],
                WAITING_REMINDER_1: [CallbackQueryHandler(add_event_reminder_1, pattern='^reminder_')],
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            per_message=False,  # Явно указываем для устранения предупреждения
        )
        
        # ConversationHandler для редактирования события
        edit_conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(edit_event_start, pattern='^edit_')],
            states={
                WAITING_EDIT_CHOICE: [CallbackQueryHandler(edit_field_choice, pattern='^edit_field_')],
                WAITING_EDIT_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_value),
                    CallbackQueryHandler(edit_category_callback, pattern='^cat_')
                ],
            },
            fallbacks=[CallbackQueryHandler(back_to_list, pattern='^back_to_list$')],
            per_message=False,  # Явно указываем для устранения предупреждения
        )
        
        # Регистрация обработчиков
        # ВАЖНО: Порядок имеет значение! Сначала команды и кнопки, потом ConversationHandler
        
        # Обработчики команд
        # Команда /start обрабатывается через city_conv_handler
        application.add_handler(CommandHandler('help', help_command))
        application.add_handler(CommandHandler('list', list_events))
        application.add_handler(CommandHandler('today', today_events))
        application.add_handler(CommandHandler('week', week_events))
        application.add_handler(CommandHandler('clear', clear_messages))
        # Команда /timezone обрабатывается через city_conv_handler
        
        # ConversationHandler для управления категориями
        categories_conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(manage_categories, pattern='^manage_categories$'),
                CallbackQueryHandler(category_add_start, pattern='^category_add$'),
                CallbackQueryHandler(category_edit_list, pattern='^category_edit_list$'),
                CallbackQueryHandler(category_delete_list, pattern='^category_delete_list$')
            ],
            states={
                WAITING_CATEGORY_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, category_add_name)
                ],
                WAITING_CATEGORY_EDIT_NAME: [
                    CallbackQueryHandler(category_edit_selected, pattern='^category_edit_'),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, category_edit_name)
                ],
                WAITING_CATEGORY_DELETE_CONFIRM: [
                    CallbackQueryHandler(category_delete_confirm, pattern='^category_delete_'),
                    CallbackQueryHandler(category_delete_yes, pattern='^category_delete_yes_'),
                    CallbackQueryHandler(category_delete_list, pattern='^category_delete_list$')
                ],
            },
            fallbacks=[
                CallbackQueryHandler(manage_categories, pattern='^manage_categories$'),
                CallbackQueryHandler(back_to_main, pattern='^back_to_main$'),
                CallbackQueryHandler(categories_done, pattern='^categories_done$'),
                CallbackQueryHandler(category_add_start, pattern='^category_add$'),
                CallbackQueryHandler(category_edit_list, pattern='^category_edit_list$'),
                CallbackQueryHandler(category_delete_list, pattern='^category_delete_list$'),
                CallbackQueryHandler(back_to_category_selection, pattern='^back_to_category_selection$')
            ],
            per_message=False,
        )
        
        # ConversationHandler для ввода города при старте и изменении часового пояса
        # Исключаем команды бота из обработки как города
        city_text_filter = (
            filters.TEXT & 
            ~filters.COMMAND & 
            ~filters.Regex('^(что завтра\\?|завтра|что сегодня\\?|сегодня|послезавтра|моё расписание|расписание|➕|✏️|🙈)\s*$')
        )
        
        city_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', start),
                CommandHandler('timezone', timezone_command)
            ],
            states={
                WAITING_CITY: [MessageHandler(city_text_filter, city_input)]
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            per_message=False,
        )
        
        # ConversationHandler для добавления события (работает и с командой /add, и с кнопкой)
        # ВАЖНО: ConversationHandler регистрируются ПЕРЕД обычными MessageHandler,
        # чтобы они имели приоритет при обработке сообщений
        application.add_handler(city_conv_handler)
        application.add_handler(add_conv_handler)
        application.add_handler(edit_conv_handler)
        application.add_handler(categories_conv_handler)
        
        # Обработчики кнопок клавиатуры (ПОСЛЕ ConversationHandler)
        # Используем более гибкие регулярные выражения для мобильной версии (учитываем возможные пробелы)
        # Эти обработчики будут срабатывать только если пользователь НЕ находится в ConversationHandler
        application.add_handler(MessageHandler(filters.Regex('^(что завтра\\?|завтра)\s*$'), tomorrow_events))
        application.add_handler(MessageHandler(filters.Regex('^(что сегодня\?|сегодня)\s*$'), today_events))
        application.add_handler(MessageHandler(filters.Regex('^моё расписание\s*$'), week_events))
        application.add_handler(MessageHandler(filters.Regex('^✏️\s*$'), edit_events_list))
        application.add_handler(MessageHandler(filters.Regex('^🙈\s*$'), clear_messages))
        
        # Обработчики callback-кнопок
        application.add_handler(CallbackQueryHandler(event_callback, pattern='^event_'))
        application.add_handler(CallbackQueryHandler(delete_event, pattern='^delete_'))
        application.add_handler(CallbackQueryHandler(confirm_delete_yes, pattern='^confirm_delete_yes$'))
        application.add_handler(CallbackQueryHandler(confirm_delete_no, pattern='^confirm_delete_no$'))
        application.add_handler(CallbackQueryHandler(confirm_delete_start, pattern='^confirm_delete_start$'))
        application.add_handler(CallbackQueryHandler(show_help, pattern='^show_help$'))
        application.add_handler(CallbackQueryHandler(clear_chat_callback, pattern='^clear_chat$'))
        application.add_handler(CallbackQueryHandler(back_to_list, pattern='^back_to_list$'))
        application.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_to_main$'))
        application.add_handler(CallbackQueryHandler(categories_done, pattern='^categories_done$'))
        
        # Обработчик выбора часового пояса
        async def timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Обработчик выбора часового пояса"""
            query = update.callback_query
            await query.answer()
            
            user_id = update.effective_user.id
            timezone_id = query.data.replace('tz_', '')
            
            if timezone_id in TIMEZONES:
                # Сохраняем часовой пояс
                set_user_timezone(user_id, timezone_id)
                timezone_name = TIMEZONES[timezone_id]
                
                await query.edit_message_text(
                    f"✅ Часовой пояс установлен: {timezone_name}",
                    parse_mode='HTML'
                )
                
                # Проверяем категории пользователя
                user_categories = get_user_categories(user_id)
                
                welcome_text = ''
                # Если у пользователя нет категорий или только стандартная "остальное", предлагаем создать
                if not user_categories or (len(user_categories) == 1 and 'other' in user_categories):
                    welcome_text = (
                        '\n\nНачни с добавления категорий. Они помогут для более удобной организации событий.\n\n'
                        'Пример категорий:\n'
                        'Учеба\n'
                        'Работа\n'
                        'Спорт\n'
                        'и тд\n\n'
                        'Нажмите "✏️" → "управление категориями" для настройки.'
                    )
                
                # Показываем основную клавиатуру
                keyboard = get_main_keyboard()
                msg = await query.message.reply_text(
                    welcome_text if welcome_text else "Выберите действие:",
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                add_message_id(user_id, msg.message_id)
            else:
                await query.edit_message_text("❌ Ошибка при выборе часового пояса")
        
        # Обработчик установки часового пояса из города
        async def timezone_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Обработчик установки часового пояса из города"""
            query = update.callback_query
            await query.answer()
            
            user_id = update.effective_user.id
            # Формат callback_data: tz_set_{timezone_encoded}_{city_encoded}
            try:
                data_parts = query.data.replace('tz_set_', '').split('_', 1)
                if len(data_parts) >= 2:
                    timezone_encoded = data_parts[0]
                    city_encoded = data_parts[1]
                    
                    # Декодируем из base64
                    timezone_name = base64.b64decode(timezone_encoded).decode('utf-8')
                    city_name = base64.b64decode(city_encoded).decode('utf-8')
                    
                    # Сохраняем город и часовой пояс
                    if city_name:
                        set_user_city(user_id, city_name)
                    set_user_timezone(user_id, timezone_name)
                    
                    # Получаем информацию о часовом поясе для отображения
                    try:
                        tz = ZoneInfo(timezone_name)
                        now = datetime.now(tz)
                        offset = now.strftime('%z')
                        offset_formatted = f"{offset[:3]}:{offset[3:]}" if len(offset) >= 5 else offset
                    except:
                        offset_formatted = ""
                    
                    success_text = (
                        f"✅ Часовой пояс установлен!\n\n"
                        f"Город: <b>{city_name}</b>\n"
                        f"Часовой пояс: <b>{timezone_name}</b>"
                    )
                    if offset_formatted:
                        success_text += f"\nСмещение: UTC{offset_formatted}"
                    
                    await query.edit_message_text(
                        success_text,
                        parse_mode='HTML'
                    )
                    
                    # Показываем основную клавиатуру
                    keyboard = get_main_keyboard()
                    msg = await query.message.reply_text(
                        "Выберите действие:",
                        parse_mode='HTML',
                        reply_markup=keyboard
                    )
                    add_message_id(user_id, msg.message_id)
                else:
                    await query.edit_message_text("❌ Ошибка при установке часового пояса")
            except Exception as e:
                logger.error(f"❌ Ошибка при декодировании callback_data: {e}")
                await query.edit_message_text("❌ Ошибка при установке часового пояса")
        
        application.add_handler(CallbackQueryHandler(timezone_set_callback, pattern='^tz_set_'))
        application.add_handler(CallbackQueryHandler(timezone_callback, pattern='^tz_'))
        
        # Обработчик для определения города и предложения установить часовой пояс
        async def handle_city_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Обработчик для определения города и предложения установить часовой пояс"""
            if not update.message or not update.message.text:
                return
            
            # Проверяем, не находимся ли мы в активном ConversationHandler
            # Если context.user_data содержит ключи, связанные с другими ConversationHandler,
            # значит пользователь находится в процессе добавления/редактирования события
            if 'new_event' in context.user_data:
                return  # Пользователь добавляет событие, не обрабатываем как город
            
            text = update.message.text.strip()
            user_id = update.effective_user.id
            
            # Пропускаем команды и кнопки клавиатуры
            excluded = ['что завтра?', 'завтра', 'что сегодня?', 'сегодня', 'послезавтра', 
                       'моё расписание', 'расписание', '➕', '✏️', '🙈']
            if text.lower() in excluded:
                return
            
            # Проверяем, похоже ли сообщение на название города
            if not is_likely_city(text):
                return
            
            # Проверяем доступность библиотек для определения часового пояса
            if not GEOCODING_AVAILABLE:
                return
            
            # Определяем часовой пояс по городу
            timezone_name = get_timezone_by_city(text)
            
            if timezone_name:
                # Получаем информацию о часовом поясе для отображения
                try:
                    tz = ZoneInfo(timezone_name)
                    now = datetime.now(tz)
                    offset = now.strftime('%z')
                    offset_formatted = f"{offset[:3]}:{offset[3:]}" if len(offset) >= 5 else offset
                except:
                    offset_formatted = ""
                
                # Создаем inline-кнопку для подтверждения установки часового пояса
                # Используем base64 для безопасной передачи названия города и часового пояса в callback_data
                timezone_encoded = base64.b64encode(timezone_name.encode('utf-8')).decode('ascii')
                city_encoded = base64.b64encode(text.encode('utf-8')).decode('ascii')
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        f"✅ Установить часовой пояс {timezone_name}" + (f" (UTC{offset_formatted})" if offset_formatted else ""),
                        callback_data=f'tz_set_{timezone_encoded}_{city_encoded}'
                    )
                ]])
                
                msg = await update.message.reply_text(
                    f"📍 Обнаружен город: <b>{text}</b>\n"
                    f"Часовой пояс: <b>{timezone_name}</b>" + 
                    (f"\nСмещение: UTC{offset_formatted}" if offset_formatted else "") +
                    "\n\nУстановить этот часовой пояс?",
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                add_message_id(user_id, msg.message_id)
                return
        
        # Обработчик для показа клавиатуры при любом необработанном сообщении
        # Исключаем кнопки клавиатуры, чтобы они обрабатывались специальными обработчиками
        async def show_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Показывает клавиатуру при любом необработанном сообщении"""
            text = update.message.text.strip() if update.message.text else ""
            # Пропускаем кнопки клавиатуры - они обрабатываются отдельными обработчиками
            if text in ['что завтра?', 'завтра', 'что сегодня?', 'сегодня', 'моё расписание', '➕', '✏️', '🙈']:
                return
            
            keyboard = get_main_keyboard()
            try:
                await update.message.reply_text(" ", reply_markup=keyboard)
            except:
                pass
        
        # Обработчик для определения города и предложения установить часовой пояс
        # Регистрируем перед show_keyboard, чтобы он обрабатывался первым
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_city_input))
        
        # Добавляем обработчик в самом конце для необработанных текстовых сообщений
        # Это обеспечит показ клавиатуры всегда (кроме кнопок)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, show_keyboard))
        
        # Обработчик ошибок для продолжения работы при временных ошибках
        async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Обработчик ошибок"""
            error = context.error
            error_msg = str(error) if error else ""
            
            # Игнорируем ошибки Conflict - они будут обработаны автоматически
            if isinstance(error, Conflict) or "Conflict" in error_msg or "terminated by other getUpdates" in error_msg:
                logger.warning(f"⚠️  Временная ошибка Conflict (будет повторная попытка): {error}")
                # Пытаемся очистить webhook и перезапустить
                try:
                    await application.bot.delete_webhook(drop_pending_updates=True)
                    logger.info("✅ Webhook очищен после Conflict ошибки")
                except:
                    pass
                return
            
            # Обработка RetryAfter - ждем указанное время
            if isinstance(error, RetryAfter):
                logger.warning(f"⚠️  Rate limit: ждем {error.retry_after} секунд")
                await asyncio.sleep(error.retry_after)
                return
            
            # Обработка временных сетевых ошибок
            if isinstance(error, (TimedOut, NetworkError)):
                logger.warning(f"⚠️  Сетевая ошибка (будет повторная попытка): {error}")
                return
            
            # Логируем другие ошибки
            logger.error(f"❌ Ошибка при обработке обновления: {error}", exc_info=error)
        
        application.add_error_handler(error_handler)
        
        # Настройка фоновой задачи для проверки напоминаний (каждую минуту)
        job_queue = application.job_queue
        if job_queue:
            job_queue.run_repeating(send_reminders, interval=60, first=10)  # Проверка каждую минуту, первая через 10 секунд
            logger.info("✅ Система напоминаний активирована")
        
        # Фоновая задача для удаления прошедших событий
        async def cleanup_past_events(context: ContextTypes.DEFAULT_TYPE):
            """Периодическая очистка прошедших событий"""
            try:
                deleted_count = delete_past_events()
                if deleted_count > 0:
                    logger.info(f"🗑️  Удалено прошедших событий: {deleted_count}")
            except Exception as e:
                logger.error(f"❌ Ошибка при очистке прошедших событий: {e}")
        
        if job_queue:
            # Очистка прошедших событий каждые 30 минут для более частой проверки
            job_queue.run_repeating(cleanup_past_events, interval=1800, first=60)  # Каждые 30 минут, первая через 1 минуту
            logger.info("✅ Система автоматической очистки прошедших событий активирована (проверка каждые 30 минут)")
        
        # Health check задача - проверка работоспособности бота
        async def health_check(context: ContextTypes.DEFAULT_TYPE):
            """Периодическая проверка работоспособности бота"""
            try:
                me = await context.bot.get_me()
                logger.debug(f"💚 Health check: бот активен (@{me.username})")
            except Exception as e:
                logger.error(f"❌ Health check failed: {e}")
        
        if job_queue:
            job_queue.run_repeating(health_check, interval=300, first=60)  # Проверка каждые 5 минут
        
        # Запуск бота с обработкой ошибок
        # Принудительная очистка прошедших событий при запуске
        deleted_on_startup = delete_past_events()
        if deleted_on_startup > 0:
            logger.info(f"🗑️  При запуске удалено прошедших событий: {deleted_on_startup}")
        logger.info("🤖 Бот запущен и готов к работе!")
        
        try:
            # Запускаем polling с автоматической очисткой pending updates
            # drop_pending_updates=True автоматически очистит все pending updates при запуске
            # Это также очистит webhook автоматически
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,  # Автоматическая очистка при запуске
                close_loop=False,  # Не закрываем event loop при ошибках
                stop_signals=None  # Обрабатываем сигналы вручную
            )
        except KeyboardInterrupt:
            logger.info("\n👋 Бот остановлен пользователем")
            shutdown_requested = True
        except Conflict as e:
            logger.error(f"\n❌ Обнаружен конфликт: другой экземпляр бота уже запущен")
            logger.info("🔄 Попытка автоматического восстановления...")
            
            # Пытаемся очистить и перезапустить
            import time
            logger.info("💡 Решение:")
            logger.info("   1. Запустите скрипт для принудительной очистки:")
            logger.info("      python3 reset_bot.py")
            logger.info("   2. Подождите 1-2 минуты")
            logger.info("   3. Затем запустите бота заново: python3 bot.py")
            raise
        except Exception as e:
            error_msg = str(e)
            if "Conflict" in error_msg or "terminated by other getUpdates" in error_msg:
                logger.error(f"\n❌ Обнаружен конфликт: {e}")
                logger.info("💡 Решение: Запустите 'python3 reset_bot.py' и подождите 1-2 минуты")
            else:
                logger.error(f"\n❌ Ошибка при запуске бота: {e}")
                import traceback
                traceback.print_exc()
            raise
        finally:
            logger.info("🔄 Завершение работы бота...")
            try:
                # Сохраняем все данные перед завершением
                logger.info("💾 Сохранение данных...")
                # Данные уже сохраняются автоматически при каждом изменении
            except Exception as e:
                logger.error(f"❌ Ошибка при сохранении данных: {e}")
            
            cleanup_lock(lock_file)
            logger.info("✅ Бот завершил работу")
    except Exception as outer_error:
        logger.error(f"❌ Критическая ошибка в main(): {outer_error}", exc_info=True)
        cleanup_lock(lock_file)
        raise


if __name__ == '__main__':
    main()
