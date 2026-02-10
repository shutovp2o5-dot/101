#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Скрипт для проверки подключения к Telegram API"""

import socket
import httpx
import os
import asyncio

async def check_telegram_connection():
    """Проверка подключения к Telegram API"""
    print("🔍 Проверка подключения к Telegram API...\n")
    
    # Проверка DNS
    print("1️⃣ Проверка DNS разрешения...")
    try:
        ip = socket.gethostbyname('api.telegram.org')
        print(f"   ✅ DNS работает: api.telegram.org → {ip}")
    except socket.gaierror as e:
        print(f"   ❌ DNS не работает: {e}")
        print("   💡 Настройте DNS: sudo networksetup -setdnsservers Wi-Fi 8.8.8.8 1.1.1.1")
        return False
    
    # Проверка TCP соединения
    print("\n2️⃣ Проверка TCP соединения...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex(('api.telegram.org', 443))
        sock.close()
        if result == 0:
            print("   ✅ TCP соединение работает")
        else:
            print(f"   ❌ TCP соединение не работает (код ошибки: {result})")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка TCP соединения: {e}")
        return False
    
    # Проверка HTTPS соединения
    print("\n3️⃣ Проверка HTTPS соединения...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get('https://api.telegram.org')
            if response.status_code == 200 or response.status_code == 404:
                print(f"   ✅ HTTPS соединение работает (статус: {response.status_code})")
            else:
                print(f"   ⚠️ HTTPS соединение работает, но статус: {response.status_code}")
    except httpx.ConnectError as e:
        print(f"   ❌ Ошибка подключения: {e}")
        print("   💡 Проверьте файрвол и настройки сети")
        return False
    except Exception as e:
        print(f"   ❌ Ошибка HTTPS: {e}")
        return False
    
    # Проверка токена
    print("\n4️⃣ Проверка токена бота...")
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        # Попробуем загрузить из .env
        try:
            env_file = os.path.join(os.path.dirname(__file__), '.env')
            if os.path.exists(env_file):
                with open(env_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            if key.strip() == 'TELEGRAM_BOT_TOKEN':
                                token = value.strip().strip("'\"")
                                break
        except:
            pass
    
    if not token:
        print("   ❌ Токен не найден!")
        print("   💡 Установите токен: export TELEGRAM_BOT_TOKEN='ваш_токен'")
        return False
    
    print(f"   ✅ Токен найден: {token[:10]}...{token[-5:]}")
    
    # Проверка метода getMe
    print("\n5️⃣ Проверка метода getMe...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f'https://api.telegram.org/bot{token}/getMe'
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    bot_info = data.get('result', {})
                    print(f"   ✅ Бот работает!")
                    print(f"   📝 Имя: {bot_info.get('first_name', 'N/A')}")
                    print(f"   📝 Username: @{bot_info.get('username', 'N/A')}")
                    return True
                else:
                    print(f"   ❌ Ошибка API: {data.get('description', 'Unknown error')}")
                    return False
            else:
                print(f"   ❌ HTTP ошибка: {response.status_code}")
                print(f"   Ответ: {response.text[:200]}")
                return False
    except Exception as e:
        print(f"   ❌ Ошибка при проверке бота: {e}")
        return False

if __name__ == '__main__':
    result = asyncio.run(check_telegram_connection())
    print("\n" + "="*50)
    if result:
        print("✅ Все проверки пройдены! Бот должен работать.")
    else:
        print("❌ Обнаружены проблемы с подключением.")
        print("💡 Исправьте проблемы выше и попробуйте снова.")
    print("="*50)
