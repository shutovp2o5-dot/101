#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общий модуль для работы с проектами/категориями
Объединяет проекты из задач и категории из расписания
"""

import json
import os
from typing import Dict, List, Optional

# Общий файл для проектов/категорий
SHARED_PROJECTS_FILE = 'shared_projects.json'


def load_shared_projects() -> Dict[str, Dict[str, str]]:
    """Загрузка общих проектов/категорий"""
    if os.path.exists(SHARED_PROJECTS_FILE):
        try:
            with open(SHARED_PROJECTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Ошибка при загрузке общих проектов: {e}")
            return {}
    return {}


def save_shared_projects(projects_data: Dict[str, Dict[str, str]]):
    """Сохранение общих проектов/категорий"""
    try:
        with open(SHARED_PROJECTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(projects_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка при сохранении общих проектов: {e}")


def get_user_projects(user_id: str) -> Dict[str, str]:
    """Получить проекты пользователя"""
    data = load_shared_projects()
    return data.get(str(user_id), {})


def add_user_project(user_id: str, project_id: str, project_name: str):
    """Добавить проект пользователю"""
    data = load_shared_projects()
    user_id_str = str(user_id)
    if user_id_str not in data:
        data[user_id_str] = {}
    data[user_id_str][project_id] = project_name
    save_shared_projects(data)


def delete_user_project(user_id: str, project_id: str):
    """Удалить проект пользователя"""
    data = load_shared_projects()
    user_id_str = str(user_id)
    if user_id_str in data and project_id in data[user_id_str]:
        del data[user_id_str][project_id]
        save_shared_projects(data)


def sync_projects_from_schedule(user_id: str, schedule_module):
    """Синхронизировать проекты из категорий расписания"""
    if not schedule_module:
        return
    
    try:
        # Пробуем разные способы получения категорий
        categories = {}
        if hasattr(schedule_module, 'get_user_categories'):
            categories = schedule_module.get_user_categories(str(user_id))
        elif hasattr(schedule_module, 'load_user_categories'):
            all_categories = schedule_module.load_user_categories()
            categories = all_categories.get(str(user_id), {})
        
        if categories:
            data = load_shared_projects()
            user_id_str = str(user_id)
            if user_id_str not in data:
                data[user_id_str] = {}
            
            # Добавляем категории как проекты
            for cat_id, cat_name in categories.items():
                if isinstance(cat_name, dict):
                    # Если это словарь, берем название
                    cat_name = cat_name.get('name', str(cat_id))
                if cat_id not in data[user_id_str]:
                    data[user_id_str][cat_id] = cat_name
            
            save_shared_projects(data)
    except Exception as e:
        print(f"Ошибка при синхронизации проектов из расписания: {e}")
        import traceback
        traceback.print_exc()


def sync_projects_from_tasks(user_id: str, tasks_module):
    """Синхронизировать проекты из задач"""
    if not tasks_module or not hasattr(tasks_module, 'load_data'):
        return
    
    try:
        tasks_data = tasks_module.load_data()
        user_tasks_data = tasks_data.get(str(user_id), {})
        if isinstance(user_tasks_data, dict):
            projects_data = user_tasks_data.get('projects_data', {})
            if projects_data:
                data = load_shared_projects()
                user_id_str = str(user_id)
                if user_id_str not in data:
                    data[user_id_str] = {}
                
                # Добавляем проекты
                for project_name, project_info in projects_data.items():
                    project_id = project_info.get('id', project_name)
                    if project_id not in data[user_id_str]:
                        data[user_id_str][project_id] = project_name
                
                save_shared_projects(data)
    except Exception as e:
        print(f"Ошибка при синхронизации проектов из задач: {e}")
