# Решение проблемы Conflict

## Проблема
Ошибка: `Conflict: terminated by other getUpdates request; make sure that only one bot instance is running`

Это означает, что запущено несколько экземпляров бота одновременно.

## Решение

### Шаг 1: Остановите все процессы бота

**Вариант A (мягкая остановка):**
```bash
cd /Users/viatcheslav/Desktop/unified-bot
./stop_bot.sh
```

**Вариант B (принудительная остановка):**
```bash
cd /Users/viatcheslav/Desktop/unified-bot
./kill_all_bots.sh
```

**Вариант C (если ничего не помогает):**
```bash
cd /Users/viatcheslav/Desktop/unified-bot
./force_stop.sh
```
⚠️ ВНИМАНИЕ: Это остановит ВСЕ процессы Python3!

### Шаг 2: Подождите 10 секунд
Telegram API нужно время, чтобы освободить соединение.

### Шаг 3: Запустите бота заново
```bash
cd /Users/viatcheslav/Desktop/unified-bot
python3 unified_bot.py
```

## Проверка процессов

Чтобы найти все запущенные процессы бота:
```bash
cd /Users/viatcheslav/Desktop/unified-bot
./find_bot_processes.sh
```

## Предотвращение проблемы

1. **Всегда используйте один терминал** для запуска бота
2. **Перед запуском** проверяйте, не запущен ли уже бот: `./find_bot_processes.sh`
3. **Используйте скрипт запуска** с проверкой: `./start_bot.sh`

## Если проблема сохраняется

1. Проверьте, не запущен ли бот в другом терминале или окне
2. Проверьте фоновые процессы: `ps aux | grep python`
3. Перезагрузите компьютер (крайний случай)
