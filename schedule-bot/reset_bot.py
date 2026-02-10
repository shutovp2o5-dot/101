#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для принудительной очистки webhook и pending updates бота
Используйте этот скрипт, если бот выдает ошибку Conflict
"""

import os
import asyncio
from telegram import Bot

def main():
    # Получение токена
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
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
        print("❌ Ошибка: Не найден TELEGRAM_BOT_TOKEN!")
        return
    
    async def cleanup():
        bot = Bot(token)
        try:
            # Удаляем webhook и все pending updates
            result = await bot.delete_webhook(drop_pending_updates=True)
            print("✅ Webhook удален, pending updates очищены")
            print(f"   Результат: {result}")
            
            # Дополнительно: получаем информацию о боте для проверки
            bot_info = await bot.get_me()
            print(f"✅ Бот активен: @{bot_info.username}")
            
        except Exception as e:
            print(f"❌ Ошибка при очистке: {e}")
            raise
    
    print("🔄 Очистка webhook и pending updates...")
    asyncio.run(cleanup())
    print("✅ Готово! Теперь можно запустить бота заново.")

if __name__ == '__main__':
    main()
