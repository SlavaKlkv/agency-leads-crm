# Leadroom

Мини-CRM для заявок агентства. Клиент оставляет имя, контакт и запрос через Telegram-бота, после чего заявка автоматически появляется в веб-интерфейсе. Менеджер также может создать лид вручную, назначить теги и отфильтровать по ним общий список.

## Что реализовано

- пошаговый Telegram-диалог: имя → контакт → запрос;
- кнопка Telegram для отправки номера телефона;
- создание лида после завершения диалога;
- автоматические теги `Telegram` и `Новый`;
- защита webhook секретным заголовком;
- защита от повторной обработки одного Telegram update;
- ручное создание лида в CRM;
- добавление и удаление тегов;
- фильтрация списка лидов по тегу;
- PostgreSQL как основная база данных;
- Dockerfile и Render Blueprint для развёртывания.

Подключение входящих сообщений обычного пользовательского Telegram не входит в текущий MVP. Предлагаемый контур этой интеграции описан в [SUBMISSION.md](SUBMISSION.md).

## Стек

- Python 3.12+
- FastAPI и Uvicorn
- SQLAlchemy 2
- PostgreSQL 17 и Psycopg 3
- Jinja2 и серверный HTML
- Pytest
- Docker и Docker Compose

## Локальный запуск

Понадобятся [uv](https://docs.astral.sh/uv/) и Docker.

1. Установить зависимости:

```bash
uv sync
```

2. Создать локальный файл настроек:

```bash
cp .env.example .env
```

Файл `.env` уже исключён из Git. Шаблон содержит локальное подключение к PostgreSQL на порту `55432`.

3. Запустить PostgreSQL:

```bash
docker compose up -d db
```

4. Запустить CRM с переменными из `.env`:

```bash
uv run uvicorn app.main:app --reload --env-file .env
```

После запуска CRM доступна по адресу [http://127.0.0.1:8000](http://127.0.0.1:8000), а проверка состояния — по адресу [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

Если порт `8000` занят, можно выбрать другой:

```bash
uv run uvicorn app.main:app --reload --env-file .env --port 8010
```

## Настройки окружения

| Переменная | Назначение | Локальное значение |
| --- | --- | --- |
| `APP_NAME` | Название CRM в интерфейсе | `Leadroom` |
| `DATABASE_URL` | Строка подключения SQLAlchemy к PostgreSQL | `postgresql+psycopg://postgres:postgres@localhost:55432/leadroom` |
| `TELEGRAM_BOT_TOKEN` | Токен бота от BotFather | заполняется вручную |
| `TELEGRAM_WEBHOOK_SECRET` | Секрет проверки запросов Telegram | заполняется вручную |
| `PUBLIC_BASE_URL` | Публичный HTTPS-адрес без завершающего `/` | заполняется после развёртывания |

`TELEGRAM_WEBHOOK_SECRET` должен содержать от 1 до 256 символов: латинские буквы, цифры, `_` или `-`.

## Подключение Telegram-бота

1. Создать бота через BotFather и получить токен.
2. Заполнить в `.env` значения `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` и `PUBLIC_BASE_URL`.
3. Перезапустить приложение с обновлённым `.env`.
4. Зарегистрировать webhook после публикации приложения:

```bash
curl -X POST https://your-domain.example/api/telegram/setup
```

Приложение зарегистрирует адрес `https://your-domain.example/api/telegram/webhook`. Каждый входящий запрос проверяется по заголовку `X-Telegram-Bot-Api-Secret-Token`.

Проверить состояние приложения и наличие основных настроек можно через:

```bash
curl http://127.0.0.1:8000/health
```

Значение `bot_configured: true` подтверждает только наличие токена в окружении. Работу Telegram следует отдельно проверить реальным сообщением боту и появлением лида в CRM.

## Тесты

```bash
uv run pytest
```

Тесты проверяют:

- ручное создание лида;
- назначение тегов и фильтрацию;
- полный Telegram-сценарий от `/start` до создания лида;
- автоматические теги Telegram-лида;
- защиту от повторной доставки update;
- отклонение webhook с неверным секретом.

Тестовый набор использует временную SQLite через тот же SQLAlchemy-слой. Рабочая и развёртываемая конфигурации используют PostgreSQL; подключение к ней дополнительно проверяется при локальном интеграционном запуске.

## Docker

Собрать образ приложения:

```bash
docker build -t agency-leads-crm:local .
```

Остановить локальную PostgreSQL:

```bash
docker compose down
```

Данные PostgreSQL находятся в именованном Docker volume `postgres_data` и сохраняются после обычного `docker compose down`.

## Развёртывание на Render

Файл [render.yaml](render.yaml) описывает:

- Docker web service;
- управляемую PostgreSQL;
- автоматическую передачу `DATABASE_URL` приложению;
- health check по `/health`;
- секретные настройки Telegram.

После создания сервисов нужно:

1. указать `TELEGRAM_BOT_TOKEN`;
2. указать публичный адрес сервиса в `PUBLIC_BASE_URL`;
3. дождаться успешного health check;
4. один раз вызвать `POST /api/telegram/setup`;
5. отправить реальную заявку боту и убедиться, что лид с тегами появился в CRM.

## Структура проекта

```text
app/
├── config.py       # переменные окружения
├── db.py           # модели и операции SQLAlchemy
├── main.py         # веб-маршруты и Telegram webhook
├── telegram.py     # Telegram API и сценарий диалога
├── static/         # стили интерфейса
└── templates/      # HTML-шаблоны CRM
tests/
└── test_app.py     # пользовательские и интеграционные сценарии
compose.yaml        # локальная PostgreSQL
render.yaml         # публикация web service и PostgreSQL
Dockerfile          # образ приложения
SUBMISSION.md       # набросок продукта и разбор решения
```
