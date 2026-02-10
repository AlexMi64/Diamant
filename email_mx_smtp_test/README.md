# Email MX + SMTP Checker

Тестовое задание: скрипт проверяет email-адреса по DNS MX и SMTP handshake (без отправки письма).

## Что делает

- принимает список email-адресов через аргументы командной строки;
- проверяет существование домена и MX-записей;
- выполняет SMTP-проверку получателя через `EHLO -> MAIL FROM -> RCPT TO -> QUIT`;
- выводит таблицу со статусом для каждого email.

## Статусы домена

- `домен валиден`
- `домен отсутствует`
- `МХ-записи отсутствуют или некорректны`

## Требования

- Python 3.9+
- пакет `dnspython`

## Установка зависимости

```bash
pip install dnspython
```

Если системный `pip` ограничен (PEP 668), используйте виртуальное окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install dnspython
```

## Запуск

```bash
python email_check.py user1@example.com user2@example.com
```

С опциональными таймаутами:

```bash
python email_check.py user1@example.com --dns-timeout 3 --smtp-timeout 8
```

## Формат вывода

Таблица с колонками:

- `email`
- `domain_status`
- `smtp_result`
- `smtp_code`
- `details`

`smtp_result` является диагностическим полем и не переопределяет итоговый `domain_status`.
