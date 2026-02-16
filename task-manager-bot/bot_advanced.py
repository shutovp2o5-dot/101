#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram бот для управления проектами и задачами
Упрощенная версия без кнопок - только команды
"""

import json
import os
import re
import io
import tempfile
import base64
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
    JobQueue
)

# Импорт для работы с часовыми поясами
try:
    import pytz
    TIMEZONE_AVAILABLE = True
except ImportError:
    TIMEZONE_AVAILABLE = False
    print("⚠️  pytz не установлен. Установите: pip3 install pytz")

# Определяем часовой пояс
def get_timezone():
    """Получает часовой пояс системы или использует московское время по умолчанию"""
    if not TIMEZONE_AVAILABLE:
        return None
    
    try:
        # Пробуем определить локальный часовой пояс системы
        import time
        local_tz_name = time.tzname[0] if time.daylight == 0 else time.tzname[1]
        
        # Пробуем получить из переменной окружения
        tz_env = os.environ.get('TZ')
        if tz_env:
            try:
                return pytz.timezone(tz_env)
            except:
                pass
        
        # Пробуем определить по системному времени
        try:
            # Для macOS/Linux
            import subprocess
            result = subprocess.run(['date', '+%Z'], capture_output=True, text=True)
            if result.returncode == 0:
                tz_name = result.stdout.strip()
                # Маппинг распространенных названий
                tz_mapping = {
                    'MSK': 'Europe/Moscow',
                    'MSD': 'Europe/Moscow',
                    'CET': 'Europe/Berlin',
                    'CEST': 'Europe/Berlin',
                    'UTC': 'UTC',
                }
                if tz_name in tz_mapping:
                    return pytz.timezone(tz_mapping[tz_name])
        except:
            pass
        
        # По умолчанию используем московское время (для русскоязычных пользователей)
        return pytz.timezone('Europe/Moscow')
    except Exception as e:
        print(f"Ошибка определения часового пояса: {e}")
        # По умолчанию московское время
        return pytz.timezone('Europe/Moscow') if TIMEZONE_AVAILABLE else None

# Получаем часовой пояс
TZ = get_timezone()

def now():
    """Возвращает текущее время с учетом часового пояса"""
    if TZ:
        return datetime.now(TZ)
    else:
        return datetime.now()

# Опциональные импорты для голосовых сообщений
VOICE_SUPPORT = False
recognizer = None

try:
    import speech_recognition as sr
    from pydub import AudioSegment
    VOICE_SUPPORT = True
    recognizer = sr.Recognizer()
    print("✅ SpeechRecognition и pydub установлены - голосовые сообщения работают!")
except ImportError:
    VOICE_SUPPORT = False
    print("⚠️  SpeechRecognition и pydub не установлены")
    print("   Для работы голосовых сообщений выполните:")
    print("   pip3 install SpeechRecognition pydub")
    print("   brew install ffmpeg")

# Состояния для ConversationHandler
(WAITING_TASK_TITLE, WAITING_TASK_COMMENT, WAITING_TASK_PROJECT, WAITING_TASK_DEADLINE, WAITING_TASK_REMINDER, WAITING_TASK_RECURRENCE, WAITING_TASK_CATEGORY) = range(7)
(WAITING_PROJECT_NAME, WAITING_PROJECT_TYPE, WAITING_PROJECT_TARGET_TASKS, WAITING_PROJECT_PRIORITY, WAITING_PROJECT_END_DATE, WAITING_PROJECT_CATEGORY) = range(7, 13)
(WAITING_TASK_COMPLETE_CONFIRM, WAITING_TASK_RESCHEDULE) = range(13, 15)
(WAITING_EDIT_TASK_SELECT, WAITING_EDIT_FIELD_SELECT, WAITING_EDIT_TITLE, WAITING_EDIT_COMMENT, WAITING_EDIT_PROJECT, WAITING_EDIT_DEADLINE, WAITING_EDIT_REMINDER, WAITING_EDIT_RECURRENCE) = range(15, 23)
(WAITING_EDIT_PROJECT_TARGET_TASKS, WAITING_PROJECT_COMPLETE_CONFIRM, WAITING_EDIT_PROJECT_NAME) = range(23, 26)

# Файл для хранения данных
DATA_FILE = 'tasks_data.json'



def capitalize_first(text: str) -> str:
    """Редактура: делает первую букву заглавной"""
    if not text:
        return text
    return text[0].upper() + text[1:] if len(text) > 1 else text.upper()


def normalize_voice_text(text: str) -> str:
    """Нормализует текст после распознавания голоса для лучшего парсинга дедлайнов"""
    if not text:
        return text
    
    # Сохраняем оригинал для логирования
    original = text
    
    # Приводим к нижнему регистру для обработки
    normalized = text.lower().strip()
    
    # Словарь замен для исправления частых ошибок распознавания
    replacements = [
        # Относительные даты - исправление вариантов написания
        (r'\bзавтрашний день\b', 'завтра'),
        (r'\bзавтрашний\b', 'завтра'),
        (r'\bпосле завтра\b', 'послезавтра'),
        (r'\bпосле завтрашний\b', 'послезавтра'),
        (r'\bпослезавтрашний\b', 'послезавтра'),
        (r'\bпосле завтрашнего дня\b', 'послезавтра'),
        (r'\bсегодняшний день\b', 'сегодня'),
        (r'\bсегодняшний\b', 'сегодня'),
        
        # Время суток - исправление падежей
        (r'\bутром\b', 'утра'),
        (r'\bднем\b', 'дня'),
        (r'\bднём\b', 'дня'),
        (r'\bвечером\b', 'вечера'),
        (r'\bночью\b', 'ночи'),
        
        # Время - исправление форматов "16 часов 30" -> "16:30"
        (r'\b(\d{1,2})\s*часов\s*(\d{1,2})\s*(?:минут?|м)?\b', r'\1:\2'),
        (r'\b(\d{1,2})\s*час\s*(\d{1,2})\s*(?:минут?|м)?\b', r'\1:\2'),
        (r'\b(\d{1,2})\s*ч\s*(\d{1,2})\s*(?:минут?|м)?\b', r'\1:\2'),
        (r'\b(\d{1,2})\s*часа\s*(\d{1,2})\s*(?:минут?|м)?\b', r'\1:\2'),
        # "16 часов" -> "16:00"
        (r'\b(\d{1,2})\s+часов\b', r'\1:00'),
        (r'\b(\d{1,2})\s+час\b', r'\1:00'),
        (r'\b(\d{1,2})\s+ч\b', r'\1:00'),
        
        # Убираем лишние слова перед временем
        (r'\bвремя\s+(\d{1,2}[:.]?\d{0,2})\b', r'\1'),
        (r'\bв\s+(\d{1,2}[:.]?\d{0,2})\s+часов\b', r'в \1'),
        (r'\bв\s+(\d{1,2}[:.]?\d{0,2})\s+час\b', r'в \1'),
        (r'\bв\s+(\d{1,2}[:.]?\d{0,2})\s+часа\b', r'в \1'),
        
        # Нормализация пробелов вокруг двоеточий и точек
        (r'\s*:\s*', ':'),
        (r'\s*\.\s*', '.'),
        
        # Исправление времени без разделителя: "16 00" -> "16:00" (только если это похоже на время)
        (r'\bв\s+(\d{1,2})\s+(\d{2})\b', r'в \1:\2'),  # "в 16 00" -> "в 16:00"
        (r'\b(\d{1,2})\s+(\d{2})\s+(часов?|ч|минут?|м|утра|дня|вечера|ночи|завтра|сегодня|послезавтра)', r'\1:\2 \3'),
        (r'\b(\d{1,2})\s+(\d{2})\s*$', r'\1:\2'),  # В конце строки "16 00" -> "16:00"
        # "16 30" после даты -> "16:30"
        (r'\b(завтра|сегодня|послезавтра|\d{1,2}\s+(?:январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья]))\s+(\d{1,2})\s+(\d{2})\b', r'\1 в \2:\3'),
        # "16 30" в любом месте -> "16:30" (если похоже на время)
        (r'\b(\d{1,2})\s+(\d{2})\b(?!\s*(?:январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр|дня|недел|месяц))', r'\1:\2'),
        
        # Исправление порядка: время перед датой -> дата перед временем
        (r'\bв\s+(\d{1,2}[:.]?\d{0,2})\s+(завтра|сегодня|послезавтра)\b', r'\2 в \1'),
        (r'\b(\d{1,2}[:.]\d{2})\s+(завтра|сегодня|послезавтра)\b', r'\2 в \1'),
        (r'\b(\d{1,2})\s+(завтра|сегодня|послезавтра)\b', r'\2 в \1'),
        
        # Исправление "в" перед временем суток
        (r'\bв\s+(\d{1,2})\s+(утра|дня|вечера|ночи)\b', r'\1 \2'),
        (r'\bв\s+(\d{1,2})\s+часа?\s+(утра|дня|вечера|ночи)\b', r'\1 \2'),
        
        # Исправление написания чисел прописью (если распознано неправильно)
        (r'\bодиннадцать\b', 'одиннадцать'),
        (r'\bдвенадцать\b', 'двенадцать'),
        (r'\bтринадцать\b', 'тринадцать'),
        (r'\bчетырнадцать\b', 'четырнадцать'),
        (r'\bпятнадцать\b', 'пятнадцать'),
        (r'\bшестнадцать\b', 'шестнадцать'),
        (r'\bсемнадцать\b', 'семнадцать'),
        (r'\bвосемнадцать\b', 'восемнадцать'),
        (r'\bдевятнадцать\b', 'девятнадцать'),
        (r'\bдвадцать\b', 'двадцать'),
        
        # Исправление "через" - убираем лишние пробелы
        (r'\bчерез\s+(\d+)\s+(дня|дней|день)\b', r'через \1 дня'),
        (r'\bчерез\s+(\d+)\s+(неделю|недели|недель)\b', r'через \1 недели'),
        
        # Исправление дат - убираем "-го", "-е" и т.д.
        (r'\b(\d{1,2})[-гое]\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])', r'\1 \2'),
        (r'\b(\d{1,2})\s+[-гое]\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])', r'\1 \2'),
        
        # Убираем лишние знаки препинания, которые могут мешать
        (r'[,;]\s*', ' '),
    ]
    
    # Применяем замены последовательно
    try:
        for pattern, replacement in replacements:
            try:
                normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
            except re.error as e:
                print(f"⚠️ Ошибка в регулярном выражении '{pattern}': {e}")
                # Пропускаем проблемное выражение и продолжаем
                continue
    except Exception as e:
        print(f"❌ Критическая ошибка при нормализации текста: {e}")
        import traceback
        traceback.print_exc()
        # Возвращаем оригинальный текст, если нормализация не удалась
        return capitalize_first(original)
    
    # Нормализуем множественные пробелы
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Убираем пробелы в начале и конце
    normalized = normalized.strip()
    
    # Восстанавливаем первую заглавную букву
    normalized = capitalize_first(normalized)
    
    if original != normalized:
        print(f"Нормализованный текст после распознавания голоса: '{original}' -> '{normalized}'")
    return normalized


def format_date_readable(date_dt: datetime) -> str:
    """Форматирует дату в читаемый формат: сегодня/завтра/10 февраля"""
    current_time = now()
    if current_time.tzinfo:
        current_time = current_time.replace(tzinfo=None)
    if date_dt.tzinfo:
        date_dt = date_dt.replace(tzinfo=None)
    
    now_dt = current_time
    today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    date_start = date_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    
    days_diff = (date_start - today_start).days
    
    if days_diff == 0:
        return "сегодня"
    elif days_diff == 1:
        return "завтра"
    elif days_diff == 2:
        return "послезавтра"
    else:
        day = date_dt.day
        month = months[date_dt.month]
        return f"{day} {month}"


def format_date_full(date_dt: datetime) -> str:
    """Форматирует дату в полном формате: сегодня, 8 февраля, вскр"""
    current_time = now()
    if current_time.tzinfo:
        current_time = current_time.replace(tzinfo=None)
    if date_dt.tzinfo:
        date_dt = date_dt.replace(tzinfo=None)
    
    now_dt = current_time
    today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    date_start = date_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    
    weekdays_short = {
        0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'
    }
    
    days_diff = (date_start - today_start).days
    
    # Относительная дата
    if days_diff == 0:
        relative_date = "сегодня"
    elif days_diff == 1:
        relative_date = "завтра"
    elif days_diff == 2:
        relative_date = "послезавтра"
    else:
        relative_date = None
    
    # Полная дата
    day = date_dt.day
    month = months[date_dt.month]
    full_date = f"{day} {month}"
    
    # День недели
    weekday = weekdays_short[date_dt.weekday()]
    
    # Формируем результат
    if relative_date:
        return f"{relative_date}, {full_date}, {weekday}"
    else:
        return f"{full_date}, {weekday}"


def format_deadline_readable(deadline_dt: datetime) -> str:
    """Форматирует дедлайн в читаемый формат: завтра/10 февраля/12 февраля + время"""
    # Получаем текущее время с учетом часового пояса
    current_time = now()
    
    # Приводим к naive datetime для сравнения (если есть часовой пояс, убираем его)
    if current_time.tzinfo:
        current_time = current_time.replace(tzinfo=None)
    if deadline_dt.tzinfo:
        deadline_dt = deadline_dt.replace(tzinfo=None)
    
    now_dt = current_time
    today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    deadline_start = deadline_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Названия месяцев
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    
    # Вычисляем разницу в днях
    days_diff = (deadline_start - today_start).days
    
    # Форматируем время
    time_str = deadline_dt.strftime('%H:%M')
    
    # Определяем формат даты
    if days_diff == 0:
        date_str = "сегодня"
    elif days_diff == 1:
        date_str = "завтра"
    elif days_diff == 2:
        date_str = "послезавтра"
    else:
        # Формат: "10 февраля"
        day = deadline_dt.day
        month = months[deadline_dt.month]
        date_str = f"{day} {month}"
    
    # Если время не 23:59 (конец дня), добавляем его
    if deadline_dt.hour == 23 and deadline_dt.minute == 59:
        return date_str
    else:
        return f"{date_str} {time_str}"


async def transcribe_voice(voice_file, update: Update = None) -> Optional[str]:
    """Транскрибация голосового сообщения в текст"""
    # Сначала пробуем использовать caption от Telegram (если есть)
    if update and update.message.voice:
        if update.message.caption:
            print(f"✅ Использован caption от Telegram: {update.message.caption}")
            # Нормализуем caption для лучшего парсинга дедлайнов
            return normalize_voice_text(update.message.caption)
    
    # Проверяем доступность библиотек
    if not VOICE_SUPPORT:
        print("❌ VOICE_SUPPORT = False")
        return None
    
    if recognizer is None:
        print("❌ recognizer is None")
        return None
    
    print("✅ Библиотеки для распознавания доступны")
    
    temp_ogg = None
    temp_wav = None
    try:
        # Скачиваем файл
        print("Скачивание голосового файла...")
        file_content = await voice_file.download_as_bytearray()
        
        if not file_content or len(file_content) == 0:
            print("Ошибка: файл пустой")
            return None
        
        print(f"Файл скачан, размер: {len(file_content)} байт")
        
        # Создаем временные файлы
        temp_ogg = tempfile.NamedTemporaryFile(delete=False, suffix='.ogg').name
        with open(temp_ogg, 'wb') as f:
            f.write(file_content)
        
        print("Конвертация OGG в WAV...")
        # Конвертируем OGG в WAV с оптимизированными параметрами
        try:
            # Используем более быстрый метод конвертации
            # Пробуем сначала from_ogg, если не получается - from_file
            try:
                audio = AudioSegment.from_ogg(temp_ogg)
            except:
                print("Попытка конвертации через from_file...")
                audio = AudioSegment.from_file(temp_ogg, format="ogg")
            
            # Оптимизируем аудио для быстрого распознавания
            # Моно, 16kHz - оптимально для распознавания речи
            print("Оптимизация аудио (моно, 16kHz)...")
            audio = audio.set_channels(1).set_frame_rate(16000)
            
            temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix='.wav').name
            # Используем быстрый экспорт с параметрами для ffmpeg
            print("Экспорт в WAV...")
            try:
                audio.export(temp_wav, format="wav", parameters=["-ac", "1", "-ar", "16000"])
            except:
                # Если не получилось с параметрами, пробуем без них
                print("Экспорт без дополнительных параметров...")
                audio.export(temp_wav, format="wav")
            print("✅ Конвертация завершена")
        except Exception as e:
            print(f"❌ Ошибка конвертации аудио: {e}")
            import traceback
            traceback.print_exc()
            return None
        
        # Используем распознаватель речи
        print("Распознавание речи...")
        try:
            with sr.AudioFile(temp_wav) as source:
                # Читаем весь файл без дополнительной обработки
                audio_data = recognizer.record(source)
            
            # Распознаем речь с таймаутом
            print("Отправка запроса к Google Speech API...")
            try:
                # Используем show_all=False для более быстрого ответа
                text = recognizer.recognize_google(audio_data, language='ru-RU', show_all=False)
                print(f"Распознано через SpeechRecognition: {text}")
                # Нормализуем текст для лучшего парсинга дедлайнов
                normalized_text = normalize_voice_text(text)
                return normalized_text
            except sr.UnknownValueError:
                print("Не удалось распознать речь - Google не смог распознать аудио")
                return None
            except sr.RequestError as e:
                print(f"Ошибка запроса к сервису распознавания: {e}")
                print("Возможные причины: проблемы с интернетом или недоступность Google Speech API")
                return None
        except Exception as e:
            print(f"Ошибка при чтении аудио файла: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    except Exception as e:
        print(f"Общая ошибка обработки голосового сообщения: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        # Удаляем временные файлы
        try:
            if temp_ogg and os.path.exists(temp_ogg):
                os.unlink(temp_ogg)
                print(f"Удален временный файл: {temp_ogg}")
            if temp_wav and os.path.exists(temp_wav):
                os.unlink(temp_wav)
                print(f"Удален временный файл: {temp_wav}")
        except Exception as e:
            print(f"Ошибка удаления временных файлов: {e}")


def load_data() -> Dict:
    """Загрузка данных из файла"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {'users': {}, 'projects': {}}
    return {'users': {}, 'projects': {}}


def save_data(data: Dict):
    """Сохранение данных в файл"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user_tasks(user_id: str) -> List[Dict]:
    """Получение задач пользователя"""
    data = load_data()
    return data.get('users', {}).get(str(user_id), {}).get('tasks', [])


def get_user_projects(user_id: str) -> List[str]:
    """Получение проектов пользователя (только активных, не завершенных)"""
    try:
        data = load_data()
        data_changed = False
        user_data = data.get('users', {}).get(str(user_id), {})
        if not user_data:
            return []
        
        projects_data = user_data.get('projects_data', {})
        if not projects_data:
            return []
        
        active_projects = []
        for project_name, project_data in projects_data.items():
            if isinstance(project_data, dict):
                if not project_data.get('completed', False):
                    active_projects.append(project_name)
            else:
                # Если project_data не словарь, все равно добавляем проект
                active_projects.append(project_name)
        return active_projects
    except Exception as e:
        print(f"Ошибка в get_user_projects для user_id {user_id}: {e}")
        import traceback
        traceback.print_exc()
        return []


def update_user_project(user_id: str, project_name: str, updates: Dict):
    """Обновление проекта пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if 'users' not in data or user_id_str not in data['users']:
        return False
    
    projects_data = data['users'][user_id_str].get('projects_data', {})
    if project_name in projects_data:
        projects_data[project_name].update(updates)
        save_data(data)
        return True
    return False


def rename_user_project(user_id: str, old_name: str, new_name: str):
    """Переименование проекта пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if 'users' not in data or user_id_str not in data['users']:
        return False
    
    projects_data = data['users'][user_id_str].get('projects_data', {})
    if old_name not in projects_data:
        return False
    
    # Проверяем, что новое имя не занято
    if new_name in projects_data:
        return False
    
    # Сохраняем данные проекта
    project_data = projects_data[old_name]
    
    # Удаляем старое имя и добавляем новое
    del projects_data[old_name]
    projects_data[new_name] = project_data
    
    # Обновляем все задачи, которые ссылаются на этот проект
    tasks = data['users'][user_id_str].get('tasks', [])
    for task in tasks:
        if task.get('project') == old_name:
            task['project'] = new_name
    
    # Обновляем список projects, если он используется
    projects_list = data['users'][user_id_str].get('projects', [])
    if old_name in projects_list:
        projects_list.remove(old_name)
        if new_name not in projects_list:
            projects_list.append(new_name)
    
    save_data(data)
    return True


def save_user_task(user_id: str, task: Dict):
    """Сохранение задачи пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if 'users' not in data:
        data['users'] = {}
    if user_id_str not in data['users']:
        data['users'][user_id_str] = {'tasks': [], 'projects': [], 'tags': [], 'projects_data': {}}
    
    data['users'][user_id_str]['tasks'].append(task)
    save_data(data)


def update_user_task(user_id: str, task_id: str, updates: Dict):
    """Обновление задачи пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if 'users' not in data or user_id_str not in data['users']:
        return False
    
    tasks = data['users'][user_id_str]['tasks']
    for i, task in enumerate(tasks):
        if task.get('id') == task_id:
            # Сохраняем источник из оригинальной задачи, если он не указан в обновлении
            if 'source' not in updates and 'source' in task:
                updates['source'] = task['source']
            # Если источника нет ни в оригинале, ни в обновлении, устанавливаем по умолчанию
            if 'source' not in updates:
                updates['source'] = 'tasks'
            tasks[i].update(updates)
            save_data(data)
            return True
    return False


def delete_user_task(user_id: str, task_id: str) -> bool:
    """Удаление задачи пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str not in data.get('users', {}):
        return False
    tasks = data['users'][user_id_str]['tasks']
    new_tasks = [t for t in tasks if str(t.get('id')) != str(task_id)]
    if len(new_tasks) == len(tasks):
        return False
    data['users'][user_id_str]['tasks'] = new_tasks
    save_data(data)
    return True


def get_user_task_by_id(user_id: str, task_id: str) -> Optional[Dict]:
    """Получение задачи по ID"""
    tasks = get_user_tasks(str(user_id))
    for task in tasks:
        if task.get('id') == task_id:
            return task
    return None


def add_user_project(user_id: str, project_name: str, category: str = None, project_type: str = None, target_tasks: int = None, priority: int = None, end_date: str = None):
    """Добавление проекта пользователя
    
    Args:
        user_id: ID пользователя
        project_name: Название проекта
        category: Категория проекта (опционально)
        project_type: Тип проекта ('software' или 'project')
        target_tasks: Количество запланированных задач (для проектных проектов)
        priority: Приоритет проекта (1, 2, 3) - только для проектных проектов
        end_date: Дата окончания проекта в формате YYYY-MM-DD (опционально)
    """
    data = load_data()
    user_id_str = str(user_id)
    
    if 'users' not in data:
        data['users'] = {}
    if user_id_str not in data['users']:
        data['users'][user_id_str] = {'tasks': [], 'projects': [], 'tags': [], 'projects_data': {}}
    
    if 'projects_data' not in data['users'][user_id_str]:
        data['users'][user_id_str]['projects_data'] = {}
    
    if project_name not in data['users'][user_id_str]['projects_data']:
        project_data = {}
        if category:
            project_data['category'] = category
        if project_type:
            project_data['type'] = project_type
        if target_tasks is not None:
            project_data['target_tasks'] = target_tasks
        if priority is not None:
            project_data['priority'] = priority
        if end_date:
            project_data['end_date'] = end_date
        data['users'][user_id_str]['projects_data'][project_name] = project_data
        save_data(data)
        return True
    return False


def get_user_project_categories(user_id: str) -> List[str]:
    """Получение категорий проектов пользователя"""
    data = load_data()
    projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
    categories = set()
    for project_data in projects_data.values():
        if 'category' in project_data:
            categories.add(project_data['category'])
    return sorted(list(categories))


def get_main_keyboard():
    """Главное меню с кнопками"""
    keyboard = [
        [KeyboardButton("Добавить задачу"), KeyboardButton("Список задач")],
        [KeyboardButton("Статистика"), KeyboardButton("Проекты")],
        [KeyboardButton("Редактировать")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def extract_deadline_from_text(text: str) -> tuple[str, Optional[datetime]]:
    """Извлекает дедлайн из текста и возвращает (очищенный_текст, дедлайн)"""
    text_lower = text.lower().strip()
    original_text = text
    print(f"Извлечение дедлайна из текста: '{text}'")
    
    # Словарь числительных прописью для использования в паттернах
    # Важно: сначала идут более длинные фразы (многословные), потом короткие
    number_words_list = ['двадцать три', 'двадцать две', 'двадцать два', 'двадцать одна', 'двадцать один',
                         'двадцать', 'девятнадцать', 'восемнадцать', 'семнадцать', 'шестнадцать',
                         'пятнадцать', 'четырнадцать', 'тринадцать', 'двенадцать', 'одиннадцать',
                         'десять', 'девять', 'восемь', 'семь', 'шесть', 'пять', 'четыре',
                         'три', 'две', 'два', 'одну', 'одна', 'один']
    number_words_pattern = '|'.join(number_words_list)
    
    # Список паттернов для поиска дедлайна (от более специфичных к менее специфичным)
    # Каждый паттерн - это кортеж (regex_pattern, priority)
    # priority: чем выше, тем раньше проверяется
    deadline_patterns = [
        # "в 16:00 послезавтра", "в 16:00 завтра", "в 16:00 сегодня" - время перед датой
        (r'\bв\s+(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?\s+(завтра|сегодня|послезавтра)\b', 13),
        # "завтра в 7 утра", "сегодня в 2 часа дня", "послезавтра в шесть вечера"
        (r'\b(завтра|сегодня|послезавтра)\s+(?:в\s+)?(?:(\d{1,2})|(' + number_words_pattern + r'))(?:\s+часа?)?\s+(утра|дня|вечера|ночи)\b', 12),
        # "7 утра", "2 часа дня", "шесть вечера" - в любом месте текста
        (r'\b(?:(\d{1,2})|(' + number_words_pattern + r'))(?:\s+часа?)?\s+(утра|дня|вечера|ночи)\b', 11),
        # "завтра в 14", "сегодня в 19:30", "послезавтра в 15" - в любом месте текста
        (r'\b(завтра|сегодня|послезавтра)\s+в\s+(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?\b', 10),
        # "вторник 14:00", "понедельник в 10:30", "пт 18:00" - день недели + время
        (r'\b(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|пн|вт|ср|чт|пт|сб|вс)\s+(?:в\s+)?(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?\b', 10),
        # "15 февраля в 14:00", "15 февраля в 14", "16 февраля 2026 в 15:30" - дата с временем
        (r'\b(\d{1,2})\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])(?:\s+(\d{4}))?\s+в\s+(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?\b', 10),
        # "25.01.2026 18:00", "25.01.2026 в 18:00" - дата с временем
        (r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(?:в\s+)?(\d{1,2})[:.](\d{2}))?\b', 9),
        # "25/01/2026 18:00", "25/01/2026 в 18:00"
        (r'\b(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(?:в\s+)?(\d{1,2})[:.](\d{2}))?\b', 9),
        # "завтра 14", "сегодня 19:30" (без "в") - в любом месте текста
        (r'\b(завтра|сегодня|послезавтра)\s+(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?\b', 8),
        # "15 февраля", "16 февраля 2026" - формат с названием месяца (без времени)
        (r'\b(\d{1,2})\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])(?:\s+(\d{4}))?\b', 7),
        # "через неделю", "через 3 дня"
        (r'\bчерез\s+(\d+)\s+(недел[ияю]?|дн[яей]|месяц[аев]?)\b', 6),
        # "через неделю" (без числа)
        (r'\bчерез\s+недел[юя]\b', 5),
        # "в 19:00", "в 19" - только если перед этим есть дата или в конце текста
        (r'(?:\d{1,2}\s+(?:январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])|завтра|сегодня|послезавтра|\d{1,2}[./]\d{1,2})\s+в\s+(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?\b', 4),
        # "сегодня", "завтра", "послезавтра" (отдельно) - в любом месте текста
        (r'\b(сегодня|завтра|послезавтра)\b', 3),
        # "19:00", "19.00" (в конце текста, как время) - только формат времени с разделителем
        (r'\b([0-2]?[0-9])[:.](\d{2})\s*$', 2),  # Только формат времени с разделителем
    ]
    
    # Сортируем по приоритету (от большего к меньшему)
    deadline_patterns.sort(key=lambda x: x[1], reverse=True)
    
    # Пробуем найти дедлайн, начиная с более специфичных паттернов
    for pattern, priority in deadline_patterns:
        match = re.search(pattern, text_lower)
        if match:
            # Извлекаем найденный фрагмент
            found_text = match.group(0).strip()
            print(f"Найден паттерн (приоритет {priority}): '{found_text}' по шаблону '{pattern}'")
            
            # Для паттернов с "в" и временем проверяем контекст
            # Если это паттерн типа "(?:^|\s)в\s+", проверяем, что перед ним есть дата
            if r'(?:^|\s)в\s+' in pattern and not r'\d{1,2}\s+(?:январ' in pattern:
                # Проверяем, что перед "в" есть относительная дата или дата с месяцем
                before_match = text_lower[:match.start()]
                after_match = text_lower[match.end():]
                
                # Если после времени есть слова типа "часов", "минут", "утра", "вечера" - это не дедлайн
                if re.search(r'\b(часов?|минут|утра|вечера|дня|ночи)\b', after_match):
                    continue
                
                # Проверяем, что перед "в" есть дата (относительная или с месяцем)
                has_date_before = (
                    re.search(r'\b(завтра|сегодня|послезавтра)\s*$', before_match) or
                    re.search(r'\d{1,2}\s+(?:январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])\s*$', before_match) or
                    re.search(r'\d{1,2}[./]\d{1,2}(?:[./]\d{4})?\s*$', before_match)
                )
                
                if not has_date_before and match.end() < len(text_lower):
                    # Пропускаем этот паттерн, если нет даты перед "в" и это не в конце
                    continue
            
            # Для относительных дат без времени проверяем, что после них нет времени
            # (чтобы не дублировать с паттернами выше, которые уже обработали "завтра в 14")
            if pattern == r'\b(сегодня|завтра|послезавтра)\b':
                after_match = text_lower[match.end():].strip()
                # Если после относительной даты есть время (число или "в" + число), пропускаем
                # так как это уже обработано паттернами выше с более высоким приоритетом
                if re.match(r'^\s*(в\s+)?\d', after_match):
                    print(f"Пропускаем '{found_text}' - после неё есть время, которое обработано паттерном выше")
                    continue
            
            # Если найденная дата не содержит время, проверяем, есть ли время сразу после неё
            # Это нужно для случаев типа "15 февраля в 14:00", где паттерн может найти только "15 февраля"
            extended_text = found_text
            extended_end = match.end()
            
            # Проверяем, содержит ли найденный текст время суток (утра, дня, вечера, ночи)
            # Если да, то не нужно искать дополнительное время
            has_time_of_day = re.search(r'\b(утра|дня|вечера|ночи)\b', found_text.lower())
            
            # Проверяем, есть ли время после найденной даты
            # Это нужно для случаев, когда паттерн находит только дату, а время идет отдельно
            if not has_time_of_day and 'в' not in found_text.lower() and not re.search(r'\d{1,2}[:.]\d{2}', found_text):
                after_match_text = text_lower[match.end():]
                # Ищем паттерн времени после даты: "в 14:00", "в 14", "14:00", "в 7 утра"
                # Более гибкий паттерн для поиска времени, включая варианты с пробелами и без разделителей
                time_patterns = [
                    r'^\s+в\s+(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?\b',  # "в 14:00" или "в 14"
                    r'^\s+(\d{1,2})(?:\s*[:.]\s*(\d{2}))\b',  # "14:00" или "14.00"
                    r'^\s+(\d{1,2})\s+(\d{2})\b',  # "14 00" (время без разделителя)
                    r'^\s+(?:в\s+)?(?:(\d{1,2})|(' + number_words_pattern + r'))(?:\s+часа?)?\s+(утра|дня|вечера|ночи)\b',  # "в 7 утра", "2 часа дня"
                    r'^\s+(\d{1,2})\s+(?:часов?|ч|часа)\s*(?:(\d{1,2})\s*(?:минут?|м|минуты))?\b',  # "14 часов", "14 часов 30 минут"
                    r'^\s+(\d{1,2})\s+(?:часов?|ч)\s*(?:(\d{1,2}))?\b',  # "14 часов 30", "14 часов"
                ]
                
                for time_pattern in time_patterns:
                    time_after_match = re.match(time_pattern, after_match_text)
                    if time_after_match:
                        # Найдено время после даты, добавляем его к найденному тексту
                        time_text = time_after_match.group(0).strip()
                        
                        # Нормализуем формат времени для правильного парсинга
                        # "14 00" -> "14:00"
                        time_text_normalized = re.sub(r'(\d{1,2})\s+(\d{2})\b', r'\1:\2', time_text)
                        # "14 часов 30" -> "14:30"
                        time_text_normalized = re.sub(r'(\d{1,2})\s+часов?\s+(\d{1,2})\b', r'\1:\2', time_text_normalized)
                        # "14 часов" -> "14:00"
                        time_text_normalized = re.sub(r'(\d{1,2})\s+часов?\b', r'\1:00', time_text_normalized)
                        
                        # Если в паттерне нет "в", добавляем его для правильного парсинга
                        if not time_text_normalized.startswith('в') and not re.search(r'\b(утра|дня|вечера|ночи)\b', time_text_normalized):
                            extended_text = found_text + ' в ' + time_text_normalized
                        else:
                            extended_text = found_text + ' ' + time_text_normalized
                        # Правильно вычисляем позицию конца расширенного текста
                        extended_end = match.end() + time_after_match.end()
                        print(f"Найдено время после даты: '{time_text}' -> '{time_text_normalized}', расширенный текст: '{extended_text}'")
                        break
            
            # Пробуем распарсить найденный фрагмент (возможно расширенный) как дедлайн
            # Обрезаем пробелы, чтобы паттерны в parse_deadline работали правильно
            extended_text_clean = extended_text.strip()
            deadline_dt = parse_deadline(extended_text_clean)
            
            if deadline_dt:
                # Удаляем найденный фрагмент (возможно расширенный) из текста
                start_pos = match.start()
                end_pos = extended_end
                
                # Удаляем найденный фрагмент и очищаем пробелы
                before_text = text[:start_pos].strip()
                after_text = text[end_pos:].strip()
                
                cleaned_text = (before_text + ' ' + after_text).strip()
                cleaned_text = re.sub(r'\s+', ' ', cleaned_text)  # Убираем множественные пробелы
                cleaned_text = cleaned_text.strip()
                
                # Если текст стал пустым, оставляем исходный текст без дедлайна
                if not cleaned_text:
                    print(f"Текст стал пустым после удаления дедлайна, возвращаем исходный текст")
                    return text, None
                
                print(f"Дедлайн найден: '{found_text}' -> {deadline_dt}, очищенный текст: '{cleaned_text}'")
                return cleaned_text, deadline_dt
    
    # Если дедлайн не найден, возвращаем исходный текст
    print(f"Дедлайн не найден в тексте: '{text}'")
    return text, None


def parse_deadline(deadline_str: str, deadline=None) -> Optional[datetime]:
    """Парсинг дедлайна из строки - поддерживает множество форматов"""
    deadline_str = deadline_str.strip().lower()
    # Нормализуем пробелы вокруг двоеточий и точек в времени
    deadline_str = re.sub(r'\s*([:.])\s*', r'\1', deadline_str)  # Убираем пробелы вокруг : и .
    deadline_str = re.sub(r'\s+', ' ', deadline_str)  # Убираем множественные пробелы
    current_time = now()
    # Приводим к naive datetime для работы
    if current_time.tzinfo:
        now_dt = current_time.replace(tzinfo=None)
    else:
        now_dt = current_time
    
    # Словарь числительных прописью
    number_words = {
        'один': 1, 'одна': 1, 'одну': 1,
        'два': 2, 'две': 2,
        'три': 3,
        'четыре': 4,
        'пять': 5,
        'шесть': 6,
        'семь': 7,
        'восемь': 8,
        'девять': 9,
        'десять': 10,
        'одиннадцать': 11,
        'двенадцать': 12,
        'тринадцать': 13,
        'четырнадцать': 14,
        'пятнадцать': 15,
        'шестнадцать': 16,
        'семнадцать': 17,
        'восемнадцать': 18,
        'девятнадцать': 19,
        'двадцать': 20,
        'двадцать один': 21, 'двадцать одна': 21,
        'двадцать два': 22, 'двадцать две': 22,
        'двадцать три': 23,
    }
    
    # Паттерны для времени суток: "7 утра", "2 часа дня", "шесть вечера"
    time_of_day_patterns = [
        # "7 утра", "шесть утра", "2 часа утра"
        (r'^(?:(\d{1,2})|(' + '|'.join(number_words.keys()) + r'))(?:\s+часа?)?\s+(утра|ночи)\b', 'morning'),
        # "2 часа дня", "шесть дня", "14 дня"
        (r'^(?:(\d{1,2})|(' + '|'.join(number_words.keys()) + r'))(?:\s+часа?)?\s+дня\b', 'afternoon'),
        # "шесть вечера", "7 вечера", "2 часа вечера"
        (r'^(?:(\d{1,2})|(' + '|'.join(number_words.keys()) + r'))(?:\s+часа?)?\s+вечера\b', 'evening'),
        # "12 ночи", "полночь"
        (r'^(?:(\d{1,2})|(' + '|'.join(number_words.keys()) + r'))(?:\s+часа?)?\s+ночи\b', 'night'),
    ]
    
    # Пробуем распарсить время суток
    for pattern, time_type in time_of_day_patterns:
        match = re.match(pattern, deadline_str)
        if match:
            hour_str = match.group(1) if match.group(1) else None
            word_str = match.group(2) if match.group(2) else None
            
            if hour_str:
                hour = int(hour_str)
            elif word_str:
                hour = number_words.get(word_str)
            else:
                continue
            
            if hour is None:
                continue
            
            # Преобразуем час в зависимости от времени суток
            if time_type == 'morning':
                # "утра" = 0-11 часов (AM)
                if hour > 11:
                    hour = hour % 12
            elif time_type == 'afternoon':
                # "дня" = 12-17 часов (PM, после полудня)
                # Если указано 1-5, то это 13-17
                # Если указано 12, то это 12
                if hour == 0:
                    hour = 12  # "0 часов дня" = полдень
                elif hour < 12:
                    hour = hour + 12  # 1-11 -> 13-23
                    # Ограничиваем до 17 для "дня" (если больше, значит это вечер)
                    if hour > 17:
                        hour = hour - 12
                # Если уже 12-17, оставляем как есть
            elif time_type == 'evening':
                # "вечера" = 18-23 часов (PM, вечер)
                # Если указано 6-11, то это 18-23
                if hour < 12:
                    hour = hour + 12  # 6-11 -> 18-23
                # Если уже 12-23, оставляем как есть
            elif time_type == 'night':
                # "ночи" = 0-5 часов (AM, ночь)
                if hour >= 12:
                    hour = hour % 12
                # Если уже 0-5, оставляем как есть
            
            # Ограничиваем час в диапазоне 0-23
            hour = hour % 24
            
            # Если время уже прошло сегодня, переносим на завтра
            result = now_dt.replace(hour=hour, minute=0, second=0, microsecond=0)
            if result < now_dt:
                result += timedelta(days=1)
            return result
    
    # Паттерн для формата "в 16:00 послезавтра", "в 16:00 завтра", "в 16:00 сегодня" (время перед датой)
    time_before_date_pattern = r'^в\s+(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?\s+(завтра|сегодня|послезавтра)$'
    match = re.match(time_before_date_pattern, deadline_str)
    if match:
        hour = int(match.group(1))
        minute_str = match.group(2)
        date_word = match.group(3)
        
        minute = int(minute_str) if minute_str else 0
        
        print(f"Парсинг формата 'время перед датой': '{deadline_str}' -> час={hour}, минута={minute}, дата={date_word}")
        
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            days_offset = 0
            if date_word == 'завтра':
                days_offset = 1
            elif date_word == 'послезавтра':
                days_offset = 2
            # 'сегодня' -> days_offset = 0
            
            result = (now_dt + timedelta(days=days_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Если время уже прошло в указанный день, переносим на следующий день
            if result < now_dt:
                result += timedelta(days=1)
            print(f"Результат парсинга: {result}")
            return result
        else:
            print(f"⚠️ Некорректное время: час={hour}, минута={minute}")
    
    # Паттерны для относительных дат с временем суток: "завтра в 7 утра", "сегодня в 2 часа дня"
    relative_time_patterns = [
        (r'^(завтра|сегодня|послезавтра)\s+(?:в\s+)?(?:(\d{1,2})|(' + '|'.join(number_words.keys()) + r'))(?:\s+часа?)?\s+(утра|дня|вечера|ночи)\b', True),
    ]
    
    for pattern, _ in relative_time_patterns:
        match = re.match(pattern, deadline_str)
        if match:
            date_word = match.group(1)
            hour_str = match.group(2) if match.group(2) else None
            word_str = match.group(3) if match.group(3) else None
            time_of_day = match.group(4)
            
            if hour_str:
                hour = int(hour_str)
            elif word_str:
                hour = number_words.get(word_str)
            else:
                continue
            
            if hour is None:
                continue
            
            # Преобразуем час в зависимости от времени суток (та же логика, что и выше)
            if time_of_day == 'утра':
                # "утра" = 0-11 часов (AM)
                if hour > 11:
                    hour = hour % 12
            elif time_of_day == 'дня':
                # "дня" = 12-17 часов (PM, после полудня)
                if hour == 0:
                    hour = 12  # "0 часов дня" = полдень
                elif hour < 12:
                    hour = hour + 12  # 1-11 -> 13-23
                    # Ограничиваем до 17 для "дня"
                    if hour > 17:
                        hour = hour - 12
            elif time_of_day == 'вечера':
                # "вечера" = 18-23 часов (PM, вечер)
                if hour < 12:
                    hour = hour + 12  # 6-11 -> 18-23
            elif time_of_day == 'ночи':
                # "ночи" = 0-5 часов (AM, ночь)
                if hour >= 12:
                    hour = hour % 12
            
            hour = hour % 24
            
            days_offset = 0
            if date_word == 'завтра':
                days_offset = 1
            elif date_word == 'послезавтра':
                days_offset = 2
            
            result = (now_dt + timedelta(days=days_offset)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if result < now_dt:
                result += timedelta(days=1)
            return result
    
    # Паттерн для формата "завтра до 18:00", "послезавтра до 14:00"
    until_time_pattern = r'^(завтра|сегодня|послезавтра)\s+до\s+(\d{1,2})(?:[:.](\d{2}))?$'
    match = re.match(until_time_pattern, deadline_str)
    if match:
        date_word = match.group(1)
        hour = int(match.group(2))
        minute_str = match.group(3)
        minute = int(minute_str) if minute_str else 0
        
        print(f"Парсинг формата 'дата до время': '{deadline_str}' -> дата={date_word}, час={hour}, минута={minute}")
        
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            days_offset = 0
            if date_word == 'завтра':
                days_offset = 1
            elif date_word == 'послезавтра':
                days_offset = 2
            # 'сегодня' -> days_offset = 0
            
            result = (now_dt + timedelta(days=days_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Если время уже прошло в указанный день, переносим на следующий день
            if result < now_dt:
                result += timedelta(days=1)
            print(f"Результат парсинга: {result}")
            return result
    
    # Относительные даты с временем: "завтра в 14", "сегодня в 19:30", "завтра в 14:00"
    # Также обрабатываем форматы "завтра в 14 часов 30", "завтра 14 30", "завтра в 14 часов"
    date_time_patterns = [
        r'^(завтра|сегодня|послезавтра)\s+в\s+(\d{1,2})\s+часов?\s+(\d{1,2})\s*(?:минут?|м)?$',  # завтра в 14 часов 30
        r'^(завтра|сегодня|послезавтра)\s+в\s+(\d{1,2})\s+часов?$',  # завтра в 14 часов
        r'^(завтра|сегодня|послезавтра)\s+в\s+(\d{1,2})(?:[:.](\d{2}))?$',  # завтра в 14, завтра в 14:00
        r'^(завтра|сегодня|послезавтра)\s+(\d{1,2})\s+(\d{2})$',     # завтра 14 30
        r'^(завтра|сегодня|послезавтра)\s+(\d{1,2})(?:[:.](\d{2}))?$',     # завтра 14, завтра 14:00
    ]
    
    for pattern in date_time_patterns:
        match = re.match(pattern, deadline_str)
        if match:
            date_word = match.group(1)
            hour = int(match.group(2))
            # Проверяем, есть ли минуты в группе 3 или 4
            minute = 0
            if len(match.groups()) >= 3 and match.group(3):
                try:
                    minute = int(match.group(3))
                except (ValueError, TypeError):
                    pass
            
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                days_offset = 0
                if date_word == 'завтра':
                    days_offset = 1
                elif date_word == 'послезавтра':
                    days_offset = 2
                # 'сегодня' -> days_offset = 0
                
                result = (now_dt + timedelta(days=days_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                # Если время уже прошло в указанный день, переносим на следующий день
                if result < now_dt:
                    result += timedelta(days=1)
                print(f"Парсинг относительной даты с временем: '{deadline_str}' -> {result}")
                return result
    
    # Только час без минут: "19" -> сегодня в 19:00
    hour_only_pattern = r'^(\d{1,2})$'
    match = re.match(hour_only_pattern, deadline_str)
    if match:
        hour = int(match.group(1))
        if 0 <= hour <= 23:
            result = now_dt.replace(hour=hour, minute=0, second=0, microsecond=0)
            # Если время уже прошло сегодня, переносим на завтра
            if result < now_dt:
                result += timedelta(days=1)
            return result
    
    # Относительные даты
    relative_dates = {
        'сегодня': 0,
        'сегодняшний день': 0,
        'завтра': 1,
        'послезавтра': 2,
        'через день': 1,
        'через 2 дня': 2,
        'через 3 дня': 3,
        'через неделю': 7,
        'через 2 недели': 14,
    }
    
    if deadline_str in relative_dates:
        days = relative_dates[deadline_str]
        return (now_dt + timedelta(days=days)).replace(hour=23, minute=59, second=59, microsecond=0)
    
    # День недели + время: "вторник 14:00", "понедельник в 10:30", "пт 18:00"
    weekdays_map = {
        'понедельник': 0, 'пн': 0,
        'вторник': 1, 'вт': 1,
        'среда': 2, 'ср': 2,
        'четверг': 3, 'чт': 3,
        'пятница': 4, 'пт': 4,
        'суббота': 5, 'сб': 5,
        'воскресенье': 6, 'вс': 6,
    }
    weekday_time_pattern = r'^(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|пн|вт|ср|чт|пт|сб|вс)\s+(?:в\s+)?(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?$'
    match = re.match(weekday_time_pattern, deadline_str)
    if match:
        day_name = match.group(1).lower()
        target_weekday = weekdays_map.get(day_name)
        if target_weekday is not None:
            hour = int(match.group(2))
            minute_str = match.group(3)
            minute = int(minute_str) if minute_str else 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                current_weekday = now_dt.weekday()
                days_ahead = target_weekday - current_weekday
                if days_ahead < 0:
                    days_ahead += 7
                elif days_ahead == 0:
                    # тот же день: если время уже прошло, берём следующий раз в этот день недели
                    candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if candidate < now_dt:
                        days_ahead = 7
                    else:
                        return candidate
                if days_ahead > 0:
                    result = (now_dt + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                    return result
    
    # Дни недели (только название — конец дня 23:59)
    weekdays = {
        'понедельник': 0,
        'вторник': 1,
        'среда': 2,
        'четверг': 3,
        'пятница': 4,
        'суббота': 5,
        'воскресенье': 6,
        'пн': 0,
        'вт': 1,
        'ср': 2,
        'чт': 3,
        'пт': 4,
        'сб': 5,
        'вс': 6,
    }
    
    if deadline_str in weekdays:
        target_weekday = weekdays[deadline_str]
        current_weekday = now_dt.weekday()
        days_ahead = target_weekday - current_weekday
        if days_ahead <= 0:
            days_ahead += 7
        return (now_dt + timedelta(days=days_ahead)).replace(hour=23, minute=59, second=59, microsecond=0)
    
    # Относительные даты с числом (через N дней/часов/минут/недель)
    if deadline_str.startswith('через'):
        try:
            parts = deadline_str.split()
            if len(parts) >= 3:
                amount = int(parts[1])
                unit = parts[2]
                
                if 'день' in unit or 'дней' in unit or 'дня' in unit:
                    return (now_dt + timedelta(days=amount)).replace(hour=23, minute=59, second=59, microsecond=0)
                elif 'недел' in unit or 'неделя' in unit:
                    return (now_dt + timedelta(weeks=amount)).replace(hour=23, minute=59, second=59, microsecond=0)
                elif 'месяц' in unit or 'месяцев' in unit or 'месяца' in unit:
                    # Приблизительно 30 дней в месяце
                    return (now_dt + timedelta(days=amount * 30)).replace(hour=23, minute=59, second=59, microsecond=0)
        except:
            pass
    
    # Только время (HH:MM, HH.MM, HH MM) - считаем что это сегодня
    # Также обрабатываем форматы "14 часов 30", "14 часов", "в 14 часов 30"
    time_only_patterns = [
        r'^в\s+(\d{1,2})\s+часов?\s+(\d{1,2})\s*(?:минут?|м)?$',  # в 14 часов 30
        r'^в\s+(\d{1,2})\s+часов?$',  # в 14 часов
        r'^(\d{1,2})\s+часов?\s+(\d{1,2})\s*(?:минут?|м)?$',  # 14 часов 30
        r'^(\d{1,2})\s+часов?$',  # 14 часов
        r'^(\d{1,2})[:.](\d{2})$',  # 18:30 или 18.30
        r'^(\d{1,2})\s+(\d{2})$',   # 18 30
    ]
    
    for pattern in time_only_patterns:
        match = re.match(pattern, deadline_str)
        if match:
            hour = int(match.group(1))
            minute = 0
            # Проверяем, есть ли минуты в группе 2 (для паттернов с "часов" минуты могут быть в группе 2)
            if len(match.groups()) >= 2:
                try:
                    minute_str = match.group(2)
                    if minute_str and minute_str.isdigit():
                        minute = int(minute_str)
                except (ValueError, TypeError, IndexError):
                    minute = 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                result = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # Если время уже прошло сегодня, переносим на завтра
                if result < now_dt:
                    result += timedelta(days=1)
                print(f"Парсинг времени: '{deadline_str}' -> {result}")
                return result
    
    # Различные форматы даты и времени
    # Формат: дата + время
    date_time_patterns = [
        # DD.MM.YYYY HH:MM
        r'^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2})[:.](\d{2})$',
        # DD/MM/YYYY HH:MM
        r'^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2})[:.](\d{2})$',
        # YYYY-MM-DD HH:MM
        r'^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2})[:.](\d{2})$',
        # DD-MM-YYYY HH:MM
        r'^(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2})[:.](\d{2})$',
    ]
    
    for pattern in date_time_patterns:
        match = re.match(pattern, deadline_str)
        if match:
            try:
                if len(match.groups()) == 5:
                    if '-' in deadline_str and len(match.group(1)) == 4:
                        # YYYY-MM-DD
                        year, month, day, hour, minute = map(int, match.groups())
                    elif '/' in deadline_str:
                        # DD/MM/YYYY
                        day, month, year, hour, minute = map(int, match.groups())
                    elif '-' in deadline_str:
                        # DD-MM-YYYY
                        day, month, year, hour, minute = map(int, match.groups())
                    else:
                        # DD.MM.YYYY
                        day, month, year, hour, minute = map(int, match.groups())
                    
                    if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59:
                        return datetime(year, month, day, hour, minute, 0)
            except:
                continue
    
    # Формат "15 февраля", "16 февраля" и т.д.
    months_ru = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
        'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
        'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
        'январь': 1, 'февраль': 2, 'март': 3, 'апрель': 4,
        'май': 5, 'июнь': 6, 'июль': 7, 'август': 8,
        'сентябрь': 9, 'октябрь': 10, 'ноябрь': 11, 'декабрь': 12
    }
    
    # Паттерн для "15 февраля", "15 февраля 2026", "15 февраля в 14:00", "15 февраля в 14"
    date_month_patterns = [
        r'^(\d{1,2})\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])(?:\s+(\d{4}))?(?:\s+в\s+(\d{1,2})(?:\s*[:.]?\s*(\d{2}))?)?$',
    ]
    
    for date_month_pattern in date_month_patterns:
        match = re.match(date_month_pattern, deadline_str)
        if match:
            try:
                day = int(match.group(1))
                month_name = match.group(2).lower()
                year_str = match.group(3)
                hour_str = match.group(4)
                minute_str = match.group(5)
                
                if month_name in months_ru:
                    month = months_ru[month_name]
                    if year_str:
                        year = int(year_str)
                    else:
                        year = now_dt.year
                        # Если дата уже прошла в этом году, берем следующий год
                        if datetime(year, month, day) < now_dt.replace(hour=0, minute=0, second=0):
                            year += 1
                    
                    if 1 <= month <= 12 and 1 <= day <= 31:
                        # Если указано время, используем его
                        if hour_str:
                            hour = int(hour_str)
                            minute = int(minute_str) if minute_str else 0
                            if 0 <= hour <= 23 and 0 <= minute <= 59:
                                return datetime(year, month, day, hour, minute, 0)
                        # Иначе конец дня
                        return datetime(year, month, day, 23, 59, 59)
            except:
                continue
    
    # Только дата (без времени) - различные форматы
    date_only_patterns = [
        # DD.MM.YYYY
        r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$',
        # DD/MM/YYYY
        r'^(\d{1,2})/(\d{1,2})/(\d{4})$',
        # YYYY-MM-DD
        r'^(\d{4})-(\d{1,2})-(\d{1,2})$',
        # DD-MM-YYYY
        r'^(\d{1,2})-(\d{1,2})-(\d{4})$',
        # DD.MM (текущий год)
        r'^(\d{1,2})\.(\d{1,2})$',
        # DD/MM (текущий год)
        r'^(\d{1,2})/(\d{1,2})$',
    ]
    
    for pattern in date_only_patterns:
        match = re.match(pattern, deadline_str)
        if match:
            try:
                groups = match.groups()
                if len(groups) == 3:
                    if '-' in deadline_str and len(groups[0]) == 4:
                        # YYYY-MM-DD
                        year, month, day = map(int, groups)
                    elif '/' in deadline_str:
                        # DD/MM/YYYY
                        day, month, year = map(int, groups)
                    elif '-' in deadline_str:
                        # DD-MM-YYYY
                        day, month, year = map(int, groups)
                    else:
                        # DD.MM.YYYY
                        day, month, year = map(int, groups)
                elif len(groups) == 2:
                    # DD.MM или DD/MM (текущий год)
                    day, month = map(int, groups)
                    year = now_dt.year
                    # Если дата уже прошла в этом году, берем следующий год
                    if datetime(year, month, day) < now_dt.replace(hour=0, minute=0, second=0):
                        year += 1
                
                if 1 <= month <= 12 and 1 <= day <= 31:
                    return datetime(year, month, day, 23, 59, 59)
            except:
                continue
    
    return None


def parse_reminder(reminder_str: str, deadline: Optional[datetime] = None) -> Optional[datetime]:
    """Парсинг напоминания из строки - поддерживает множество форматов
    Если указан deadline, напоминания типа 'за X' вычисляются относительно дедлайна"""
    reminder_str = reminder_str.strip().lower()
    current_time = now()
    # Приводим к naive datetime для работы
    if current_time.tzinfo:
        now_dt = current_time.replace(tzinfo=None)
    else:
        now_dt = current_time
    
    # Определяем базовую точку отсчета: если есть дедлайн и напоминание начинается с "за", используем дедлайн
    use_deadline = deadline is not None and reminder_str.startswith('за')
    base_time = deadline if use_deadline else now_dt
    
    # Относительные напоминания
    relative_reminders = {
        'за 15 минут': timedelta(minutes=15),
        'через 15 минут': timedelta(minutes=15),
        'за полчаса': timedelta(minutes=30),
        'через полчаса': timedelta(minutes=30),
        'за 30 минут': timedelta(minutes=30),
        'через 30 минут': timedelta(minutes=30),
        'за час': timedelta(hours=1),
        'через час': timedelta(hours=1),
        'за 2 часа': timedelta(hours=2),
        'через 2 часа': timedelta(hours=2),
        'за 3 часа': timedelta(hours=3),
        'через 3 часа': timedelta(hours=3),
        'за день': timedelta(days=1),
        'через день': timedelta(days=1),
        'за неделю': timedelta(weeks=1),
        'через неделю': timedelta(weeks=1),
    }
    
    if reminder_str in relative_reminders:
        delta = relative_reminders[reminder_str]
        if use_deadline:
            # Вычитаем из дедлайна
            result = base_time - delta
        else:
            # Добавляем к текущему времени
            result = base_time + delta
        
        # Проверяем, что напоминание не в прошлом
        if result < now_dt:
            result = now_dt + timedelta(minutes=1)
        return result
    
    # Относительные напоминания с числом (через N минут/часов/дней)
    if reminder_str.startswith('через') or reminder_str.startswith('за'):
        try:
            parts = reminder_str.split()
            if len(parts) >= 3:
                amount = int(parts[1])
                unit = parts[2]
                
                if 'минут' in unit or 'минуты' in unit or 'минуту' in unit:
                    delta = timedelta(minutes=amount)
                elif 'час' in unit or 'часов' in unit or 'часа' in unit:
                    delta = timedelta(hours=amount)
                elif 'день' in unit or 'дней' in unit or 'дня' in unit:
                    delta = timedelta(days=amount)
                elif 'недел' in unit or 'неделя' in unit or 'недели' in unit:
                    delta = timedelta(weeks=amount)
                else:
                    return None
                
                if use_deadline:
                    # Вычитаем из дедлайна
                    result = base_time - delta
                else:
                    # Добавляем к текущему времени
                    result = base_time + delta
                
                # Проверяем, что напоминание не в прошлом
                if result < now_dt:
                    result = now_dt + timedelta(minutes=1)
                return result
        except:
            pass
    
    # Только время (HH:MM, HH.MM) - считаем что это сегодня
    time_patterns = [
        r'^(\d{1,2})[:.](\d{2})$',  # 18:30 или 18.30
        r'^(\d{1,2})\s+(\d{2})$',   # 18 30
    ]
    
    for pattern in time_patterns:
        match = re.match(pattern, reminder_str)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                result = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # Если время уже прошло сегодня, переносим на завтра
                if result < now_dt:
                    result += timedelta(days=1)
                return result
    
    # Различные форматы даты и времени (используем ту же логику что и для дедлайна)
    date_time_patterns = [
        r'^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2})[:.](\d{2})$',
        r'^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2})[:.](\d{2})$',
        r'^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2})[:.](\d{2})$',
        r'^(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2})[:.](\d{2})$',
    ]
    
    for pattern in date_time_patterns:
        match = re.match(pattern, reminder_str)
        if match:
            try:
                if len(match.groups()) == 5:
                    if '-' in reminder_str and len(match.group(1)) == 4:
                        year, month, day, hour, minute = map(int, match.groups())
                    elif '/' in reminder_str:
                        day, month, year, hour, minute = map(int, match.groups())
                    elif '-' in reminder_str:
                        day, month, year, hour, minute = map(int, match.groups())
                    else:
                        day, month, year, hour, minute = map(int, match.groups())
                    
                    if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59:
                        return datetime(year, month, day, hour, minute, 0)
            except:
                continue
    
    # Только дата (без времени)
    date_patterns = [
        r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$',
        r'^(\d{1,2})/(\d{1,2})/(\d{4})$',
        r'^(\d{4})-(\d{1,2})-(\d{1,2})$',
        r'^(\d{1,2})-(\d{1,2})-(\d{4})$',
        r'^(\d{1,2})\.(\d{1,2})$',
        r'^(\d{1,2})/(\d{1,2})$',
    ]
    
    for pattern in date_patterns:
        match = re.match(pattern, reminder_str)
        if match:
            try:
                groups = match.groups()
                if len(groups) == 3:
                    if '-' in reminder_str and len(groups[0]) == 4:
                        year, month, day = map(int, groups)
                    elif '/' in reminder_str:
                        day, month, year = map(int, groups)
                    elif '-' in reminder_str:
                        day, month, year = map(int, groups)
                    else:
                        day, month, year = map(int, groups)
                elif len(groups) == 2:
                    day, month = map(int, groups)
                    year = now_dt.year
                    if datetime(year, month, day) < now_dt.replace(hour=0, minute=0, second=0):
                        year += 1
                
                if 1 <= month <= 12 and 1 <= day <= 31:
                    return datetime(year, month, day, 9, 0, 0)  # По умолчанию 9:00
            except:
                continue
    
    return None


# ========== ОБРАБОТЧИКИ КОМАНД ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    keyboard = get_main_keyboard()
    await update.message.reply_text("🧌", reply_markup=keyboard)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "<b>Справка по командам:</b>\n\n"
        "<b>/add</b> - Добавить новую задачу\n"
        "  Процесс:\n"
        "  1. Название задачи\n"
        "  2. Комментарий (или /skip)\n"
        "  3. Выбор проекта (кнопками)\n"
        "  4. Дедлайн (или /skip)\n"
        "  5. Напоминание (или /skip)\n"
        "  6. Регулярность (одноразовая/ежедневная/еженедельная)\n\n"
        "<b>/list</b> - Показать все задачи\n\n"
        "<b>/projects</b> - Показать все проекты\n\n"
        "<b>/addproject</b> - Добавить новый проект\n\n"
        "<b>/stats</b> - Показать статистику\n\n"
        "<b>/cancel</b> - Отменить текущую операцию\n\n"
        "<b>Форматы дедлайна:</b>\n"
        "• Сегодня\n"
        "• Завтра\n"
        "• 25.01.2026\n"
        "• 25.01.2026 18:00\n"
        "• Через 3 дня\n\n"
        "<b>Форматы напоминания:</b>\n"
        "• За час\n"
        "• За 2 часа\n"
        "• За день\n"
        "• Через 30 минут\n"
        "• 25.01.2026 18:00\n\n"
        "<b>Регулярность задачи:</b>\n"
        "• Одноразовая - задача выполняется один раз\n"
        "• Ежедневная - задача повторяется каждый день\n"
        "• Еженедельная - задача повторяется каждую неделю"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')


# ========== ОБРАБОТЧИКИ ДОБАВЛЕНИЯ ЗАДАЧИ ==========

async def save_bot_message(message, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет message_id сообщения бота для последующего удаления"""
    if 'bot_messages' not in context.user_data:
        context.user_data['bot_messages'] = []
    if message:
        context.user_data['bot_messages'].append(message.message_id)

async def save_user_message(message, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет message_id сообщения пользователя для последующего удаления"""
    if 'user_messages' not in context.user_data:
        context.user_data['user_messages'] = []
    if message:
        context.user_data['user_messages'].append(message.message_id)

async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления задачи"""
    context.user_data.clear()
    context.user_data['bot_messages'] = []  # Список для хранения message_id сообщений бота
    context.user_data['user_messages'] = []  # Список для хранения message_id сообщений пользователя
    # Сохраняем сообщение пользователя, которое запустило процесс
    await save_user_message(update.message, context)
    msg = await update.message.reply_text("Какая задача?")
    await save_bot_message(msg, context)
    return WAITING_TASK_TITLE


async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение названия задачи (текст или голос)"""
    # Сохраняем сообщение пользователя
    await save_user_message(update.message, context)
    
    task_title = None
    raw_text = None
    
    # Проверяем голосовое сообщение
    if update.message.voice:
        print(f"Получено голосовое сообщение: duration={update.message.voice.duration}, file_id={update.message.voice.file_id}")
        
        # Пробуем использовать caption от Telegram (если есть)
        if update.message.caption:
            raw_text = update.message.caption.strip()
            print(f"Использован caption от Telegram: {raw_text}")
            # Нормализуем caption для лучшего парсинга дедлайнов
            raw_text = normalize_voice_text(raw_text)
        elif VOICE_SUPPORT:
            try:
                print("Получение файла голосового сообщения...")
                voice_file = await update.message.voice.get_file()
                print(f"Файл получен: file_path={voice_file.file_path}, file_size={voice_file.file_size}")
                
                print("Начало распознавания голосового сообщения...")
                transcribed_text = await transcribe_voice(voice_file, update)
                
                if transcribed_text:
                    raw_text = transcribed_text.strip()
                    print(f"✅ Успешно распознано: {raw_text}")
                else:
                    print("❌ Не удалось распознать голосовое сообщение")
                    await update.message.reply_text(
                        "Не удалось распознать голосовое сообщение.\n\n"
                        "💡 Попробуйте:\n"
                        "• Говорить четче и медленнее\n"
                        "• Уменьшить фоновый шум\n"
                        "• Написать текст вместо голосового сообщения"
                    )
                    return WAITING_TASK_TITLE
            except Exception as e:
                print(f"❌ Ошибка при обработке голосового сообщения: {e}")
                import traceback
                traceback.print_exc()
                await update.message.reply_text("Ошибка обработки голосового сообщения. Попробуйте написать текст:")
                return WAITING_TASK_TITLE
        else:
            # Если модули не установлены, просто просим повторить текстом
            await update.message.reply_text("Повторите текстом:")
            return WAITING_TASK_TITLE
    elif update.message.text:
        raw_text = update.message.text.strip()
    
    if not raw_text:
        await update.message.reply_text("Ошибка: Название задачи не может быть пустым. Попробуйте снова:")
        return WAITING_TASK_TITLE
    
    # Пытаемся извлечь дедлайн из текста
    task_title, deadline_dt = extract_deadline_from_text(raw_text)
    
    # Если дедлайн найден, сохраняем его
    if deadline_dt:
        context.user_data['task_deadline'] = deadline_dt.isoformat()
        print(f"Дедлайн извлечен из текста: {deadline_dt}")
    
    if not task_title:
        await update.message.reply_text("Ошибка: Название задачи не может быть пустым. Попробуйте снова:")
        return WAITING_TASK_TITLE
    
    context.user_data['task_title'] = task_title
    
    # Формируем ответное сообщение
    response_text = f"Задача: {task_title}"
    if deadline_dt:
        deadline_formatted = format_deadline_readable(deadline_dt)
        response_text += f"\nДедлайн: {deadline_formatted}"
    
    msg = await update.message.reply_text(response_text)
    await save_bot_message(msg, context)
    print(f"Сохранена задача: '{task_title}', дедлайн: {deadline_dt}")
    
    # Проверяем, есть ли уже дедлайн из названия задачи
    if 'task_deadline' in context.user_data and context.user_data['task_deadline']:
        # Дедлайн уже есть, переходим к комментарию
        print("Дедлайн найден, переходим к комментарию")
        msg = await update.message.reply_text("Что-то уточним, или /skip")
        await save_bot_message(msg, context)
        return WAITING_TASK_COMMENT
    else:
        # Дедлайна нет, спрашиваем его
        print("Дедлайн не найден, спрашиваем его")
        msg = await update.message.reply_text("Когда?")
        await save_bot_message(msg, context)
        return WAITING_TASK_DEADLINE


async def add_task_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение комментария задачи (текст или голос)"""
    print(f"[DEBUG] add_task_comment вызвана")
    print(f"[DEBUG] update.message: {update.message is not None}")
    
    # Проверяем, есть ли сообщение
    if not update.message:
        print("[DEBUG] Ошибка: update.message отсутствует")
        return WAITING_TASK_COMMENT
    
    print(f"[DEBUG] update.message.text: {update.message.text if hasattr(update.message, 'text') else 'N/A'}")
    print(f"[DEBUG] update.message.voice: {update.message.voice is not None if hasattr(update.message, 'voice') else 'N/A'}")
    
    # Сохраняем сообщение пользователя
    await save_user_message(update.message, context)
    
    text = None
    
    # Проверяем голосовое сообщение
    if hasattr(update.message, 'voice') and update.message.voice:
        print(f"Получено голосовое сообщение для комментария: duration={update.message.voice.duration}, file_id={update.message.voice.file_id}")
        
        # Пробуем использовать caption от Telegram (если есть)
        if update.message.caption:
            text = update.message.caption.strip()
            print(f"Использован caption от Telegram для комментария: {text}")
            # Нормализуем caption для лучшего парсинга
            text = normalize_voice_text(text)
        elif VOICE_SUPPORT:
            try:
                print("Получение файла голосового сообщения для комментария...")
                voice_file = await update.message.voice.get_file()
                print(f"Файл получен: file_path={voice_file.file_path}, file_size={voice_file.file_size}")
                
                print("Начало распознавания голосового сообщения для комментария...")
                transcribed_text = await transcribe_voice(voice_file, update)
                
                if transcribed_text:
                    text = transcribed_text.strip()
                    print(f"✅ Успешно распознано для комментария: {text}")
                    await update.message.reply_text(text)
                else:
                    print("❌ Не удалось распознать голосовое сообщение для комментария")
                    await update.message.reply_text(
                        "Не удалось распознать голосовое сообщение.\n\n"
                        "💡 Попробуйте:\n"
                        "• Говорить четче и медленнее\n"
                        "• Написать текст или /skip"
                    )
                    return WAITING_TASK_COMMENT
            except Exception as e:
                print(f"❌ Ошибка при обработке голосового сообщения для комментария: {e}")
                import traceback
                traceback.print_exc()
                await update.message.reply_text("Ошибка обработки голосового сообщения. Попробуйте написать текст:")
                return WAITING_TASK_COMMENT
        else:
            # Если модули не установлены, просто просим повторить текстом
            await update.message.reply_text("Повторите текстом или /skip:")
            return WAITING_TASK_COMMENT
    elif hasattr(update.message, 'text') and update.message.text:
        text = update.message.text.strip()
        print(f"Получен текст для комментария: {text}")
    else:
        # Если это команда /skip через CommandHandler, text будет None
        print("Команда /skip или пустое сообщение")
        text = None
    
    try:
        # Обрабатываем /skip или пустой текст
        if not text or text.lower() == '/skip' or text.lower() == 'skip':
            context.user_data['task_comment'] = None
            print("Комментарий пропущен (/skip)")
        else:
            context.user_data['task_comment'] = text
            print(f"Комментарий сохранен: {text}")
        
        # Получаем проекты пользователя
        user_id = update.effective_user.id
        try:
            projects = get_user_projects(str(user_id))
            print(f"Найдено проектов: {len(projects)}")
            # Убеждаемся, что projects - это список строк
            if not isinstance(projects, list):
                print(f"[WARNING] get_user_projects вернул не список: {type(projects)}")
                projects = []
            # Фильтруем только строки
            projects = [p for p in projects if isinstance(p, str) and p.strip()]
            print(f"Проектов после фильтрации: {len(projects)}")
        except Exception as e:
            print(f"[ERROR] Ошибка при получении проектов: {e}")
            import traceback
            traceback.print_exc()
            projects = []
        
        if not projects:
            # Если проектов нет, создаем задачу без проекта
            context.user_data['task_project'] = None
            print("Проектов нет, переходим к напоминаниям")
            # Переходим к напоминаниям
            keyboard = [
                [InlineKeyboardButton("За час", callback_data="reminder_1h")],
                [InlineKeyboardButton("За 3 часа", callback_data="reminder_3h")],
                [InlineKeyboardButton("За 6 часов", callback_data="reminder_6h")],
                [InlineKeyboardButton("За день", callback_data="reminder_1d")],
                [InlineKeyboardButton("Пропустить", callback_data="skip_reminder")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            msg = await update.message.reply_text(
                "Напоминание:",
                reply_markup=reply_markup
            )
            await save_bot_message(msg, context)
            print("Возвращаем WAITING_TASK_REMINDER")
            return WAITING_TASK_REMINDER
        
        # Показываем проекты кнопками
        print("Показываем список проектов")
        keyboard = []
        for project in projects:
            # Обрезаем длинные имена проектов для callback_data (лимит 64 байта)
            prefix = "project_"
            max_project_bytes = 64 - len(prefix.encode('utf-8')) - 1  # -1 для безопасности
            
            project_bytes = project.encode('utf-8')
            if len(project_bytes) > max_project_bytes:
                # Если название слишком длинное, обрезаем его по байтам
                truncated_bytes = project_bytes[:max_project_bytes]
                # Убираем неполные символы в конце
                try:
                    project_callback = truncated_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    # Если последний байт неполный, убираем его
                    project_callback = truncated_bytes[:-1].decode('utf-8')
                callback_data = f"{prefix}{project_callback}"
            else:
                callback_data = f"{prefix}{project}"
            
            keyboard.append([InlineKeyboardButton(project, callback_data=callback_data)])
        keyboard.append([InlineKeyboardButton("Пропустить", callback_data="skip_project")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text(
            "К какому проекту относится?",
            reply_markup=reply_markup
        )
        await save_bot_message(msg, context)
        print("Возвращаем WAITING_TASK_PROJECT")
        return WAITING_TASK_PROJECT
    except Exception as e:
        print(f"[ERROR] Ошибка в add_task_comment: {e}")
        import traceback
        traceback.print_exc()
        try:
            if update.message:
                await update.message.reply_text("Произошла ошибка. Попробуйте снова или отправьте /skip:")
        except Exception as e2:
            print(f"[ERROR] Не удалось отправить сообщение об ошибке: {e2}")
        return WAITING_TASK_COMMENT


async def add_task_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора проекта через кнопки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "skip_project":
        context.user_data['task_project'] = None
        # Переходим к напоминаниям
        keyboard = [
            [InlineKeyboardButton("За час", callback_data="reminder_1h")],
            [InlineKeyboardButton("За 3 часа", callback_data="reminder_3h")],
            [InlineKeyboardButton("За 6 часов", callback_data="reminder_6h")],
            [InlineKeyboardButton("За день", callback_data="reminder_1d")],
            [InlineKeyboardButton("Пропустить", callback_data="skip_reminder")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Напоминание:", parse_mode='HTML')
        msg = await query.message.reply_text("Напоминание:", reply_markup=reply_markup)
        await save_bot_message(msg, context)
        return WAITING_TASK_REMINDER
    elif query.data == "new_project":
        await query.edit_message_text(
            "Введите название нового проекта:"
        )
        return WAITING_TASK_PROJECT
    else:
        project_name = query.data.replace('project_', '')
        context.user_data['task_project'] = project_name
        # Переходим к напоминаниям
        keyboard = [
            [InlineKeyboardButton("За час", callback_data="reminder_1h")],
            [InlineKeyboardButton("За 3 часа", callback_data="reminder_3h")],
            [InlineKeyboardButton("За 6 часов", callback_data="reminder_6h")],
            [InlineKeyboardButton("За день", callback_data="reminder_1d")],
            [InlineKeyboardButton("Пропустить", callback_data="skip_reminder")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Проект: <b>{project_name}</b>\n\nНапоминание:", parse_mode='HTML')
        msg = await query.message.reply_text("Напоминание:", reply_markup=reply_markup)
        await save_bot_message(msg, context)
        return WAITING_TASK_REMINDER


async def add_task_project_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание нового проекта из текста"""
    # Сохраняем сообщение пользователя
    await save_user_message(update.message, context)
    
    project_name = update.message.text.strip()
    user_id = update.effective_user.id
    
    if not project_name:
        await update.message.reply_text("Ошибка: Название проекта не может быть пустым. Попробуйте снова:")
        return WAITING_TASK_PROJECT
    
    # Создаем проект
    add_user_project(str(user_id), project_name)
    context.user_data['task_project'] = project_name
    
    # Переходим к напоминаниям
    keyboard = [
        [InlineKeyboardButton("За час", callback_data="reminder_1h")],
        [InlineKeyboardButton("За 3 часа", callback_data="reminder_3h")],
        [InlineKeyboardButton("За 6 часов", callback_data="reminder_6h")],
        [InlineKeyboardButton("За день", callback_data="reminder_1d")],
        [InlineKeyboardButton("Пропустить", callback_data="skip_reminder")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg1 = await update.message.reply_text(
        f"Проект <b>{project_name}</b> создан!\n\nНапоминание:",
        parse_mode='HTML'
    )
    await save_bot_message(msg1, context)
    msg2 = await update.message.reply_text("Напоминание:", reply_markup=reply_markup)
    await save_bot_message(msg2, context)
    return WAITING_TASK_REMINDER


async def add_task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение дедлайна задачи (текст или голос)"""
    # Сохраняем сообщение пользователя
    await save_user_message(update.message, context)
    
    text = None
    
    # Проверяем голосовое сообщение
    if update.message.voice:
        # Пробуем использовать caption от Telegram (если есть)
        if update.message.caption:
            text = update.message.caption.strip()
        elif VOICE_SUPPORT:
            voice_file = await update.message.voice.get_file()
            transcribed_text = await transcribe_voice(voice_file, update)
            
            if transcribed_text:
                text = transcribed_text.strip()
                await update.message.reply_text(text)
            else:
                await update.message.reply_text(
                    "Не удалось распознать голосовое сообщение.\n\n"
                    "💡 Попробуйте:\n"
                    "• Говорить четче и медленнее\n"
                    "• Написать дедлайн текстом или /skip"
                )
                return WAITING_TASK_DEADLINE
        else:
            # Если модули не установлены, просто просим повторить текстом
            await update.message.reply_text("Повторите текстом или /skip:")
            return WAITING_TASK_DEADLINE
    elif update.message.text:
        text = update.message.text.strip()
    
    # Проверяем команду /skip
    if not text or text.lower() == '/skip' or text.lower() == 'skip':
        deadline = None
    else:
        deadline_dt = parse_deadline(text)
        if deadline_dt is None:
            await update.message.reply_text(
                "Ошибка: Неверный формат даты. Попробуйте снова или отправьте /skip:"
            )
            return WAITING_TASK_DEADLINE
        deadline = deadline_dt.isoformat()
    
    context.user_data['task_deadline'] = deadline
    
    # После дедлайна переходим к комментарию
    msg = await update.message.reply_text("Что-то уточним, или /skip")
    await save_bot_message(msg, context)
    return WAITING_TASK_COMMENT


async def add_task_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора напоминания через кнопки"""
    query = update.callback_query
    await query.answer()
    
    reminder = None
    
    # Получаем дедлайн задачи
    deadline_str = context.user_data.get('task_deadline')
    
    if query.data == "skip_reminder":
        reminder = None
    elif deadline_str:
        # Если есть дедлайн, вычисляем напоминание относительно дедлайна
        deadline_dt = datetime.fromisoformat(deadline_str)
        
        if query.data == "reminder_1h":
            reminder = (deadline_dt - timedelta(hours=1)).isoformat()
        elif query.data == "reminder_3h":
            reminder = (deadline_dt - timedelta(hours=3)).isoformat()
        elif query.data == "reminder_6h":
            reminder = (deadline_dt - timedelta(hours=6)).isoformat()
        elif query.data == "reminder_1d":
            reminder = (deadline_dt - timedelta(days=1)).isoformat()
        
        # Проверяем, что напоминание не в прошлом
        if reminder:
            reminder_dt = datetime.fromisoformat(reminder)
            current_time = now()
            if current_time.tzinfo:
                current_time = current_time.replace(tzinfo=None)
            if reminder_dt < current_time:
                # Если напоминание в прошлом, устанавливаем на текущее время + небольшой интервал
                reminder = (current_time + timedelta(minutes=1)).isoformat()
    else:
        # Если дедлайна нет, вычисляем относительно текущего времени
        current_time = now()
        if current_time.tzinfo:
            now_dt = current_time.replace(tzinfo=None)
        else:
            now_dt = current_time
        if query.data == "reminder_1h":
            reminder = (now_dt + timedelta(hours=1)).isoformat()
        elif query.data == "reminder_3h":
            reminder = (now_dt + timedelta(hours=3)).isoformat()
        elif query.data == "reminder_6h":
            reminder = (now_dt + timedelta(hours=6)).isoformat()
        elif query.data == "reminder_1d":
            reminder = (now_dt + timedelta(days=1)).isoformat()
    
    # Сохраняем напоминание и переходим к выбору регулярности
    context.user_data['task_reminder'] = reminder
    
    # Показываем меню выбора регулярности
    keyboard = [
        [InlineKeyboardButton("Одноразовая", callback_data="recurrence_once")],
        [InlineKeyboardButton("Ежедневная", callback_data="recurrence_daily")],
        [InlineKeyboardButton("Еженедельная", callback_data="recurrence_weekly")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Регулярность:", parse_mode='HTML')
    msg = await query.message.reply_text("Регулярность:", reply_markup=reply_markup)
    await save_bot_message(msg, context)
    
    return WAITING_TASK_RECURRENCE


async def add_task_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение напоминания задачи (fallback для текстового ввода)"""
    # Сохраняем сообщение пользователя
    await save_user_message(update.message, context)
    
    text = update.message.text.strip()
    
    # Проверяем команду /skip
    if text.lower() == '/skip' or text.lower() == 'skip':
        reminder = None
    else:
        # Получаем дедлайн для вычисления напоминания относительно него
        deadline_str = context.user_data.get('task_deadline')
        deadline_dt = None
        if deadline_str:
            deadline_dt = datetime.fromisoformat(deadline_str)
        
        reminder_dt = parse_reminder(text, deadline_dt)
        if reminder_dt is None:
            await update.message.reply_text(
                "Ошибка: Неверный формат напоминания. Попробуйте снова или отправьте /skip:\n\n"
                "Примеры: 'за час', 'за 2 часа', '25.01.2026 18:00'"
            )
            return WAITING_TASK_REMINDER
        reminder = reminder_dt.isoformat()
    
    # Сохраняем напоминание и переходим к выбору регулярности
    context.user_data['task_reminder'] = reminder
    
    # Показываем меню выбора регулярности
    keyboard = [
        [InlineKeyboardButton("Одноразовая", callback_data="recurrence_once")],
        [InlineKeyboardButton("Ежедневная", callback_data="recurrence_daily")],
        [InlineKeyboardButton("Еженедельная", callback_data="recurrence_weekly")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_text("Регулярность:", reply_markup=reply_markup)
    await save_bot_message(msg, context)
    
    return WAITING_TASK_RECURRENCE


async def add_task_recurrence_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора регулярности через кнопки"""
    query = update.callback_query
    await query.answer()
    
    # Определяем тип регулярности
    recurrence_map = {
        "recurrence_once": "once",
        "recurrence_daily": "daily",
        "recurrence_weekly": "weekly"
    }
    
    recurrence = recurrence_map.get(query.data, "once")
    context.user_data['task_recurrence'] = recurrence
    
    # После выбора регулярности предлагаем выбрать категорию
    keyboard = [
        [InlineKeyboardButton("📅 событие", callback_data="task_category_event")],
        [InlineKeyboardButton("✅ задача", callback_data="task_category_task")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "Выбери категорию для этого пункта плана:\n\n"
        "📅 событие — если это конкретное событие во времени\n"
        "✅ задача — если это просто задача без жёсткой привязки к событию"
    )
    
    await query.edit_message_text(text, reply_markup=reply_markup)
    return WAITING_TASK_CATEGORY


async def add_task_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор категории задачи (📅 событие или ✅ задача) и финальное создание задачи"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "task_category_event":
        category = "event"
    else:
        category = "task"
    
    context.user_data['task_category'] = category
    
    # Берём итоговое название задачи без добавления служебных эмодзи
    task_title = context.user_data.get('task_title', '')
    
    # Создаем задачу
    user_id = update.effective_user.id
    current_time = now()
    if current_time.tzinfo:
        current_time_naive = current_time.replace(tzinfo=None)
    else:
        current_time_naive = current_time
    
    task = {
        'id': f"{current_time_naive.timestamp()}",
        'title': task_title,
        'comment': context.user_data.get('task_comment'),
        'project': context.user_data.get('task_project'),
        'category': context.user_data.get('task_category', 'task'),
        'deadline': context.user_data.get('task_deadline'),
        'reminder': context.user_data.get('task_reminder'),
        'recurrence': context.user_data.get('task_recurrence', 'once'),
        'completed': False,
        'created_at': current_time_naive.isoformat(),
        'source': 'tasks'  # Добавляем источник создания задачи
    }
    
    save_user_task(str(user_id), task)
    
    text = "✅\n"
    text += f"{task['title']}\n"
    if task.get('deadline'):
        deadline_dt = datetime.fromisoformat(task['deadline'])
        deadline_formatted = format_deadline_readable(deadline_dt)
        text += f"Дедлайн: {deadline_formatted}\n"
    if task.get('project'):
        text += f"Проект: {task['project']}\n"
    
    recurrence_names = {
        'once': 'Одноразовая',
        'daily': 'Ежедневная',
        'weekly': 'Еженедельная'
    }
    text += f"Регулярность: {recurrence_names.get(task['recurrence'], 'Одноразовая')}\n"
    
    # Сохраняем списки сообщений для удаления перед очисткой context
    bot_messages = context.user_data.get('bot_messages', [])
    user_messages = context.user_data.get('user_messages', [])
    bot_messages.append(query.message.message_id)  # Добавляем текущее сообщение бота
    
    # Сохраняем message_id для удаления
    done_message = None
    try:
        keyboard = get_main_keyboard()
        await query.edit_message_text(text, parse_mode='HTML')
        done_message = await query.message.reply_text("Готово!", reply_markup=keyboard)
        if done_message:
            bot_messages.append(done_message.message_id)
    except Exception as e:
        print(f"Ошибка при отправке сообщения: {e}")
    
    # Удаляем всю историю сообщений (и бота, и пользователя)
    try:
        chat_id = query.message.chat_id
        
        # Удаляем сообщения бота
        for msg_id in bot_messages:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                print(f"Не удалось удалить сообщение бота {msg_id}: {e}")
                # Продолжаем удаление остальных сообщений
        
        # Пытаемся удалить сообщения пользователя
        # Примечание: в личном чате бот не может удалять сообщения пользователя,
        # но попробуем на случай, если это группа или канал
        for msg_id in user_messages:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                # В личном чате это нормально - бот не может удалять сообщения пользователя
                # Просто игнорируем ошибку
                pass
    except Exception as e:
        print(f"Ошибка при удалении сообщений: {e}")
    
    # Очищаем context и вызываем /start
    context.user_data.clear()
    
    # Вызываем команду /start - отправляем главное меню
    keyboard = get_main_keyboard()
    await query.message.reply_text("🧌", reply_markup=keyboard)
    
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена операции"""
    context.user_data.clear()
    keyboard = get_main_keyboard()
    await update.message.reply_text("Операция отменена.", reply_markup=keyboard)
    return ConversationHandler.END


# ========== ОБРАБОТЧИКИ СПИСКА ЗАДАЧ ==========

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список задач с кнопками"""
    user_id = update.effective_user.id
    tasks = get_user_tasks(str(user_id))
    
    if not tasks:
        await update.message.reply_text("У вас пока нет задач.\nИспользуйте /add для добавления задачи.")
        return
    
    text = f"<b>Ваши задачи</b> ({len(tasks)}):\n\n"
    
    recurrence_names = {
        'once': 'Одноразовая',
        'daily': 'Ежедневная',
        'weekly': 'Еженедельная'
    }
    
    keyboard = []
    
    for i, task in enumerate(tasks, 1):
        completed = task.get('completed', False)
        if completed:
            status = "✅"
            task_title = f"<s>{task['title']}</s>"
        else:
            status = "⏳"
            task_title = f"<b>{task['title']}</b>"
        
        text += f"{i}. {status} {task_title}\n"
        if task.get('comment'):
            text += f"   Комментарий: {task['comment']}\n"
        if task.get('project'):
            text += f"   Проект: {task['project']}\n"
        if task.get('deadline'):
            deadline_dt = datetime.fromisoformat(task['deadline'])
            deadline_formatted = format_deadline_readable(deadline_dt)
            text += f"   Дедлайн: {deadline_formatted}\n"
        if task.get('reminder'):
            reminder_dt = datetime.fromisoformat(task['reminder'])
            text += f"   Напоминание: {reminder_dt.strftime('%d.%m.%Y %H:%M')}\n"
        recurrence = task.get('recurrence', 'once')
        text += f"   Регулярность: {recurrence_names.get(recurrence, 'Одноразовая')}\n"
        text += "\n"
        
        # Добавляем кнопку для задачи (только если не выполнена)
        if not completed:
            task_id = task.get('id', '')
            # Обрезаем название задачи для кнопки, если слишком длинное
            button_text = task['title'][:40] + "..." if len(task['title']) > 40 else task['title']
            keyboard.append([InlineKeyboardButton(f"{i}. {button_text}", callback_data=f"task_complete_{task_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)


async def task_complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на кнопку задачи"""
    query = update.callback_query
    
    try:
        task_id = query.data.replace("task_complete_", "")
        user_id = query.from_user.id
        task = get_user_task_by_id(str(user_id), task_id)
        
        if not task:
            await query.answer("Задача не найдена", show_alert=True)
            return ConversationHandler.END
        
        await query.answer()
        
        # Сохраняем task_id в context для последующего использования
        context.user_data['task_id'] = task_id
        context.user_data['task_title'] = task.get('title', '')
        
        # Сохраняем период расписания из context (если есть)
        schedule_period = context.user_data.get('current_schedule_period')
        context.user_data['schedule_period'] = schedule_period
        
        # Показываем вопрос "готово?"
        keyboard = [
            [InlineKeyboardButton("Да", callback_data="task_confirm_yes")],
            [InlineKeyboardButton("Нет", callback_data="task_confirm_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Задача: <b>{task['title']}</b>\n\n"
            "Готово?",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return WAITING_TASK_COMPLETE_CONFIRM
    except Exception as e:
        print(f"Ошибка в task_complete_callback: {e}")
        import traceback
        traceback.print_exc()
        try:
            await query.answer("Произошла ошибка", show_alert=True)
        except:
            pass
        return ConversationHandler.END


async def task_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка подтверждения выполнения задачи"""
    query = update.callback_query
    await query.answer()
    
    task_id = context.user_data.get('task_id')
    task_title = context.user_data.get('task_title', '')
    schedule_period = context.user_data.get('schedule_period')  # Сохраняем период расписания
    user_id = query.from_user.id
    
    if query.data == "task_confirm_yes":
        # Получаем задачу для проверки времени
        task = get_user_task_by_id(str(user_id), task_id)
        warning_text = ""
        
        # Проверяем, есть ли у задачи конкретное время (не конец дня)
        if task and task.get('deadline'):
            deadline_dt = datetime.fromisoformat(task['deadline'])
            if deadline_dt.tzinfo:
                deadline_dt = deadline_dt.replace(tzinfo=None)
            
            # Проверяем, выполняется ли задача раньше запланированного времени
            current_time = now()
            if current_time.tzinfo:
                current_time = current_time.replace(tzinfo=None)
            
            # Если задача имеет конкретное время (не 23:59) и выполняется раньше
            if deadline_dt.hour != 23 or deadline_dt.minute != 59:
                if current_time < deadline_dt:
                    time_diff = deadline_dt - current_time
                    hours = int(time_diff.total_seconds() // 3600)
                    minutes = int((time_diff.total_seconds() % 3600) // 60)
                    if hours > 0:
                        warning_text = f"\n\n✅ Выполнено раньше срока на {hours} ч. {minutes} мин."
                    elif minutes > 0:
                        warning_text = f"\n\n✅ Выполнено раньше срока на {minutes} мин."
                    else:
                        warning_text = f"\n\n✅ Выполнено раньше срока"
        
        # Помечаем задачу как выполненную
        update_user_task(str(user_id), task_id, {'completed': True})
        
        await query.edit_message_text(
            f"✅ Задача <b>{task_title}</b> выполнена!{warning_text}",
            parse_mode='HTML'
        )
        
        # Если задача была из расписания, возвращаем к расписанию того же периода
        if schedule_period:
            # Обновляем расписание того же периода
            await refresh_schedule(query, context, schedule_period, str(user_id))
        else:
            # Иначе отправляем обновленный список задач
            await send_updated_task_list(query.message, str(user_id))
        
        context.user_data.clear()
        return ConversationHandler.END
    
    elif query.data == "task_confirm_no":
        # Спрашиваем о переносе дедлайна
        await query.edit_message_text(
            f"Задача: <b>{task_title}</b>\n\n"
            "Перенести на:",
            parse_mode='HTML'
        )
        return WAITING_TASK_RESCHEDULE


async def task_reschedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка переноса дедлайна задачи"""
    task_id = context.user_data.get('task_id')
    task_title = context.user_data.get('task_title', '')
    user_id = update.effective_user.id
    
    text = None
    
    # Проверяем голосовое сообщение
    if update.message.voice:
        # Пробуем использовать caption от Telegram (если есть)
        if update.message.caption:
            text = update.message.caption.strip()
            text = normalize_voice_text(text)
        elif VOICE_SUPPORT:
            try:
                voice_file = await update.message.voice.get_file()
                transcribed_text = await transcribe_voice(voice_file, update)
                if transcribed_text:
                    text = transcribed_text.strip()
                    await update.message.reply_text(text)
                else:
                    await update.message.reply_text(
                        "Не удалось распознать голосовое сообщение. Попробуйте написать текстом:"
                    )
                    return WAITING_TASK_RESCHEDULE
            except Exception as e:
                print(f"Ошибка при обработке голосового сообщения: {e}")
                await update.message.reply_text("Ошибка обработки голосового сообщения. Попробуйте написать текстом:")
                return WAITING_TASK_RESCHEDULE
        else:
            await update.message.reply_text("Повторите текстом:")
            return WAITING_TASK_RESCHEDULE
    elif update.message.text:
        text = update.message.text.strip()
    
    if not text:
        await update.message.reply_text("Ошибка: Введите дату. Попробуйте снова:")
        return WAITING_TASK_RESCHEDULE
    
    # Парсим новый дедлайн
    deadline_dt = parse_deadline(text)
    if deadline_dt is None:
        await update.message.reply_text(
            "Ошибка: Неверный формат даты. Попробуйте снова:\n\n"
            "Примеры: 'завтра', '15 февраля', '15.02.2026 18:00'"
        )
        return WAITING_TASK_RESCHEDULE
    
    # Обновляем дедлайн задачи
    update_user_task(str(user_id), task_id, {'deadline': deadline_dt.isoformat()})
    
    deadline_formatted = format_deadline_readable(deadline_dt)
    schedule_period = context.user_data.get('schedule_period')
    
    await update.message.reply_text(
        f"✅ Дедлайн задачи <b>{task_title}</b> перенесен на {deadline_formatted}",
        parse_mode='HTML'
    )
    
    # Если задача была из расписания, возвращаем к расписанию того же периода
    if schedule_period:
        # Создаем фиктивный query для обновления расписания
        from telegram import Update as TelegramUpdate
        fake_query = type('obj', (object,), {
            'data': schedule_period,
            'from_user': update.effective_user,
            'message': update.message,
            'answer': lambda: None,
            'edit_message_text': lambda text, **kwargs: update.message.reply_text(text, **kwargs)
        })()
        fake_update = TelegramUpdate(update_id=update.update_id, callback_query=fake_query)
        await schedule_callback(fake_update, context)
    else:
        # Иначе отправляем обновленный список задач
        await send_updated_task_list(update.message, str(user_id))
    
    context.user_data.clear()
    return ConversationHandler.END


async def refresh_schedule(query, context: ContextTypes.DEFAULT_TYPE, schedule_period: str, user_id: str):
    """Обновление расписания после выполнения задачи"""
    # Создаем фиктивный query для обновления расписания
    from telegram import Update as TelegramUpdate
    fake_query = type('obj', (object,), {
        'data': schedule_period,
        'from_user': query.from_user,
        'message': query.message,
        'answer': lambda: None,
        'edit_message_text': lambda text, **kwargs: query.message.reply_text(text, **kwargs)
    })()
    fake_update = TelegramUpdate(update_id=0, callback_query=fake_query)
    await schedule_callback(fake_update, context)


async def send_updated_task_list(message, user_id: str):
    """Отправка обновленного списка задач"""
    tasks = get_user_tasks(user_id)
    
    if not tasks:
        await message.reply_text("У вас пока нет задач.\nИспользуйте /add для добавления задачи.")
        return
    
    text = f"<b>Ваши задачи</b> ({len(tasks)}):\n\n"
    
    recurrence_names = {
        'once': 'Одноразовая',
        'daily': 'Ежедневная',
        'weekly': 'Еженедельная'
    }
    
    keyboard = []
    
    for i, task in enumerate(tasks, 1):
        completed = task.get('completed', False)
        if completed:
            status = "✅"
            task_title = f"<s>{task['title']}</s>"
        else:
            status = "⏳"
            task_title = f"<b>{task['title']}</b>"
        
        text += f"{i}. {status} {task_title}\n"
        if task.get('comment'):
            text += f"   Комментарий: {task['comment']}\n"
        if task.get('project'):
            text += f"   Проект: {task['project']}\n"
        if task.get('deadline'):
            deadline_dt = datetime.fromisoformat(task['deadline'])
            deadline_formatted = format_deadline_readable(deadline_dt)
            text += f"   Дедлайн: {deadline_formatted}\n"
        if task.get('reminder'):
            reminder_dt = datetime.fromisoformat(task['reminder'])
            text += f"   Напоминание: {reminder_dt.strftime('%d.%m.%Y %H:%M')}\n"
        recurrence = task.get('recurrence', 'once')
        text += f"   Регулярность: {recurrence_names.get(recurrence, 'Одноразовая')}\n"
        text += "\n"
        
        # Добавляем кнопку для задачи (только если не выполнена)
        if not completed:
            task_id = task.get('id', '')
            button_text = task['title'][:40] + "..." if len(task['title']) > 40 else task['title']
            keyboard.append([InlineKeyboardButton(f"{i}. {button_text}", callback_data=f"task_complete_{task_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)


# ========== ОБРАБОТЧИКИ РЕДАКТИРОВАНИЯ ЗАДАЧ ==========

async def edit_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало редактирования задачи - показываем список задач"""
    user_id = update.effective_user.id
    tasks = get_user_tasks(str(user_id))
    
    if not tasks:
        await update.message.reply_text("У вас пока нет задач для редактирования.")
        return ConversationHandler.END
    
    text = "<b>Выберите задачу для редактирования:</b>\n\n"
    keyboard = []
    
    recurrence_names = {
        'once': 'Одноразовая',
        'daily': 'Ежедневная',
        'weekly': 'Еженедельная'
    }
    
    for i, task in enumerate(tasks, 1):
        completed = task.get('completed', False)
        status = "✅" if completed else "⏳"
        task_title = task.get('title', 'Без названия')
        
        # Формируем краткую информацию о задаче
        task_info = f"{i}. {status} {task_title}"
        if task.get('deadline'):
            deadline_dt = datetime.fromisoformat(task['deadline'])
            deadline_formatted = format_deadline_readable(deadline_dt)
            task_info += f" ({deadline_formatted})"
        
        text += f"{task_info}\n"
        
        # Добавляем кнопку для задачи
        button_text = task_title[:40] + "..." if len(task_title) > 40 else task_title
        keyboard.append([InlineKeyboardButton(f"{i}. {button_text}", callback_data=f"edit_task_{task.get('id', '')}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)
    await save_bot_message(msg, context)
    
    return WAITING_EDIT_TASK_SELECT


async def edit_task_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора задачи для редактирования"""
    query = update.callback_query
    await query.answer()
    
    task_id = query.data.replace("edit_task_", "")
    user_id = query.from_user.id
    task = get_user_task_by_id(str(user_id), task_id)
    
    if not task:
        await query.answer("Задача не найдена", show_alert=True)
        return ConversationHandler.END
    
    # Сохраняем task_id в context
    context.user_data['edit_task_id'] = task_id
    
    # Показываем меню выбора поля для редактирования
    keyboard = [
        [InlineKeyboardButton("Название", callback_data="edit_field_title")],
        [InlineKeyboardButton("Комментарий", callback_data="edit_field_comment")],
        [InlineKeyboardButton("Проект", callback_data="edit_field_project")],
        [InlineKeyboardButton("Дедлайн", callback_data="edit_field_deadline")],
        [InlineKeyboardButton("Напоминание", callback_data="edit_field_reminder")],
        [InlineKeyboardButton("Регулярность", callback_data="edit_field_recurrence")],
        [InlineKeyboardButton("🗑 Удалить задачу", callback_data="edit_field_delete")],
        [InlineKeyboardButton("Отмена", callback_data="edit_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Показываем текущую информацию о задаче
    task_info = f"<b>Задача:</b> {task.get('title', 'Без названия')}\n\n"
    task_info += "<b>Что хотите изменить?</b>"
    
    await query.edit_message_text(task_info, parse_mode='HTML', reply_markup=reply_markup)
    
    return WAITING_EDIT_FIELD_SELECT


async def edit_field_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора поля для редактирования"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "edit_cancel":
        keyboard = get_main_keyboard()
        await query.edit_message_text("Редактирование отменено.", reply_markup=keyboard)
        context.user_data.clear()
        return ConversationHandler.END
    
    field = query.data.replace("edit_field_", "")
    task_id = context.user_data.get('edit_task_id')
    user_id = query.from_user.id
    task = get_user_task_by_id(str(user_id), task_id)
    
    if not task:
        await query.answer("Задача не найдена", show_alert=True)
        return ConversationHandler.END
    
    context.user_data['edit_field'] = field
    
    if field == "title":
        await query.edit_message_text("Введите новое название задачи:")
        return WAITING_EDIT_TITLE
    elif field == "comment":
        await query.edit_message_text("Введите новый комментарий (или /skip для удаления):")
        return WAITING_EDIT_COMMENT
    elif field == "project":
        # Показываем список проектов
        projects = get_user_projects(str(user_id))
        keyboard = []
        for project in projects:
            keyboard.append([InlineKeyboardButton(project, callback_data=f"edit_project_task_{project}")])
        keyboard.append([InlineKeyboardButton("Удалить проект", callback_data="edit_project_task_remove")])
        keyboard.append([InlineKeyboardButton("Отмена", callback_data="edit_cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите проект (или удалите текущий):", reply_markup=reply_markup)
        return WAITING_EDIT_PROJECT
    elif field == "deadline":
        await query.edit_message_text(
            "Введите новый дедлайн (или /skip для удаления).\n\n"
            "Например: завтра 18:00, вторник 14:00, 15.02.2026"
        )
        return WAITING_EDIT_DEADLINE
    elif field == "reminder":
        # Показываем кнопки для напоминания
        keyboard = [
            [InlineKeyboardButton("За час", callback_data="edit_reminder_1h")],
            [InlineKeyboardButton("За 3 часа", callback_data="edit_reminder_3h")],
            [InlineKeyboardButton("За 6 часов", callback_data="edit_reminder_6h")],
            [InlineKeyboardButton("За день", callback_data="edit_reminder_1d")],
            [InlineKeyboardButton("Удалить напоминание", callback_data="edit_reminder_remove")],
            [InlineKeyboardButton("Отмена", callback_data="edit_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите напоминание:", reply_markup=reply_markup)
        return WAITING_EDIT_REMINDER
    elif field == "recurrence":
        # Показываем кнопки для регулярности
        keyboard = [
            [InlineKeyboardButton("Одноразовая", callback_data="edit_recurrence_once")],
            [InlineKeyboardButton("Ежедневная", callback_data="edit_recurrence_daily")],
            [InlineKeyboardButton("Еженедельная", callback_data="edit_recurrence_weekly")],
            [InlineKeyboardButton("Отмена", callback_data="edit_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите регулярность:", reply_markup=reply_markup)
        return WAITING_EDIT_RECURRENCE
    elif field == "delete":
        # Удаление задачи
        if delete_user_task(str(user_id), task_id):
            keyboard = get_main_keyboard()
            await query.edit_message_text("✅ Задача удалена.", reply_markup=keyboard)
        else:
            await query.answer("Не удалось удалить задачу", show_alert=True)
        context.user_data.clear()
        return ConversationHandler.END
    
    return ConversationHandler.END


async def edit_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактирование названия задачи"""
    task_id = context.user_data.get('edit_task_id')
    user_id = update.effective_user.id
    
    text = None
    if update.message.voice:
        if update.message.caption:
            text = update.message.caption.strip()
            text = normalize_voice_text(text)
        elif VOICE_SUPPORT:
            try:
                voice_file = await update.message.voice.get_file()
                transcribed_text = await transcribe_voice(voice_file, update)
                if transcribed_text:
                    text = transcribed_text.strip()
                else:
                    await update.message.reply_text("Не удалось распознать голосовое сообщение. Попробуйте написать текстом:")
                    return WAITING_EDIT_TITLE
            except Exception as e:
                print(f"Ошибка при обработке голосового сообщения: {e}")
                await update.message.reply_text("Ошибка обработки голосового сообщения. Попробуйте написать текстом:")
                return WAITING_EDIT_TITLE
        else:
            await update.message.reply_text("Повторите текстом:")
            return WAITING_EDIT_TITLE
    elif update.message.text:
        text = update.message.text.strip()
    
    if not text:
        await update.message.reply_text("Ошибка: Введите название задачи.")
        return WAITING_EDIT_TITLE
    
    title = capitalize_first(text)
    
    update_user_task(str(user_id), task_id, {'title': title})
    
    await update.message.reply_text(f"✅ Название задачи изменено на: <b>{title}</b>", parse_mode='HTML')
    
    keyboard = get_main_keyboard()
    await update.message.reply_text("Редактирование завершено.", reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END


async def edit_task_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактирование комментария задачи"""
    task_id = context.user_data.get('edit_task_id')
    user_id = update.effective_user.id
    
    if update.message.text and update.message.text.strip() == "/skip":
        update_user_task(str(user_id), task_id, {'comment': None})
        await update.message.reply_text("✅ Комментарий удален.")
    else:
        text = None
        if update.message.voice:
            if update.message.caption:
                text = update.message.caption.strip()
                text = normalize_voice_text(text)
            elif VOICE_SUPPORT:
                try:
                    voice_file = await update.message.voice.get_file()
                    transcribed_text = await transcribe_voice(voice_file, update)
                    if transcribed_text:
                        text = transcribed_text.strip()
                    else:
                        await update.message.reply_text("Не удалось распознать голосовое сообщение. Попробуйте написать текстом:")
                        return WAITING_EDIT_COMMENT
                except Exception as e:
                    print(f"Ошибка при обработке голосового сообщения: {e}")
                    await update.message.reply_text("Ошибка обработки голосового сообщения. Попробуйте написать текстом:")
                    return WAITING_EDIT_COMMENT
            else:
                await update.message.reply_text("Повторите текстом:")
                return WAITING_EDIT_COMMENT
        elif update.message.text:
            text = update.message.text.strip()
        
        if text:
            comment = capitalize_first(text)
            update_user_task(str(user_id), task_id, {'comment': comment})
            await update.message.reply_text(f"✅ Комментарий изменен на: <b>{comment}</b>", parse_mode='HTML')
        else:
            await update.message.reply_text("Ошибка: Введите комментарий или /skip для удаления.")
            return WAITING_EDIT_COMMENT
    
    keyboard = get_main_keyboard()
    await update.message.reply_text("Редактирование завершено.", reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END


async def edit_task_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора проекта при редактировании"""
    query = update.callback_query
    await query.answer()
    
    task_id = context.user_data.get('edit_task_id')
    user_id = query.from_user.id
    
    if query.data == "edit_project_task_remove":
        update_user_task(str(user_id), task_id, {'project': None})
        await query.edit_message_text("✅ Проект удален из задачи.")
    elif query.data.startswith("edit_project_task_"):
        project_name = query.data.replace("edit_project_task_", "")
        if project_name:  # Проверяем, что название не пустое
            update_user_task(str(user_id), task_id, {'project': project_name})
            await query.edit_message_text(f"✅ Проект изменен на: <b>{project_name}</b>", parse_mode='HTML')
        else:
            return WAITING_EDIT_PROJECT
    elif query.data == "edit_cancel":
        keyboard = get_main_keyboard()
        await query.edit_message_text("Редактирование отменено.", reply_markup=keyboard)
        context.user_data.clear()
        return ConversationHandler.END
    else:
        return WAITING_EDIT_PROJECT
    
    keyboard = get_main_keyboard()
    await query.message.reply_text("Редактирование завершено.", reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END


async def edit_task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактирование дедлайна задачи"""
    task_id = context.user_data.get('edit_task_id')
    user_id = update.effective_user.id
    
    if update.message.text and update.message.text.strip() == "/skip":
        update_user_task(str(user_id), task_id, {'deadline': None})
        await update.message.reply_text("✅ Дедлайн удален.")
    else:
        text = None
        if update.message.voice:
            if update.message.caption:
                text = update.message.caption.strip()
                text = normalize_voice_text(text)
            elif VOICE_SUPPORT:
                try:
                    voice_file = await update.message.voice.get_file()
                    transcribed_text = await transcribe_voice(voice_file, update)
                    if transcribed_text:
                        text = transcribed_text.strip()
                    else:
                        await update.message.reply_text("Не удалось распознать голосовое сообщение. Попробуйте написать текстом:")
                        return WAITING_EDIT_DEADLINE
                except Exception as e:
                    print(f"Ошибка при обработке голосового сообщения: {e}")
                    await update.message.reply_text("Ошибка обработки голосового сообщения. Попробуйте написать текстом:")
                    return WAITING_EDIT_DEADLINE
            else:
                await update.message.reply_text("Повторите текстом:")
                return WAITING_EDIT_DEADLINE
        elif update.message.text:
            text = update.message.text.strip()
        
        if not text:
            await update.message.reply_text("Ошибка: Введите дедлайн или /skip для удаления.")
            return WAITING_EDIT_DEADLINE
        
        deadline_dt = parse_deadline(text)
        if deadline_dt is None:
            await update.message.reply_text(
                "Ошибка: Неверный формат даты. Попробуйте снова:\n\n"
                "Примеры: 'завтра', '15 февраля', '15.02.2026 18:00'"
            )
            return WAITING_EDIT_DEADLINE
        
        update_user_task(str(user_id), task_id, {'deadline': deadline_dt.isoformat()})
        deadline_formatted = format_deadline_readable(deadline_dt)
        await update.message.reply_text(f"✅ Дедлайн изменен на: <b>{deadline_formatted}</b>", parse_mode='HTML')
    
    keyboard = get_main_keyboard()
    await update.message.reply_text("Редактирование завершено.", reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END


async def edit_task_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора напоминания при редактировании"""
    query = update.callback_query
    await query.answer()
    
    task_id = context.user_data.get('edit_task_id')
    user_id = query.from_user.id
    task = get_user_task_by_id(str(user_id), task_id)
    
    if not task:
        await query.answer("Задача не найдена", show_alert=True)
        return ConversationHandler.END
    
    if query.data == "edit_reminder_remove":
        update_user_task(str(user_id), task_id, {'reminder': None})
        await query.edit_message_text("✅ Напоминание удалено.")
    elif query.data == "edit_cancel":
        keyboard = get_main_keyboard()
        await query.edit_message_text("Редактирование отменено.", reply_markup=keyboard)
        context.user_data.clear()
        return ConversationHandler.END
    else:
        # Получаем дедлайн задачи
        deadline_str = task.get('deadline')
        if not deadline_str:
            await query.answer("Сначала установите дедлайн для задачи", show_alert=True)
            return WAITING_EDIT_REMINDER
        
        deadline_dt = datetime.fromisoformat(deadline_str)
        if deadline_dt.tzinfo:
            deadline_dt = deadline_dt.replace(tzinfo=None)
        
        # Вычисляем время напоминания
        reminder_offset = query.data.replace("edit_reminder_", "")
        if reminder_offset == "1h":
            reminder_dt = deadline_dt - timedelta(hours=1)
        elif reminder_offset == "3h":
            reminder_dt = deadline_dt - timedelta(hours=3)
        elif reminder_offset == "6h":
            reminder_dt = deadline_dt - timedelta(hours=6)
        elif reminder_offset == "1d":
            reminder_dt = deadline_dt - timedelta(days=1)
        else:
            return WAITING_EDIT_REMINDER
        
        update_user_task(str(user_id), task_id, {'reminder': reminder_dt.isoformat()})
        await query.edit_message_text(
            f"✅ Напоминание установлено на: <b>{reminder_dt.strftime('%d.%m.%Y %H:%M')}</b>",
            parse_mode='HTML'
        )
    
    keyboard = get_main_keyboard()
    await query.message.reply_text("Редактирование завершено.", reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END


async def edit_task_recurrence_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора регулярности при редактировании"""
    query = update.callback_query
    await query.answer()
    
    task_id = context.user_data.get('edit_task_id')
    user_id = query.from_user.id
    
    if query.data == "edit_cancel":
        keyboard = get_main_keyboard()
        await query.edit_message_text("Редактирование отменено.", reply_markup=keyboard)
        context.user_data.clear()
        return ConversationHandler.END
    
    recurrence = query.data.replace("edit_recurrence_", "")
    recurrence_names = {
        'once': 'Одноразовая',
        'daily': 'Ежедневная',
        'weekly': 'Еженедельная'
    }
    
    update_user_task(str(user_id), task_id, {'recurrence': recurrence})
    await query.edit_message_text(
        f"✅ Регулярность изменена на: <b>{recurrence_names.get(recurrence, 'Одноразовая')}</b>",
        parse_mode='HTML'
    )
    
    keyboard = get_main_keyboard()
    await query.message.reply_text("Редактирование завершено.", reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END


# ========== ОБРАБОТЧИКИ РАСПИСАНИЯ ==========

async def schedule_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню расписания с кнопками выбора периода"""
    keyboard = [
        [InlineKeyboardButton("Сегодня", callback_data="schedule_today")],
        [InlineKeyboardButton("Завтра", callback_data="schedule_tomorrow")],
        [InlineKeyboardButton("Неделя", callback_data="schedule_week")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите период:", reply_markup=reply_markup)


async def schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора периода в расписании"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    tasks = get_user_tasks(str(user_id))
    current_time = now()
    if current_time.tzinfo:
        now_dt = current_time.replace(tzinfo=None)
    else:
        now_dt = current_time
    
    filtered_tasks = []
    period_name = ""
    
    # Определяем начало сегодняшнего дня для фильтрации вчерашних задач
    today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if query.data == "schedule_today":
        # Задачи на сегодня
        today_end = now_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        period_name = "сегодня"
        
        for task in tasks:
            if task.get('deadline') and not task.get('completed'):
                deadline_dt = datetime.fromisoformat(task['deadline'])
                if deadline_dt.tzinfo:
                    deadline_dt = deadline_dt.replace(tzinfo=None)
                # Исключаем задачи, которые стали вчерашними (дедлайн до начала сегодняшнего дня)
                if deadline_dt >= today_start and today_start <= deadline_dt <= today_end:
                    filtered_tasks.append(task)
    
    elif query.data == "schedule_tomorrow":
        # Задачи на завтра
        tomorrow_start = (now_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_end = (now_dt + timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
        period_name = "завтра"
        
        for task in tasks:
            if task.get('deadline') and not task.get('completed'):
                deadline_dt = datetime.fromisoformat(task['deadline'])
                if deadline_dt.tzinfo:
                    deadline_dt = deadline_dt.replace(tzinfo=None)
                # Исключаем задачи, которые стали вчерашними (дедлайн до начала сегодняшнего дня)
                if deadline_dt >= today_start and tomorrow_start <= deadline_dt <= tomorrow_end:
                    filtered_tasks.append(task)
    
    elif query.data == "schedule_week":
        # Задачи на неделю (7 дней вперед)
        week_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = (now_dt + timedelta(days=7)).replace(hour=23, minute=59, second=59, microsecond=999999)
        period_name = "неделю"
        
        for task in tasks:
            if task.get('deadline') and not task.get('completed'):
                deadline_dt = datetime.fromisoformat(task['deadline'])
                if deadline_dt.tzinfo:
                    deadline_dt = deadline_dt.replace(tzinfo=None)
                # Исключаем задачи, которые стали вчерашними (дедлайн до начала сегодняшнего дня)
                if deadline_dt >= today_start and week_start <= deadline_dt <= week_end:
                    filtered_tasks.append(task)
    
    # Сортируем задачи по дедлайну (от раннего к позднему)
    filtered_tasks.sort(key=lambda t: datetime.fromisoformat(t['deadline']) if t.get('deadline') else datetime.max)
    
    # Формируем сообщение
    if not filtered_tasks:
        text = f"<b>Задачи на {period_name}</b>\n\n"
        text += "Задач не найдено."
    else:
        # Определяем дату для заголовка (для дня) - используем полный формат
        if query.data == "schedule_today":
            date_header_full = format_date_full(now_dt)
            date_header_key = format_date_readable(now_dt)
        elif query.data == "schedule_tomorrow":
            tomorrow_dt = now_dt + timedelta(days=1)
            date_header_full = format_date_full(tomorrow_dt)
            date_header_key = format_date_readable(tomorrow_dt)
        else:
            date_header_full = None
            date_header_key = None  # Для недели не нужен общий заголовок
        
        # Разделяем задачи на те, что с временем и без времени, группируем по датам
        tasks_by_date = {}  # date_key -> {'with_time': [(deadline_dt, task), ...], 'without_time': [task, ...]}
        
        current_time = now()
        if current_time.tzinfo:
            current_time = current_time.replace(tzinfo=None)
        
        for task in filtered_tasks:
            if task.get('deadline'):
                deadline_dt = datetime.fromisoformat(task['deadline'])
                if deadline_dt.tzinfo:
                    deadline_dt = deadline_dt.replace(tzinfo=None)
                
                # Получаем дату задачи
                date_key = format_date_readable(deadline_dt)
                
                if date_key not in tasks_by_date:
                    tasks_by_date[date_key] = {'with_time': [], 'without_time': []}
                
                # Проверяем, есть ли конкретное время (не конец дня)
                if deadline_dt.hour != 23 or deadline_dt.minute != 59:
                    tasks_by_date[date_key]['with_time'].append((deadline_dt, task))
                else:
                    tasks_by_date[date_key]['without_time'].append(task)
        
        # Формируем текст
        if query.data == "schedule_week":
            # Для недели группируем по датам
            text = f"<b>Расписание на неделю</b> ({len(filtered_tasks)} задач):\n\n"
            
            # Создаем словарь для отображения дат в полном формате
            date_display_map = {}  # date_key -> date_full_format
            for task in filtered_tasks:
                if task.get('deadline'):
                    deadline_dt = datetime.fromisoformat(task['deadline'])
                    if deadline_dt.tzinfo:
                        deadline_dt = deadline_dt.replace(tzinfo=None)
                    date_key = format_date_readable(deadline_dt)
                    if date_key not in date_display_map:
                        date_display_map[date_key] = format_date_full(deadline_dt)
            
            # Сортируем даты в правильном порядке (по datetime дедлайна первой задачи)
            def get_date_sort_key(date_key):
                # Находим первую задачу с этой датой для сортировки
                for task in filtered_tasks:
                    if task.get('deadline'):
                        deadline_dt = datetime.fromisoformat(task['deadline'])
                        if deadline_dt.tzinfo:
                            deadline_dt = deadline_dt.replace(tzinfo=None)
                        if format_date_readable(deadline_dt) == date_key:
                            return deadline_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                return datetime.max
            
            date_order = sorted(tasks_by_date.keys(), key=get_date_sort_key)
            
            for date_key in date_order:
                date_tasks = tasks_by_date[date_key]
                # Используем полный формат даты для отображения
                date_display = date_display_map.get(date_key, date_key)
                text += f"<b>{date_display}</b>\n\n"
                
                # Выводим задачи с временем
                date_tasks['with_time'].sort(key=lambda x: x[0])  # Сортируем по времени
                for deadline_dt, task in date_tasks['with_time']:
                    time_str = deadline_dt.strftime('%H:%M')
                    text += f"<u>{time_str}</u>\n"
                    text += f"<b>{task['title']}</b>\n\n"
                
                # Выводим задачи без времени
                if date_tasks['without_time']:
                    text += "дедлайн:\n"
                    for i, task in enumerate(date_tasks['without_time'], 1):
                        text += f"{i}. <b>{task['title']}</b>\n"
                    text += "\n"
        else:
            # Для дня (сегодня/завтра) показываем дату один раз в полном формате
            text = f"<b>{date_header_full}</b>\n\n"
            
            # Получаем задачи для этой даты (должна быть только одна дата)
            date_tasks = tasks_by_date.get(date_header_key, {'with_time': [], 'without_time': []})
            
            # Выводим задачи с временем
            date_tasks['with_time'].sort(key=lambda x: x[0])  # Сортируем по времени
            for deadline_dt, task in date_tasks['with_time']:
                time_str = deadline_dt.strftime('%H:%M')
                text += f"<u>{time_str}</u>\n"
                text += f"<b>{task['title']}</b>\n\n"
            
            # Выводим задачи без времени
            if date_tasks['without_time']:
                text += "дедлайн:\n"
                for i, task in enumerate(date_tasks['without_time'], 1):
                    text += f"{i}. <b>{task['title']}</b>\n"
                    text += "\n"
        
        # Создаем кнопки для каждой задачи
        keyboard = []
        for task in filtered_tasks:
            if not task.get('completed'):
                task_id = task.get('id', '')
                task_title = task.get('title', '')
                button_text = task_title[:40] + "..." if len(task_title) > 40 else task_title
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"task_complete_{task_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Сохраняем период расписания в context для последующего использования
        context.user_data['current_schedule_period'] = query.data
        
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)


# ========== ОБРАБОТЧИКИ ДОБАВЛЕНИЯ ПРОЕКТОВ ==========

async def add_project_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления проекта из callback (кнопки)"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "<b>Добавление проекта</b>\n\n"
        "Введите название проекта:",
        parse_mode='HTML'
    )
    return WAITING_PROJECT_NAME


async def add_project_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления проекта"""
    context.user_data.clear()
    await update.message.reply_text(
        "<b>Добавление проекта</b>\n\n"
        "Введите название проекта:",
        parse_mode='HTML'
    )
    return WAITING_PROJECT_NAME


async def add_project_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение названия проекта"""
    project_name = update.message.text.strip()
    
    if not project_name:
        await update.message.reply_text("Ошибка: Название проекта не может быть пустым. Попробуйте снова:")
        return WAITING_PROJECT_NAME
    
    user_id = update.effective_user.id
    projects = get_user_projects(str(user_id))
    
    # Проверяем на дубликаты
    if project_name in projects:
        await update.message.reply_text(
            f"Проект <b>{project_name}</b> уже существует. Введите другое название:",
            parse_mode='HTML'
        )
        return WAITING_PROJECT_NAME
    
    context.user_data['project_name'] = project_name
    
    # Показываем кнопки для выбора типа проекта
    keyboard = [
        [InlineKeyboardButton("Программное", callback_data="project_type_software")],
        [InlineKeyboardButton("Проектное", callback_data="project_type_project")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Проект <b>{project_name}</b>\n\n"
        "Выберите тип проекта:",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    return WAITING_PROJECT_TYPE


async def add_project_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора типа проекта через кнопки"""
    query = update.callback_query
    await query.answer()
    
    project_type = None
    if query.data == "project_type_software":
        project_type = "software"
        type_name = "Программное"
    elif query.data == "project_type_project":
        project_type = "project"
        type_name = "Проектное"
    
    context.user_data['project_type'] = project_type
    
    project_name = context.user_data.get('project_name')
    
    # Если проектный, спрашиваем количество шагов
    if project_type == "project":
        try:
            await query.edit_message_text(
                f"Проект <b>{project_name}</b>\n"
                f"Тип: {type_name}\n\n"
                "Сколько шагов (задач) предположительно предстоит предпринять?",
                parse_mode='HTML'
            )
        except:
            await query.message.reply_text(
                f"Проект <b>{project_name}</b>\n"
                f"Тип: {type_name}\n\n"
                "Сколько шагов (задач) предположительно предстоит предпринять?",
                parse_mode='HTML'
            )
        return WAITING_PROJECT_TARGET_TASKS
    else:
        # Для программного сразу завершаем создание проекта
        user_id = query.from_user.id
        add_user_project(str(user_id), project_name, None, project_type, None)
        
        text_msg = f"✅ Проект <b>{project_name}</b> успешно добавлен!\n"
        text_msg += f"Тип: {type_name}"
        
        keyboard = get_main_keyboard()
        try:
            await query.edit_message_text(text_msg, parse_mode='HTML')
            await query.message.reply_text("Готово!", reply_markup=keyboard)
        except:
            await query.message.reply_text(text_msg, parse_mode='HTML', reply_markup=keyboard)
        
        context.user_data.clear()
        return ConversationHandler.END


async def add_project_target_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение количества шагов для проектного проекта"""
    text = update.message.text.strip()
    project_name = context.user_data.get('project_name')
    
    try:
        target_tasks = int(text)
        if target_tasks <= 0:
            await update.message.reply_text(
                "Ошибка: Количество шагов должно быть положительным числом. Попробуйте снова:"
            )
            return WAITING_PROJECT_TARGET_TASKS
    except ValueError:
        await update.message.reply_text(
            "Ошибка: Введите число. Попробуйте снова:"
        )
        return WAITING_PROJECT_TARGET_TASKS
    
    context.user_data['target_tasks'] = target_tasks
    
    # После ввода количества шагов спрашиваем приоритет
    keyboard = [
        [InlineKeyboardButton("Приоритет 1", callback_data="project_priority_1")],
        [InlineKeyboardButton("Приоритет 2", callback_data="project_priority_2")],
        [InlineKeyboardButton("Приоритет 3", callback_data="project_priority_3")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Проект <b>{project_name}</b>\n"
        f"Запланировано шагов: {target_tasks}\n\n"
        "Выберите приоритет проекта:",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    
    return WAITING_PROJECT_PRIORITY


async def add_project_priority_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора приоритета проекта через кнопки"""
    query = update.callback_query
    await query.answer()
    
    # Извлекаем приоритет из callback_data
    priority_str = query.data.replace("project_priority_", "")
    try:
        priority = int(priority_str)
        if priority not in [1, 2, 3]:
            await query.edit_message_text("Ошибка: Неверный приоритет. Попробуйте снова.")
            return WAITING_PROJECT_PRIORITY
    except ValueError:
        await query.edit_message_text("Ошибка: Неверный формат приоритета. Попробуйте снова.")
        return WAITING_PROJECT_PRIORITY
    
    context.user_data['priority'] = priority
    
    # После выбора приоритета спрашиваем дату окончания проекта
    project_name = context.user_data.get('project_name')
    
    keyboard = [
        [InlineKeyboardButton("Пропустить", callback_data="project_end_date_skip")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            f"Проект <b>{project_name}</b>\n"
            f"Приоритет: {priority}\n\n"
            "Введите дату окончания проекта (или нажмите 'Пропустить'):\n\n"
            "Примеры:\n"
            "• завтра\n"
            "• 15 февраля\n"
            "• 25.02.2026\n"
            "• через неделю",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except:
        await query.message.reply_text(
            f"Проект <b>{project_name}</b>\n"
            f"Приоритет: {priority}\n\n"
            "Введите дату окончания проекта (или нажмите 'Пропустить'):\n\n"
            "Примеры:\n"
            "• завтра\n"
            "• 15 февраля\n"
            "• 25.02.2026\n"
            "• через неделю",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    return WAITING_PROJECT_END_DATE


async def add_project_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода даты окончания проекта"""
    # Проверяем, это callback (пропустить) или сообщение с датой
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == "project_end_date_skip":
            # Пропускаем дату окончания
            end_date = None
        else:
            return WAITING_PROJECT_END_DATE
    else:
        # Парсим дату из текста
        text = update.message.text.strip()
        
        if text.lower() in ['пропустить', 'skip', '/skip']:
            end_date = None
        else:
            # Используем parse_deadline для парсинга даты
            deadline_dt = parse_deadline(text)
            
            if deadline_dt is None:
                await update.message.reply_text(
                    "Не удалось распознать дату. Попробуйте снова или напишите 'пропустить':\n\n"
                    "Примеры:\n"
                    "• завтра\n"
                    "• 15 февраля\n"
                    "• 25.02.2026"
                )
                return WAITING_PROJECT_END_DATE
            
            # Сохраняем дату в формате YYYY-MM-DD
            end_date = deadline_dt.strftime('%Y-%m-%d')
    
    context.user_data['end_date'] = end_date
    
    # Завершаем создание проекта
    project_name = context.user_data.get('project_name')
    project_type = context.user_data.get('project_type', 'project')
    target_tasks = context.user_data.get('target_tasks')
    priority = context.user_data.get('priority')
    user_id = update.effective_user.id if update.message else update.callback_query.from_user.id
    
    add_user_project(str(user_id), project_name, None, project_type, target_tasks, priority, end_date)
    
    # Форматируем дату для отображения
    if end_date:
        try:
            end_date_dt = datetime.strptime(end_date, '%Y-%m-%d')
            end_date_formatted = end_date_dt.strftime('%d.%m.%Y')
        except:
            end_date_formatted = end_date
    else:
        end_date_formatted = None
    
    text_msg = f"✅ Проект <b>{project_name}</b> успешно добавлен!\n"
    text_msg += f"Тип: Проектное\n"
    text_msg += f"Запланировано шагов: {target_tasks}\n"
    text_msg += f"Приоритет: {priority}"
    if end_date_formatted:
        text_msg += f"\nДата окончания: {end_date_formatted}"
    
    keyboard = get_main_keyboard()
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text_msg, parse_mode='HTML')
            await update.callback_query.message.reply_text("Готово!", reply_markup=keyboard)
        except:
            await update.callback_query.message.reply_text(text_msg, parse_mode='HTML', reply_markup=keyboard)
    else:
        await update.message.reply_text(text_msg, parse_mode='HTML', reply_markup=keyboard)
    
    context.user_data.clear()
    return ConversationHandler.END


async def add_project_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение категории проекта"""
    text = update.message.text.strip()
    project_name = context.user_data.get('project_name')
    project_type = context.user_data.get('project_type', 'software')
    target_tasks = context.user_data.get('target_tasks')
    user_id = update.effective_user.id
    
    if not text or text.lower() == '/skip' or text.lower() == 'skip':
        category = None
    else:
        category = text
    
    # Сохраняем проект
    add_user_project(str(user_id), project_name, category, project_type, target_tasks)
    
    type_name = "Программное" if project_type == "software" else "Проектное"
    text_msg = f"✅ Проект <b>{project_name}</b> успешно добавлен!\n"
    text_msg += f"Тип: {type_name}"
    if target_tasks:
        text_msg += f"\nЗапланировано шагов: {target_tasks}"
    if category:
        text_msg += f"\nКатегория: {category}"
    
    keyboard = get_main_keyboard()
    await update.message.reply_text(text_msg, parse_mode='HTML', reply_markup=keyboard)
    
    context.user_data.clear()
    return ConversationHandler.END


# ========== ОБРАБОТЧИКИ РАЗДЕЛА ПРОЕКТЫ ==========

async def projects_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список проектов как кнопки"""
    try:
        user_id = update.effective_user.id
        projects = get_user_projects(str(user_id))
        
        if not projects:
            keyboard = [
                [InlineKeyboardButton("Добавить...", callback_data="add_project")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "У вас пока нет проектов.",
                reply_markup=reply_markup
            )
            return
        
        # Создаем кнопки для проектов
        keyboard = []
        for project in projects:
            if project:  # Проверяем, что название проекта не пустое
                # Telegram ограничивает callback_data до 64 байт
                # "project_info_" = 13 символов, оставляем ~50 байт для названия
                prefix = "project_info_"
                max_project_bytes = 64 - len(prefix.encode('utf-8')) - 1  # -1 для безопасности
                
                project_bytes = project.encode('utf-8')
                if len(project_bytes) > max_project_bytes:
                    # Если название слишком длинное, обрезаем его по байтам
                    truncated_bytes = project_bytes[:max_project_bytes]
                    # Убираем неполные символы в конце
                    try:
                        project_callback = truncated_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        # Если последний байт неполный, убираем его
                        project_callback = truncated_bytes[:-1].decode('utf-8', errors='ignore')
                else:
                    project_callback = project
                
                callback_data = f"{prefix}{project_callback}"
                keyboard.append([InlineKeyboardButton(project, callback_data=callback_data)])
        
        # Добавляем кнопки "Редактировать" и "Добавить..."
        keyboard.append([InlineKeyboardButton("Редактировать", callback_data="edit_projects_list")])
        keyboard.append([InlineKeyboardButton("Добавить...", callback_data="add_project")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<b>Проекты</b> ({len(projects)}):",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"Ошибка в projects_list: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text("Произошла ошибка при загрузке проектов.")


async def project_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на проект"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "projects_list":
        await projects_list_callback(update, context)
        return
    
    if query.data == "projects_summary":
        await show_projects_summary(query, context)
        return
    
    # Обработка кнопки "Редактировать" в списке проектов
    if query.data == "edit_projects_list":
        await edit_projects_list_start(query, context)
        return
    
    # Извлекаем название проекта из callback_data
    if query.data.startswith("project_info_"):
        project_name = query.data.replace("project_info_", "")
        await show_project_info(query, context, project_name)
        return
    
    # Обработка кнопки "Задачи по проекту"
    if query.data.startswith("project_tasks_"):
        project_name = query.data.replace("project_tasks_", "")
        await show_project_tasks(query, context, project_name)
        return
    
    # Обработка кнопки "Редактировать название проекта"
    if query.data.startswith("edit_project_name_"):
        # Обрабатывается через ConversationHandler
        return
    
    # Обработка кнопки "Редактировать проект" и "Проект готов?" 
    # обрабатываются через ConversationHandler


async def show_projects_summary_from_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику проектов из главного меню"""
    # Завершаем любые активные ConversationHandler
    if context.user_data.get('edit_project_name') or context.user_data.get('edit_project_name_old'):
        context.user_data.clear()
    
    user_id = update.effective_user.id
    data = load_data()
    projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
    all_tasks = get_user_tasks(str(user_id))
    
    if not projects_data:
        await update.message.reply_text("У вас пока нет проектов.")
        return
    
    # Разделяем проекты на программные и проектные, активные и завершенные
    software_projects = []
    project_projects = []
    completed_projects = []
    
    for project_name, project_data in projects_data.items():
        project_type = project_data.get('type', 'software')
        is_completed = project_data.get('completed', False)
        
        if is_completed:
            completed_projects.append((project_name, project_data))
        elif project_type == 'software':
            software_projects.append((project_name, project_data))
        else:
            project_projects.append((project_name, project_data))
    
    text = "<b>Статистика</b>\n\n"
    
    # Программные проекты
    if software_projects:
        text += "<b>Программные:</b>\n"
        current_time = now()
        if current_time.tzinfo:
            current_time = current_time.replace(tzinfo=None)
        
        for project_name, project_data in software_projects:
            project_tasks = [task for task in all_tasks if task.get('project') == project_name]
            
            # Сортируем задачи по дате создания (новые первые)
            sorted_tasks = sorted(project_tasks, key=lambda t: t.get('created_at', ''), reverse=True)
            # Берем последние 10 задач для прогресс-бара
            recent_tasks = sorted_tasks[:10]
            
            # Формируем прогресс-бар из эмодзи статусов
            progress_bar = ""
            for task in recent_tasks:
                completed = task.get('completed', False)
                
                if completed:
                    # Выполнено - зеленый
                    progress_bar += "🟢"
                # Невыполненные задачи не показываем в прогресс-баре
            
            if not progress_bar:
                progress_bar = "Нет выполненных задач"
            
            text += f"{project_name}: {progress_bar}\n"
        text += "\n"
    else:
        text += "<b>Программные:</b>\nНет программных проектов\n\n"
    
    # Проектные проекты
    if project_projects:
        text += "<b>Проектные:</b>\n"
        for project_name, project_data in project_projects:
            project_tasks = [task for task in all_tasks if task.get('project') == project_name]
            completed_tasks = sum(1 for task in project_tasks if task.get('completed', False))
            target_tasks = project_data.get('target_tasks')
            
            if target_tasks is not None and target_tasks > 0:
                progress = min(completed_tasks / target_tasks * 100, 100)
            else:
                total_tasks = len(project_tasks)
                if total_tasks > 0:
                    progress = min(completed_tasks / total_tasks * 100, 100)
                else:
                    progress = 0
            
            bar_length = 20
            filled = int(progress / 100 * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            text += f"{project_name}:\n{bar} {progress:.0f}%\n"
    else:
        text += "<b>Проектные:</b>\nНет проектных проектов\n"
    
    # Завершенные проекты
    if completed_projects:
        text += "\n<b>Завершенные проекты:</b>\n"
        for project_name, project_data in completed_projects:
            text += f"<s>{project_name}</s>\n"
    
    await update.message.reply_text(text, parse_mode='HTML')


async def show_projects_summary(query, context: ContextTypes.DEFAULT_TYPE):
    """Показать сводную информацию по всем проектам"""
    user_id = query.from_user.id
    data = load_data()
    projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
    all_tasks = get_user_tasks(str(user_id))
    
    # Разделяем проекты на проектные и программные, активные и завершенные
    project_projects = []
    software_projects = []
    completed_projects = []
    
    for project_name, project_data in projects_data.items():
        project_type = project_data.get('type', 'software')
        is_completed = project_data.get('completed', False)
        
        if is_completed:
            completed_projects.append((project_name, project_data))
        elif project_type == 'project':
            project_projects.append((project_name, project_data))
        else:
            software_projects.append((project_name, project_data))
    
    text = "<b>Статистика</b>\n\n"
    
    # Проектные проекты - сортируем по приоритету (1, 2, 3), затем по названию
    if project_projects:
        # Сортируем проекты: сначала по приоритету (1 - самый высокий), затем по названию
        def sort_key(item):
            project_name, project_data = item
            priority = project_data.get('priority', 3)  # По умолчанию приоритет 3 (низкий)
            return (priority, project_name.lower())
        
        sorted_project_projects = sorted(project_projects, key=sort_key)
        
        text += "<b>Проектные:</b>\n\n"
        for project_name, project_data in sorted_project_projects:
            project_tasks = [task for task in all_tasks if task.get('project') == project_name]
            completed_tasks = sum(1 for task in project_tasks if task.get('completed', False))
            target_tasks = project_data.get('target_tasks')
            priority = project_data.get('priority', 3)
            end_date = project_data.get('end_date')
            
            if target_tasks is not None and target_tasks > 0:
                progress = min(completed_tasks / target_tasks * 100, 100)
            else:
                total_tasks = len(project_tasks)
                if total_tasks > 0:
                    progress = min(completed_tasks / total_tasks * 100, 100)
                else:
                    progress = 0
            
            # Прогресс бар (20 символов)
            bar_length = 20
            filled = int(progress / 100 * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            
            # Формируем строку с информацией о проекте
            project_info = f"{project_name} [Приоритет {priority}]"
            if end_date:
                try:
                    end_date_dt = datetime.strptime(end_date, '%Y-%m-%d')
                    end_date_formatted = end_date_dt.strftime('%d.%m.%Y')
                    project_info += f"\n📅 До {end_date_formatted}"
                except:
                    project_info += f"\n📅 До {end_date}"
            
            text += f"{project_info}:\n{bar} {progress:.0f}%\n\n"
    else:
        text += "<b>Проектные:</b>\nНет проектных проектов\n\n"
    
    # Программные проекты
    if software_projects:
        text += "<b>Программные:</b>\n\n"
        current_time = now()
        if current_time.tzinfo:
            current_time = current_time.replace(tzinfo=None)
        
        for project_name, project_data in software_projects:
            project_tasks = [task for task in all_tasks if task.get('project') == project_name]
            
            # Сортируем задачи по дате создания (новые первые)
            sorted_tasks = sorted(project_tasks, key=lambda t: t.get('created_at', ''), reverse=True)
            # Берем последние 10 задач для прогресс-бара
            recent_tasks = sorted_tasks[:10]
            
            # Формируем прогресс-бар из эмодзи статусов
            progress_bar = ""
            for task in recent_tasks:
                completed = task.get('completed', False)
                
                if completed:
                    # Выполнено - зеленый
                    progress_bar += "🟢"
                # Невыполненные задачи не показываем в прогресс-баре
            
            if not progress_bar:
                progress_bar = "Нет выполненных задач"
            
            text += f"{project_name}\n{progress_bar}\n\n"
    else:
        text += "<b>Программные:</b>\nНет программных проектов\n\n"
    
    # Выполненные проекты
    if completed_projects:
        text += "<b>Завершенные проекты:</b>\n"
        for project_name, project_data in completed_projects:
            text += f"<s>{project_name}</s>\n"
        text += "\n"
    
    # Кнопка "Назад"
    keyboard = [
        [InlineKeyboardButton("← Назад", callback_data="projects_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)


async def show_project_info(query, context: ContextTypes.DEFAULT_TYPE, project_name: str):
    """Показать информацию о проекте"""
    user_id = query.from_user.id
    data = load_data()
    projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
    project_data = projects_data.get(project_name, {})
    
    # Получаем тип проекта
    project_type = project_data.get('type', 'software')
    type_name = "Программное" if project_type == "software" else "Проектное"
    
    # Получаем задачи проекта
    all_tasks = get_user_tasks(str(user_id))
    project_tasks = [task for task in all_tasks if task.get('project') == project_name]
    
    # Формируем текст в новом формате
    text = f"<b>{project_name}</b>\n"
    text += f"<i>{type_name}</i>\n"
    
    # Добавляем информацию о приоритете и дате окончания для проектных проектов
    if project_type == "project":
        priority = project_data.get('priority')
        end_date = project_data.get('end_date')
        
        if priority:
            text += f"Приоритет: {priority}\n"
        
        if end_date:
            try:
                end_date_dt = datetime.strptime(end_date, '%Y-%m-%d')
                end_date_formatted = end_date_dt.strftime('%d.%m.%Y')
                text += f"📅 Дата окончания: {end_date_formatted}\n"
            except:
                text += f"📅 Дата окончания: {end_date}\n"
    
    text += "\n"
    
    # Вычисляем прогресс
    completed_tasks = sum(1 for task in project_tasks if task.get('completed', False))
    
    if project_type == "software":
        # Программное: прогресс-бар из статусов последних задач
        # Сортируем задачи по дате создания (новые первые)
        sorted_tasks = sorted(project_tasks, key=lambda t: t.get('created_at', ''), reverse=True)
        # Берем последние 10 задач для прогресс-бара
        recent_tasks = sorted_tasks[:10]
        
        current_time = now()
        if current_time.tzinfo:
            current_time = current_time.replace(tzinfo=None)
        
        # Формируем прогресс-бар из эмодзи статусов
        progress_bar = ""
        for task in recent_tasks:
            completed = task.get('completed', False)
            
            if completed:
                # Выполнено - зеленый
                progress_bar += "🟢"
            # Невыполненные задачи не показываем в прогресс-баре
        
        # Если задач нет, показываем сообщение
        if not progress_bar:
            progress_bar = "Нет выполненных задач"
        
        text += f"{progress_bar}\n"
    else:
        # Проектное: прогресс по выполненным/запланированным шагам
        target_tasks = project_data.get('target_tasks')
        if target_tasks is not None and target_tasks > 0:
            progress = min(completed_tasks / target_tasks * 100, 100)
            text += f"Прогресс: {completed_tasks}/{target_tasks} задач\n"
        else:
            # Если target_tasks не задан, используем общее количество задач
            total_tasks = len(project_tasks)
            if total_tasks > 0:
                progress = min(completed_tasks / total_tasks * 100, 100)
                text += f"Прогресс: {completed_tasks}/{total_tasks} задач\n"
            else:
                progress = 0
                text += f"Прогресс: 0/0 задач\n"
        
        # Прогресс бар (20 символов)
        bar_length = 20
        filled = int(progress / 100 * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)
        text += f"{bar} {progress:.0f}%\n"
    
    # Кнопки для проектного проекта
    keyboard = []
    if project_type == "project":
        keyboard.append([InlineKeyboardButton("Задачи по проекту", callback_data=f"project_tasks_{project_name}")])
        keyboard.append([InlineKeyboardButton("Редактировать проект", callback_data=f"edit_project_{project_name}")])
        keyboard.append([InlineKeyboardButton("Проект готов?", callback_data=f"project_complete_{project_name}")])
    else:
        keyboard.append([InlineKeyboardButton("Задачи по проекту", callback_data=f"project_tasks_{project_name}")])
    
    keyboard.append([InlineKeyboardButton("← Назад", callback_data="projects_list")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)


async def show_project_tasks(query, context: ContextTypes.DEFAULT_TYPE, project_name: str):
    """Показать задачи проекта с кнопками"""
    user_id = query.from_user.id
    all_tasks = get_user_tasks(str(user_id))
    project_tasks = [task for task in all_tasks if task.get('project') == project_name]
    
    if not project_tasks:
        keyboard = [
            [InlineKeyboardButton("← Назад к проекту", callback_data=f"project_info_{project_name}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f'<b>Задачи проекта "{project_name}"</b>\n\nЗадач не найдено.',
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    text = f'<b>Задачи проекта "{project_name}"</b> ({len(project_tasks)}):\n\n'
    
    recurrence_names = {
        'once': 'Одноразовая',
        'daily': 'Ежедневная',
        'weekly': 'Еженедельная'
    }
    
    keyboard = []
    
    # Сортируем задачи по дедлайну (если есть)
    def get_task_sort_key(task):
        if task.get('deadline'):
            deadline_dt = datetime.fromisoformat(task['deadline'])
            if deadline_dt.tzinfo:
                deadline_dt = deadline_dt.replace(tzinfo=None)
            return deadline_dt
        return datetime.max
    
    sorted_tasks = sorted(project_tasks, key=get_task_sort_key)
    
    for i, task in enumerate(sorted_tasks, 1):
        completed = task.get('completed', False)
        
        # Формируем дату дедлайна (жирным) с днем недели
        if task.get('deadline'):
            deadline_dt = datetime.fromisoformat(task['deadline'])
            if deadline_dt.tzinfo:
                deadline_dt = deadline_dt.replace(tzinfo=None)
            deadline_formatted = format_date_full(deadline_dt)
            text += f"<b>{deadline_formatted}</b>\n"
        else:
            text += "<b>Без дедлайна</b>\n"
        
        # Название задачи (подчеркнуто)
        if completed:
            task_title = f"<s><u>{task['title']}</u></s>"
        else:
            task_title = f"<u>{task['title']}</u>"
        text += f"{task_title}\n"
        
        # Комментарий (курсивом, с новой строки)
        if task.get('comment'):
            text += f"<i>{task['comment']}</i>\n"
        
        text += "\n"
        
        # Добавляем кнопку для задачи (только если не выполнена)
        if not completed:
            task_id = task.get('id', '')
            # Обрезаем название задачи для кнопки, если слишком длинное
            button_text = task['title'][:40] + "..." if len(task['title']) > 40 else task['title']
            keyboard.append([InlineKeyboardButton(f"{i}. {button_text}", callback_data=f"task_complete_{task_id}")])
    
    # Добавляем кнопку "Назад к проекту"
    keyboard.append([InlineKeyboardButton("← Назад к проекту", callback_data=f"project_info_{project_name}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    # Сохраняем период расписания в context для последующего использования (если задача будет выполнена)
    context.user_data['current_schedule_period'] = None  # Не из расписания
    
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)


async def edit_project_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало редактирования проекта - запрос нового количества шагов"""
    query = update.callback_query
    await query.answer()
    
    # Извлекаем обрезанное название проекта из callback_data
    truncated_name = query.data.replace("edit_project_", "")
    
    # Получаем полное имя проекта из маппинга (если был сохранен)
    project_mapping = context.user_data.get('project_name_mapping', {})
    project_name = project_mapping.get(truncated_name, truncated_name)
    
    # Если маппинга нет, пытаемся найти проект по обрезанному имени
    user_id = query.from_user.id
    if project_name == truncated_name:
        projects = get_user_projects(str(user_id))
        # Ищем проект, который начинается с обрезанного имени или содержит его
        for proj in projects:
            if proj.startswith(truncated_name) or truncated_name in proj:
                project_name = proj
                break
    
    data = load_data()
    projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
    project_data = projects_data.get(project_name, {})
    
    # Проверяем, что проект найден и является проектным
    if not project_data:
        await query.answer("Проект не найден", show_alert=True)
        return ConversationHandler.END
    
    project_type = project_data.get('type', 'programmatic')
    if project_type != 'project':
        await query.answer("Эта функция доступна только для проектных проектов", show_alert=True)
        return ConversationHandler.END
    
    current_target = project_data.get('target_tasks', 0)
    
    context.user_data['edit_project_name'] = project_name
    
    await query.edit_message_text(
        f"Проект: <b>{project_name}</b>\n"
        f"Текущее количество шагов: {current_target}\n\n"
        "Введите новое количество шагов:",
        parse_mode='HTML'
    )
    return WAITING_EDIT_PROJECT_TARGET_TASKS


async def edit_project_target_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода нового количества шагов проекта"""
    text = update.message.text.strip()
    project_name = context.user_data.get('edit_project_name')
    user_id = update.effective_user.id
    
    try:
        target_tasks = int(text)
        if target_tasks <= 0:
            await update.message.reply_text(
                "Ошибка: Количество шагов должно быть положительным числом. Попробуйте снова:"
            )
            return WAITING_EDIT_PROJECT_TARGET_TASKS
    except ValueError:
        await update.message.reply_text(
            "Ошибка: Введите число. Попробуйте снова:"
        )
        return WAITING_EDIT_PROJECT_TARGET_TASKS
    
    # Проверяем, что проект найден
    if not project_name:
        await update.message.reply_text(
            "Ошибка: Не найдено название проекта. Попробуйте снова."
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    # Обновляем проект
    try:
        update_user_project(str(user_id), project_name, {'target_tasks': target_tasks})
        
        # Получаем обновленную информацию о проекте
        data = load_data()
        projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
        project_data = projects_data.get(project_name, {})
        project_type = project_data.get('type', 'software')
        type_name = "Программное" if project_type == "software" else "Проектное"
        
        # Получаем задачи проекта
        all_tasks = get_user_tasks(str(user_id))
        project_tasks = [task for task in all_tasks if task.get('project') == project_name]
        completed_tasks = sum(1 for task in project_tasks if task.get('completed', False))
        
        # Формируем текст с информацией о проекте
        text = f"✅ Количество шагов проекта <b>{project_name}</b> обновлено до {target_tasks}\n\n"
        text += f"<b>{project_name}</b>\n"
        text += f"<i>{type_name}</i>\n\n"
        
        if project_type == "project":
            # Проектное: прогресс по выполненным/запланированным шагам
            current_target = project_data.get('target_tasks', target_tasks)
            if current_target is not None and current_target > 0:
                progress = min(completed_tasks / current_target * 100, 100)
                text += f"Прогресс: {completed_tasks}/{current_target} задач\n"
            else:
                total_tasks = len(project_tasks)
                if total_tasks > 0:
                    progress = min(completed_tasks / total_tasks * 100, 100)
                    text += f"Прогресс: {completed_tasks}/{total_tasks} задач\n"
                else:
                    progress = 0
                    text += f"Прогресс: 0/0 задач\n"
            
            # Прогресс бар (20 символов)
            bar_length = 20
            filled = int(progress / 100 * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            text += f"{bar} {progress:.0f}%\n"
        
        # Отправляем сообщение с информацией о проекте
        keyboard = []
        if project_type == "project":
            keyboard.append([InlineKeyboardButton("Задачи по проекту", callback_data=f"project_tasks_{project_name}")])
            keyboard.append([InlineKeyboardButton("Редактировать проект", callback_data=f"edit_project_{project_name}")])
            keyboard.append([InlineKeyboardButton("Проект готов?", callback_data=f"project_complete_{project_name}")])
        else:
            keyboard.append([InlineKeyboardButton("Задачи по проекту", callback_data=f"project_tasks_{project_name}")])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data="projects_list")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"[ERROR] Ошибка при обновлении количества шагов: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(
            f"Ошибка: Не удалось обновить количество шагов.\n\n"
            f"Ошибка: {str(e)}",
            parse_mode='HTML'
        )
    
    context.user_data.clear()
    return ConversationHandler.END


async def edit_projects_list_start(query, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список проектов для редактирования названий"""
    await query.answer()
    
    user_id = query.from_user.id
    projects = get_user_projects(str(user_id))
    
    if not projects:
        await query.edit_message_text("У вас пока нет проектов для редактирования.")
        return
    
    keyboard = []
    # Сохраняем маппинг обрезанных имен на полные имена в user_data
    project_mapping = {}
    for project in projects:
        if project:
            prefix = "edit_project_name_"
            max_project_bytes = 64 - len(prefix.encode('utf-8')) - 1
            
            project_bytes = project.encode('utf-8')
            if len(project_bytes) > max_project_bytes:
                truncated_bytes = project_bytes[:max_project_bytes]
                try:
                    project_callback = truncated_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    project_callback = truncated_bytes[:-1].decode('utf-8', errors='ignore')
            else:
                project_callback = project
            
            # Сохраняем маппинг
            project_mapping[project_callback] = project
            
            callback_data = f"{prefix}{project_callback}"
            keyboard.append([InlineKeyboardButton(project, callback_data=callback_data)])
    
    # Сохраняем маппинг в context для использования в edit_project_name_start
    context.user_data['project_name_mapping'] = project_mapping
    
    keyboard.append([InlineKeyboardButton("← Назад", callback_data="projects_list")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "<b>Выберите проект для редактирования названия:</b>",
        parse_mode='HTML',
        reply_markup=reply_markup
    )


async def edit_project_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало редактирования названия проекта"""
    query = update.callback_query
    await query.answer()
    
    # Извлекаем обрезанное имя из callback_data
    truncated_name = query.data.replace("edit_project_name_", "")
    
    # Получаем полное имя проекта из маппинга
    project_mapping = context.user_data.get('project_name_mapping', {})
    project_name = project_mapping.get(truncated_name, truncated_name)
    
    # Если маппинга нет, пытаемся найти проект по обрезанному имени
    if project_name == truncated_name:
        user_id = query.from_user.id
        projects = get_user_projects(str(user_id))
        # Ищем проект, который начинается с обрезанного имени
        for proj in projects:
            if proj.startswith(truncated_name) or truncated_name in proj:
                project_name = proj
                break
    
    user_id = query.from_user.id
    context.user_data['edit_project_name_old'] = project_name
    
    await query.edit_message_text(
        f"Проект: <b>{project_name}</b>\n\n"
        "Введите новое название проекта:",
        parse_mode='HTML'
    )
    return WAITING_EDIT_PROJECT_NAME


async def edit_project_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода нового названия проекта"""
    text = update.message.text.strip()
    old_name = context.user_data.get('edit_project_name_old')
    user_id = update.effective_user.id
    
    if not text:
        await update.message.reply_text("Ошибка: Введите название проекта.")
        return WAITING_EDIT_PROJECT_NAME
    
    if not old_name:
        await update.message.reply_text("Ошибка: Не найдено старое название проекта. Попробуйте снова.")
        context.user_data.clear()
        return ConversationHandler.END
    
    new_name = text
    
    # Проверяем, что новое имя не занято
    projects = get_user_projects(str(user_id))
    data = load_data()
    projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
    
    # Проверяем активные проекты (исключая текущий проект)
    if new_name in projects and new_name != old_name:
        await update.message.reply_text(
            f"Ошибка: Проект с названием <b>{new_name}</b> уже существует.",
            parse_mode='HTML'
        )
        return WAITING_EDIT_PROJECT_NAME
    
    # Проверяем все проекты (включая завершенные, исключая текущий)
    if new_name in projects_data and new_name != old_name:
        await update.message.reply_text(
            f"Ошибка: Проект с названием <b>{new_name}</b> уже существует.",
            parse_mode='HTML'
        )
        return WAITING_EDIT_PROJECT_NAME
    
    # Переименовываем проект
    try:
        success = rename_user_project(str(user_id), old_name, new_name)
        if success:
            await update.message.reply_text(
                f"✅ Проект переименован: <b>{old_name}</b> → <b>{new_name}</b>",
                parse_mode='HTML'
            )
            
            # Возвращаемся к информации о проекте с новым именем
            from telegram import Update as TelegramUpdate
            fake_query = type('obj', (object,), {
                'from_user': update.effective_user,
                'message': update.message,
                'answer': lambda: None,
                'edit_message_text': lambda text, **kwargs: update.message.reply_text(text, **kwargs)
            })()
            fake_update = TelegramUpdate(update_id=update.update_id, callback_query=fake_query)
            fake_query.data = f"project_info_{new_name}"
            await show_project_info(fake_query, context, new_name)
        else:
            print(f"[ERROR] Не удалось переименовать проект: old_name='{old_name}', new_name='{new_name}'")
            await update.message.reply_text(
                f"Ошибка: Не удалось переименовать проект <b>{old_name}</b>.\n\n"
                f"Возможные причины:\n"
                f"• Проект не найден\n"
                f"• Новое имя уже занято\n"
                f"• Ошибка сохранения данных",
                parse_mode='HTML'
            )
    except Exception as e:
        print(f"[ERROR] Исключение при переименовании проекта: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(
            f"Ошибка: Произошла ошибка при переименовании проекта: {str(e)}",
            parse_mode='HTML'
        )
    
    context.user_data.clear()
    return ConversationHandler.END


async def project_complete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало подтверждения готовности проекта"""
    query = update.callback_query
    await query.answer()
    
    project_name = query.data.replace("project_complete_", "")
    user_id = query.from_user.id
    context.user_data['complete_project_name'] = project_name
    
    keyboard = [
        [InlineKeyboardButton("Да", callback_data="project_complete_yes")],
        [InlineKeyboardButton("Нет", callback_data="project_complete_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"Проект: <b>{project_name}</b>\n\n"
        "Проект готов?",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    return WAITING_PROJECT_COMPLETE_CONFIRM


async def project_complete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение готовности проекта"""
    query = update.callback_query
    await query.answer()
    
    project_name = context.user_data.get('complete_project_name')
    user_id = query.from_user.id
    
    if query.data == "project_complete_yes":
        # Помечаем проект как завершенный
        update_user_project(str(user_id), project_name, {'completed': True})
        
        await query.edit_message_text(
            f"✅ Проект <b>{project_name}</b> завершен!",
            parse_mode='HTML'
        )
        
        # Возвращаемся к списку проектов
        from telegram import Update as TelegramUpdate
        fake_query = type('obj', (object,), {
            'from_user': query.from_user,
            'message': query.message,
            'answer': lambda: None,
            'edit_message_text': lambda text, **kwargs: query.message.reply_text(text, **kwargs)
        })()
        fake_update = TelegramUpdate(update_id=0, callback_query=fake_query)
        fake_query.data = "projects_list"
        await projects_list_callback(fake_update, context)
        
        context.user_data.clear()
        return ConversationHandler.END
    else:
        # Отмена - возвращаемся к информации о проекте
        from telegram import Update as TelegramUpdate
        fake_query = type('obj', (object,), {
            'from_user': query.from_user,
            'message': query.message,
            'answer': lambda: None,
            'edit_message_text': lambda text, **kwargs: query.message.reply_text(text, **kwargs)
        })()
        fake_update = TelegramUpdate(update_id=0, callback_query=fake_query)
        fake_query.data = f"project_info_{project_name}"
        await show_project_info(fake_query, context, project_name)
        
        context.user_data.clear()
        return ConversationHandler.END


async def projects_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки "Назад" в списке проектов"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    projects = get_user_projects(str(user_id))
    
    if not projects:
        keyboard = [
            [InlineKeyboardButton("Добавить...", callback_data="add_project")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "У вас пока нет проектов.",
            reply_markup=reply_markup
        )
        return
    
    # Создаем кнопки для проектов
    keyboard = []
    for project in projects:
        if project:  # Проверяем, что название проекта не пустое
            # Telegram ограничивает callback_data до 64 байт
            prefix = "project_info_"
            max_project_bytes = 64 - len(prefix.encode('utf-8')) - 1
            
            project_bytes = project.encode('utf-8')
            if len(project_bytes) > max_project_bytes:
                truncated_bytes = project_bytes[:max_project_bytes]
                try:
                    project_callback = truncated_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    project_callback = truncated_bytes[:-1].decode('utf-8', errors='ignore')
            else:
                project_callback = project
            
            callback_data = f"{prefix}{project_callback}"
            keyboard.append([InlineKeyboardButton(project, callback_data=callback_data)])
    
    # Добавляем кнопки "Редактировать", "Статистика" и "Добавить..."
    keyboard.append([InlineKeyboardButton("Редактировать", callback_data="edit_projects_list")])
    keyboard.append([InlineKeyboardButton("Статистика", callback_data="projects_summary")])
    keyboard.append([InlineKeyboardButton("Добавить...", callback_data="add_project")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"<b>Проекты</b> ({len(projects)}):",
        parse_mode='HTML',
        reply_markup=reply_markup
    )


# ========== ОБРАБОТЧИКИ СТАТИСТИКИ ==========

async def stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню статистики"""
    user_id = update.effective_user.id
    tasks = get_user_tasks(str(user_id))
    projects = get_user_projects(str(user_id))
    categories = get_user_project_categories(str(user_id))
    
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.get('completed'))
    incomplete_tasks = total_tasks - completed_tasks
    
    text = "<b>Статистика</b>\n\n"
    text += f"Всего задач: {total_tasks}\n"
    text += f"Выполнено: {completed_tasks}\n"
    text += f"Осталось: {incomplete_tasks}\n"
    text += f"Проектов: {len(projects)}\n"
    text += f"Категорий: {len(categories)}\n"
    
    await update.message.reply_text(text, parse_mode='HTML')


# ========== MAIN ==========

def main():
    """Основная функция запуска бота"""
    # Загружаем токен из .env
    try:
        env_file = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(env_file):
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        value = value.strip().strip("'\"")
                        os.environ[key] = value
    except Exception as e:
        print(f"Ошибка при загрузке .env файла: {e}")
    
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        print("Ошибка: Не указан TELEGRAM_BOT_TOKEN!")
        print("Создайте файл .env с содержимым: TELEGRAM_BOT_TOKEN=ваш_токен")
        return
    
    application = Application.builder().token(token).build()
    
    # ConversationHandler для добавления задачи
    add_task_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('add', add_task_start),
            MessageHandler(filters.Regex('^Добавить задачу$'), add_task_start)
        ],
        states={
            WAITING_TASK_TITLE: [
                MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, add_task_title)
            ],
            WAITING_TASK_COMMENT: [
                CommandHandler('skip', add_task_comment),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_comment),
                MessageHandler(filters.VOICE, add_task_comment)
            ],
            WAITING_TASK_PROJECT: [
                CallbackQueryHandler(add_task_project_callback, pattern='^project_|^new_project|^skip_project'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_project_text)
            ],
            WAITING_TASK_DEADLINE: [
                MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, add_task_deadline),
                CommandHandler('skip', add_task_deadline)
            ],
            WAITING_TASK_REMINDER: [
                CallbackQueryHandler(add_task_reminder_callback, pattern='^reminder_|^skip_reminder'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_reminder),
                CommandHandler('skip', add_task_reminder)
            ],
            WAITING_TASK_RECURRENCE: [
                CallbackQueryHandler(add_task_recurrence_callback, pattern='^recurrence_')
            ],
            WAITING_TASK_CATEGORY: [
                CallbackQueryHandler(add_task_category_callback, pattern='^task_category_')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
        per_chat=True,
    )
    
    application.add_handler(add_task_conv_handler)
    
    # ConversationHandler для добавления проекта
    add_project_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('addproject', add_project_start),
            CallbackQueryHandler(add_project_start_callback, pattern='^add_project$')
        ],
        states={
            WAITING_PROJECT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_project_name)
            ],
            WAITING_PROJECT_TYPE: [
                CallbackQueryHandler(add_project_type_callback, pattern='^project_type_')
            ],
            WAITING_PROJECT_TARGET_TASKS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_project_target_tasks)
            ],
            WAITING_PROJECT_PRIORITY: [
                CallbackQueryHandler(add_project_priority_callback, pattern='^project_priority_')
            ],
            WAITING_PROJECT_END_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_project_end_date),
                CallbackQueryHandler(add_project_end_date, pattern='^project_end_date_')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
        per_chat=True,
    )
    
    application.add_handler(add_project_conv_handler)
    
    # Команды
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('list', list_tasks))
    application.add_handler(CommandHandler('projects', projects_list))
    application.add_handler(CommandHandler('stats', stats_menu))
    
    # ConversationHandler для обработки выполнения задач (регистрируем ПЕРЕД другими callback обработчиками)
    task_complete_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(task_complete_callback, pattern='^task_complete_')
        ],
        states={
            WAITING_TASK_COMPLETE_CONFIRM: [
                CallbackQueryHandler(task_confirm_callback, pattern='^task_confirm_')
            ],
            WAITING_TASK_RESCHEDULE: [
                MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, task_reschedule)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
        per_chat=True,
        per_user=True,
    )
    
    application.add_handler(task_complete_conv_handler)
    
    # ConversationHandler для редактирования задач
    edit_task_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^Редактировать$'), edit_task_start)
        ],
        states={
            WAITING_EDIT_TASK_SELECT: [
                CallbackQueryHandler(edit_task_select_callback, pattern='^edit_task_')
            ],
            WAITING_EDIT_FIELD_SELECT: [
                CallbackQueryHandler(edit_field_select_callback, pattern='^edit_field_|^edit_cancel$')
            ],
            WAITING_EDIT_TITLE: [
                MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, edit_task_title)
            ],
            WAITING_EDIT_COMMENT: [
                MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, edit_task_comment),
                CommandHandler('skip', edit_task_comment)
            ],
            WAITING_EDIT_PROJECT: [
                CallbackQueryHandler(edit_task_project_callback, pattern='^edit_project_task_|^edit_cancel$')
            ],
            WAITING_EDIT_DEADLINE: [
                MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, edit_task_deadline),
                CommandHandler('skip', edit_task_deadline)
            ],
            WAITING_EDIT_REMINDER: [
                CallbackQueryHandler(edit_task_reminder_callback, pattern='^edit_reminder_|^edit_cancel$')
            ],
            WAITING_EDIT_RECURRENCE: [
                CallbackQueryHandler(edit_task_recurrence_callback, pattern='^edit_recurrence_|^edit_cancel$')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
        per_chat=True,
        per_user=True,
    )
    
    application.add_handler(edit_task_conv_handler)
    
    # Обработчик кнопки "Список задач" - показывает меню выбора периода
    application.add_handler(MessageHandler(filters.Regex('^Список задач$'), schedule_menu))
    application.add_handler(CallbackQueryHandler(schedule_callback, pattern='^schedule_'))
    
    # ConversationHandler для редактирования проекта и подтверждения готовности (регистрируем ПЕРЕД другими обработчиками)
    project_edit_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_project_name_start, pattern='^edit_project_name_'),
            CallbackQueryHandler(edit_project_start, pattern='^edit_project_[^n]')  # Не начинается с 'n' чтобы не конфликтовать с edit_project_name_
        ],
        states={
            WAITING_EDIT_PROJECT_TARGET_TASKS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^Статистика$|^Проекты$|^Добавить задачу$|^Список задач$|^Редактировать$'), edit_project_target_tasks)
            ],
            WAITING_EDIT_PROJECT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^Статистика$|^Проекты$|^Добавить задачу$|^Список задач$|^Редактировать$'), edit_project_name)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            MessageHandler(filters.Regex('^Статистика$|^Проекты$|^Добавить задачу$|^Список задач$|^Редактировать$'), lambda u, c: ConversationHandler.END)
        ],
        per_message=False,
        per_chat=True,
        per_user=True,
    )
    
    project_complete_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(project_complete_start, pattern='^project_complete_')
        ],
        states={
            WAITING_PROJECT_COMPLETE_CONFIRM: [
                CallbackQueryHandler(project_complete_confirm, pattern='^project_complete_yes$|^project_complete_no$')
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            MessageHandler(filters.Regex('^Статистика$|^Проекты$|^Добавить задачу$|^Список задач$|^Редактировать$'), lambda u, c: ConversationHandler.END)
        ],
        per_message=False,
        per_chat=True,
        per_user=True,
    )
    
    application.add_handler(project_edit_conv_handler)
    application.add_handler(project_complete_conv_handler)
    
    # Обработчик кнопки "Проекты"
    application.add_handler(MessageHandler(filters.Regex('^Проекты$'), projects_list))
    application.add_handler(CallbackQueryHandler(project_info_callback, pattern='^project_info_|^projects_list$|^projects_summary$|^project_tasks_|^edit_projects_list$'))
    
    # Обработчик кнопки "Статистика" в главном меню
    # Регистрируем ПОСЛЕ ConversationHandler, чтобы он имел приоритет при обработке кнопок главного меню
    application.add_handler(MessageHandler(filters.Regex('^Статистика$'), show_projects_summary_from_menu))
    
    if VOICE_SUPPORT:
        print("✅ Бот запущен! Голосовые сообщения поддерживаются.")
    else:
        print("⚠️  Бот запущен! Голосовые сообщения недоступны (установите SpeechRecognition и pydub)")
    
    # Функция для проверки и отправки напоминаний
    async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
        """Проверка напоминаний и отправка сообщений"""
        data = load_data()
        current_time = now()
        if current_time.tzinfo:
            current_time = current_time.replace(tzinfo=None)
        
        current_minute = current_time.replace(second=0, microsecond=0)
        reminders_checked = 0
        reminders_sent = 0
        
        # Проверяем всех пользователей
        for user_id_str, user_data in data.get('users', {}).items():
            tasks = user_data.get('tasks', [])
            
            for task in tasks:
                # Пропускаем выполненные задачи
                if task.get('completed', False):
                    continue
                
                reminder_str = task.get('reminder')
                if not reminder_str:
                    continue
                
                reminders_checked += 1
                
                try:
                    reminder_dt = datetime.fromisoformat(reminder_str)
                    if reminder_dt.tzinfo:
                        reminder_dt = reminder_dt.replace(tzinfo=None)
                    
                    # Проверяем, наступило ли время напоминания (с точностью до минуты)
                    # Сравниваем только дату и время до минут (без секунд)
                    reminder_minute = reminder_dt.replace(second=0, microsecond=0)
                    
                    # Отправляем напоминание, если текущая минута совпадает с минутой напоминания
                    if reminder_minute == current_minute:
                        # Проверяем, не было ли уже отправлено это напоминание
                        # Используем комбинацию user_id + task_id + дата/время напоминания как ключ
                        reminder_key = f"reminder_{user_id_str}_{task.get('id')}_{reminder_minute.isoformat()}"
                        
                        if not context.bot_data.get(reminder_key, False):
                            # Формируем сообщение
                            task_title = task.get('title', 'Без названия')
                            deadline_str = task.get('deadline')
                            
                            message = f"🔔 <b>Напоминание о задаче</b>\n\n"
                            message += f"<b>{task_title}</b>\n"
                            
                            if deadline_str:
                                deadline_dt = datetime.fromisoformat(deadline_str)
                                if deadline_dt.tzinfo:
                                    deadline_dt = deadline_dt.replace(tzinfo=None)
                                deadline_formatted = format_deadline_readable(deadline_dt)
                                message += f"Дедлайн: {deadline_formatted}\n"
                            
                            if task.get('comment'):
                                message += f"Комментарий: {task.get('comment')}\n"
                            
                            if task.get('project'):
                                message += f"Проект: {task.get('project')}\n"
                            
                            try:
                                await context.bot.send_message(
                                    chat_id=int(user_id_str),
                                    text=message,
                                    parse_mode='HTML'
                                )
                                # Помечаем, что напоминание отправлено
                                context.bot_data[reminder_key] = True
                                reminders_sent += 1
                                print(f"✅ Напоминание отправлено пользователю {user_id_str} для задачи '{task.get('title', 'Без названия')}'")

                                # Обновляем время напоминания для регулярных задач
                                recurrence = task.get('recurrence', 'once')
                                if recurrence in ('daily', 'weekly'):
                                    if recurrence == 'daily':
                                        next_reminder_dt = reminder_dt + timedelta(days=1)
                                    else:
                                        next_reminder_dt = reminder_dt + timedelta(days=7)

                                    # Сохраняем новое время напоминания в задаче
                                    task['reminder'] = next_reminder_dt.isoformat()
                                    data_changed = True

                            except Exception as e:
                                print(f"❌ Ошибка при отправке напоминания пользователю {user_id_str}: {e}")
                
                except Exception as e:
                    print(f"❌ Ошибка при обработке напоминания для задачи {task.get('id')}: {e}")
        
        # Если изменили данные (переназначили напоминания для регулярных задач) — сохраняем
        if data_changed:
            try:
                save_data(data)
            except Exception as e:
                print(f"❌ Ошибка сохранения данных после обновления напоминаний: {e}")

        # Логируем статистику (только если были проверки)
        if reminders_checked > 0:
            print(f"[Напоминания] Проверено: {reminders_checked}, отправлено: {reminders_sent}, текущее время: {current_minute.strftime('%Y-%m-%d %H:%M')}")
        
        # Очищаем старые флаги отправленных напоминаний (старше 1 часа)
        current_keys = list(context.bot_data.keys())
        for key in current_keys:
            if key.startswith("reminder_"):
                # Извлекаем дату/время из ключа (формат: reminder_{user_id}_{task_id}_{iso_datetime})
                try:
                    parts = key.split('_')
                    if len(parts) >= 4:
                        # Последняя часть - это ISO datetime
                        reminder_datetime_str = '_'.join(parts[3:])
                        reminder_datetime = datetime.fromisoformat(reminder_datetime_str)
                        # Удаляем флаги старше 1 часа
                        if (current_time - reminder_datetime).total_seconds() > 3600:
                            del context.bot_data[key]
                except Exception:
                    # Если не удалось распарсить, пропускаем
                    pass
    
    # Добавляем периодическую задачу для проверки напоминаний (каждую минуту)
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_reminders, interval=60, first=10)  # Каждую минуту, первый запуск через 10 секунд
    
    print("Бот запущен! (с кнопкой 'Добавить задачу')")
    print("Попытка подключения к Telegram API...")
    
    # Запускаем бота с обработкой ошибок подключения
    try:
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
    except (NetworkError, TimedOut) as e:
        print(f"\n❌ Ошибка сети при работе бота: {e}")
        print("\n⚠️  Проблема с подключением к Telegram API.")
        print("Возможные причины:")
        print("1. Нет интернет-соединения")
        print("2. Telegram API недоступен")
        print("3. Проблемы с прокси/файрволом")
        print("4. Неверный токен бота")
        print("\nПопробуйте:")
        print("- Проверить интернет-соединение")
        print("- Проверить токен в файле .env")
        print("- Запустить бота позже")
        import sys
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹️  Бот остановлен пользователем")
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        print(f"\n❌ Ошибка при работе бота: {error_msg}")
        print(f"Тип ошибки: {error_type}")
        
        # Проверяем, является ли это ошибкой подключения
        if any(keyword in error_type for keyword in ["Connect", "Network", "Timeout", "Connection"]):
            print("\n⚠️  Проблема с подключением к Telegram API.")
            print("Возможные причины:")
            print("1. Нет интернет-соединения")
            print("2. Telegram API недоступен")
            print("3. Проблемы с прокси/файрволом")
            print("\nПопробуйте:")
            print("- Проверить интернет-соединение")
            print("- Запустить бота позже")
        
        import traceback
        traceback.print_exc()
        import sys
        sys.exit(1)


if __name__ == '__main__':
    main()
