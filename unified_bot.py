#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Объединенный Telegram бот: Расписание + Задачи
"""

import sys
import os
import importlib.util
import logging
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime, time

# Базовая директория проекта
BASE_DIR = Path(__file__).resolve().parent

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Добавляем пути к обоим ботам (относительно текущей директории проекта)
sys.path.insert(0, str(BASE_DIR / 'schedule-bot'))
sys.path.insert(0, str(BASE_DIR / 'task-manager-bot'))

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler
)

# Импортируем утилиты для оберток
from wrappers import (
    create_schedule_wrapper,
    create_schedule_entry_wrapper,
    wrap_schedule_handler,
    create_tasks_wrapper,
    create_tasks_entry_wrapper,
    wrap_tasks_handler,
    create_add_project_wrapper,
    create_edit_project_wrapper
)

# Вспомогательная функция для завершения ConversationHandler
async def end_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершает ConversationHandler при нажатии на кнопки главного меню"""
    return ConversationHandler.END

# Режимы работы бота
MODE_MAIN = "main"
MODE_SCHEDULE = "schedule"
MODE_TASKS = "tasks"
MODE_PLAN = "plan"

# Состояния для единого сценария добавления дела (событие/задача)
(WAITING_UNIFIED_TITLE,
 WAITING_UNIFIED_DEADLINE,
 WAITING_UNIFIED_COMMENT,
 WAITING_UNIFIED_PROJECT,
 WAITING_UNIFIED_RECURRENCE,
 WAITING_UNIFIED_REMINDER,
 WAITING_UNIFIED_TYPE) = range(100, 107)

def get_unified_main_keyboard():
    """Главное меню объединенного бота"""
    keyboard = [
        [KeyboardButton("➕")],
        [KeyboardButton("Проекты"), KeyboardButton("Статистика")],
        [KeyboardButton("📋 План")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def unified_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start для объединенного бота"""
    try:
        if not update.message:
            logger.warning("unified_start вызван без сообщения")
            return
        
        context.user_data['bot_mode'] = MODE_MAIN
        keyboard = get_unified_main_keyboard()
        await update.message.reply_text(
            "👋 <b>Добро пожаловать в объединенный бот!</b>\n\n"
            "Нажмите ➕, чтобы добавить новое дело.\n"
            "В конце вы выберете, это 📅 событие или ✅ задача.",
            parse_mode='HTML',
            reply_markup=keyboard
        )
        logger.info(f"Команда /start обработана для пользователя {update.effective_user.id}")
    except Exception as e:
        logger.error(f"Ошибка в unified_start: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text("❌ Ошибка при обработке команды /start")
        except Exception as send_error:
            logger.error(f"Не удалось отправить сообщение об ошибке: {send_error}")

def get_schedule_keyboard():
    """Клавиатура раздела расписания"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕"), KeyboardButton("✏️")],
        [KeyboardButton("🏠 Главное меню")]
    ], resize_keyboard=True)

async def switch_to_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение в режим расписания"""
    try:
        if not update.message:
            logger.warning("switch_to_schedule вызван без сообщения")
            return
        
        context.user_data['bot_mode'] = MODE_SCHEDULE
        
        await update.message.reply_text(
            "📅 <b>Режим: Расписание</b>\n\n"
            "Выберите действие:",
            parse_mode='HTML',
            reply_markup=get_schedule_keyboard()
        )
        logger.debug(f"Пользователь {update.effective_user.id} переключился в режим расписания")
    except Exception as e:
        logger.error(f"Ошибка при переключении в режим расписания: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text("❌ Ошибка при переключении режима")
        except Exception:
            pass

def get_plan_keyboard():
    """Компактная клавиатура раздела плана"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("📅 План на сегодня"), KeyboardButton("📅 План на завтра")],
        [KeyboardButton("📅 План на неделю"), KeyboardButton("📅 План на месяц")],
        [KeyboardButton("📅 План на год"), KeyboardButton("📅 План на 3 года")],
        [KeyboardButton("✅ Управление задачами")],
        [KeyboardButton("🏠 Главное меню")]
    ], resize_keyboard=True)

def get_tasks_keyboard():
    """Клавиатура раздела задач"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕"), KeyboardButton("✏️")],
        [KeyboardButton("🏠 Главное меню")]
    ], resize_keyboard=True)

async def switch_to_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение в режим задач"""
    try:
        if not update.message:
            logger.warning("switch_to_tasks вызван без сообщения")
            return
        
        context.user_data['bot_mode'] = MODE_TASKS
        
        await update.message.reply_text(
            "✅ <b>Режим: Задачи</b>\n\n"
            "Выберите действие:",
            parse_mode='HTML',
            reply_markup=get_tasks_keyboard()
        )
        logger.debug(f"Пользователь {update.effective_user.id} переключился в режим задач")
    except Exception as e:
        logger.error(f"Ошибка при переключении в режим задач: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text("❌ Ошибка при переключении режима")
        except Exception:
            pass

async def switch_to_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение в режим плана - показывает объединенный план из задач и расписания"""
    try:
        if not update.message:
            logger.warning("switch_to_plan вызван без сообщения")
            return
        
        context.user_data['bot_mode'] = MODE_PLAN
        
        # Сразу показываем план на сегодня при входе в раздел
        user_id = update.effective_user.id
        schedule_module = context.application.bot_data.get('schedule_module')
        tasks_module = context.application.bot_data.get('tasks_module')
        
        events, tasks = get_combined_plan(user_id, schedule_module, tasks_module, days=1)
        text = format_combined_plan_text(events, tasks, "сегодня")
        
        await update.message.reply_text(
            text,
            parse_mode='HTML',
            reply_markup=get_plan_keyboard()
        )
        logger.debug(f"Пользователь {user_id} переключился в режим плана")
    except Exception as e:
        logger.error(f"Ошибка при переключении в режим плана: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Ошибка при загрузке плана. Попробуйте позже.",
                    reply_markup=get_plan_keyboard()
                )
        except Exception:
            pass

async def show_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать проекты с возможностью управления (добавление, редактирование)"""
    try:
        if not update.message:
            logger.warning("show_projects вызван без сообщения")
            return
        
        tasks_module = context.application.bot_data.get('tasks_module')
        
        if tasks_module and hasattr(tasks_module, 'projects_list'):
            # Используем функционал управления проектами из task-manager-bot
            # Временно устанавливаем режим задач для корректной работы функции
            old_mode = context.user_data.get('bot_mode', MODE_MAIN)
            context.user_data['bot_mode'] = MODE_TASKS
            try:
                await tasks_module.projects_list(update, context)
            except Exception as e:
                logger.error(f"Ошибка при показе проектов: {e}", exc_info=True)
                await update.message.reply_text(
                    "❌ Ошибка при загрузке проектов",
                    reply_markup=get_unified_main_keyboard()
                )
            finally:
                # Возвращаем режим обратно
                context.user_data['bot_mode'] = old_mode
        else:
            # Fallback: показываем простой список проектов
            await update.message.reply_text(
                "❌ Функционал управления проектами недоступен",
                reply_markup=get_unified_main_keyboard()
            )
    except Exception as e:
        logger.error(f"Ошибка в show_projects: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Ошибка при обработке запроса",
                    reply_markup=get_unified_main_keyboard()
                )
        except Exception:
            pass

async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню"""
    try:
        if not update.message:
            logger.warning("back_to_main_menu вызван без сообщения")
            return
        
        context.user_data['bot_mode'] = MODE_MAIN
        keyboard = get_unified_main_keyboard()
        await update.message.reply_text(
            "🏠 <b>Главное меню</b>\n\n"
            "Нажмите ➕, чтобы добавить новое дело.\n"
            "В конце вы выберете, это 📅 событие или ✅ задача.",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при возврате в главное меню: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text("❌ Ошибка при возврате в меню")
        except Exception:
            pass


async def unified_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт единого сценария добавления дела (событие или задача) по кнопке ➕"""
    try:
        if not update.message:
            logger.warning("unified_add_start вызван без сообщения")
            return
        
        # Очищаем временные данные
        context.user_data.pop('unified_item', None)
        
        context.user_data['unified_item'] = {}
        await update.message.reply_text("Как назовём?")
        return WAITING_UNIFIED_TITLE
    except Exception as e:
        logger.error(f"Ошибка в unified_add_start: {e}", exc_info=True)
        if update.message:
            await update.message.reply_text("❌ Ошибка при запуске добавления дела")
        return ConversationHandler.END


async def unified_add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение названия и попытка вытащить дедлайн из текста (через модуль задач)"""
    try:
        if not update.message or not update.message.text:
            await update.message.reply_text("Название не может быть пустым. Попробуйте ещё раз:")
            return WAITING_UNIFIED_TITLE
        
        raw_text = update.message.text.strip()
        if not raw_text:
            await update.message.reply_text("Название не может быть пустым. Попробуйте ещё раз:")
            return WAITING_UNIFIED_TITLE
        
        tasks_module = context.application.bot_data.get('tasks_module')
        
        title = raw_text
        deadline_iso = None
        
        # Пробуем использовать логику извлечения дедлайна из модуля задач
        if tasks_module and hasattr(tasks_module, 'extract_deadline_from_text'):
            try:
                extracted_title, deadline_dt = tasks_module.extract_deadline_from_text(raw_text)
                if extracted_title:
                    title = extracted_title
                if deadline_dt:
                    if deadline_dt.tzinfo:
                        deadline_dt = deadline_dt.replace(tzinfo=None)
                    deadline_iso = deadline_dt.isoformat()
            except Exception as e:
                logger.error(f"Ошибка extract_deadline_from_text: {e}", exc_info=True)
        
        context.user_data.setdefault('unified_item', {})
        context.user_data['unified_item']['title'] = title
        if deadline_iso:
            context.user_data['unified_item']['deadline'] = deadline_iso
        
        # Формируем ответ
        response = f"Название: {title}"
        if deadline_iso:
            # Красиво форматируем дедлайн через tasks_module, если можно
            try:
                if tasks_module and hasattr(tasks_module, 'format_deadline_readable'):
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(deadline_iso)
                    if dt.tzinfo:
                        dt = dt.replace(tzinfo=None)
                    response += f"\nДедлайн: {tasks_module.format_deadline_readable(dt)}"
            except Exception:
                pass
        
        await update.message.reply_text(response)
        
        # Если дедлайн уже найден, сразу переходим к комментарию
        if deadline_iso:
            await update.message.reply_text("Что-то уточним, или /skip")
            return WAITING_UNIFIED_COMMENT
        else:
            await update.message.reply_text("Какой дедлайн?")
            return WAITING_UNIFIED_DEADLINE
    except Exception as e:
        logger.error(f"Ошибка в unified_add_title: {e}", exc_info=True)
        if update.message:
            await update.message.reply_text("❌ Ошибка при обработке названия. Попробуйте ещё раз.")
        return WAITING_UNIFIED_TITLE


async def unified_add_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос/уточнение дедлайна (общий формат, через парсер задач, если есть)"""
    try:
        if not update.message or not update.message.text:
            await update.message.reply_text("Пожалуйста, укажите дедлайн текстом.")
            return WAITING_UNIFIED_DEADLINE
        
        text = update.message.text.strip()
        if not text:
            await update.message.reply_text("Пожалуйста, укажите дедлайн текстом.")
            return WAITING_UNIFIED_DEADLINE
        
        tasks_module = context.application.bot_data.get('tasks_module')
        
        deadline_iso = None
        if tasks_module and hasattr(tasks_module, 'parse_deadline'):
            try:
                # parse_deadline(text, deadline=None) -> datetime | None
                deadline_dt = tasks_module.parse_deadline(text, None)
                if not deadline_dt:
                    await update.message.reply_text("Не понял дедлайн. Попробуйте другой формат (например, «сегодня 18:00»).")
                    return WAITING_UNIFIED_DEADLINE
                if deadline_dt.tzinfo:
                    deadline_dt = deadline_dt.replace(tzinfo=None)
                deadline_iso = deadline_dt.isoformat()
            except Exception as e:
                logger.error(f"Ошибка parse_deadline: {e}", exc_info=True)
        
        if not deadline_iso:
            await update.message.reply_text("Не удалось распознать дедлайн. Попробуйте ещё раз.")
            return WAITING_UNIFIED_DEADLINE
        
        context.user_data.setdefault('unified_item', {})
        context.user_data['unified_item']['deadline'] = deadline_iso
        
        # Красиво покажем дедлайн
        tasks_module = context.application.bot_data.get('tasks_module')
        try:
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(deadline_iso)
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            if tasks_module and hasattr(tasks_module, 'format_deadline_readable'):
                formatted = tasks_module.format_deadline_readable(dt)
            else:
                formatted = dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            formatted = deadline_iso
        
        await update.message.reply_text(f"Дедлайн: {formatted}")
        await update.message.reply_text("Что-то уточним, или /skip")
        return WAITING_UNIFIED_COMMENT
    except Exception as e:
        logger.error(f"Ошибка в unified_add_deadline: {e}", exc_info=True)
        if update.message:
            await update.message.reply_text("❌ Ошибка при обработке дедлайна. Попробуйте ещё раз.")
        return WAITING_UNIFIED_DEADLINE


async def unified_add_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Комментарий (или /skip)"""
    try:
        if update.message and update.message.text and update.message.text.strip() == '/skip':
            comment = ''
        else:
            comment = (update.message.text or '').strip()
        
        context.user_data.setdefault('unified_item', {})
        context.user_data['unified_item']['comment'] = comment
        
        # Переходим к выбору проекта
        tasks_module = context.application.bot_data.get('tasks_module')
        projects = []
        if tasks_module and hasattr(tasks_module, 'get_user_projects'):
            try:
                projects = tasks_module.get_user_projects(str(update.effective_user.id)) or []
            except Exception as e:
                logger.error(f"Ошибка get_user_projects: {e}", exc_info=True)
        
        if projects:
            # Показываем варианты проектов списком
            text_lines = ["В каком проекте? Напишите название или выберите из существующих:\n"]
            for p in projects[:20]:
                text_lines.append(f"- {p}")
            await update.message.reply_text("\n".join(text_lines))
        else:
            await update.message.reply_text("В каком проекте? (можно написать новое название)")
        
        return WAITING_UNIFIED_PROJECT
    except Exception as e:
        logger.error(f"Ошибка в unified_add_comment: {e}", exc_info=True)
        if update.message:
            await update.message.reply_text("❌ Ошибка при сохранении комментария. Попробуйте ещё раз.")
        return WAITING_UNIFIED_COMMENT


async def unified_add_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор/ввод проекта"""
    try:
        if not update.message or not update.message.text:
            await update.message.reply_text("Пожалуйста, укажите проект текстом.")
            return WAITING_UNIFIED_PROJECT
        
        project = update.message.text.strip()
        context.user_data.setdefault('unified_item', {})
        context.user_data['unified_item']['project'] = project
        
        # Сохраняем проект в системе задач (чтобы он появился в списках)
        tasks_module = context.application.bot_data.get('tasks_module')
        if tasks_module and hasattr(tasks_module, 'add_user_project'):
            try:
                tasks_module.add_user_project(
                    str(update.effective_user.id),
                    project_name=project
                )
            except Exception as e:
                logger.error(f"Ошибка add_user_project: {e}", exc_info=True)
        
        # Переходим к регулярности
        keyboard = [
            [InlineKeyboardButton("Одноразовое", callback_data="unified_recur_once")],
            [InlineKeyboardButton("Ежедневное", callback_data="unified_recur_daily")],
            [InlineKeyboardButton("Еженедельное", callback_data="unified_recur_weekly")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Какая регулярность?",
            reply_markup=reply_markup
        )
        return WAITING_UNIFIED_RECURRENCE
    except Exception as e:
        logger.error(f"Ошибка в unified_add_project: {e}", exc_info=True)
        if update.message:
            await update.message.reply_text("❌ Ошибка при сохранении проекта. Попробуйте ещё раз.")
        return WAITING_UNIFIED_PROJECT


async def unified_add_recurrence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора регулярности"""
    try:
        query = update.callback_query
        if not query:
            logger.warning("unified_add_recurrence без callback_query")
            return WAITING_UNIFIED_RECURRENCE
        
        await query.answer()
        
        recurrence = 'once'
        data = query.data
        if data == 'unified_recur_daily':
            recurrence = 'daily'
        elif data == 'unified_recur_weekly':
            recurrence = 'weekly'
        
        context.user_data.setdefault('unified_item', {})
        context.user_data['unified_item']['recurrence'] = recurrence
        
        # Переходим к напоминанию
        keyboard = [
            [InlineKeyboardButton("За час", callback_data="unified_rem_1h")],
            [InlineKeyboardButton("За 3 часа", callback_data="unified_rem_3h")],
            [InlineKeyboardButton("За 6 часов", callback_data="unified_rem_6h")],
            [InlineKeyboardButton("За день", callback_data="unified_rem_1d")],
            [InlineKeyboardButton("Без напоминания", callback_data="unified_rem_none")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Как напомнить?",
            reply_markup=reply_markup
        )
        return WAITING_UNIFIED_REMINDER
    except Exception as e:
        logger.error(f"Ошибка в unified_add_recurrence: {e}", exc_info=True)
        if update.callback_query:
            await update.callback_query.answer("❌ Ошибка при выборе регулярности", show_alert=True)
        return WAITING_UNIFIED_RECURRENCE


async def unified_add_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора напоминания (для задач)"""
    try:
        query = update.callback_query
        if not query:
            logger.warning("unified_add_reminder без callback_query")
            return WAITING_UNIFIED_REMINDER
        
        await query.answer()
        
        context.user_data.setdefault('unified_item', {})
        
        # Сохраняем относительное напоминание в виде ISO-даты, ориентируясь на дедлайн
        item = context.user_data['unified_item']
        deadline_iso = item.get('deadline')
        reminder_iso = None
        
        from datetime import datetime as _dt, timedelta as _td
        if deadline_iso:
            try:
                deadline_dt = _dt.fromisoformat(deadline_iso)
                if deadline_dt.tzinfo:
                    deadline_dt = deadline_dt.replace(tzinfo=None)
                
                if query.data == 'unified_rem_1h':
                    reminder_iso = (deadline_dt - _td(hours=1)).isoformat()
                elif query.data == 'unified_rem_3h':
                    reminder_iso = (deadline_dt - _td(hours=3)).isoformat()
                elif query.data == 'unified_rem_6h':
                    reminder_iso = (deadline_dt - _td(hours=6)).isoformat()
                elif query.data == 'unified_rem_1d':
                    reminder_iso = (deadline_dt - _td(days=1)).isoformat()
            except Exception as e:
                logger.error(f"Ошибка вычисления времени напоминания: {e}", exc_info=True)
        
        if query.data == 'unified_rem_none':
            reminder_iso = None
        
        item['reminder'] = reminder_iso
        
        # Финальный шаг – выбор, что это: событие или задача
        keyboard = [
            [InlineKeyboardButton("📅 Событие", callback_data="unified_type_event")],
            [InlineKeyboardButton("✅ Задачи", callback_data="unified_type_task")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Что это?",
            reply_markup=reply_markup
        )
        return WAITING_UNIFIED_TYPE
    except Exception as e:
        logger.error(f"Ошибка в unified_add_reminder: {e}", exc_info=True)
        if update.callback_query:
            await update.callback_query.answer("❌ Ошибка при выборе напоминания", show_alert=True)
        return WAITING_UNIFIED_REMINDER


async def unified_choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Финальный выбор: сохранить как событие или как задачу"""
    try:
        query = update.callback_query
        if not query:
            logger.warning("unified_choose_type без callback_query")
            return ConversationHandler.END
        
        await query.answer()
        
        item = context.user_data.get('unified_item') or {}
        title = item.get('title') or "Без названия"
        deadline_iso = item.get('deadline')
        comment = item.get('comment') or ""
        project = item.get('project') or ""
        recurrence = item.get('recurrence', 'once')
        reminder_iso = item.get('reminder')
        
        user_id = query.from_user.id
        
        if query.data == 'unified_type_event':
            # Сохраняем как событие расписания
            schedule_module = context.application.bot_data.get('schedule_module')
            if not schedule_module:
                await query.edit_message_text("❌ Модуль расписания недоступен")
                return ConversationHandler.END
            
            from datetime import datetime as _dt
            if not deadline_iso:
                await query.edit_message_text("❌ Для события нужен дедлайн (дата и время)")
                return ConversationHandler.END
            
            dt = _dt.fromisoformat(deadline_iso)
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            
            date_str = dt.strftime('%Y-%m-%d')
            time_str = dt.strftime('%H:%M')
            
            event = {
                'title': title,
                'date': date_str,
                'time': time_str,
                'description': comment,
                'category': project or 'other',
                'repeat_type': recurrence,
                'reminders': [],
                'source': 'schedule'
            }
            
            # Преобразуем напоминание в минуты до события, если оно есть
            if reminder_iso:
                try:
                    rem_dt = _dt.fromisoformat(reminder_iso)
                    if rem_dt.tzinfo:
                        rem_dt = rem_dt.replace(tzinfo=None)
                    delta_min = int((dt - rem_dt).total_seconds() // 60)
                    if delta_min > 0:
                        event['reminders'] = [delta_min]
                except Exception as e:
                    logger.error(f"Ошибка вычисления reminders для события: {e}", exc_info=True)
            
            try:
                # Используем функцию сохранения из schedule_module
                if hasattr(schedule_module, 'save_user_event'):
                    schedule_module.save_user_event(str(user_id), event)
                else:
                    # fallback: напрямую работаем с файлом schedule_data.json
                    import json, os
                    data_file = os.path.join(os.path.dirname(__file__), 'schedule_data.json')
                    if os.path.exists(data_file):
                        with open(data_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                    else:
                        data = {}
                    user_events = data.get(str(user_id), [])
                    user_events.append(event)
                    data[str(user_id)] = user_events
                    with open(data_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                
                await query.edit_message_text(
                    f"📅 Событие сохранено:\n\n<u>{time_str}</u> - {title}",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Ошибка сохранения события: {e}", exc_info=True)
                await query.edit_message_text("❌ Ошибка при сохранении события")
        else:
            # Сохраняем как задачу
            tasks_module = context.application.bot_data.get('tasks_module')
            if not tasks_module:
                await query.edit_message_text("❌ Модуль задач недоступен")
                return ConversationHandler.END
            
            from datetime import datetime as _dt
            current_time = _dt.now()
            if current_time.tzinfo:
                current_time = current_time.replace(tzinfo=None)
            
            task = {
                'id': f"{current_time.timestamp()}",
                'title': title,
                'comment': comment or None,
                'project': project or None,
                'deadline': deadline_iso,
                'reminder': reminder_iso,
                'recurrence': recurrence,
                'completed': False,
                'created_at': current_time.isoformat(),
                'source': 'tasks'
            }
            
            try:
                if hasattr(tasks_module, 'save_user_task'):
                    tasks_module.save_user_task(str(user_id), task)
                else:
                    # fallback: прямое сохранение в tasks_data.json
                    import json, os
                    data_file = os.path.join(os.path.dirname(__file__), 'tasks_data.json')
                    if os.path.exists(data_file):
                        with open(data_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                    else:
                        data = {}
                    users = data.setdefault('users', {})
                    user_data = users.setdefault(str(user_id), {'tasks': [], 'projects': [], 'tags': [], 'projects_data': {}})
                    user_data.setdefault('tasks', []).append(task)
                    with open(data_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                
            except Exception as e:
                logger.error(f"Ошибка сохранения задачи: {e}", exc_info=True)
                await query.edit_message_text("❌ Ошибка при сохранении задачи")
                return ConversationHandler.END
            
            await query.edit_message_text(
                f"✅ Задача сохранена:\n\n<b>{title}</b>",
                parse_mode='HTML'
            )
        
        # Очищаем временные данные
        context.user_data.pop('unified_item', None)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в unified_choose_type: {e}", exc_info=True)
        if update.callback_query:
            await update.callback_query.answer("❌ Ошибка при сохранении", show_alert=True)
        return ConversationHandler.END

def get_combined_plan(user_id: str, schedule_module: Optional[Any], tasks_module: Optional[Any], 
                      days: int = 1, start_date_offset: int = 0) -> Tuple[List[Dict], List[Dict]]:
    """Получить объединенный план на указанное количество дней
    
    Args:
        user_id: ID пользователя
        schedule_module: Модуль расписания (может быть None)
        tasks_module: Модуль задач (может быть None)
        days: Количество дней для плана
        start_date_offset: Смещение начала периода (0 = сегодня, 1 = завтра и т.д.)
    
    Returns:
        Кортеж (events, tasks) - списки событий и задач
    """
    from datetime import datetime, timedelta
    
    # Получаем события из расписания
    events = []
    if schedule_module and hasattr(schedule_module, 'get_user_events'):
        try:
            events = schedule_module.get_user_events(str(user_id))
            if not isinstance(events, list):
                logger.warning(f"get_user_events вернул не список для пользователя {user_id}")
                events = []
        except Exception as e:
            logger.error(f"Ошибка при получении событий для пользователя {user_id}: {e}", exc_info=True)
            events = []
    
    # Получаем задачи
    tasks = []
    if tasks_module:
        try:
            # Пробуем использовать get_user_tasks если доступна (предпочтительный метод)
            if hasattr(tasks_module, 'get_user_tasks'):
                tasks = tasks_module.get_user_tasks(str(user_id))
                if not isinstance(tasks, list):
                    logger.warning(f"get_user_tasks вернул не список для пользователя {user_id}")
                    tasks = []
            elif hasattr(tasks_module, 'load_data'):
                # Альтернативный способ через load_data
                tasks_data = tasks_module.load_data()
                if not isinstance(tasks_data, dict):
                    logger.warning(f"load_data вернул не словарь")
                    tasks_data = {}
                
                # Проверяем разные структуры данных
                if 'users' in tasks_data:
                    # Структура: {'users': {user_id: {'tasks': [...]}}}
                    user_data = tasks_data.get('users', {}).get(str(user_id), {})
                    if isinstance(user_data, dict):
                        tasks = user_data.get('tasks', [])
                elif str(user_id) in tasks_data:
                    # Структура: {user_id: {'tasks': [...]}} или {user_id: [...]}
                    user_tasks_data = tasks_data.get(str(user_id), {})
                    if isinstance(user_tasks_data, dict):
                        tasks = user_tasks_data.get('tasks', [])
                    elif isinstance(user_tasks_data, list):
                        tasks = user_tasks_data
            
            # Фильтруем только незавершенные задачи
            tasks = [t for t in tasks if isinstance(t, dict) and not t.get('completed', False)]
        except Exception as e:
            logger.error(f"Ошибка при получении задач для пользователя {user_id}: {e}", exc_info=True)
            tasks = []
    
    # Фильтруем по дате
    try:
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = today + timedelta(days=start_date_offset)
        end_date = start_date + timedelta(days=days)
    except Exception as e:
        logger.error(f"Ошибка при вычислении дат: {e}", exc_info=True)
        return [], []
    
    # Фильтруем события
    filtered_events = []
    for event in events:
        if not isinstance(event, dict):
            logger.warning(f"Событие не является словарем: {event}")
            continue
        
        try:
            event_date_str = event.get('date', '')
            if not event_date_str:
                continue
            event_date = datetime.strptime(event_date_str, '%Y-%m-%d').date()
            if start_date.date() <= event_date < end_date.date():
                filtered_events.append(event)
        except ValueError as e:
            logger.warning(f"Неверный формат даты события '{event_date_str}': {e}")
            continue
        except Exception as e:
            logger.error(f"Ошибка при фильтрации события: {e}, event: {event}", exc_info=True)
            continue
    
    # Фильтруем задачи (включая задачи без дедлайна для больших периодов)
    # Завершенные задачи уже отфильтрованы выше
    filtered_tasks = []
    tasks_without_deadline = []
    
    for task in tasks:
        if not isinstance(task, dict):
            logger.warning(f"Задача не является словарем: {task}")
            continue
        
        # Дополнительная проверка на завершенность (на случай если фильтрация выше не сработала)
        if task.get('completed', False):
            continue
        
        deadline = task.get('deadline')
        if deadline:
            try:
                if isinstance(deadline, str):
                    # Пробуем разные форматы дедлайна
                    task_date = None
                    # Формат ISO с временем: 2026-02-09T16:30:00
                    if 'T' in deadline:
                        task_date = datetime.fromisoformat(deadline.replace('Z', '+00:00')).date()
                    # Формат только даты: 2026-02-09
                    elif len(deadline) == 10 and deadline.count('-') == 2:
                        task_date = datetime.strptime(deadline, '%Y-%m-%d').date()
                    else:
                        # Пробуем другие форматы
                        try:
                            task_date = datetime.fromisoformat(deadline).date()
                        except ValueError:
                            logger.debug(f"Не удалось распарсить дедлайн '{deadline}'")
                            continue
                    
                    if task_date and start_date.date() <= task_date < end_date.date():
                        filtered_tasks.append(task)
                else:
                    logger.debug(f"Дедлайн задачи не является строкой: {type(deadline)}")
                    continue
            except ValueError as e:
                logger.warning(f"Неверный формат дедлайна задачи '{deadline}': {e}")
                continue
            except Exception as e:
                logger.error(f"Ошибка при фильтрации задачи с дедлайном '{deadline}': {e}", exc_info=True)
                continue
        else:
            # Задачи без дедлайна показываем только для больших периодов (неделя и больше)
            if days >= 7:
                tasks_without_deadline.append(task)
    
    # Добавляем задачи без дедлайна в конец списка
    filtered_tasks.extend(tasks_without_deadline)
    
    logger.debug(f"Получен план для пользователя {user_id}: {len(filtered_events)} событий, {len(filtered_tasks)} задач")
    return filtered_events, filtered_tasks

def format_combined_plan_text(events: List[Dict], tasks: List[Dict], period_name: str) -> str:
    """Форматирование объединенного плана в текст - объединяет события и задачи по датам
    
    Args:
        events: Список событий
        tasks: Список задач
        period_name: Название периода (например, "сегодня", "неделю")
    
    Returns:
        Отформатированный текст плана
    """
    from datetime import datetime
    
    try:
        text = f"📋 <b>План на {period_name}</b>\n\n"
        
        # Считаем события и задачи так же, как выводим их ниже:
        # события = события расписания + задачи с category == 'event'
        # задачи  = остальные задачи
        events_from_tasks = [
            t for t in tasks
            if isinstance(t, dict) and t.get('deadline') and t.get('category') == 'event'
        ]
        pure_tasks = [
            t for t in tasks
            if isinstance(t, dict) and not (t.get('deadline') and t.get('category') == 'event')
        ]
        
        events_count = len(events) + len(events_from_tasks)
        tasks_count = len(pure_tasks)
        total_count = events_count + tasks_count
        
        if total_count == 0:
            text += "Нет событий и задач на этот период."
            return text
        
        # Красивое описание количества дел
        if events_count == 0:
            events_part = "нет событий"
        elif events_count == 1:
            events_part = "одно событие"
        else:
            events_part = f"{events_count} событий"
        
        if tasks_count == 0:
            tasks_part = "нет задач"
        elif tasks_count == 1:
            tasks_part = "одна задача"
        else:
            tasks_part = f"{tasks_count} задач"
        
        text += f"Всего дел: <b>{total_count}</b> ({events_part} и {tasks_part})\n\n"
    except Exception as e:
        logger.error(f"Ошибка при форматировании заголовка плана: {e}", exc_info=True)
        return f"❌ Ошибка при форматировании плана на {period_name}"
    
    # Объединяем события и задачи в один список по датам
    items_by_date = {}
    
    # Добавляем события (ВСЕГДА из раздела Расписание)
    for event in events[:200]:  # Ограничиваем до 200 событий
        date_str = event.get('date', '')
        if date_str:
            # Устанавливаем источник по умолчанию для старых событий
            if 'source' not in event:
                event['source'] = 'schedule'
            if date_str not in items_by_date:
                items_by_date[date_str] = {'events': [], 'tasks': []}
            items_by_date[date_str]['events'].append(event)
    
    # Добавляем задачи с дедлайном
    tasks_with_deadline = [t for t in tasks if isinstance(t, dict) and t.get('deadline')]
    for task in tasks_with_deadline[:200]:  # Ограничиваем до 200 задач
        deadline = task.get('deadline', '')
        if deadline:
            # Извлекаем дату из дедлайна (может быть ISO формат с временем)
            deadline_date_str = deadline
            try:
                if 'T' in deadline:
                    # ISO формат с временем: извлекаем только дату
                    deadline_date = datetime.fromisoformat(deadline.replace('Z', '+00:00')).date()
                    deadline_date_str = deadline_date.strftime('%Y-%m-%d')
                elif len(deadline) > 10:
                    # Пробуем извлечь дату из других форматов
                    try:
                        deadline_date = datetime.fromisoformat(deadline).date()
                        deadline_date_str = deadline_date.strftime('%Y-%m-%d')
                    except:
                        pass
            except Exception as e:
                print(f"Ошибка при парсинге дедлайна '{deadline}': {e}")
                pass
            
            # Устанавливаем источник по умолчанию для старых задач
            if 'source' not in task:
                task['source'] = 'tasks'
            if deadline_date_str not in items_by_date:
                items_by_date[deadline_date_str] = {'events': [], 'tasks': []}
            items_by_date[deadline_date_str]['tasks'].append(task)
    
    # Выводим объединенный план по датам
    try:
        # Вспомогательная функция для очистки служебного эмодзи из названия
        def _clean_title(title: str) -> str:
            if not isinstance(title, str):
                return title
            if title.startswith('🪡 '):
                return title[2:]
            if title.startswith('🪡'):
                return title[1:]
            return title
        
        first_date = True
        for date_str in sorted(items_by_date.keys()):
            # Форматируем дату в формат "10.02, вт"
            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                # День недели на русском
                weekdays_ru = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
                weekday_short = weekdays_ru[date_obj.weekday()]
                formatted_date = f"{date_obj.strftime('%d.%m')}, {weekday_short}"
            except ValueError:
                logger.warning(f"Неверный формат даты '{date_str}'")
                formatted_date = date_str
            
            # Для первой даты не добавляем перенос строки перед ней
            if first_date:
                text += f"<b>{formatted_date}</b>\n"
                first_date = False
            else:
                text += f"\n<b>{formatted_date}</b>\n"
            
            date_items = items_by_date[date_str]
            
            # Объединяем события и задачи в один список с временем
            all_items = []
            
            # Добавляем события (из раздела Расписание)
            for event in date_items.get('events', []):
                if not isinstance(event, dict):
                    continue
                time_str = event.get('time', '00:00')
                title = event.get('title', 'Без названия')
                title = _clean_title(title)
                description = event.get('description', '')
                category = event.get('category', '')
                source = event.get('source', 'schedule')  # По умолчанию 'schedule' для событий
                all_items.append({
                    'time': time_str,
                    'title': title,
                    'comment': description,
                    'project': category,  # Используем category как project для событий
                    'type': 'event',
                    'source': source
                })
            
            # Добавляем задачи (из раздела Задачи)
            for task in date_items.get('tasks', []):
                if not isinstance(task, dict):
                    continue
                task_deadline = task.get('deadline', '')
                time_str = '00:00'
                if task_deadline and 'T' in task_deadline:
                    try:
                        deadline_dt = datetime.fromisoformat(task_deadline.replace('Z', '+00:00'))
                        time_str = deadline_dt.strftime('%H:%M')
                    except (ValueError, AttributeError):
                        pass
                
                title = task.get('title', 'Без названия')
                comment = task.get('comment', '')
                project = task.get('project', '')
                source = task.get('source', 'tasks')  # По умолчанию 'tasks' для задач
                category = task.get('category', 'task')
                
                # Категория "event" в задачах считается событием плана
                if category == 'event':
                    all_items.append({
                        'time': time_str,
                        'title': _clean_title(title),
                        'comment': comment,
                        'project': project,
                        'type': 'event',
                        'source': source
                    })
                else:
                    all_items.append({
                        'time': time_str,
                        'title': _clean_title(title),
                        'comment': comment,
                        'project': project,
                        'type': 'task',
                        'source': source
                    })
            
            # Сортируем: сначала события (schedule + задачи категории event), затем обычные задачи, внутри по времени
            all_items.sort(
                key=lambda x: (
                    x.get('type') != 'event',                  # все события (event) раньше задач
                    x.get('source', 'unknown') != 'schedule',  # внутри событий: расписание раньше задач-category-event
                    x.get('time', '00:00')
                )
            )
            
            # Разделяем события и задачи для наглядного вывода
            events_items = [item for item in all_items if item.get('type') == 'event']
            tasks_items = [item for item in all_items if item.get('type') == 'task']
            
            # Сначала выводим события (если есть)
            if events_items:
                text += " / 📅 Расписание\n\n"
                for item in events_items:
                    time_str = item.get('time', '00:00')
                    title = item.get('title', 'Без названия')
                    comment = item.get('comment', '')
                    project = item.get('project', '')
                    
                    # Время (подчёркнутое) и название на одной строке
                    text += f"<u>{time_str}</u> - {title}\n"
                    
                    # Комментарий и проект на следующих строках (если есть)
                    if comment:
                        text += f"<i>{comment}</i>\n"
                    if project:
                        text += f"{project}\n"
                    
                    # Пустая строка между делами
                    text += "\n"
            
            # Затем выводим задачи (если есть)
            if tasks_items:
                text += "/✅ Задачи \n\n"
                for item in tasks_items:
                    time_str = item.get('time', '00:00')
                    title = _clean_title(item.get('title', 'Без названия'))
                    comment = item.get('comment', '')
                    project = item.get('project', '')

                    # Время (подчёркнутое) и название на одной строке
                    text += f"<u>{time_str}</u> - {title}\n"
                    
                    # Комментарий и проект на следующих строках (если есть)
                    if comment:
                        text += f"<i>{comment}</i>\n"
                    if project:
                        text += f"{project}\n"
                    
                    # Пустая строка между делами
                    text += "\n"
    except Exception as e:
        logger.error(f"Ошибка при форматировании плана по датам: {e}", exc_info=True)
        text += "\n❌ Ошибка при форматировании плана"
    
    # Задачи без дедлайна (показываем отдельно в конце)
    try:
        tasks_without_deadline = [t for t in tasks if isinstance(t, dict) and not t.get('deadline')]
        if tasks_without_deadline:
            text += "\nдедлайн сегодня:\n"
            for task in tasks_without_deadline[:50]:
                if not isinstance(task, dict):
                    continue
                title = _clean_title(task.get('title', 'Без названия'))
                comment = task.get('comment', '')
                project = task.get('project', '')
                
                # Название на одной строке
                text += f"{title}\n"
                
                # Комментарий и проект на следующих строках (если есть)
                if comment:
                    text += f"<i>{comment}</i>\n"
                if project:
                    text += f"{project}\n"
                
                # Пустая строка между делами
                text += "\n"
            
            if len(tasks_without_deadline) > 50:
                text += f"\n... и еще {len(tasks_without_deadline) - 50} задач\n"
    except Exception as e:
        logger.error(f"Ошибка при форматировании задач без дедлайна: {e}", exc_info=True)
    
    return text

async def show_plan_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """План на сегодня"""
    try:
        if not update.message:
            logger.warning("show_plan_today вызван без сообщения")
            return
        
        user_id = update.effective_user.id
        schedule_module = context.application.bot_data.get('schedule_module')
        tasks_module = context.application.bot_data.get('tasks_module')
        
        events, tasks = get_combined_plan(user_id, schedule_module, tasks_module, days=1)
        text = format_combined_plan_text(events, tasks, "сегодня")
        
        keyboard = [
            [KeyboardButton("📅 План на сегодня")],
            [KeyboardButton("📅 План на завтра")],
            [KeyboardButton("📅 План на неделю")],
            [KeyboardButton("📅 План на месяц")],
            [KeyboardButton("📅 План на год")],
            [KeyboardButton("📅 План на 3 года")],
            [KeyboardButton("🏠 Главное меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при показе плана на сегодня: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Ошибка при загрузке плана на сегодня",
                    reply_markup=get_plan_keyboard()
                )
        except Exception:
            pass

async def show_plan_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """План на завтра"""
    try:
        if not update.message:
            logger.warning("show_plan_tomorrow вызван без сообщения")
            return
        
        user_id = update.effective_user.id
        schedule_module = context.application.bot_data.get('schedule_module')
        tasks_module = context.application.bot_data.get('tasks_module')
        
        # План на завтра = с завтрашнего дня на 1 день
        events, tasks = get_combined_plan(user_id, schedule_module, tasks_module, days=1, start_date_offset=1)
        text = format_combined_plan_text(events, tasks, "завтра")
        
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=get_plan_keyboard())
    except Exception as e:
        logger.error(f"Ошибка при показе плана на завтра: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Ошибка при загрузке плана на завтра",
                    reply_markup=get_plan_keyboard()
                )
        except Exception:
            pass

async def show_plan_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """План на неделю"""
    try:
        if not update.message:
            logger.warning("show_plan_week вызван без сообщения")
            return
        
        user_id = update.effective_user.id
        schedule_module = context.application.bot_data.get('schedule_module')
        tasks_module = context.application.bot_data.get('tasks_module')
        
        events, tasks = get_combined_plan(user_id, schedule_module, tasks_module, days=7)
        text = format_combined_plan_text(events, tasks, "неделю")
        
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=get_plan_keyboard())
    except Exception as e:
        logger.error(f"Ошибка при показе плана на неделю: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Ошибка при загрузке плана на неделю",
                    reply_markup=get_plan_keyboard()
                )
        except Exception:
            pass

async def show_plan_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """План на месяц"""
    try:
        if not update.message:
            logger.warning("show_plan_month вызван без сообщения")
            return
        
        user_id = update.effective_user.id
        schedule_module = context.application.bot_data.get('schedule_module')
        tasks_module = context.application.bot_data.get('tasks_module')
        
        events, tasks = get_combined_plan(user_id, schedule_module, tasks_module, days=30)
        text = format_combined_plan_text(events, tasks, "месяц")
        
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=get_plan_keyboard())
    except Exception as e:
        logger.error(f"Ошибка при показе плана на месяц: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Ошибка при загрузке плана на месяц",
                    reply_markup=get_plan_keyboard()
                )
        except Exception:
            pass

async def show_plan_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """План на год"""
    try:
        if not update.message:
            logger.warning("show_plan_year вызван без сообщения")
            return
        
        user_id = update.effective_user.id
        schedule_module = context.application.bot_data.get('schedule_module')
        tasks_module = context.application.bot_data.get('tasks_module')
        
        events, tasks = get_combined_plan(user_id, schedule_module, tasks_module, days=365)
        text = format_combined_plan_text(events, tasks, "год")
        
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=get_plan_keyboard())
    except Exception as e:
        logger.error(f"Ошибка при показе плана на год: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Ошибка при загрузке плана на год",
                    reply_markup=get_plan_keyboard()
                )
        except Exception:
            pass

async def show_plan_3years(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """План на 3 года"""
    try:
        if not update.message:
            logger.warning("show_plan_3years вызван без сообщения")
            return
        
        user_id = update.effective_user.id
        schedule_module = context.application.bot_data.get('schedule_module')
        tasks_module = context.application.bot_data.get('tasks_module')
        
        events, tasks = get_combined_plan(user_id, schedule_module, tasks_module, days=1095)  # 3 года
        text = format_combined_plan_text(events, tasks, "3 года")
        
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=get_plan_keyboard())
    except Exception as e:
        logger.error(f"Ошибка при показе плана на 3 года: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Ошибка при загрузке плана на 3 года",
                    reply_markup=get_plan_keyboard()
                )
        except Exception:
            pass

async def show_tasks_management_from_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список задач для управления из раздела План"""
    # Определяем, откуда пришел запрос (message или callback_query)
    if update.message:
        user_id = update.effective_user.id
        send_func = update.message.reply_text
    elif update.callback_query:
        user_id = update.callback_query.from_user.id
        send_func = update.callback_query.message.reply_text
    else:
        return
    
    tasks_module = context.application.bot_data.get('tasks_module')
    
    if not tasks_module:
        await send_func(
            "❌ Модуль задач недоступен",
            reply_markup=get_plan_keyboard()
        )
        return
    
    try:
        # Получаем все задачи пользователя
        if hasattr(tasks_module, 'get_user_tasks'):
            tasks = tasks_module.get_user_tasks(str(user_id))
        else:
            await send_func(
                "❌ Не удалось получить задачи",
                reply_markup=get_plan_keyboard()
            )
            return
        
        if not tasks:
            await send_func(
                "📝 У вас пока нет задач.\n\nДобавьте задачи в разделе «Задачи».",
                reply_markup=get_plan_keyboard()
            )
            return
        
        # Формируем список задач с кнопками для отметки
        text = "<b>✅ Управление задачами</b>\n\n"
        text += "Нажмите на задачу: выполнить, редактировать (название или время) или вернуться.\n\n"
        
        keyboard = []
        
        # Показываем только невыполненные задачи
        incomplete_tasks = [t for t in tasks if not t.get('completed', False)]
        
        for i, task in enumerate(incomplete_tasks[:50], 1):  # Ограничиваем до 50 задач
            title = task.get('title', 'Без названия')
            task_id = task.get('id', '')
            deadline = task.get('deadline', '')
            project = task.get('project', '')
            
            # Формируем текст кнопки
            button_text = f"{i}. {title}"
            if deadline:
                try:
                    from datetime import datetime
                    if 'T' in deadline:
                        deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                        deadline_str = deadline_dt.strftime('%d.%m %H:%M')
                    else:
                        deadline_dt = datetime.strptime(deadline, '%Y-%m-%d')
                        deadline_str = deadline_dt.strftime('%d.%m')
                    button_text += f" ({deadline_str})"
                except:
                    pass
            
            if project:
                button_text += f" [{project}]"
            
            # Обрезаем текст кнопки, если слишком длинный
            if len(button_text) > 60:
                button_text = button_text[:57] + "..."
            
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"plan_task_complete_{task_id}")])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад к плану", callback_data="plan_back_to_plan")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await send_func(
            text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
    except Exception as e:
        print(f"❌ Ошибка при показе управления задачами: {e}")
        import traceback
        traceback.print_exc()
        await send_func(
            "❌ Произошла ошибка при загрузке задач",
            reply_markup=get_plan_keyboard()
        )

async def check_deadline_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Проверка задач с дедлайном сегодня и отправка сводного напоминания в 18:00"""
    try:
        current_time = datetime.now()
        
        tasks_module = context.application.bot_data.get('tasks_module')
        if not tasks_module:
            logger.warning("Модуль задач недоступен для проверки дедлайнов")
            return
        
        # Получаем все данные задач
        if not hasattr(tasks_module, 'load_data'):
            logger.warning("tasks_module не имеет метода load_data")
            return
        
        try:
            data = tasks_module.load_data()
            if not isinstance(data, dict):
                logger.warning("load_data вернул не словарь")
                return
        except Exception as e:
            logger.error(f"Ошибка при загрузке данных задач: {e}", exc_info=True)
            return
        
        today = current_time.date()
        reminders_sent = 0
        
        # Проверяем всех пользователей
        users_data = data.get('users', {})
        if not isinstance(users_data, dict):
            logger.warning("users_data не является словарем")
            return
        
        for user_id_str, user_data in users_data.items():
            if not isinstance(user_data, dict):
                continue
            
            tasks = user_data.get('tasks', [])
            if not isinstance(tasks, list):
                continue
            
            # Находим задачи с дедлайном сегодня
            tasks_today = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                
                # Пропускаем выполненные задачи
                if task.get('completed', False):
                    continue
                
                deadline = task.get('deadline')
                if not deadline:
                    continue
                
                try:
                    # Парсим дедлайн
                    if isinstance(deadline, str):
                        if 'T' in deadline:
                            deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                        else:
                            deadline_dt = datetime.strptime(deadline, '%Y-%m-%d')
                    else:
                        continue
                    
                    if deadline_dt.tzinfo:
                        deadline_dt = deadline_dt.replace(tzinfo=None)
                    
                    deadline_date = deadline_dt.date()
                    
                    # Проверяем, что дедлайн сегодня
                    if deadline_date == today:
                        tasks_today.append((task, deadline_dt))
                
                except (ValueError, AttributeError) as e:
                    logger.debug(f"Ошибка при парсинге дедлайна '{deadline}': {e}")
                    continue
            
            # Если есть задачи с дедлайном сегодня, отправляем напоминание
            if tasks_today:
                # Формируем сообщение
                message = "🔔 <b>Напоминание: дедлайн сегодня!</b>\n\n"
                message += f"У вас <b>{len(tasks_today)}</b> задач с дедлайном на сегодня:\n\n"
                
                for i, (task, deadline_dt) in enumerate(tasks_today[:10], 1):  # Ограничиваем до 10 задач
                    title = task.get('title', 'Без названия')
                    project = task.get('project', '')
                    
                    # Форматируем время дедлайна (показываем только если время не 00:00)
                    time_str = ''
                    if deadline_dt.hour != 0 or deadline_dt.minute != 0:
                        time_str = deadline_dt.strftime('%H:%M')
                    
                    task_line = f"{i}. <b>{title}</b>"
                    if time_str:
                        task_line += f" ({time_str})"
                    if project:
                        task_line += f" [{project}]"
                    message += task_line + "\n"
                
                if len(tasks_today) > 10:
                    message += f"\n... и еще {len(tasks_today) - 10} задач"
                
                # Отправляем сообщение пользователю
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id_str),
                        text=message,
                        parse_mode='HTML'
                    )
                    reminders_sent += 1
                    logger.info(f"✅ Напоминание о дедлайнах отправлено пользователю {user_id_str} ({len(tasks_today)} задач)")
                except Exception as e:
                    logger.error(f"❌ Ошибка при отправке напоминания пользователю {user_id_str}: {e}", exc_info=True)
        
        if reminders_sent > 0:
            logger.info(f"📅 Напоминания о дедлайнах отправлены {reminders_sent} пользователям")
    
    except Exception as e:
        logger.error(f"Ошибка в check_deadline_reminders: {e}", exc_info=True)


async def check_task_reminders_unified(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая проверка напоминаний по задачам (как в task-manager-bot)"""
    try:
        # Получаем модуль задач из bot_data
        tasks_module = context.application.bot_data.get('tasks_module')
        if not tasks_module or not hasattr(tasks_module, 'load_data'):
            logger.warning("tasks_module недоступен для проверки напоминаний задач")
            return
        
        data = tasks_module.load_data()
        
        # Используем функцию now() из модуля задач, если есть
        if hasattr(tasks_module, 'now'):
            current_time = tasks_module.now()
            if current_time.tzinfo:
                current_time = current_time.replace(tzinfo=None)
        else:
            current_time = datetime.now()
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
                    
                    # Сравниваем только до минут
                    reminder_minute = reminder_dt.replace(second=0, microsecond=0)
                    
                    if reminder_minute == current_minute:
                        # Ключ, чтобы не слать дубликаты
                        reminder_key = f"reminder_{user_id_str}_{task.get('id')}_{reminder_minute.isoformat()}"
                        
                        if not context.bot_data.get(reminder_key, False):
                            task_title = task.get('title', 'Без названия')
                            deadline_str = task.get('deadline')
                            
                            message = "🔔 <b>Напоминание о задаче</b>\n\n"
                            message += f"<b>{task_title}</b>\n"
                            
                            if deadline_str:
                                deadline_dt = datetime.fromisoformat(deadline_str)
                                if deadline_dt.tzinfo:
                                    deadline_dt = deadline_dt.replace(tzinfo=None)
                                
                                # Используем форматирование из модуля задач, если есть
                                if hasattr(tasks_module, 'format_deadline_readable'):
                                    deadline_formatted = tasks_module.format_deadline_readable(deadline_dt)
                                else:
                                    deadline_formatted = deadline_dt.strftime('%d.%m.%Y %H:%M')
                                
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
                                context.bot_data[reminder_key] = True
                                reminders_sent += 1
                                logger.info(f"✅ Напоминание по задаче отправлено пользователю {user_id_str} для задачи '{task_title}'")
                            except Exception as e:
                                logger.error(f"❌ Ошибка при отправке напоминания пользователю {user_id_str}: {e}", exc_info=True)
                
                except Exception as e:
                    logger.error(f"❌ Ошибка при обработке напоминания для задачи {task.get('id')}: {e}", exc_info=True)
        
        if reminders_checked > 0:
            logger.info(f"[Напоминания задач] Проверено: {reminders_checked}, отправлено: {reminders_sent}, текущее время: {current_minute.strftime('%Y-%m-%d %H:%M')}")
        
        # Чистим старые ключи напоминаний (старше 1 часа)
        current_keys = list(context.bot_data.keys())
        for key in current_keys:
            if key.startswith("reminder_"):
                try:
                    parts = key.split('_')
                    if len(parts) >= 4:
                        reminder_datetime_str = '_'.join(parts[3:])
                        reminder_datetime = datetime.fromisoformat(reminder_datetime_str)
                        if (current_time - reminder_datetime).total_seconds() > 3600:
                            del context.bot_data[key]
                except Exception:
                    continue
    except Exception as e:
        logger.error(f"Ошибка в check_task_reminders_unified: {e}", exc_info=True)


def load_env_file(env_path: str) -> bool:
    """Загрузить переменные окружения из .env файла
    
    Returns:
        True если файл успешно загружен, False в противном случае
    """
    try:
        if not os.path.exists(env_path):
            logger.warning(f"Файл .env не найден: {env_path}")
            return False
        
        with open(env_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    try:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip("'\"")
                        os.environ[key] = value
                    except ValueError as e:
                        logger.warning(f"Неверный формат строки {line_num} в .env: {line}")
        
        logger.info("Файл .env успешно загружен")
        return True
    except Exception as e:
        logger.error(f"Ошибка при загрузке .env файла: {e}", exc_info=True)
        return False

def load_module(module_path: str, module_name: str) -> Optional[Any]:
    """Загрузить модуль из файла
    
    Args:
        module_path: Путь к файлу модуля
        module_name: Имя модуля для логирования
    
    Returns:
        Загруженный модуль или None в случае ошибки
    """
    try:
        if not os.path.exists(module_path):
            logger.error(f"Файл модуля {module_name} не найден: {module_path}")
            return None
        
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            logger.error(f"Не удалось создать spec для модуля {module_name}")
            return None
        
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        logger.info(f"✅ Модуль {module_name} успешно загружен")
        return module
    except Exception as e:
        logger.error(f"❌ Ошибка при загрузке модуля {module_name}: {e}", exc_info=True)
        return None

def main():
    """Основная функция запуска объединенного бота"""
    # Загружаем токен из .env (локально) или из переменных окружения (Railway и т.п.)
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    load_env_file(env_file)
    
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN')
    
    # Диагностика: какие переменные с TELEGRAM видны (без вывода значения токена)
    telegram_vars = [k for k in os.environ if 'TELEGRAM' in k.upper()]
    logger.info(f"Переменные окружения с TELEGRAM: {telegram_vars if telegram_vars else 'нет'}")
    if token:
        logger.info(f"TELEGRAM_BOT_TOKEN найден, длина: {len(token)}")
    
    if not token:
        logger.error("Не указан TELEGRAM_BOT_TOKEN!")
        logger.error("Создайте файл .env с содержимым: TELEGRAM_BOT_TOKEN=ваш_токен")
        return
    
    # Загружаем модули ботов один раз при старте
    schedule_module = None
    tasks_module = None
    
    schedule_bot_path = str(BASE_DIR / 'schedule-bot' / 'bot.py')
    schedule_module = load_module(schedule_bot_path, "schedule_bot")
    
    tasks_bot_path = str(BASE_DIR / 'task-manager-bot' / 'bot_advanced.py')
    tasks_module = load_module(tasks_bot_path, "tasks_bot")
    
    if not schedule_module and not tasks_module:
        logger.error("Не удалось загрузить ни один из модулей бота!")
        logger.error("Проверьте пути к модулям в коде")
        return
    
    # Настройка команд бота для подсказок
    async def post_init(app: Application) -> None:
        """Инициализация после создания приложения"""
        try:
            commands = [
                BotCommand("start", "Начать работу с ботом")
            ]
            await app.bot.set_my_commands(commands)
            logger.info("Команды бота установлены")
        except Exception as e:
            logger.error(f"Ошибка при установке команд бота: {e}", exc_info=True)
        
        # Сохраняем модули в bot_data после создания приложения
        app.bot_data['schedule_module'] = schedule_module
        app.bot_data['tasks_module'] = tasks_module
        logger.info("Модули сохранены в bot_data")
        
        # Настраиваем фоновые задачи напоминаний
        try:
            job_queue = app.job_queue
            if job_queue:
                # 1) Сводное напоминание о задачах с дедлайном сегодня в 18:00
                job_queue.run_daily(
                    check_deadline_reminders,
                    time=time(18, 0),
                    name="deadline_deadlines_summary"
                )
                logger.info("✅ Сводные напоминания о дедлайнах настроены на 18:00 каждый день")

                # 2) Минутные напоминания по событиям расписания (из schedule_bot)
                if schedule_module and hasattr(schedule_module, 'send_reminders'):
                    job_queue.run_repeating(
                        schedule_module.send_reminders,
                        interval=60,
                        first=10,
                        name="schedule_event_reminders"
                    )
                    logger.info("✅ Минутные напоминания по событиям расписания активированы")
                else:
                    logger.warning("schedule_module.send_reminders недоступен, напоминания по событиям не будут работать")

                # 3) Минутные напоминания по задачам (как в task-manager-bot)
                job_queue.run_repeating(
                    check_task_reminders_unified,
                    interval=60,
                    first=10,
                    name="task_reminders"
                )
                logger.info("✅ Минутные напоминания по задачам активированы")
            else:
                logger.warning("job_queue недоступен для настройки напоминаний")
        except Exception as e:
            logger.error(f"Ошибка при настройке напоминаний: {e}", exc_info=True)
    
    # Создаем приложение с поддержкой job_queue для напоминаний и post_init
    # job_queue включен по умолчанию в python-telegram-bot 20.x
    application = Application.builder().token(token).post_init(post_init).build()
    
    # ВАЖНО: ConversationHandler должны быть зарегистрированы ПЕРВЫМИ!
    # Регистрируем обработчики из бота расписания (если модуль загружен)
    if schedule_module:
        try:
            # Переопределяем get_main_keyboard в модуле расписания глобально для unified_bot
            # Это гарантирует, что все функции завершения будут использовать правильную клавиатуру
            if hasattr(schedule_module, 'get_main_keyboard'):
                schedule_module.get_main_keyboard = get_schedule_keyboard
                logger.info("✅ get_main_keyboard переопределен для раздела расписания")
            
            # Переопределяем функции категорий, чтобы они использовали проекты из tasks_module
            if tasks_module:
                def get_user_categories_unified(user_id: str):
                    """Получение категорий пользователя через проекты"""
                    # Получаем проекты из tasks_module
                    try:
                        if not hasattr(tasks_module, 'get_user_projects'):
                            logger.warning("tasks_module не имеет метода get_user_projects")
                            return {'other': 'остальное'}
                        
                        projects = tasks_module.get_user_projects(str(user_id))
                        if not isinstance(projects, list):
                            logger.warning(f"get_user_projects вернул не список: {type(projects)}")
                            projects = []
                    except Exception as e:
                        logger.error(f"Ошибка при получении проектов для пользователя {user_id}: {e}", exc_info=True)
                        projects = []
                    
                    # Преобразуем проекты в формат категорий {project_name: project_name}
                    # Используем имя проекта как и ID, и название для совместимости
                    categories = {}
                    for project in projects:
                        if project and isinstance(project, str):  # Проверяем, что проект не пустой и строка
                            # Используем имя проекта как ключ и значение
                            categories[project] = project
                    
                    # Если проектов нет, возвращаем дефолтную категорию
                    if not categories:
                        categories['other'] = 'остальное'
                    
                    return categories
                
                def add_user_category_unified(user_id: str, category_id: str, category_name: str):
                    """Добавление категории через создание проекта"""
                    # Используем category_name как имя проекта
                    try:
                        if not hasattr(tasks_module, 'get_user_projects') or not hasattr(tasks_module, 'load_data'):
                            logger.warning("tasks_module не имеет необходимых методов для добавления категории")
                            return
                        
                        # Проверяем, существует ли проект
                        projects = tasks_module.get_user_projects(str(user_id))
                        if not isinstance(projects, list):
                            logger.warning(f"get_user_projects вернул не список: {type(projects)}")
                            projects = []
                        
                        if category_name not in projects:
                            # Создаем новый проект через tasks_module
                            data = tasks_module.load_data()
                            if not isinstance(data, dict):
                                logger.error("load_data вернул не словарь")
                                return
                            
                            user_id_str = str(user_id)
                            if 'users' not in data:
                                data['users'] = {}
                            if user_id_str not in data['users']:
                                data['users'][user_id_str] = {'tasks': [], 'projects': [], 'tags': [], 'projects_data': {}}
                            if 'projects_data' not in data['users'][user_id_str]:
                                data['users'][user_id_str]['projects_data'] = {}
                            
                            # Добавляем проект
                            from datetime import datetime
                            data['users'][user_id_str]['projects_data'][category_name] = {
                                'completed': False,
                                'created_at': datetime.now().isoformat()
                            }
                            
                            if hasattr(tasks_module, 'save_data'):
                                tasks_module.save_data(data)
                                logger.info(f"✅ Проект '{category_name}' добавлен для пользователя {user_id}")
                            else:
                                logger.error("tasks_module не имеет метода save_data")
                    except Exception as e:
                        logger.error(f"Ошибка при добавлении проекта '{category_name}' для пользователя {user_id}: {e}", exc_info=True)
                
                def delete_user_category_unified(user_id: str, category_id: str) -> bool:
                    """Удаление категории через удаление проекта"""
                    try:
                        if not hasattr(tasks_module, 'load_data') or not hasattr(tasks_module, 'get_user_projects'):
                            logger.warning("tasks_module не имеет необходимых методов для удаления категории")
                            return False
                        
                        # Используем category_id как имя проекта
                        data = tasks_module.load_data()
                        if not isinstance(data, dict):
                            logger.error("load_data вернул не словарь")
                            return False
                        
                        user_id_str = str(user_id)
                        if 'users' not in data or user_id_str not in data['users']:
                            logger.debug(f"Пользователь {user_id} не найден в данных")
                            return False
                        
                        projects_data = data['users'][user_id_str].get('projects_data', {})
                        if category_id in projects_data:
                            # Нельзя удалить последний проект (должен остаться хотя бы один)
                            active_projects = tasks_module.get_user_projects(str(user_id))
                            if not isinstance(active_projects, list):
                                logger.warning("get_user_projects вернул не список")
                                active_projects = []
                            
                            if len(active_projects) <= 1:
                                logger.info(f"Нельзя удалить последний проект для пользователя {user_id}")
                                return False
                            
                            del projects_data[category_id]
                            
                            # Также обновляем задачи, убирая ссылку на проект
                            tasks = data['users'][user_id_str].get('tasks', [])
                            for task in tasks:
                                if isinstance(task, dict) and task.get('project') == category_id:
                                    task['project'] = None
                            
                            # Обновляем события, убирая ссылку на категорию
                            if schedule_module and hasattr(schedule_module, 'get_user_events'):
                                try:
                                    events = schedule_module.get_user_events(str(user_id))
                                    if isinstance(events, list):
                                        updated = False
                                        for event in events:
                                            if isinstance(event, dict) and event.get('category') == category_id:
                                                event['category'] = 'other'
                                                updated = True
                                        
                                        if updated and hasattr(schedule_module, 'update_user_event'):
                                            # Сохраняем обновленные события через update_user_event
                                            for event in events:
                                                if isinstance(event, dict) and event.get('category') == 'other' and 'id' in event:
                                                    schedule_module.update_user_event(str(user_id), event['id'], event)
                                except Exception as e:
                                    logger.error(f"Ошибка при обновлении событий при удалении категории: {e}", exc_info=True)
                            
                            if hasattr(tasks_module, 'save_data'):
                                tasks_module.save_data(data)
                                logger.info(f"✅ Категория '{category_id}' удалена для пользователя {user_id}")
                                return True
                            else:
                                logger.error("tasks_module не имеет метода save_data")
                                return False
                        
                        return False
                    except Exception as e:
                        logger.error(f"Ошибка при удалении категории '{category_id}' для пользователя {user_id}: {e}", exc_info=True)
                        return False
                
                def update_user_category_unified(user_id: str, category_id: str, new_name: str):
                    """Обновление категории через переименование проекта"""
                    try:
                        if not hasattr(tasks_module, 'rename_user_project'):
                            logger.warning("tasks_module не имеет метода rename_user_project")
                            return False
                        
                        # Используем tasks_module.rename_user_project
                        result = tasks_module.rename_user_project(str(user_id), category_id, new_name)
                        if result:
                            # Также обновляем события, которые используют эту категорию
                            if schedule_module and hasattr(schedule_module, 'get_user_events'):
                                try:
                                    events = schedule_module.get_user_events(str(user_id))
                                    if isinstance(events, list):
                                        updated = False
                                        for event in events:
                                            if isinstance(event, dict) and event.get('category') == category_id:
                                                event['category'] = new_name
                                                updated = True
                                        
                                        if updated:
                                            # Сохраняем обновленные события
                                            if hasattr(schedule_module, 'save_user_events'):
                                                schedule_module.save_user_events(str(user_id), events)
                                            elif hasattr(schedule_module, 'save_data'):
                                                # Альтернативный способ сохранения
                                                data = schedule_module.load_data() if hasattr(schedule_module, 'load_data') else {}
                                                if isinstance(data, dict) and 'users' in data and str(user_id) in data['users']:
                                                    data['users'][str(user_id)]['events'] = events
                                                    schedule_module.save_data(data)
                                except Exception as e:
                                    logger.error(f"Ошибка при обновлении событий при переименовании категории: {e}", exc_info=True)
                        
                        return result
                    except Exception as e:
                        logger.error(f"Ошибка при обновлении категории '{category_id}' для пользователя {user_id}: {e}", exc_info=True)
                        return False
                
                # Переопределяем функции категорий в schedule_module
                schedule_module.get_user_categories = get_user_categories_unified
                schedule_module.add_user_category = add_user_category_unified
                schedule_module.delete_user_category = delete_user_category_unified
                schedule_module.update_user_category = update_user_category_unified
                logger.info("✅ Функции категорий переопределены для использования проектов")
            
            # Используем обертки из модуля wrappers
            
            # ConversationHandler для добавления события - РЕГИСТРИРУЕМ ПЕРВЫМ!
            # Проверяем наличие необходимых функций и состояний
            if (hasattr(schedule_module, 'add_event_start') and 
                hasattr(schedule_module, 'add_event_title') and
                hasattr(schedule_module, 'WAITING_TITLE')):
                try:
                    # Получаем состояния из модуля
                    WAITING_TITLE = schedule_module.WAITING_TITLE
                    WAITING_DATE = schedule_module.WAITING_DATE
                    WAITING_TIME = schedule_module.WAITING_TIME
                    WAITING_DESCRIPTION = schedule_module.WAITING_DESCRIPTION
                    WAITING_CATEGORY = schedule_module.WAITING_CATEGORY
                    WAITING_REPEAT = schedule_module.WAITING_REPEAT
                    # Проверяем наличие WAITING_REMINDER_1
                    WAITING_REMINDER_1 = getattr(schedule_module, 'WAITING_REMINDER_1', None)
                    
                    # Используем обертки из модуля wrappers
                    
                    # Создаем обертку для add_event_title с поддержкой голоса и измененным порядком
                    async def add_event_title_with_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
                        """Обертка для add_event_title с поддержкой голосовых сообщений и измененным порядком"""
                        # Устанавливаем режим расписания
                        context.user_data['bot_mode'] = MODE_SCHEDULE
                        
                        raw_text = None
                        
                        # Если это голосовое сообщение, обрабатываем его
                        if update.message.voice:
                            # Используем функции из tasks_module для обработки голоса
                            if tasks_module and hasattr(tasks_module, 'transcribe_voice'):
                                try:
                                    print(f"Получено голосовое сообщение для события: duration={update.message.voice.duration}")
                                    
                                    # Пробуем использовать caption от Telegram (если есть)
                                    if update.message.caption:
                                        raw_text = update.message.caption.strip()
                                        print(f"Использован caption от Telegram: {raw_text}")
                                        if hasattr(tasks_module, 'normalize_voice_text'):
                                            raw_text = tasks_module.normalize_voice_text(raw_text)
                                    else:
                                        # Получаем файл голосового сообщения
                                        voice_file = await update.message.voice.get_file()
                                        print(f"Файл получен: file_path={voice_file.file_path}")
                                        
                                        # Транскрибируем голос
                                        transcribed_text = await tasks_module.transcribe_voice(voice_file, update)
                                        
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
                                            return WAITING_TITLE
                                    
                                    # Заменяем текст сообщения на распознанный текст
                                    update.message.text = raw_text
                                    
                                except Exception as e:
                                    print(f"❌ Ошибка при обработке голосового сообщения: {e}")
                                    import traceback
                                    traceback.print_exc()
                                    await update.message.reply_text("Ошибка обработки голосового сообщения. Попробуйте написать текст:")
                                    return WAITING_TITLE
                            else:
                                await update.message.reply_text("Повторите текстом:")
                                return WAITING_TITLE
                        elif update.message.text:
                            raw_text = update.message.text.strip()
                        
                        if not raw_text:
                            await update.message.reply_text("Ошибка: Название события не может быть пустым. Попробуйте снова:")
                            return WAITING_TITLE
                        
                        # Сохраняем название события
                        if 'new_event' not in context.user_data:
                            context.user_data['new_event'] = {}
                        context.user_data['new_event']['title'] = raw_text
                        
                        # Сохраняем ID сообщения пользователя (используем функцию из schedule_module если есть)
                        if hasattr(schedule_module, 'add_user_message_id'):
                            schedule_module.add_user_message_id(update.effective_user.id, update.message.message_id)
                        
                        # Изменяем порядок: после названия переходим к описанию (как в задачах)
                        msg = await update.message.reply_text(
                            "Что-то уточним, или /skip",
                            parse_mode='HTML'
                        )
                        if hasattr(schedule_module, 'add_message_id'):
                            schedule_module.add_message_id(update.effective_user.id, msg.message_id)
                        
                        return WAITING_DESCRIPTION
                    
                    # Создаем обертку для add_event_description с измененным порядком
                    async def add_event_description_reordered(update: Update, context: ContextTypes.DEFAULT_TYPE):
                        """Обертка для add_event_description с измененным порядком (после описания -> категория)"""
                        # Устанавливаем режим расписания
                        context.user_data['bot_mode'] = MODE_SCHEDULE
                        
                        description_text = None
                        
                        # Обрабатываем голосовое сообщение если есть
                        if update.message.voice:
                            if tasks_module and hasattr(tasks_module, 'transcribe_voice'):
                                try:
                                    print(f"Получено голосовое сообщение для описания события")
                                    
                                    if update.message.caption:
                                        description_text = update.message.caption.strip()
                                        if hasattr(tasks_module, 'normalize_voice_text'):
                                            description_text = tasks_module.normalize_voice_text(description_text)
                                    else:
                                        voice_file = await update.message.voice.get_file()
                                        transcribed_text = await tasks_module.transcribe_voice(voice_file, update)
                                        if transcribed_text:
                                            description_text = transcribed_text.strip()
                                            await update.message.reply_text(description_text)
                                        else:
                                            await update.message.reply_text(
                                                "Не удалось распознать голосовое сообщение. Попробуйте написать текст или /skip"
                                            )
                                            return WAITING_DESCRIPTION
                                except Exception as e:
                                    print(f"❌ Ошибка при обработке голосового сообщения: {e}")
                                    await update.message.reply_text("Ошибка обработки голосового сообщения. Попробуйте написать текст или /skip:")
                                    return WAITING_DESCRIPTION
                            else:
                                await update.message.reply_text("Повторите текстом или /skip:")
                                return WAITING_DESCRIPTION
                        elif update.message.text:
                            if update.message.text.lower() != '/skip':
                                description_text = update.message.text.strip()
                            else:
                                description_text = ''
                        
                        # Сохраняем описание
                        if 'new_event' not in context.user_data:
                            context.user_data['new_event'] = {}
                        context.user_data['new_event']['description'] = description_text if description_text else ''
                        
                        # Сохраняем ID сообщения пользователя
                        if update.message.text.lower() != '/skip':
                            if hasattr(schedule_module, 'add_user_message_id'):
                                schedule_module.add_user_message_id(update.effective_user.id, update.message.message_id)
                        
                        # После описания переходим к категории (как в задачах после комментария переходят к проекту)
                        user_id = update.effective_user.id
                        user_categories = schedule_module.get_user_categories(user_id)
                        
                        # Проверяем, есть ли категории у пользователя
                        if not user_categories or len(user_categories) == 0:
                            keyboard = [
                                [InlineKeyboardButton("Создать категории", callback_data="manage_categories")]
                            ]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            msg = await update.message.reply_text(
                                "У вас пока нет категорий. Создайте их, чтобы продолжить добавление события.",
                                reply_markup=reply_markup,
                                parse_mode='HTML'
                            )
                            if hasattr(schedule_module, 'add_message_id'):
                                schedule_module.add_message_id(user_id, msg.message_id)
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
                        if hasattr(schedule_module, 'add_message_id'):
                            schedule_module.add_message_id(user_id, msg.message_id)
                        return WAITING_CATEGORY
                    
                    # Создаем обертку для add_event_category с измененным порядком
                    async def add_event_category_reordered(update: Update, context: ContextTypes.DEFAULT_TYPE):
                        """Обертка для add_event_category с измененным порядком (после категории -> дата)"""
                        # Устанавливаем режим расписания
                        context.user_data['bot_mode'] = MODE_SCHEDULE
                        
                        query = update.callback_query
                        await query.answer()
                        
                        category = query.data.replace('category_', '')
                        if 'new_event' not in context.user_data:
                            context.user_data['new_event'] = {}
                        context.user_data['new_event']['category'] = category
                        
                        # После категории переходим к дате (как в задачах после проекта переходят к дедлайну)
                        msg = await query.edit_message_text(
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
                        if hasattr(schedule_module, 'add_message_id'):
                            schedule_module.add_message_id(query.from_user.id, msg.message_id)
                        
                        return WAITING_DATE
                    
                    # Создаем обертку для add_event_time с измененным порядком
                    async def add_event_time_reordered(update: Update, context: ContextTypes.DEFAULT_TYPE):
                        """Обертка для add_event_time с измененным порядком (после времени -> напоминание)"""
                        # Устанавливаем режим расписания
                        context.user_data['bot_mode'] = MODE_SCHEDULE
                        
                        # Вызываем оригинальную функцию, но перехватываем возвращаемое значение
                        # Временно заменяем текст сообщения, чтобы функция работала правильно
                        original_text = update.message.text
                        result = await schedule_module.add_event_time(update, context)
                        
                        # Если функция вернула WAITING_DESCRIPTION, меняем на WAITING_REMINDER_1 или WAITING_REPEAT
                        if result == schedule_module.WAITING_DESCRIPTION:
                            # После времени переходим к напоминанию (как в задачах после дедлайна переходят к напоминанию)
                            if WAITING_REMINDER_1 is not None:
                                # Создаем клавиатуру для выбора напоминания
                                keyboard = [
                                    [InlineKeyboardButton("За 15 минут", callback_data="reminder_15")],
                                    [InlineKeyboardButton("За 30 минут", callback_data="reminder_30")],
                                    [InlineKeyboardButton("За 1 час", callback_data="reminder_60")],
                                    [InlineKeyboardButton("За 2 часа", callback_data="reminder_120")],
                                    [InlineKeyboardButton("Без напоминания", callback_data="reminder_0")]
                                ]
                                reply_markup = InlineKeyboardMarkup(keyboard)
                                
                                msg = await update.message.reply_text(
                                    "Выберите напоминание:",
                                    reply_markup=reply_markup,
                                    parse_mode='HTML'
                                )
                                if hasattr(schedule_module, 'add_message_id'):
                                    schedule_module.add_message_id(update.effective_user.id, msg.message_id)
                                return WAITING_REMINDER_1
                            else:
                                # Если нет напоминания, переходим к повторению
                                keyboard = [
                                    [InlineKeyboardButton("Одноразовое", callback_data="repeat_once")],
                                    [InlineKeyboardButton("Ежедневное", callback_data="repeat_daily")],
                                    [InlineKeyboardButton("Еженедельное", callback_data="repeat_weekly")]
                                ]
                                reply_markup = InlineKeyboardMarkup(keyboard)
                                
                                msg = await update.message.reply_text(
                                    "Выберите тип повторения:",
                                    reply_markup=reply_markup
                                )
                                if hasattr(schedule_module, 'add_message_id'):
                                    schedule_module.add_message_id(update.effective_user.id, msg.message_id)
                                return WAITING_REPEAT
                        
                        return result
                    
                    # Создаем states для ConversationHandler (оборачиваем функции для правильного режима)
                    states = {
                        WAITING_TITLE: [
                            MessageHandler(
                                (filters.TEXT | filters.VOICE) & ~filters.COMMAND,
                                wrap_schedule_handler(add_event_title_with_voice)
                            )
                        ],
                        WAITING_DATE: [
                            MessageHandler(
                                filters.TEXT & ~filters.COMMAND,
                                wrap_schedule_handler(schedule_module.add_event_date)
                            )
                        ],
                        WAITING_TIME: [
                            MessageHandler(
                                filters.TEXT & ~filters.COMMAND,
                                wrap_schedule_handler(add_event_time_reordered)
                            )
                        ],
                        WAITING_DESCRIPTION: [
                            MessageHandler(
                                (filters.TEXT | filters.VOICE) & ~filters.COMMAND,
                                wrap_schedule_handler(add_event_description_reordered)
                            ),
                            CommandHandler('skip', wrap_schedule_handler(add_event_description_reordered))
                        ],
                        WAITING_CATEGORY: [
                            CallbackQueryHandler(
                                wrap_schedule_handler(add_event_category_reordered),
                                pattern='^category_'
                            ),
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.manage_categories),
                                pattern='^manage_categories$'
                            ),
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.back_to_category_selection),
                                pattern='^back_to_category_selection$'
                            ) if hasattr(schedule_module, 'back_to_category_selection') else None
                        ],
                        WAITING_REPEAT: [
                            CallbackQueryHandler(
                                wrap_schedule_handler(add_event_repeat_reordered),
                                pattern='^repeat_'
                            )
                        ],
                    }
                    
                    # Добавляем дополнительные обработчики для категорий если есть
                    if hasattr(schedule_module, 'manage_categories'):
                        states[WAITING_CATEGORY].append(
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.manage_categories),
                                pattern='^manage_categories$'
                            )
                        )
                    if hasattr(schedule_module, 'back_to_category_selection'):
                        states[WAITING_CATEGORY].append(
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.back_to_category_selection),
                                pattern='^back_to_category_selection$'
                            )
                        )
                    
                    # Создаем обертку для add_event_reminder_1 с измененным порядком
                    async def add_event_reminder_1_reordered(update: Update, context: ContextTypes.DEFAULT_TYPE):
                        """Обертка для add_event_reminder_1 с измененным порядком (после напоминания -> повторение)"""
                        # Устанавливаем режим расписания
                        context.user_data['bot_mode'] = MODE_SCHEDULE
                        
                        query = update.callback_query
                        await query.answer()
                        
                        reminder_data = query.data.replace('reminder_', '')
                        
                        if reminder_data == 'none' or reminder_data == '0':
                            # Если выбрано "Без напоминания", сохраняем пустой список
                            if 'new_event' not in context.user_data:
                                context.user_data['new_event'] = {}
                            context.user_data['new_event']['reminders'] = []
                        else:
                            # Сохраняем напоминание
                            reminder_minutes = int(reminder_data)
                            if 'new_event' not in context.user_data:
                                context.user_data['new_event'] = {}
                            if 'reminders' not in context.user_data['new_event']:
                                context.user_data['new_event']['reminders'] = []
                            context.user_data['new_event']['reminders'] = [reminder_minutes]
                        
                        # После напоминания переходим к повторению (как в задачах после напоминания переходят к регулярности)
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
                        if hasattr(schedule_module, 'add_message_id'):
                            schedule_module.add_message_id(query.from_user.id, msg.message_id)
                        
                        return WAITING_REPEAT
                    
                    # Создаем обертку для add_event_repeat с измененным порядком
                    async def add_event_repeat_reordered(update: Update, context: ContextTypes.DEFAULT_TYPE):
                        """Обертка для add_event_repeat с измененным порядком (после повторения -> завершение)"""
                        # Устанавливаем режим расписания
                        context.user_data['bot_mode'] = MODE_SCHEDULE
                        
                        query = update.callback_query
                        await query.answer()
                        
                        repeat_type = query.data.replace('repeat_', '')
                        if 'new_event' not in context.user_data:
                            context.user_data['new_event'] = {}
                        context.user_data['new_event']['repeat_type'] = repeat_type
                        
                        # После повторения завершаем создание события (как в задачах после регулярности завершается создание)
                        # Используем функцию finish_event_creation из schedule_module
                        if hasattr(schedule_module, 'finish_event_creation'):
                            return await schedule_module.finish_event_creation(query, context)
                        else:
                            # Если функции нет, вызываем оригинальную add_event_repeat, которая должна завершить создание
                            return await schedule_module.add_event_repeat(update, context)
                    
                    # Добавляем WAITING_REMINDER_1 если есть
                    if WAITING_REMINDER_1 is not None:
                        states[WAITING_REMINDER_1] = [
                            CallbackQueryHandler(
                                wrap_schedule_handler(add_event_reminder_1_reordered),
                                pattern='^reminder_'
                            )
                        ]
                    
                    # ConversationHandler добавления событий из оригинального расписания
                    # Больше не вешаем его на кнопку ➕, так как используется единый сценарий unified_add
                    add_conv_handler = ConversationHandler(
                        entry_points=[],
                        states=states,
                        fallbacks=[
                            CommandHandler('cancel', wrap_schedule_handler(schedule_module.cancel))
                        ],
                        per_message=False,
                    )
                    application.add_handler(add_conv_handler)
                    logger.info("✅ ConversationHandler для добавления событий зарегистрирован (без привязки к кнопке ➕)")
                    
                    # ConversationHandler для редактирования события
                    WAITING_EDIT_CHOICE = schedule_module.WAITING_EDIT_CHOICE
                    WAITING_EDIT_VALUE = schedule_module.WAITING_EDIT_VALUE
                    
                    edit_conv_handler = ConversationHandler(
                        entry_points=[
                            CallbackQueryHandler(
                                create_schedule_wrapper(schedule_module.edit_event_start),
                                pattern='^edit_'
                            )
                        ],
                        states={
                            WAITING_EDIT_CHOICE: [
                                CallbackQueryHandler(
                                    wrap_schedule_handler(schedule_module.edit_field_choice),
                                    pattern='^edit_field_'
                                )
                            ],
                            WAITING_EDIT_VALUE: [
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_schedule_handler(schedule_module.edit_field_value)
                                ),
                                CallbackQueryHandler(
                                    wrap_schedule_handler(schedule_module.edit_category_callback),
                                    pattern='^cat_'
                                )
                            ],
                        },
                        fallbacks=[
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.back_to_list),
                                pattern='^back_to_list$'
                            )
                        ],
                        per_message=False,
                    )
                    application.add_handler(edit_conv_handler)
                    logger.info("✅ ConversationHandler для редактирования событий зарегистрирован")
                    
                    # ConversationHandler для управления категориями
                    WAITING_CATEGORY_NAME = schedule_module.WAITING_CATEGORY_NAME
                    WAITING_CATEGORY_EDIT_NAME = schedule_module.WAITING_CATEGORY_EDIT_NAME
                    WAITING_CATEGORY_DELETE_CONFIRM = schedule_module.WAITING_CATEGORY_DELETE_CONFIRM
                    
                    categories_conv_handler = ConversationHandler(
                        entry_points=[
                            CallbackQueryHandler(
                                create_schedule_wrapper(schedule_module.manage_categories),
                                pattern='^manage_categories$'
                            ),
                            CallbackQueryHandler(
                                create_schedule_wrapper(schedule_module.category_add_start),
                                pattern='^category_add$'
                            ),
                            CallbackQueryHandler(
                                create_schedule_wrapper(schedule_module.category_edit_list),
                                pattern='^category_edit_list$'
                            ),
                            CallbackQueryHandler(
                                create_schedule_wrapper(schedule_module.category_delete_list),
                                pattern='^category_delete_list$'
                            )
                        ],
                        states={
                            WAITING_CATEGORY_NAME: [
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_schedule_handler(schedule_module.category_add_name)
                                )
                            ],
                            WAITING_CATEGORY_EDIT_NAME: [
                                CallbackQueryHandler(
                                    wrap_schedule_handler(schedule_module.category_edit_selected),
                                    pattern='^category_edit_'
                                ),
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_schedule_handler(schedule_module.category_edit_name)
                                )
                            ],
                            WAITING_CATEGORY_DELETE_CONFIRM: [
                                CallbackQueryHandler(
                                    wrap_schedule_handler(schedule_module.category_delete_confirm),
                                    pattern='^category_delete_'
                                ),
                                CallbackQueryHandler(
                                    wrap_schedule_handler(schedule_module.category_delete_yes),
                                    pattern='^category_delete_yes_'
                                ),
                                CallbackQueryHandler(
                                    wrap_schedule_handler(schedule_module.category_delete_list),
                                    pattern='^category_delete_list$'
                                )
                            ],
                        },
                        fallbacks=[
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.manage_categories),
                                pattern='^manage_categories$'
                            ),
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.back_to_main),
                                pattern='^back_to_main$'
                            ),
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.categories_done),
                                pattern='^categories_done$'
                            ),
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.category_add_start),
                                pattern='^category_add$'
                            ),
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.category_edit_list),
                                pattern='^category_edit_list$'
                            ),
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.category_delete_list),
                                pattern='^category_delete_list$'
                            ),
                            CallbackQueryHandler(
                                wrap_schedule_handler(schedule_module.back_to_category_selection),
                                pattern='^back_to_category_selection$'
                            )
                        ],
                        per_message=False,
                    )
                    application.add_handler(categories_conv_handler)
                    print("✅ ConversationHandler для управления категориями зарегистрирован")
                    
                except Exception as e:
                    print(f"⚠️  Ошибка при создании ConversationHandler для расписания: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Регистрируем основные обработчики расписания
            # Команды (работают только в режиме расписания)
            if hasattr(schedule_module, 'help_command'):
                application.add_handler(CommandHandler(
                    'help',
                    create_schedule_wrapper(schedule_module.help_command)
                ))
            if hasattr(schedule_module, 'list_events'):
                application.add_handler(CommandHandler(
                    'list',
                    create_schedule_wrapper(schedule_module.list_events)
                ))
            if hasattr(schedule_module, 'today_events'):
                application.add_handler(CommandHandler(
                    'today',
                    create_schedule_wrapper(schedule_module.today_events)
                ))
            if hasattr(schedule_module, 'week_events'):
                application.add_handler(CommandHandler(
                    'week',
                    create_schedule_wrapper(schedule_module.week_events)
                ))
            if hasattr(schedule_module, 'clear_messages'):
                application.add_handler(CommandHandler(
                    'clear',
                    create_schedule_wrapper(schedule_module.clear_messages)
                ))
            
            # Кнопки клавиатуры
            if hasattr(schedule_module, 'tomorrow_events'):
                application.add_handler(MessageHandler(
                    filters.Regex('^что завтра\\?\s*$'),
                    create_schedule_wrapper(schedule_module.tomorrow_events)
                ))
            if hasattr(schedule_module, 'today_events'):
                application.add_handler(MessageHandler(
                    filters.Regex('^что сегодня\\?\s*$'),
                    create_schedule_wrapper(schedule_module.today_events)
                ))
            if hasattr(schedule_module, 'week_events'):
                application.add_handler(MessageHandler(
                    filters.Regex('^моё расписание\s*$'),
                    create_schedule_wrapper(schedule_module.week_events)
                ))
            if hasattr(schedule_module, 'edit_events_list'):
                application.add_handler(MessageHandler(
                    filters.Regex('^✏️\s*$'),
                    create_schedule_wrapper(schedule_module.edit_events_list)
                ))
            if hasattr(schedule_module, 'clear_messages'):
                application.add_handler(MessageHandler(
                    filters.Regex('^🙈\s*$'),
                    create_schedule_wrapper(schedule_module.clear_messages)
                ))
            
            # Callback handlers
            if hasattr(schedule_module, 'event_callback'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.event_callback),
                    pattern='^event_'
                ))
            if hasattr(schedule_module, 'delete_event'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.delete_event),
                    pattern='^delete_'
                ))
            if hasattr(schedule_module, 'confirm_delete_yes'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.confirm_delete_yes),
                    pattern='^confirm_delete_yes$'
                ))
            if hasattr(schedule_module, 'confirm_delete_no'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.confirm_delete_no),
                    pattern='^confirm_delete_no$'
                ))
            if hasattr(schedule_module, 'confirm_delete_start'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.confirm_delete_start),
                    pattern='^confirm_delete_start$'
                ))
            if hasattr(schedule_module, 'back_to_list'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.back_to_list),
                    pattern='^back_to_list$'
                ))
            if hasattr(schedule_module, 'back_to_main'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.back_to_main),
                    pattern='^back_to_main$'
                ))
            if hasattr(schedule_module, 'show_help'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.show_help),
                    pattern='^show_help$'
                ))
            if hasattr(schedule_module, 'clear_chat_callback'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.clear_chat_callback),
                    pattern='^clear_chat$'
                ))
            if hasattr(schedule_module, 'categories_done'):
                application.add_handler(CallbackQueryHandler(
                    create_schedule_wrapper(schedule_module.categories_done),
                    pattern='^categories_done$'
                ))
            
            logger.info("✅ Обработчики расписания зарегистрированы")
        except Exception as e:
            logger.error(f"Ошибка при регистрации обработчиков расписания: {e}", exc_info=True)
    
    # Регистрируем обработчики из бота задач (если модуль загружен)
    # ConversationHandler для задач тоже должен быть зарегистрирован рано
    if tasks_module:
        try:
            # Переопределяем get_main_keyboard в модуле задач глобально для unified_bot
            # Это гарантирует, что все функции завершения будут использовать правильную клавиатуру
            if hasattr(tasks_module, 'get_main_keyboard'):
                tasks_module.get_main_keyboard = get_tasks_keyboard
                logger.info("✅ get_main_keyboard переопределен для раздела задач")
            
            # Переопределяем rename_user_project, чтобы она также обновляла события в расписании
            if schedule_module:
                original_rename_project = tasks_module.rename_user_project
                def rename_user_project_unified(user_id: str, old_name: str, new_name: str):
                    """Переименование проекта с обновлением событий"""
                    try:
                        result = original_rename_project(user_id, old_name, new_name)
                        if result and schedule_module and hasattr(schedule_module, 'get_user_events'):
                            try:
                                events = schedule_module.get_user_events(str(user_id))
                                if isinstance(events, list):
                                    updated = False
                                    for event in events:
                                        if isinstance(event, dict) and event.get('category') == old_name:
                                            event['category'] = new_name
                                            updated = True
                                    
                                    if updated and hasattr(schedule_module, 'update_user_event'):
                                        # Сохраняем обновленные события через update_user_event
                                        for event in events:
                                            if isinstance(event, dict) and event.get('category') == new_name and 'id' in event:
                                                schedule_module.update_user_event(str(user_id), event['id'], event)
                            except Exception as e:
                                logger.error(f"Ошибка при обновлении событий при переименовании проекта: {e}", exc_info=True)
                        return result
                    except Exception as e:
                        logger.error(f"Ошибка при переименовании проекта '{old_name}' -> '{new_name}' для пользователя {user_id}: {e}", exc_info=True)
                        return False
                tasks_module.rename_user_project = rename_user_project_unified
                logger.info("✅ rename_user_project переопределена для обновления событий")
            
            # Используем обертки из модуля wrappers
            
            # ConversationHandler для добавления задачи
            if (hasattr(tasks_module, 'add_task_start') and 
                hasattr(tasks_module, 'WAITING_TASK_TITLE')):
                try:
                    WAITING_TASK_TITLE = tasks_module.WAITING_TASK_TITLE
                    WAITING_TASK_COMMENT = tasks_module.WAITING_TASK_COMMENT
                    WAITING_TASK_PROJECT = tasks_module.WAITING_TASK_PROJECT
                    WAITING_TASK_DEADLINE = tasks_module.WAITING_TASK_DEADLINE
                    WAITING_TASK_REMINDER = tasks_module.WAITING_TASK_REMINDER
                    WAITING_TASK_RECURRENCE = tasks_module.WAITING_TASK_RECURRENCE
                    # новое состояние для выбора категории
                    WAITING_TASK_CATEGORY = getattr(tasks_module, 'WAITING_TASK_CATEGORY', None)
                    
                    # Используем обертки из модуля wrappers
                    
                    add_task_conv_handler = ConversationHandler(
                        entry_points=[
                            MessageHandler(
                                filters.Regex('^➕\s*$') & ~filters.COMMAND,
                                create_tasks_entry_wrapper(tasks_module.add_task_start)
                            )
                        ],
                        states={
                            WAITING_TASK_TITLE: [
                                MessageHandler(
                                    (filters.TEXT | filters.VOICE) & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.add_task_title)
                                )
                            ],
                            WAITING_TASK_COMMENT: [
                                CommandHandler('skip', wrap_tasks_handler(tasks_module.add_task_comment)),
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.add_task_comment)
                                ),
                                MessageHandler(
                                    filters.VOICE,
                                    wrap_tasks_handler(tasks_module.add_task_comment)
                                )
                            ],
                            WAITING_TASK_PROJECT: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.add_task_project_callback),
                                    pattern='^project_|^new_project|^skip_project'
                                ),
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.add_task_project_text)
                                )
                            ],
                            WAITING_TASK_DEADLINE: [
                                MessageHandler(
                                    (filters.TEXT | filters.VOICE) & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.add_task_deadline)
                                ),
                                CommandHandler('skip', wrap_tasks_handler(tasks_module.add_task_deadline))
                            ],
                            WAITING_TASK_REMINDER: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.add_task_reminder_callback),
                                    pattern='^reminder_|^skip_reminder'
                                ),
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.add_task_reminder)
                                ),
                                CommandHandler('skip', wrap_tasks_handler(tasks_module.add_task_reminder))
                            ],
                            WAITING_TASK_RECURRENCE: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.add_task_recurrence_callback),
                                    pattern='^recurrence_'
                                )
                            ],
                            **(
                                {
                                    WAITING_TASK_CATEGORY: [
                                        CallbackQueryHandler(
                                            wrap_tasks_handler(tasks_module.add_task_category_callback),
                                            pattern='^task_category_'
                                        )
                                    ]
                                } if WAITING_TASK_CATEGORY is not None else {}
                            ),
                        },
                        fallbacks=[CommandHandler('cancel', wrap_tasks_handler(tasks_module.cancel))],
                        per_message=False,
                        per_chat=True,
                    )
                    application.add_handler(add_task_conv_handler)
                    logger.info("✅ ConversationHandler для добавления задач зарегистрирован")
                    
                    # ConversationHandler для добавления проекта
                    WAITING_PROJECT_NAME = tasks_module.WAITING_PROJECT_NAME
                    WAITING_PROJECT_TYPE = tasks_module.WAITING_PROJECT_TYPE
                    WAITING_PROJECT_TARGET_TASKS = tasks_module.WAITING_PROJECT_TARGET_TASKS
                    WAITING_PROJECT_PRIORITY = tasks_module.WAITING_PROJECT_PRIORITY
                    WAITING_PROJECT_END_DATE = tasks_module.WAITING_PROJECT_END_DATE
                    
                    # Используем обертки из модуля wrappers
                    
                    add_project_conv_handler = ConversationHandler(
                        entry_points=[
                            CallbackQueryHandler(
                                create_add_project_wrapper(tasks_module.add_project_start_callback),
                                pattern='^add_project$'
                            )
                        ],
                        states={
                            WAITING_PROJECT_NAME: [
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.add_project_name)
                                )
                            ],
                            WAITING_PROJECT_TYPE: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.add_project_type_callback),
                                    pattern='^project_type_'
                                )
                            ],
                            WAITING_PROJECT_TARGET_TASKS: [
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.add_project_target_tasks)
                                )
                            ],
                            WAITING_PROJECT_PRIORITY: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.add_project_priority_callback),
                                    pattern='^project_priority_'
                                )
                            ],
                            WAITING_PROJECT_END_DATE: [
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.add_project_end_date)
                                ),
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.add_project_end_date),
                                    pattern='^project_end_date_'
                                )
                            ],
                        },
                        fallbacks=[CommandHandler('cancel', wrap_tasks_handler(tasks_module.cancel))],
                        per_message=False,
                        per_chat=True,
                    )
                    application.add_handler(add_project_conv_handler)
                    logger.info("✅ ConversationHandler для добавления проектов зарегистрирован")
                    
                    # ConversationHandler для обработки выполнения задач
                    WAITING_TASK_COMPLETE_CONFIRM = tasks_module.WAITING_TASK_COMPLETE_CONFIRM
                    WAITING_TASK_RESCHEDULE = tasks_module.WAITING_TASK_RESCHEDULE
                    
                    task_complete_conv_handler = ConversationHandler(
                        entry_points=[
                            CallbackQueryHandler(
                                create_tasks_wrapper(tasks_module.task_complete_callback),
                                pattern='^task_complete_'
                            )
                        ],
                        states={
                            WAITING_TASK_COMPLETE_CONFIRM: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.task_confirm_callback),
                                    pattern='^task_confirm_'
                                )
                            ],
                            WAITING_TASK_RESCHEDULE: [
                                MessageHandler(
                                    (filters.TEXT | filters.VOICE) & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.task_reschedule)
                                )
                            ],
                        },
                        fallbacks=[CommandHandler('cancel', wrap_tasks_handler(tasks_module.cancel))],
                        per_message=False,
                        per_chat=True,
                        per_user=True,
                    )
                    application.add_handler(task_complete_conv_handler)
                    logger.info("✅ ConversationHandler для выполнения задач зарегистрирован")
                    
                    # ConversationHandler для редактирования задач
                    WAITING_EDIT_TASK_SELECT = tasks_module.WAITING_EDIT_TASK_SELECT
                    WAITING_EDIT_FIELD_SELECT = tasks_module.WAITING_EDIT_FIELD_SELECT
                    WAITING_EDIT_TITLE = tasks_module.WAITING_EDIT_TITLE
                    WAITING_EDIT_COMMENT = tasks_module.WAITING_EDIT_COMMENT
                    WAITING_EDIT_PROJECT = tasks_module.WAITING_EDIT_PROJECT
                    WAITING_EDIT_DEADLINE = tasks_module.WAITING_EDIT_DEADLINE
                    WAITING_EDIT_REMINDER = tasks_module.WAITING_EDIT_REMINDER
                    WAITING_EDIT_RECURRENCE = tasks_module.WAITING_EDIT_RECURRENCE
                    
                    edit_task_conv_handler = ConversationHandler(
                        entry_points=[
                            MessageHandler(
                                filters.Regex('^✏️\s*$'),
                                create_tasks_entry_wrapper(tasks_module.edit_task_start)
                            )
                        ],
                        states={
                            WAITING_EDIT_TASK_SELECT: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.edit_task_select_callback),
                                    pattern='^edit_task_'
                                )
                            ],
                            WAITING_EDIT_FIELD_SELECT: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.edit_field_select_callback),
                                    pattern='^edit_field_|^edit_cancel$'
                                )
                            ],
                            WAITING_EDIT_TITLE: [
                                MessageHandler(
                                    (filters.TEXT | filters.VOICE) & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.edit_task_title)
                                )
                            ],
                            WAITING_EDIT_COMMENT: [
                                MessageHandler(
                                    (filters.TEXT | filters.VOICE) & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.edit_task_comment)
                                ),
                                CommandHandler('skip', wrap_tasks_handler(tasks_module.edit_task_comment))
                            ],
                            WAITING_EDIT_PROJECT: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.edit_task_project_callback),
                                    pattern='^edit_project_task_|^edit_cancel$'
                                )
                            ],
                            WAITING_EDIT_DEADLINE: [
                                MessageHandler(
                                    (filters.TEXT | filters.VOICE) & ~filters.COMMAND,
                                    wrap_tasks_handler(tasks_module.edit_task_deadline)
                                ),
                                CommandHandler('skip', wrap_tasks_handler(tasks_module.edit_task_deadline))
                            ],
                            WAITING_EDIT_REMINDER: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.edit_task_reminder_callback),
                                    pattern='^edit_reminder_|^edit_cancel$'
                                )
                            ],
                            WAITING_EDIT_RECURRENCE: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.edit_task_recurrence_callback),
                                    pattern='^edit_recurrence_|^edit_cancel$'
                                )
                            ],
                        },
                        fallbacks=[CommandHandler('cancel', wrap_tasks_handler(tasks_module.cancel))],
                        per_message=False,
                        per_chat=True,
                        per_user=True,
                    )
                    application.add_handler(edit_task_conv_handler)
                    logger.info("✅ ConversationHandler для редактирования задач зарегистрирован")
                    
                    # ConversationHandler для редактирования проекта
                    WAITING_EDIT_PROJECT_TARGET_TASKS = tasks_module.WAITING_EDIT_PROJECT_TARGET_TASKS
                    WAITING_EDIT_PROJECT_NAME = tasks_module.WAITING_EDIT_PROJECT_NAME
                    
                    # Используем обертки из модуля wrappers
                    
                    project_edit_conv_handler = ConversationHandler(
                        entry_points=[
                            CallbackQueryHandler(
                                create_edit_project_wrapper(tasks_module.edit_project_name_start),
                                pattern='^edit_project_name_'
                            ),
                            CallbackQueryHandler(
                                create_edit_project_wrapper(tasks_module.edit_project_start),
                                pattern='^edit_project_(?!name_|task_)'  # Не начинается с 'name_' или 'task_'
                            )
                        ],
                        states={
                            WAITING_EDIT_PROJECT_TARGET_TASKS: [
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND & ~filters.Regex('^Статистика$|^Проекты$|^➕\s*$|^✏️\s*$|^🏠 Главное меню$'),
                                    wrap_tasks_handler(tasks_module.edit_project_target_tasks)
                                )
                            ],
                            WAITING_EDIT_PROJECT_NAME: [
                                MessageHandler(
                                    filters.TEXT & ~filters.COMMAND & ~filters.Regex('^Статистика$|^Проекты$|^➕\s*$|^✏️\s*$|^🏠 Главное меню$'),
                                    wrap_tasks_handler(tasks_module.edit_project_name)
                                )
                            ],
                        },
                        fallbacks=[
                            CommandHandler('cancel', wrap_tasks_handler(tasks_module.cancel)),
                            MessageHandler(
                                filters.Regex('^Статистика$|^Проекты$|^➕\s*$|^✏️\s*$|^🏠 Главное меню$'),
                                end_conversation_handler
                            )
                        ],
                        per_message=False,
                        per_chat=True,
                        per_user=True,
                    )
                    application.add_handler(project_edit_conv_handler)
                    logger.info("✅ ConversationHandler для редактирования проектов зарегистрирован")
                    
                    # ConversationHandler для подтверждения готовности проекта
                    WAITING_PROJECT_COMPLETE_CONFIRM = tasks_module.WAITING_PROJECT_COMPLETE_CONFIRM
                    
                    project_complete_conv_handler = ConversationHandler(
                        entry_points=[
                            CallbackQueryHandler(
                                create_tasks_wrapper(tasks_module.project_complete_start),
                                pattern='^project_complete_'
                            )
                        ],
                        states={
                            WAITING_PROJECT_COMPLETE_CONFIRM: [
                                CallbackQueryHandler(
                                    wrap_tasks_handler(tasks_module.project_complete_confirm),
                                    pattern='^project_complete_yes$|^project_complete_no$'
                                )
                            ],
                        },
                        fallbacks=[
                            CommandHandler('cancel', wrap_tasks_handler(tasks_module.cancel)),
                            MessageHandler(
                                filters.Regex('^Статистика$|^Проекты$|^➕\s*$|^✏️\s*$|^🏠 Главное меню$'),
                                end_conversation_handler
                            )
                        ],
                        per_message=False,
                        per_chat=True,
                        per_user=True,
                    )
                    application.add_handler(project_complete_conv_handler)
                    logger.info("✅ ConversationHandler для подтверждения готовности проекта зарегистрирован")
                    
                except Exception as e:
                    print(f"⚠️  Ошибка при создании ConversationHandler для задач: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Регистрируем основные обработчики задач
            # Команды (работают только в режиме задач)
            if hasattr(tasks_module, 'help_command'):
                application.add_handler(CommandHandler(
                    'help',
                    create_tasks_wrapper(tasks_module.help_command)
                ))
            if hasattr(tasks_module, 'list_tasks'):
                application.add_handler(CommandHandler(
                    'list',
                    create_tasks_wrapper(tasks_module.list_tasks)
                ))
            if hasattr(tasks_module, 'projects_list'):
                application.add_handler(CommandHandler(
                    'projects',
                    create_tasks_wrapper(tasks_module.projects_list)
                ))
            if hasattr(tasks_module, 'stats_menu'):
                application.add_handler(CommandHandler(
                    'stats',
                    create_tasks_wrapper(tasks_module.stats_menu)
                ))
            
            # Callback handlers для задач
            if hasattr(tasks_module, 'schedule_callback'):
                application.add_handler(CallbackQueryHandler(
                    create_tasks_wrapper(tasks_module.schedule_callback),
                    pattern='^schedule_'
                ))
            if hasattr(tasks_module, 'project_info_callback'):
                # Обработчик для проектов - работает из любого режима (включая главное меню)
                async def project_info_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
                    # Временно устанавливаем режим задач для корректной работы функции
                    old_mode = context.user_data.get('bot_mode', MODE_MAIN)
                    context.user_data['bot_mode'] = MODE_TASKS
                    try:
                        result = await tasks_module.project_info_callback(update, context)
                        return result
                    finally:
                        # Возвращаем режим обратно только если ConversationHandler не активен
                        if not context.user_data.get('_conversation_active'):
                            context.user_data['bot_mode'] = old_mode
                
                application.add_handler(CallbackQueryHandler(
                    project_info_wrapper,
                    pattern='^project_info_|^projects_list$|^projects_summary$|^project_tasks_|^edit_projects_list$|^add_project$'
                ))
            if hasattr(tasks_module, 'projects_list_callback'):
                # Обработчик для списка проектов - работает из любого режима
                async def projects_list_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
                    old_mode = context.user_data.get('bot_mode', MODE_MAIN)
                    context.user_data['bot_mode'] = MODE_TASKS
                    try:
                        return await tasks_module.projects_list_callback(update, context)
                    finally:
                        context.user_data['bot_mode'] = old_mode
                
                application.add_handler(CallbackQueryHandler(
                    projects_list_wrapper,
                    pattern='^projects_list_callback$'
                ))
            
            logger.info("✅ Обработчики задач зарегистрированы")
        except Exception as e:
            logger.error(f"Ошибка при регистрации обработчиков задач: {e}", exc_info=True)
    
    # Функция для показа статистики из главного меню
    async def show_statistics_from_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать статистику из главного меню"""
        try:
            if not update.message:
                logger.warning("show_statistics_from_main вызван без сообщения")
                return
            
            tasks_module = context.application.bot_data.get('tasks_module')
            if tasks_module and hasattr(tasks_module, 'show_projects_summary_from_menu'):
                # Временно устанавливаем режим задач для корректной работы функции
                old_mode = context.user_data.get('bot_mode', MODE_MAIN)
                context.user_data['bot_mode'] = MODE_TASKS
                try:
                    await tasks_module.show_projects_summary_from_menu(update, context)
                except Exception as e:
                    logger.error(f"Ошибка при показе статистики: {e}", exc_info=True)
                    await update.message.reply_text(
                        "❌ Ошибка при загрузке статистики",
                        reply_markup=get_unified_main_keyboard()
                    )
                finally:
                    # Возвращаем режим обратно
                    context.user_data['bot_mode'] = old_mode
            else:
                await update.message.reply_text(
                    "❌ Статистика недоступна",
                    reply_markup=get_unified_main_keyboard()
                )
        except Exception as e:
            logger.error(f"Ошибка в show_statistics_from_main: {e}", exc_info=True)
            try:
                if update.message:
                    await update.message.reply_text(
                        "❌ Ошибка при обработке запроса",
                        reply_markup=get_unified_main_keyboard()
                    )
            except Exception:
                pass
    
    # Главное меню - обработчики
    application.add_handler(CommandHandler('start', unified_start))
    application.add_handler(MessageHandler(filters.Regex('^Проекты$'), show_projects))  # Общий обработчик для всех режимов
    application.add_handler(MessageHandler(filters.Regex('^Статистика$'), show_statistics_from_main))  # Статистика из главного меню
    
    # Специальная команда для очистки истории переписки и данных по пользователю
    async def clear_user_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Очищает все данные пользователя (задачи, события, проекты, кеш сообщений) по триггеру 👨🏿‍🔬"""
        user_id = str(update.effective_user.id) if update.effective_user else None
        if not user_id:
            return
        
        # Очищаем данные задач/проектов в tasks_data.json
        try:
            tasks_path = str(BASE_DIR / 'tasks_data.json')
            if os.path.exists(tasks_path):
                with open(tasks_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                users = data.get('users', {})
                if user_id in users:
                    users[user_id]['tasks'] = []
                    users[user_id]['projects'] = []
                    users[user_id]['tags'] = []
                    users[user_id]['projects_data'] = {}
                data['users'] = users
                with open(tasks_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка очистки tasks_data.json для пользователя {user_id}: {e}", exc_info=True)
        
        # Очищаем события в schedule_data.json
        try:
            schedule_path = str(BASE_DIR / 'schedule_data.json')
            if os.path.exists(schedule_path):
                with open(schedule_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if user_id in data:
                    data[user_id] = []
                with open(schedule_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка очистки schedule_data.json для пользователя {user_id}: {e}", exc_info=True)
        
        # Очищаем shared_projects.json (совместные проекты)
        try:
            shared_path = str(BASE_DIR / 'shared_projects.json')
            if os.path.exists(shared_path):
                with open(shared_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if user_id in data:
                    data[user_id] = {}
                with open(shared_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка очистки shared_projects.json для пользователя {user_id}: {e}", exc_info=True)
        
        # Очищаем вспомогательные файлы с сообщениями (если есть)
        try:
            for path in [
                str(BASE_DIR / 'user_messages.json'),
                str(BASE_DIR / 'user_sent_messages.json')
            ]:
                if os.path.exists(path):
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        if isinstance(data, dict) and user_id in data:
                            data.pop(user_id, None)
                            with open(path, 'w', encoding='utf-8') as f:
                                json.dump(data, f, ensure_ascii=False, indent=2)
                    except Exception:
                        # В крайнем случае просто очищаем файл
                        with open(path, 'w', encoding='utf-8') as f:
                            f.write('{}')
        except Exception as e:
            logger.error(f"Ошибка очистки файлов сообщений для пользователя {user_id}: {e}", exc_info=True)
        
        # Сообщаем пользователю
        await update.message.reply_text(
            "🧼 Вся история дел (задачи, события, проекты пользователя) и кеш переписки на стороне бота очищены.",
            reply_markup=get_main_keyboard()
        )
    
    # Триггер по эмодзи 👨🏿‍🔬 для полной очистки истории
    application.add_handler(
        MessageHandler(filters.Regex('^👨🏿‍🔬$') & ~filters.COMMAND, clear_user_history)
    )
    application.add_handler(MessageHandler(filters.Regex('^📋 План$'), switch_to_plan))
    application.add_handler(MessageHandler(filters.Regex('^🏠 Главное меню$'), back_to_main_menu))
    
    # Обработчики планов
    application.add_handler(MessageHandler(filters.Regex('^📅 План на сегодня$'), show_plan_today))
    application.add_handler(MessageHandler(filters.Regex('^📅 План на завтра$'), show_plan_tomorrow))
    application.add_handler(MessageHandler(filters.Regex('^📅 План на неделю$'), show_plan_week))
    application.add_handler(MessageHandler(filters.Regex('^📅 План на месяц$'), show_plan_month))
    application.add_handler(MessageHandler(filters.Regex('^📅 План на год$'), show_plan_year))
    application.add_handler(MessageHandler(filters.Regex('^📅 План на 3 года$'), show_plan_3years))
    application.add_handler(MessageHandler(filters.Regex('^✅ Управление задачами$'), show_tasks_management_from_plan))
    
    # Обработчики для управления задачами из плана
    async def plan_task_complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нажатия на задачу из плана"""
        try:
            query = update.callback_query
            if not query:
                logger.warning("plan_task_complete_callback вызван без callback_query")
                return
            
            await query.answer()
            
            # Извлекаем task_id из callback_data
            task_id = query.data.replace("plan_task_complete_", "")
            
            tasks_module = context.application.bot_data.get('tasks_module')
            if not tasks_module:
                await query.edit_message_text("❌ Модуль задач недоступен")
                return
            
            user_id = query.from_user.id
            
            # Получаем задачу
            task = None
            if hasattr(tasks_module, 'get_user_task_by_id'):
                try:
                    task = tasks_module.get_user_task_by_id(str(user_id), task_id)
                except Exception as e:
                    logger.error(f"Ошибка при получении задачи по ID: {e}", exc_info=True)
            
            if not task and hasattr(tasks_module, 'get_user_tasks'):
                try:
                    tasks = tasks_module.get_user_tasks(str(user_id))
                    if isinstance(tasks, list):
                        task = next((t for t in tasks if isinstance(t, dict) and t.get('id') == task_id), None)
                except Exception as e:
                    logger.error(f"Ошибка при получении списка задач: {e}", exc_info=True)
            
            if not task:
                await query.answer("Задача не найдена", show_alert=True)
                return
            
            if not isinstance(task, dict):
                logger.error(f"Задача не является словарем: {type(task)}")
                await query.answer("Ошибка: неверный формат задачи", show_alert=True)
                return
            
            completed = task.get('completed', False)
            task_title = task.get('title', 'Без названия')
            deadline = task.get('deadline', '')
            deadline_str = ''
            if deadline:
                try:
                    if 'T' in deadline:
                        deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                        deadline_str = deadline_dt.strftime('%d.%m %H:%M')
                    else:
                        deadline_dt = datetime.strptime(deadline, '%Y-%m-%d')
                        deadline_str = deadline_dt.strftime('%d.%m')
                except Exception:
                    pass
            
            # Меню задачи: Выполнить / Редактировать / Назад
            if completed:
                keyboard = [
                    [InlineKeyboardButton("↩️ Отметить как невыполненную", callback_data=f"plan_task_uncomplete_{task_id}")],
                    [InlineKeyboardButton("✏️ Редактировать", callback_data=f"plan_task_edit_{task_id}")],
                    [InlineKeyboardButton("◀️ Назад к списку", callback_data="plan_back_to_tasks")]
                ]
            else:
                keyboard = [
                    [InlineKeyboardButton("✅ Выполнить", callback_data=f"plan_task_do_complete_{task_id}")],
                    [InlineKeyboardButton("✏️ Редактировать", callback_data=f"plan_task_edit_{task_id}")],
                    [InlineKeyboardButton("◀️ Назад к списку", callback_data="plan_back_to_tasks")]
                ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            task_info = f"<b>{task_title}</b>"
            if deadline_str:
                task_info += f" ({deadline_str})"
            await query.edit_message_text(
                f"Задача: {task_info}\n\nЧто сделать?",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Ошибка в plan_task_complete_callback: {e}", exc_info=True)
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Произошла ошибка", show_alert=True)
            except Exception:
                pass
    
    async def plan_task_do_complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Кнопка «Выполнить» — показать подтверждение"""
        try:
            query = update.callback_query
            if not query:
                return
            await query.answer()
            task_id = query.data.replace("plan_task_do_complete_", "")
            user_id = query.from_user.id
            tasks_module = context.application.bot_data.get('tasks_module')
            if not tasks_module:
                await query.edit_message_text("❌ Модуль задач недоступен")
                return
            task = None
            if hasattr(tasks_module, 'get_user_task_by_id'):
                try:
                    task = tasks_module.get_user_task_by_id(str(user_id), task_id)
                except Exception:
                    pass
            if not task and hasattr(tasks_module, 'get_user_tasks'):
                tasks = tasks_module.get_user_tasks(str(user_id))
                if isinstance(tasks, list):
                    task = next((t for t in tasks if isinstance(t, dict) and t.get('id') == task_id), None)
            if not task:
                await query.answer("Задача не найдена", show_alert=True)
                return
            context.user_data['from_plan'] = True
            context.user_data['task_id'] = task_id
            context.user_data['task_title'] = task.get('title', 'Без названия')
            keyboard = [
                [InlineKeyboardButton("Да", callback_data="plan_task_confirm_yes")],
                [InlineKeyboardButton("Нет", callback_data="plan_task_confirm_no")]
            ]
            await query.edit_message_text(
                f"Задача: <b>{context.user_data['task_title']}</b>\n\nГотово?",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Ошибка в plan_task_do_complete_callback: {e}", exc_info=True)
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Ошибка", show_alert=True)
            except Exception:
                pass
    
    async def plan_task_confirm_yes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Подтверждение выполнения задачи из плана"""
        try:
            query = update.callback_query
            if not query:
                logger.warning("plan_task_confirm_yes_callback вызван без callback_query")
                return
            
            await query.answer()
            
            task_id = context.user_data.get('task_id')
            task_title = context.user_data.get('task_title', '')
            user_id = query.from_user.id
            
            if not task_id:
                logger.warning(f"task_id не найден в user_data для пользователя {user_id}")
                await query.edit_message_text("❌ Ошибка: ID задачи не найден")
                return
            
            tasks_module = context.application.bot_data.get('tasks_module')
            if not tasks_module:
                await query.edit_message_text("❌ Модуль задач недоступен")
                return
            
            # Помечаем задачу как выполненную
            if hasattr(tasks_module, 'update_user_task'):
                try:
                    tasks_module.update_user_task(str(user_id), task_id, {'completed': True})
                except Exception as e:
                    logger.error(f"Ошибка при обновлении задачи: {e}", exc_info=True)
                    await query.edit_message_text("❌ Ошибка при обновлении задачи")
                    return
            
            await query.edit_message_text(
                f"✅ Задача <b>{task_title}</b> выполнена!",
                parse_mode='HTML'
            )
            
            # Обновляем список задач
            await show_tasks_management_from_plan_callback(query, context)
        except Exception as e:
            logger.error(f"Ошибка в plan_task_confirm_yes_callback: {e}", exc_info=True)
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Произошла ошибка", show_alert=True)
            except Exception:
                pass
    
    async def plan_task_confirm_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена выполнения задачи из плана"""
        try:
            query = update.callback_query
            if not query:
                logger.warning("plan_task_confirm_no_callback вызван без callback_query")
                return
            
            await query.answer()
            
            # Возвращаемся к списку задач
            await show_tasks_management_from_plan_callback(query, context)
        except Exception as e:
            logger.error(f"Ошибка в plan_task_confirm_no_callback: {e}", exc_info=True)
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Произошла ошибка", show_alert=True)
            except Exception:
                pass
    
    async def plan_task_uncomplete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отметка задачи как невыполненной из плана"""
        try:
            query = update.callback_query
            if not query:
                logger.warning("plan_task_uncomplete_callback вызван без callback_query")
                return
            
            await query.answer()
            
            task_id = query.data.replace("plan_task_uncomplete_", "")
            user_id = query.from_user.id
            
            tasks_module = context.application.bot_data.get('tasks_module')
            if not tasks_module:
                await query.edit_message_text("❌ Модуль задач недоступен")
                return
            
            # Получаем задачу для получения названия
            task = None
            if hasattr(tasks_module, 'get_user_task_by_id'):
                try:
                    task = tasks_module.get_user_task_by_id(str(user_id), task_id)
                except Exception as e:
                    logger.error(f"Ошибка при получении задачи по ID: {e}", exc_info=True)
            
            if not task and hasattr(tasks_module, 'get_user_tasks'):
                try:
                    tasks = tasks_module.get_user_tasks(str(user_id))
                    if isinstance(tasks, list):
                        task = next((t for t in tasks if isinstance(t, dict) and t.get('id') == task_id), None)
                except Exception as e:
                    logger.error(f"Ошибка при получении списка задач: {e}", exc_info=True)
            
            task_title = task.get('title', 'Без названия') if isinstance(task, dict) else 'Задача'
            
            # Помечаем задачу как невыполненную
            if hasattr(tasks_module, 'update_user_task'):
                try:
                    tasks_module.update_user_task(str(user_id), task_id, {'completed': False})
                except Exception as e:
                    logger.error(f"Ошибка при обновлении задачи: {e}", exc_info=True)
                    await query.edit_message_text("❌ Ошибка при обновлении задачи")
                    return
            
            await query.edit_message_text(
                f"↩️ Задача <b>{task_title}</b> отмечена как невыполненная",
                parse_mode='HTML'
            )
            
            # Обновляем список задач
            await show_tasks_management_from_plan_callback(query, context)
        except Exception as e:
            logger.error(f"Ошибка в plan_task_uncomplete_callback: {e}", exc_info=True)
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Произошла ошибка", show_alert=True)
            except Exception:
                pass
    
    async def plan_task_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню редактирования задачи: название или время"""
        try:
            query = update.callback_query
            if not query:
                return
            await query.answer()
            task_id = query.data.replace("plan_task_edit_", "")
            user_id = query.from_user.id
            tasks_module = context.application.bot_data.get('tasks_module')
            if not tasks_module:
                await query.edit_message_text("❌ Модуль задач недоступен")
                return
            task = None
            if hasattr(tasks_module, 'get_user_task_by_id'):
                try:
                    task = tasks_module.get_user_task_by_id(str(user_id), task_id)
                except Exception:
                    pass
            if not task and hasattr(tasks_module, 'get_user_tasks'):
                tasks = tasks_module.get_user_tasks(str(user_id))
                if isinstance(tasks, list):
                    task = next((t for t in tasks if isinstance(t, dict) and t.get('id') == task_id), None)
            if not task:
                await query.answer("Задача не найдена", show_alert=True)
                return
            context.user_data['plan_edit_task_id'] = task_id
            title = task.get('title', 'Без названия')
            keyboard = [
                [InlineKeyboardButton("📝 Изменить название", callback_data=f"plan_edit_title_{task_id}")],
                [InlineKeyboardButton("📅 Перенести время", callback_data=f"plan_edit_deadline_{task_id}")],
                [InlineKeyboardButton("◀️ Назад к списку", callback_data="plan_edit_back")]
            ]
            await query.edit_message_text(
                f"Редактирование: <b>{title}</b>\n\nЧто изменить?",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Ошибка в plan_task_edit_callback: {e}", exc_info=True)
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Ошибка", show_alert=True)
            except Exception:
                pass
    
    async def plan_edit_title_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Запрос нового названия задачи"""
        try:
            query = update.callback_query
            if not query:
                return
            await query.answer()
            task_id = query.data.replace("plan_edit_title_", "")
            context.user_data['plan_edit_task_id'] = task_id
            context.user_data['plan_waiting'] = 'plan_edit_title'
            await query.edit_message_text("Введите новое название задачи:")
        except Exception as e:
            logger.error(f"Ошибка в plan_edit_title_prompt_callback: {e}", exc_info=True)
    
    async def plan_edit_deadline_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Запрос новой даты/времени"""
        try:
            query = update.callback_query
            if not query:
                return
            await query.answer()
            task_id = query.data.replace("plan_edit_deadline_", "")
            context.user_data['plan_edit_task_id'] = task_id
            context.user_data['plan_waiting'] = 'plan_edit_deadline'
            await query.edit_message_text(
                "Введите новую дату или время.\n\n"
                "Например: завтра 18:00, 15.02.2026, послезавтра утра, через 2 дня"
            )
        except Exception as e:
            logger.error(f"Ошибка в plan_edit_deadline_prompt_callback: {e}", exc_info=True)
    
    async def plan_edit_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Назад из меню редактирования к списку задач"""
        try:
            query = update.callback_query
            if not query:
                return
            await query.answer()
            context.user_data.pop('plan_edit_task_id', None)
            context.user_data.pop('plan_waiting', None)
            await show_tasks_management_from_plan_callback(query, context)
        except Exception as e:
            logger.error(f"Ошибка в plan_edit_back_callback: {e}", exc_info=True)
    
    async def plan_edit_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода нового названия или даты при редактировании задачи из плана"""
        if not update.message or not update.message.text:
            return
        waiting = context.user_data.get('plan_waiting')
        if waiting not in ('plan_edit_title', 'plan_edit_deadline'):
            return
        task_id = context.user_data.get('plan_edit_task_id')
        if not task_id:
            context.user_data.pop('plan_waiting', None)
            return
        user_id = update.effective_user.id
        tasks_module = context.application.bot_data.get('tasks_module')
        if not tasks_module or not hasattr(tasks_module, 'update_user_task'):
            await update.message.reply_text("❌ Модуль задач недоступен")
            context.user_data.pop('plan_waiting', None)
            context.user_data.pop('plan_edit_task_id', None)
            return
        text = update.message.text.strip()
        if not text:
            await update.message.reply_text("Введите непустой текст.")
            return
        try:
            if waiting == 'plan_edit_title':
                tasks_module.update_user_task(str(user_id), task_id, {'title': text})
                await update.message.reply_text(f"✅ Название изменено на: <b>{text}</b>", parse_mode='HTML')
            else:
                if hasattr(tasks_module, 'parse_deadline'):
                    deadline_dt = tasks_module.parse_deadline(text, None)
                else:
                    deadline_dt = None
                if deadline_dt is None:
                    await update.message.reply_text(
                        "Не удалось распознать дату/время. Попробуйте: завтра 18:00, 15.02.2026, послезавтра"
                    )
                    return
                tasks_module.update_user_task(str(user_id), task_id, {'deadline': deadline_dt.isoformat()})
                if hasattr(tasks_module, 'format_deadline_readable'):
                    formatted = tasks_module.format_deadline_readable(deadline_dt)
                else:
                    formatted = deadline_dt.strftime('%d.%m.%Y %H:%M')
                await update.message.reply_text(f"✅ Время изменено на: <b>{formatted}</b>", parse_mode='HTML')
        except Exception as e:
            logger.error(f"Ошибка при сохранении редактирования задачи: {e}", exc_info=True)
            await update.message.reply_text("❌ Ошибка при сохранении.")
        context.user_data.pop('plan_waiting', None)
        context.user_data.pop('plan_edit_task_id', None)
        # Отправляем обновлённый список задач
        try:
            tasks = tasks_module.get_user_tasks(str(user_id)) if hasattr(tasks_module, 'get_user_tasks') else []
            if tasks:
                msg_text = "<b>✅ Управление задачами</b>\n\nНажмите на задачу:\n\n"
                keyboard = []
                incomplete = [t for t in tasks if not t.get('completed', False)]
                for i, task in enumerate(incomplete[:50], 1):
                    title = task.get('title', 'Без названия')
                    tid = task.get('id', '')
                    deadline = task.get('deadline', '')
                    project = task.get('project', '')
                    btn = f"{i}. {title}"
                    if deadline:
                        try:
                            if 'T' in deadline:
                                dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                                btn += f" ({dt.strftime('%d.%m %H:%M')})"
                            else:
                                dt = datetime.strptime(deadline, '%Y-%m-%d')
                                btn += f" ({dt.strftime('%d.%m')})"
                        except Exception:
                            pass
                    if project:
                        btn += f" [{project}]"
                    if len(btn) > 60:
                        btn = btn[:57] + "..."
                    keyboard.append([InlineKeyboardButton(btn, callback_data=f"plan_task_complete_{tid}")])
                keyboard.append([InlineKeyboardButton("◀️ Назад к плану", callback_data="plan_back_to_plan")])
                await update.message.reply_text(
                    msg_text,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception as e:
            logger.debug(f"Не удалось отправить список задач после редактирования: {e}")
    
    async def plan_back_to_tasks_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Возврат к списку задач из плана"""
        try:
            query = update.callback_query
            if not query:
                logger.warning("plan_back_to_tasks_callback вызван без callback_query")
                return
            
            await query.answer()
            
            await show_tasks_management_from_plan_callback(query, context)
        except Exception as e:
            logger.error(f"Ошибка в plan_back_to_tasks_callback: {e}", exc_info=True)
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Произошла ошибка", show_alert=True)
            except Exception:
                pass
    
    async def show_tasks_management_from_plan_callback(query, context):
        """Показать список задач для управления (для callback)"""
        try:
            if not query:
                logger.warning("show_tasks_management_from_plan_callback вызван без query")
                return
            
            user_id = query.from_user.id
            tasks_module = context.application.bot_data.get('tasks_module')
            
            if not tasks_module:
                await query.edit_message_text("❌ Модуль задач недоступен")
                return
            
            # Получаем все задачи пользователя
            tasks = []
            if hasattr(tasks_module, 'get_user_tasks'):
                try:
                    tasks = tasks_module.get_user_tasks(str(user_id))
                    if not isinstance(tasks, list):
                        logger.warning(f"get_user_tasks вернул не список: {type(tasks)}")
                        tasks = []
                except Exception as e:
                    logger.error(f"Ошибка при получении задач: {e}", exc_info=True)
                    await query.edit_message_text("❌ Ошибка при получении задач")
                    return
            else:
                await query.edit_message_text("❌ Не удалось получить задачи")
                return
            
            if not tasks:
                await query.edit_message_text(
                    "📝 У вас пока нет задач.\n\nДобавьте задачи в разделе «Задачи»."
                )
                return
            
            # Формируем список задач с кнопками для отметки
            text = "<b>✅ Управление задачами</b>\n\n"
            text += "Нажмите на задачу: выполнить, редактировать (название или время) или вернуться.\n\n"
            
            keyboard = []
            
            # Показываем только невыполненные задачи
            incomplete_tasks = [t for t in tasks if not t.get('completed', False)]
            
            for i, task in enumerate(incomplete_tasks[:50], 1):  # Ограничиваем до 50 задач
                if not isinstance(task, dict):
                    continue
                
                title = task.get('title', 'Без названия')
                task_id = task.get('id', '')
                deadline = task.get('deadline', '')
                project = task.get('project', '')
                
                if not task_id:
                    logger.warning(f"Задача без ID пропущена: {title}")
                    continue
                
                # Формируем текст кнопки
                button_text = f"{i}. {title}"
                if deadline:
                    try:
                        from datetime import datetime
                        if 'T' in deadline:
                            deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                            deadline_str = deadline_dt.strftime('%d.%m %H:%M')
                        else:
                            deadline_dt = datetime.strptime(deadline, '%Y-%m-%d')
                            deadline_str = deadline_dt.strftime('%d.%m')
                        button_text += f" ({deadline_str})"
                    except (ValueError, AttributeError):
                        pass
                
                if project:
                    button_text += f" [{project}]"
                
                # Обрезаем текст кнопки, если слишком длинный
                if len(button_text) > 60:
                    button_text = button_text[:57] + "..."
                
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"plan_task_complete_{task_id}")])
            
            keyboard.append([InlineKeyboardButton("◀️ Назад к плану", callback_data="plan_back_to_plan")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"Ошибка при показе управления задачами: {e}", exc_info=True)
            try:
                await query.edit_message_text("❌ Произошла ошибка при загрузке задач")
            except Exception:
                pass
    
    async def plan_back_to_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Возврат к плану из управления задачами"""
        try:
            query = update.callback_query
            if not query:
                logger.warning("plan_back_to_plan_callback вызван без callback_query")
                return
            
            await query.answer()
            
            user_id = query.from_user.id
            schedule_module = context.application.bot_data.get('schedule_module')
            tasks_module = context.application.bot_data.get('tasks_module')
            
            # Показываем план на сегодня
            events, tasks = get_combined_plan(user_id, schedule_module, tasks_module, days=1)
            text = format_combined_plan_text(events, tasks, "сегодня")
            
            await query.edit_message_text(
                text,
                parse_mode='HTML',
                reply_markup=None
            )
            
            # Отправляем новое сообщение с клавиатурой плана
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Выберите период для просмотра плана:",
                reply_markup=get_plan_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка в plan_back_to_plan_callback: {e}", exc_info=True)
            try:
                if update.callback_query:
                    await update.callback_query.answer("❌ Произошла ошибка", show_alert=True)
            except Exception:
                pass
    
    async def plan_tasks_completed_header_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка заголовка выполненных задач (ничего не делаем)"""
        try:
            query = update.callback_query
            if query:
                await query.answer()
        except Exception as e:
            logger.error(f"Ошибка в plan_tasks_completed_header_callback: {e}", exc_info=True)
    
    # Регистрируем обработчики callback'ов для плана
    application.add_handler(CallbackQueryHandler(
        plan_task_complete_callback,
        pattern='^plan_task_complete_'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_task_do_complete_callback,
        pattern='^plan_task_do_complete_'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_task_confirm_yes_callback,
        pattern='^plan_task_confirm_yes$'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_task_confirm_no_callback,
        pattern='^plan_task_confirm_no$'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_task_edit_callback,
        pattern='^plan_task_edit_'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_edit_title_prompt_callback,
        pattern='^plan_edit_title_'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_edit_deadline_prompt_callback,
        pattern='^plan_edit_deadline_'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_edit_back_callback,
        pattern='^plan_edit_back$'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_task_uncomplete_callback,
        pattern='^plan_task_uncomplete_'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_back_to_tasks_callback,
        pattern='^plan_back_to_tasks$'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_back_to_plan_callback,
        pattern='^plan_back_to_plan$'
    ))
    application.add_handler(CallbackQueryHandler(
        plan_tasks_completed_header_callback,
        pattern='^plan_tasks_completed_header$'
    ))
    # Ввод названия/даты при редактировании задачи из плана (только когда ждём ввод)
    class PlanEditWaitingFilter(filters.UpdateFilter):
        def __init__(self, app, **kwargs):
            super().__init__(**kwargs)
            self._app = app
        def filter(self, update):
            if not update.message or not update.message.text or not update.effective_user:
                return False
            ud = self._app.user_data.get(update.effective_user.id, {})
            return ud.get('plan_waiting') in ('plan_edit_title', 'plan_edit_deadline')
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & PlanEditWaitingFilter(application),
        plan_edit_message_handler
    ))
    
    # Обработчик ошибок
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка ошибок"""
        from telegram.error import Conflict, NetworkError, BadRequest
        
        import traceback
        error = context.error
        
        # Игнорируем ошибку Conflict (когда запущено несколько экземпляров)
        if isinstance(error, Conflict):
            logger.warning("Conflict: Другой экземпляр бота уже запущен. Остановите другие экземпляры.")
            logger.warning("Выполните: pkill -9 -f 'python3.*unified_bot.py'")
            return
        
        # Игнорируем временные сетевые ошибки
        if isinstance(error, NetworkError):
            logger.warning(f"Сетевая ошибка (возможно временная): {error}")
            return
        
        # Игнорируем некоторые BadRequest ошибки (например, сообщение уже отредактировано)
        if isinstance(error, BadRequest):
            error_msg = str(error)
            if "message is not modified" in error_msg.lower() or "message to edit not found" in error_msg.lower():
                logger.debug(f"BadRequest (можно игнорировать): {error}")
                return
        
        # Для остальных ошибок выводим полную информацию
        logger.error(f"ОШИБКА при обработке обновления: {type(error).__name__}: {error}", exc_info=True)
        
        # Пытаемся отправить сообщение пользователю только если это Update с сообщением
        if isinstance(update, Update):
            try:
                if update.message:
                    await update.message.reply_text(
                        "❌ Произошла ошибка. Попробуйте позже или отправьте /start"
                    )
                elif update.callback_query:
                    await update.callback_query.answer(
                        "❌ Произошла ошибка. Попробуйте позже.",
                        show_alert=True
                    )
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение об ошибке пользователю: {e}")
    
    application.add_error_handler(error_handler)
    
    logger.info("✅ Объединенный бот запущен!")
    logger.info("✅ Все ConversationHandler зарегистрированы")
    
    try:
        # Используем параметр для автоматического восстановления после Conflict
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False  # Не закрывать цикл при ошибках
        )
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем (Ctrl+C)")
    except Exception as e:
        from telegram.error import Conflict
        if isinstance(e, Conflict):
            logger.error("Conflict обнаружен при запуске. Убедитесь, что запущен только один экземпляр бота.")
            logger.error("Выполните: pkill -9 -f 'python3.*unified_bot.py'")
            logger.error("Затем запустите бота заново.")
        else:
            logger.error(f"Ошибка при запуске бота: {e}", exc_info=True)

if __name__ == '__main__':
    main()
