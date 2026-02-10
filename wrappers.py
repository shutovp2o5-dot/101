#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Утилиты для создания оберток обработчиков в unified_bot
"""

from typing import Callable, Awaitable
from telegram import Update
from telegram.ext import ContextTypes

# Режимы работы бота
MODE_MAIN = "main"
MODE_SCHEDULE = "schedule"
MODE_TASKS = "tasks"
MODE_PLAN = "plan"


def create_schedule_wrapper(handler_func: Callable) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]:
    """Создает обертку для обработчиков расписания с установкой режима
    
    Args:
        handler_func: Функция-обработчик из модуля расписания
    
    Returns:
        Обернутая функция с установкой режима расписания
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Устанавливаем режим расписания перед вызовом
        context.user_data['bot_mode'] = MODE_SCHEDULE
        # Вызываем функцию напрямую - она должна работать как в оригинальном боте
        return await handler_func(update, context)
    return wrapper


def create_schedule_entry_wrapper(handler_func: Callable) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]:
    """Создает обертку для entry points ConversationHandler расписания
    
    Args:
        handler_func: Функция-обработчик entry point из модуля расписания
    
    Returns:
        Обернутая функция с установкой режима расписания
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Устанавливаем режим расписания при входе в ConversationHandler
        context.user_data['bot_mode'] = MODE_SCHEDULE
        # Вызываем функцию напрямую - она должна работать как в оригинальном боте
        return await handler_func(update, context)
    return wrapper


def wrap_schedule_handler(handler_func: Callable) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]:
    """Обертка для функций внутри ConversationHandler расписания
    
    Args:
        handler_func: Функция-обработчик из модуля расписания
    
    Returns:
        Обернутая функция с установкой режима расписания
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Устанавливаем режим расписания перед вызовом
        context.user_data['bot_mode'] = MODE_SCHEDULE
        # Вызываем функцию напрямую - она должна работать как в оригинальном боте
        return await handler_func(update, context)
    return wrapper


def create_tasks_wrapper(handler_func: Callable) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]:
    """Создает обертку для обработчиков задач с установкой режима
    
    Args:
        handler_func: Функция-обработчик из модуля задач
    
    Returns:
        Обернутая функция с установкой режима задач
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Устанавливаем режим задач перед вызовом
        context.user_data['bot_mode'] = MODE_TASKS
        # Вызываем функцию напрямую - она должна работать как в оригинальном боте
        return await handler_func(update, context)
    return wrapper


def create_tasks_entry_wrapper(handler_func: Callable) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]:
    """Создает обертку для entry points ConversationHandler задач
    
    Args:
        handler_func: Функция-обработчик entry point из модуля задач
    
    Returns:
        Обернутая функция с установкой режима задач
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Устанавливаем режим задач при входе в ConversationHandler
        context.user_data['bot_mode'] = MODE_TASKS
        # Вызываем функцию напрямую - она должна работать как в оригинальном боте
        # Функция может вызывать context.user_data.clear() - это нормально
        result = await handler_func(update, context)
        # После вызова убеждаемся, что режим установлен (на случай если функция вызвала clear())
        context.user_data['bot_mode'] = MODE_TASKS
        return result
    return wrapper


def wrap_tasks_handler(handler_func: Callable) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]:
    """Обертка для функций внутри ConversationHandler задач
    
    Args:
        handler_func: Функция-обработчик из модуля задач
    
    Returns:
        Обернутая функция с установкой режима задач
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Устанавливаем режим задач перед вызовом
        context.user_data['bot_mode'] = MODE_TASKS
        # Вызываем функцию напрямую - она должна работать как в оригинальном боте
        # Функция может вызывать context.user_data.clear() - это нормально
        result = await handler_func(update, context)
        # После вызова убеждаемся, что режим установлен (на случай если функция вызвала clear())
        context.user_data['bot_mode'] = MODE_TASKS
        return result
    return wrapper


def create_add_project_wrapper(handler_func: Callable) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]:
    """Создает обертку для добавления проекта из главного меню
    
    Временно устанавливает режим задач для корректной работы функции,
    затем возвращает режим обратно, если ConversationHandler не начался.
    
    Args:
        handler_func: Функция-обработчик добавления проекта
    
    Returns:
        Обернутая функция с временной установкой режима задач
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Временно устанавливаем режим задач для корректной работы функции
        old_mode = context.user_data.get('bot_mode', MODE_MAIN)
        context.user_data['bot_mode'] = MODE_TASKS
        try:
            return await handler_func(update, context)
        finally:
            # Возвращаем режим обратно только если не начался ConversationHandler
            if context.user_data.get('bot_mode') == MODE_TASKS:
                context.user_data['bot_mode'] = old_mode
    return wrapper


def create_edit_project_wrapper(handler_func: Callable) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]:
    """Создает обертку для редактирования проекта из главного меню
    
    Временно устанавливает режим задач для корректной работы функции,
    затем возвращает режим обратно, если ConversationHandler не начался.
    
    Args:
        handler_func: Функция-обработчик редактирования проекта
    
    Returns:
        Обернутая функция с временной установкой режима задач
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Временно устанавливаем режим задач для корректной работы функции
        old_mode = context.user_data.get('bot_mode', MODE_MAIN)
        context.user_data['bot_mode'] = MODE_TASKS
        try:
            return await handler_func(update, context)
        finally:
            # Возвращаем режим обратно только если не начался ConversationHandler
            if context.user_data.get('bot_mode') == MODE_TASKS:
                context.user_data['bot_mode'] = old_mode
    return wrapper
