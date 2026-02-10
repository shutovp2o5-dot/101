#!/usr/bin/env python3
"""Тест подключения к Telegram API"""

import os
import sys
import asyncio
from telegram import Bot

async def test_connection():
    """Тест подключения"""
    # Загружаем токен
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        env_file = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(env_file):
            with open(env_file, 'r') as f:
                for line in f:
                    if line.startswith('TELEGRAM_BOT_TOKEN='):
                        token = line.split('=', 1)[1].strip().strip("'\"")
                        break
    
    if not token:
        print("❌ Токен не найден!")
        return False
    
    print(f"🔑 Токен найден: {token[:10]}...")
    print("📡 Подключение к Telegram API...")
    
    try:
        bot = Bot(token=token)
        me = await bot.get_me()
        print(f"✅ Успешное подключение!")
        print(f"   Бот: @{me.username} ({me.first_name})")
        return True
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return False
    finally:
        await bot.close()

if __name__ == '__main__':
    result = asyncio.run(test_connection())
    sys.exit(0 if result else 1)
