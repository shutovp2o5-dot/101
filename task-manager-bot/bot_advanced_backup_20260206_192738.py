#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Улучшенный Telegram бот для управления проектами и задачами
С расширенными возможностями для работы с большим количеством задач
"""

import json
import os
import asyncio
import socket
import tempfile
import aiohttp
import httpx
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
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
from telegram.error import Conflict, NetworkError

# Состояния для ConversationHandler
(WAITING_TASK_TITLE, WAITING_TASK_PROJECT, WAITING_TASK_TAGS,
 WAITING_TASK_DEADLINE, WAITING_PROJECT_NAME, WAITING_EDIT_TASK_TITLE, 
 WAITING_EDIT_TASK_DEADLINE, WAITING_EDIT_TASK_TAGS,
 WAITING_SEARCH_QUERY, WAITING_FILTER_CHOICE, WAITING_RENAME_PROJECT, 
 WAITING_PROJECT_FILTER, WAITING_ASSIGN_PROJECT, WAITING_TASK_FILTER,
 WAITING_NEW_PROJECT_NAME, WAITING_PROJECT_TYPE, WAITING_PROJECT_TARGET_TASKS,
 WAITING_PROJECT_END_DATE) = range(18)

# Файл для хранения данных
DATA_FILE = 'tasks_data.json'

# Константы
TASKS_PER_PAGE = 10
PROJECTS_PER_PAGE = 10


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
    """Получение проектов пользователя (только названия для обратной совместимости)"""
    data = load_data()
    projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
    # Возвращаем список названий проектов
    return list(projects_data.keys()) if projects_data else []


def get_project_info(user_id: str, project_name: str) -> Dict:
    """Получение информации о проекте"""
    data = load_data()
    projects_data = data.get('users', {}).get(str(user_id), {}).get('projects_data', {})
    project_info = projects_data.get(project_name, {})
    
    # Если проект существует, но нет данных - создаем дефолтные
    if project_name in projects_data and not project_info:
        project_info = {'type': 'software', 'target_tasks': None}
    elif not project_info:
        # Для старых проектов без данных
        project_info = {'type': 'software', 'target_tasks': None}
    
    return project_info


def save_project_info(user_id: str, project_name: str, project_info: Dict):
    """Сохранение информации о проекте"""
    data = load_data()
    user_id_str = str(user_id)
    
    if 'users' not in data:
        data['users'] = {}
    if user_id_str not in data['users']:
        data['users'][user_id_str] = {'tasks': [], 'projects': [], 'tags': [], 'projects_data': {}}
    
    if 'projects_data' not in data['users'][user_id_str]:
        data['users'][user_id_str]['projects_data'] = {}
    
    data['users'][user_id_str]['projects_data'][project_name] = project_info
    save_data(data)


def get_user_tags(user_id: str) -> List[str]:
    """Получение тегов пользователя"""
    data = load_data()
    return data.get('users', {}).get(str(user_id), {}).get('tags', [])


def save_user_task(user_id: str, task: Dict):
    """Сохранение задачи пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if 'users' not in data:
        data['users'] = {}
    if user_id_str not in data['users']:
        data['users'][user_id_str] = {'tasks': [], 'projects': [], 'tags': []}
    
    data['users'][user_id_str]['tasks'].append(task)
    
    # Добавляем проект в список проектов пользователя, если его там нет
    project_name = task.get('project', 'Без проекта')
    if project_name not in data['users'][user_id_str]['projects']:
        data['users'][user_id_str]['projects'].append(project_name)
    
    # Добавляем теги в список тегов пользователя
    tags = task.get('tags', [])
    for tag in tags:
        if tag not in data['users'][user_id_str]['tags']:
            data['users'][user_id_str]['tags'].append(tag)
    
    save_data(data)


def update_user_task(user_id: str, task_id: str, updated_task: Dict):
    """Обновление задачи пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str in data.get('users', {}):
        tasks = data['users'][user_id_str]['tasks']
        for i, task in enumerate(tasks):
            if task.get('id') == task_id:
                data['users'][user_id_str]['tasks'][i] = updated_task
                save_data(data)
                return True
    return False


def delete_user_task(user_id: str, task_id: str):
    """Удаление задачи пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str in data.get('users', {}):
        tasks = data['users'][user_id_str]['tasks']
        data['users'][user_id_str]['tasks'] = [t for t in tasks if t.get('id') != task_id]
        save_data(data)
        return True
    return False


def complete_user_task(user_id: str, task_id: str):
    """Отметка задачи как выполненной"""
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str in data.get('users', {}):
        tasks = data['users'][user_id_str]['tasks']
        for i, task in enumerate(tasks):
            if task.get('id') == task_id:
                task['completed'] = True
                task['completed_at'] = datetime.now().isoformat()
                data['users'][user_id_str]['tasks'][i] = task
                save_data(data)
                return True
    return False


def add_user_project(user_id: str, project_name: str, project_type: str = 'software', target_tasks: Optional[int] = None):
    """Добавление проекта пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if 'users' not in data:
        data['users'] = {}
    if user_id_str not in data['users']:
        data['users'][user_id_str] = {'tasks': [], 'projects': [], 'tags': [], 'projects_data': {}}
    
    if 'projects_data' not in data['users'][user_id_str]:
        data['users'][user_id_str]['projects_data'] = {}
    
    # Проверяем, существует ли проект
    projects_data = data['users'][user_id_str]['projects_data']
    if project_name not in projects_data:
        # Добавляем проект в список проектов
        if project_name not in data['users'][user_id_str]['projects']:
            data['users'][user_id_str]['projects'].append(project_name)
        
        # Создаем информацию о проекте
        projects_data[project_name] = {
            'type': project_type,
            'target_tasks': target_tasks
        }
        save_data(data)
        return True
    return False


def delete_user_project(user_id: str, project_name: str):
    """Удаление проекта пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str in data.get('users', {}):
        # Удаляем проект из списка проектов
        if project_name in data['users'][user_id_str]['projects']:
            data['users'][user_id_str]['projects'].remove(project_name)
        
        # Удаляем данные проекта
        if 'projects_data' in data['users'][user_id_str]:
            if project_name in data['users'][user_id_str]['projects_data']:
                del data['users'][user_id_str]['projects_data'][project_name]
        
        # Удаляем проект из всех задач
        tasks = data['users'][user_id_str]['tasks']
        for task in tasks:
            if task.get('project') == project_name:
                task['project'] = 'Без проекта'
        
        save_data(data)
        return True
    return False


def rename_user_project(user_id: str, old_name: str, new_name: str):
    """Переименование проекта пользователя"""
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str in data.get('users', {}):
        # Переименовываем в списке проектов
        if old_name in data['users'][user_id_str]['projects']:
            projects = data['users'][user_id_str]['projects']
            index = projects.index(old_name)
            projects[index] = new_name
        
        # Переименовываем в данных проектов
        if 'projects_data' in data['users'][user_id_str]:
            if old_name in data['users'][user_id_str]['projects_data']:
                data['users'][user_id_str]['projects_data'][new_name] = data['users'][user_id_str]['projects_data'][old_name]
                del data['users'][user_id_str]['projects_data'][old_name]
        
        # Обновляем проект во всех задачах
        tasks = data['users'][user_id_str]['tasks']
        for task in tasks:
            if task.get('project') == old_name:
                task['project'] = new_name
        
        save_data(data)
        return True
    return False


def get_project_statistics(user_id: str, project_name: str) -> Dict:
    """Получение детальной статистики проекта"""
    tasks = get_user_tasks(user_id)
    project_tasks = [t for t in tasks if t.get('project') == project_name]
    
    if not project_tasks:
        return {
            'total': 0,
            'completed': 0,
            'incomplete': 0,
            'overdue': 0,
            'with_deadline': 0,
            'created_today': 0,
            'completed_today': 0
        }
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    stats = {
        'total': len(project_tasks),
        'completed': sum(1 for t in project_tasks if t.get('completed', False)),
        'incomplete': sum(1 for t in project_tasks if not t.get('completed', False)),
        'overdue': 0,
        'with_deadline': sum(1 for t in project_tasks if t.get('deadline')),
        'created_today': 0,
        'completed_today': 0
    }
    
    for task in project_tasks:
        if task.get('deadline'):
            deadline = datetime.fromisoformat(task['deadline'])
            if not task.get('completed', False) and deadline < now:
                stats['overdue'] += 1
        
        if task.get('created_at'):
            created = datetime.fromisoformat(task['created_at'])
            if created >= today_start:
                stats['created_today'] += 1
        
        if task.get('completed_at'):
            completed = datetime.fromisoformat(task['completed_at'])
            if completed >= today_start:
                stats['completed_today'] += 1
    
    return stats


def calculate_project_progress(user_id: str, project_name: str) -> Dict:
    """Расчет прогресса проекта в зависимости от типа"""
    tasks = get_user_tasks(user_id)
    project_tasks = [t for t in tasks if t.get('project') == project_name]
    
    # Получаем информацию о проекте
    project_info = get_project_info(user_id, project_name)
    project_type = project_info.get('type', 'software')
    
    if project_type == 'project':
        # Для проектных проектов: прогресс зависит от target_tasks
        target_tasks = project_info.get('target_tasks')
        if target_tasks is None or target_tasks == 0:
            # Если target_tasks не установлен, используем текущее количество задач
            completed = sum(1 for t in project_tasks if t.get('completed', False))
            total = len(project_tasks)
            percentage = int((completed / total) * 100) if total > 0 else 0
            return {'completed': completed, 'total': total, 'percentage': percentage, 'target': total}
        else:
            # Используем target_tasks как базу для прогресс-бара
            completed = sum(1 for t in project_tasks if t.get('completed', False))
            percentage = int((completed / target_tasks) * 100) if target_tasks > 0 else 0
            return {'completed': completed, 'total': len(project_tasks), 'percentage': percentage, 'target': target_tasks}
    else:
        # Для программных проектов: прогресс зависит от выполненных в срок задач и просроченных
        now = datetime.now()
        completed_on_time = 0
        completed_late = 0
        overdue = 0
        
        for task in project_tasks:
            if task.get('completed', False):
                deadline = task.get('deadline')
                completed_at = task.get('completed_at')
                
                if deadline and completed_at:
                    deadline_dt = datetime.fromisoformat(deadline)
                    completed_at_dt = datetime.fromisoformat(completed_at)
                    if completed_at_dt <= deadline_dt:
                        completed_on_time += 1
                    else:
                        completed_late += 1
                elif deadline:
                    # Если есть дедлайн, но нет completed_at, считаем просроченной
                    deadline_dt = datetime.fromisoformat(deadline)
                    if now > deadline_dt:
                        completed_late += 1
                    else:
                        completed_on_time += 1
                else:
                    # Если нет дедлайна, считаем выполненной в срок
                    completed_on_time += 1
            else:
                # Невыполненные задачи с просроченным дедлайном
                deadline = task.get('deadline')
                if deadline:
                    deadline_dt = datetime.fromisoformat(deadline)
                    if now > deadline_dt:
                        overdue += 1
        
        total = len(project_tasks)
        # Процент = выполненные в срок / (всего задач - просроченные невыполненные)
        # Это мотивирует не просрочивать задачи
        effective_total = total - overdue if total > overdue else total
        percentage = int((completed_on_time / effective_total) * 100) if effective_total > 0 else 0
        
        return {
            'completed': completed_on_time + completed_late,
            'completed_on_time': completed_on_time,
            'completed_late': completed_late,
            'overdue': overdue,
            'total': total,
            'percentage': percentage
        }


def format_progress_bar(percentage: int, length: int = 10) -> str:
    """Форматирование прогресс-бара"""
    filled = int((percentage / 100) * length)
    # Используем визуальные символы блоков для более наглядного отображения
    # █ - полный блок (заполненная часть)
    # ⬜ - пустой блок (незаполненная часть)
    bar = "🟢" * filled + "⚪" * (length - filled)
    return f"{bar} {percentage}%"


def format_task(task: Dict, detailed: bool = True) -> str:
    """Форматирование задачи для отображения"""
    text = ""
    
    if task.get('completed'):
        text += "✅ "
    else:
        text += "⏳ "
    
    text += f"<b>{task.get('title', 'Без названия')}</b>\n"
    
    if detailed:
        if task.get('project'):
            text += f"📁 Проект: {task.get('project')}\n"
        
        if task.get('deadline'):
            deadline = datetime.fromisoformat(task['deadline'])
            now = datetime.now()
            if deadline < now and not task.get('completed'):
                text += f"⚠️ Просрочено: {deadline.strftime('%d.%m.%Y %H:%M')}\n"
            else:
                text += f"⏰ Дедлайн: {deadline.strftime('%d.%m.%Y %H:%M')}\n"
        
        if task.get('tags'):
            text += f"🏷️ Теги: {', '.join(task.get('tags', []))}\n"
        
        if task.get('priority') and task.get('priority') != 'none':
            priority_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(task.get('priority'), '')
            text += f"{priority_emoji} Приоритет: {task.get('priority')}\n"
        
        if task.get('created_at'):
            created = datetime.fromisoformat(task['created_at'])
            text += f"📅 Создано: {created.strftime('%d.%m.%Y %H:%M')}\n"
        
        if task.get('completed_at'):
            completed = datetime.fromisoformat(task['completed_at'])
            text += f"✅ Выполнено: {completed.strftime('%d.%m.%Y %H:%M')}\n"
    
    return text


def filter_tasks(tasks: List[Dict], project: Optional[str] = None, 
                 tags: Optional[List[str]] = None, completed: Optional[bool] = None,
                 priority: Optional[str] = None) -> List[Dict]:
    """Фильтрация задач"""
    filtered = tasks
    
    if project:
        filtered = [t for t in filtered if t.get('project') == project]
    
    if tags:
        filtered = [t for t in filtered if any(tag in t.get('tags', []) for tag in tags)]
    
    if completed is not None:
        filtered = [t for t in filtered if t.get('completed') == completed]
    
    if priority:
        filtered = [t for t in filtered if t.get('priority') == priority]
    
    return filtered


def sort_tasks(tasks: List[Dict], sort_by: str = 'deadline') -> List[Dict]:
    """Сортировка задач"""
    if sort_by == 'deadline':
        def key_func(task):
            if task.get('deadline'):
                return datetime.fromisoformat(task['deadline'])
            return datetime.max
        return sorted(tasks, key=key_func)
    elif sort_by == 'created':
        def key_func(task):
            if task.get('created_at'):
                return datetime.fromisoformat(task['created_at'])
            return datetime.min
        return sorted(tasks, key=key_func, reverse=True)
    elif sort_by == 'priority':
        priority_order = {'high': 0, 'medium': 1, 'low': 2, 'none': 3}
        return sorted(tasks, key=lambda t: priority_order.get(t.get('priority', 'none'), 3))
    else:
        return tasks


def format_tasks_list(tasks: List[Dict], page: int = 0, 
                     show_completed: bool = False) -> Tuple[str, int]:
    """Форматирование списка задач с пагинацией"""
    if not show_completed:
        tasks = [t for t in tasks if not t.get('completed', False)]
    
    if not tasks:
        return "📝 Задач нет.", 0
    
    # Сортируем по дедлайну
    tasks = sort_tasks(tasks, 'deadline')
    
    total_pages = (len(tasks) + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE
    start_idx = page * TASKS_PER_PAGE
    end_idx = min(start_idx + TASKS_PER_PAGE, len(tasks))
    page_tasks = tasks[start_idx:end_idx]
    
    text = f"📝 <b>Задачи</b> (страница {page + 1} из {total_pages})\n\n"
    
    for i, task in enumerate(page_tasks, start=start_idx + 1):
        text += f"{i}. {format_task(task, detailed=False)}\n"
    
    return text, total_pages


def create_tasks_keyboard(tasks: List[Dict], page: int = 0, 
                         include_complete_buttons: bool = False) -> List[List[InlineKeyboardButton]]:
    """Создание клавиатуры для списка задач"""
    keyboard = []
    
    if not include_complete_buttons:
        # Обычная клавиатура с задачами
        for task in tasks[:TASKS_PER_PAGE]:
            title_short = task['title'][:30] + '...' if len(task['title']) > 30 else task['title']
            keyboard.append([
                InlineKeyboardButton(
                    title_short,
                    callback_data=f"task_{task['id']}"
                )
            ])
    else:
        # Клавиатура с кнопками "Готово"
        for task in tasks[:TASKS_PER_PAGE]:
            title_short = task['title'][:25] + '...' if len(task['title']) > 25 else task['title']
            keyboard.append([
                InlineKeyboardButton(
                    f"{'✅' if task.get('completed') else '⏳'} {title_short}",
                    callback_data=f"task_{task['id']}"
                ),
                InlineKeyboardButton(
                    "✅" if not task.get('completed') else "↩️",
                    callback_data=f"complete_{task['id']}" if not task.get('completed') else f"uncomplete_{task['id']}"
                )
            ])
    
    return keyboard


def parse_deadline(deadline_str: str) -> Optional[datetime]:
    """Парсинг дедлайна из строки"""
    deadline_str = deadline_str.strip().lower()
    now = datetime.now()
    
    if deadline_str == 'сегодня':
        return now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif deadline_str == 'завтра':
        return (now + timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
    elif deadline_str == 'послезавтра':
        return (now + timedelta(days=2)).replace(hour=23, minute=59, second=59, microsecond=0)
    elif deadline_str.startswith('через'):
        try:
            days = int(deadline_str.split()[1])
            return (now + timedelta(days=days)).replace(hour=23, minute=59, second=59, microsecond=0)
        except:
            return None
    else:
        try:
            # Пробуем распарсить дату в формате DD.MM.YYYY или DD.MM.YYYY HH:MM
            if ' ' in deadline_str:
                date_str, time_str = deadline_str.split(' ', 1)
                date_parts = date_str.split('.')
                time_parts = time_str.split(':')
                return datetime(
                    int(date_parts[2]),
                    int(date_parts[1]),
                    int(date_parts[0]),
                    int(time_parts[0]),
                    int(time_parts[1]) if len(time_parts) > 1 else 0
                )
            else:
                date_parts = deadline_str.split('.')
                return datetime(
                    int(date_parts[2]),
                    int(date_parts[1]),
                    int(date_parts[0]),
                    23, 59, 59
                )
        except:
            return None


def get_main_keyboard():
    """Создание основной клавиатуры"""
    keyboard = [
        [
            KeyboardButton("Добавить задачу")
        ],
        [
            KeyboardButton("План задач"),
            KeyboardButton("Проекты")
        ],
        [
            KeyboardButton("Статистика"),
            KeyboardButton("Фильтры")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_projects_keyboard():
    """Создание клавиатуры управления проектами"""
    keyboard = [
        [
            KeyboardButton("➕ Новый проект"),
            KeyboardButton("📊 Статистика")
        ],
        [
            KeyboardButton("🔙 Главное меню")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    try:
        user_id = update.effective_user.id if update.effective_user else "unknown"
        print(f"✅ Получена команда /start от пользователя {user_id}")
        
        welcome_text = (
            "Привет! Я улучшенный бот для управления проектами и задачами.\n\n"
            "Я помогу вам:\n"
            "• Управлять большим количеством задач и проектов\n"
            "• Устанавливать теги для задач\n"
            "• Искать и фильтровать задачи\n"
            "• Отслеживать срочные и просроченные задачи\n"
            "• Видеть детальную статистику по проектам\n\n"
            "Используйте команды или кнопки меню для начала работы!"
        )
        keyboard = get_main_keyboard()
        await update.message.reply_text(welcome_text, reply_markup=keyboard)
        print(f"✅ Команда /start успешно обработана для пользователя {user_id}")
    except Exception as e:
        print(f"❌ Ошибка при обработке команды /start: {e}")
        import traceback
        traceback.print_exception(type(e), e, e.__traceback__)
        try:
            await update.message.reply_text(
                "❌ Произошла ошибка при обработке команды.\n"
                "Попробуйте еще раз через несколько секунд."
            )
        except Exception as send_error:
            print(f"❌ Не удалось отправить сообщение об ошибке: {send_error}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "<b>Справка по использованию бота:</b>\n\n"
        "<b>Добавить задачу:</b>\n"
        "Создание новой задачи с названием, тегами и дедлайном.\n"
        "После создания можно назначить проект.\n\n"
        "<b>План задач:</b>\n"
        "Просмотр всех ваших задач с пагинацией и возможностью управления.\n\n"
        "<b>Проекты:</b>\n"
        "Управление проектами и просмотр задач внутри каждого проекта.\n\n"
        "<b>Статистика:</b>\n"
        "Детальная статистика по всем проектам и задачам.\n\n"
        "<b>Фильтры:</b>\n"
        "Фильтрация задач по проекту, тегам, статусу.\n\n"
        "<b>Формат дедлайна:</b>\n"
        "• Сегодня\n"
        "• Завтра\n"
        "• Послезавтра\n"
        "• 25.01.2026\n"
        "• 25.01.2026 18:00\n"
        "• Через 3 дня"
    )
    keyboard = get_main_keyboard()
    await update.message.reply_text(help_text, parse_mode='HTML', reply_markup=keyboard)


async def update_keyboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновление клавиатуры"""
    keyboard = get_main_keyboard()
    await update.message.reply_text(
        "Клавиатура обновлена!\n\n"
        "Доступные кнопки:\n"
        "➕ Добавить задачу - создать новую задачу\n"
        "План задач - просмотреть все задачи\n"
        "Проекты - управление проектами\n"
        "Статистика - статистика по задачам и проектам\n"
        "Фильтры - фильтрация задач",
        reply_markup=keyboard
    )


async def add_new_project_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать добавление нового проекта из кнопки"""
    user_id = update.effective_user.id
    
    # Очищаем предыдущие данные
    context.user_data['new_project'] = {}
    
    keyboard = [
        [
            InlineKeyboardButton("💻 Программный", callback_data="project_type_software"),
            InlineKeyboardButton("📋 Проектный", callback_data="project_type_project")
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_add_project")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📁 Выберите <b>тип проекта</b>:\n\n"
        "💻 <b>Программный</b> - прогресс зависит от выполненных в срок задач\n"
        "📋 <b>Проектный</b> - можно задать целевое количество шагов и дату окончания",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    
    return WAITING_PROJECT_TYPE


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню"""
    main_keyboard = get_main_keyboard()
    await update.message.reply_text(
        "🏠 Главное меню\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard
    )


# ВАЖНО: Файл очень большой. Остальные функции будут добавлены в следующих частях.
# Для полного восстановления нужно добавить:
# - Обработчики задач (add_task_start, add_task_title, etc.)
# - Обработчики проектов (show_projects, project_tasks_callback, etc.)
# - Обработчики статистики
# - ConversationHandler'ы
# - Функцию main()
#
# Из-за ограничений размера, продолжение будет в следующих сообщениях.
