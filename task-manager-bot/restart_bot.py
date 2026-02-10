#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для перезапуска Telegram бота
"""

import os
import sys
import time
import subprocess
import signal

def stop_bot():
    """Остановка бота"""
    print("⏹️  Остановка бота...")
    
    try:
        # Ищем процессы бота
        result = subprocess.run(
            ['pgrep', '-f', 'bot_advanced.py'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        print(f"   Остановлен процесс {pid}")
                    except ProcessLookupError:
                        pass
                    except Exception as e:
                        print(f"   Ошибка при остановке процесса {pid}: {e}")
            
            time.sleep(2)
            
            # Принудительная остановка, если процессы все еще работают
            result = subprocess.run(
                ['pgrep', '-f', 'bot_advanced.py'],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    if pid:
                        try:
                            os.kill(int(pid), signal.SIGKILL)
                            print(f"   Принудительно остановлен процесс {pid}")
                        except:
                            pass
            
            print("✅ Бот остановлен")
        else:
            print("ℹ️  Бот не был запущен")
            
    except Exception as e:
        print(f"⚠️  Ошибка при остановке: {e}")
        # Пробуем через pkill
        subprocess.run(['pkill', '-f', 'bot_advanced.py'], 
                      capture_output=True)
        time.sleep(2)

def check_connection():
    """Проверка подключения"""
    print("\n🔍 Проверка подключения...")
    
    try:
        import socket
        socket.gethostbyname('api.telegram.org')
        print("✅ DNS работает")
    except:
        print("⚠️  DNS не работает")
    
    # Проверяем наличие скрипта проверки
    if os.path.exists('check_connection.py'):
        try:
            result = subprocess.run(
                ['python3', 'check_connection.py'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if '✅ Все проверки пройдены' in result.stdout:
                print("✅ Подключение к Telegram API работает")
                return True
        except:
            pass
    
    print("⚠️  Проверьте подключение: python3 check_connection.py")
    return False

def start_bot():
    """Запуск бота"""
    print("\n🚀 Запуск бота...")
    
    # Проверяем наличие токена
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        # Проверяем .env файл
        env_file = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(env_file):
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if 'TELEGRAM_BOT_TOKEN' in line and '=' in line:
                        token = line.split('=', 1)[1].strip().strip("'\"")
                        break
    
    if not token:
        print("❌ Ошибка: Токен бота не найден!")
        print("💡 Создайте файл .env с содержимым: TELEGRAM_BOT_TOKEN=ваш_токен")
        return False
    
    # Запускаем бота в фоне
    log_file = 'bot_run.log'
    try:
        with open(log_file, 'a') as log:
            process = subprocess.Popen(
                [sys.executable, 'bot_advanced.py'],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=os.path.dirname(__file__)
            )
        
        time.sleep(3)
        
        # Проверяем, что процесс все еще работает
        if process.poll() is None:
            print(f"✅ Бот успешно запущен (PID: {process.pid})")
            print("\n📋 Полезные команды:")
            print("   Просмотр логов: tail -f bot_run.log")
            print("   Остановка бота: python3 restart_bot.py --stop")
            print("   Статус бота: ps aux | grep bot_advanced.py")
            return True
        else:
            print("❌ Бот не запустился. Проверьте логи:")
            print(f"   tail -20 {log_file}")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при запуске: {e}")
        return False

def show_logs():
    """Показать последние строки лога"""
    log_file = 'bot_run.log'
    if os.path.exists(log_file):
        print("\n📝 Последние строки лога:")
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines[-10:]:
                    if any(keyword in line for keyword in ['✅', '❌', '🤖', '📡', 'работает', 'ошибка', 'запущен', 'DNS', 'подключ']):
                        print(f"   {line.strip()}")
        except:
            pass

def main():
    """Основная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Перезапуск Telegram бота')
    parser.add_argument('--stop', action='store_true', help='Только остановить бота')
    parser.add_argument('--start', action='store_true', help='Только запустить бота')
    parser.add_argument('--check', action='store_true', help='Только проверить подключение')
    
    args = parser.parse_args()
    
    if args.stop:
        stop_bot()
    elif args.start:
        check_connection()
        start_bot()
        show_logs()
    elif args.check:
        check_connection()
    else:
        # Полный перезапуск
        print("🔄 Перезапуск Telegram бота...\n")
        stop_bot()
        check_connection()
        start_bot()
        show_logs()

if __name__ == '__main__':
    main()
