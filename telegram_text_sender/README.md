# Telegram Text Sender

Мини-интеграция с Telegram: скрипт читает текст из `.txt` и отправляет его в приватный чат через Telegram-бота.

## Возможности

- чтение текста из файла `.txt`;
- отправка в чат по `chat_id` через Bot API;
- автоматическая разбивка длинного текста на части до 4096 символов;
- ретраи при временных ошибках (`429`, `5xx`, сетевые сбои);
- без внешних зависимостей (только стандартная библиотека Python).

## Требования

- Python 3.9+
- переменная окружения `BOT_TOKEN`

## 1) Создать бота и получить токен

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram.
2. Выполните `/newbot` и следуйте шагам.
3. Скопируйте токен вида `123456:ABC...`.
4. Экспортируйте токен в текущую сессию терминала:

```bash
export BOT_TOKEN="123456:ABCDEF..."
```

## 2) Получить `chat_id` приватного чата

1. Напишите любое сообщение вашему боту в Telegram.
2. Выполните запрос:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/getUpdates"
```

3. Найдите поле `chat.id` в ответе JSON. Это и есть нужный `chat_id`.

## 3) Подготовить текст

Создайте файл, например `message.txt`, в кодировке UTF-8.

## 4) Запуск

Из папки `/Users/a/tgbot/VisaPoland/Diamant/telegram_text_sender`:

```bash
python send_text.py --chat-id 123456789 ./message.txt
```

Опциональные параметры:

```bash
python send_text.py --chat-id 123456789 ./message.txt --timeout 10 --max-retries 3
```

## Интерпретация ошибок

- `Input error: BOT_TOKEN is not set`  
  Не задана переменная окружения `BOT_TOKEN`.
- `Input error: Input file not found`  
  Неверный путь к файлу.
- `Telegram API error: HTTP 400/403 ...`  
  Неверный `chat_id` или у бота нет доступа к чату.
- `Telegram API error: HTTP 429 ...`  
  Сработал лимит, скрипт выполнит повторные попытки.
- `Network error after retries ...`  
  Проблема сети или недоступен API после всех ретраев.

## Коды выхода

- `0` — успешно отправлено;
- `1` — ошибка Telegram API/сети после попыток повтора;
- `2` — ошибка входных параметров/окружения.
