# Настройка и проверка Leadroom

## Локальный запуск

Нужны [uv](https://docs.astral.sh/uv/) и Docker. Выполните команды из корня репозитория:

```bash
uv sync
```

```bash
cp .env.example .env
```

```bash
docker compose up -d db
```

```bash
uv run uvicorn app.main:app --reload --env-file .env
```

Приложение откроется на [http://127.0.0.1:8000](http://127.0.0.1:8000). Шаблон `.env.example` содержит локальную строку PostgreSQL на порту `55432`; `.env` не добавляйте в Git. Если порт `8000` занят, укажите `--port 8010` в команде запуска. Данные PostgreSQL сохраняются в Docker volume `postgres_data` после обычного `docker compose down`.

Проверка приложения:

```bash
uv run pytest -q
```

Тесты используют временную SQLite через тот же слой SQLAlchemy. Рабочая конфигурация использует PostgreSQL.

## Telegram-бот

Создайте бота в BotFather. В `.env` задайте `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` и публичный HTTPS-адрес `PUBLIC_BASE_URL` без завершающего `/`. Секрет webhook допускает 1–256 латинских букв, цифр, `_` и `-`.

После публикации сервиса зарегистрируйте webhook:

```bash
curl -X POST https://your-domain.example/api/telegram/setup
```

Telegram будет отправлять сообщения на `/api/telegram/webhook`; приложение проверяет заголовок `X-Telegram-Bot-Api-Secret-Token` и не обрабатывает один `update_id` повторно. Ответ setup означает регистрацию webhook, поэтому для проверки отправьте **новое** сообщение боту и убедитесь, что лид появился в CRM.

## Обычный Telegram-аккаунт

В [my.telegram.org](https://my.telegram.org/) создайте API-приложение. В локальном `.env` задайте `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` и `TELEGRAM_USER_ENABLED=false`. Затем создайте строку сессии:

```bash
uv run python -m app.telegram_login generate
```

Генератор спросит номер, код Telegram и пароль 2FA при наличии. Он запишет `TELEGRAM_SESSION` в локальный `.env` и выведет строку для копирования. Не публикуйте её: она даёт доступ к аккаунту.

В Render Dashboard задайте `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` и `TELEGRAM_SESSION`, затем выберите **Save and deploy**. `TELEGRAM_USER_ENABLED` на Render можно не добавлять: при отсутствии переменной режим обычного Telegram-аккаунта включён. Задайте `TELEGRAM_USER_ENABLED=false`, только если его нужно явно выключить. Не импортируйте весь локальный `.env`: в нём может быть локальный `DATABASE_URL`. Оставьте локальное `TELEGRAM_USER_ENABLED=false`, чтобы серверы не использовали одну StringSession одновременно.

Первое личное сообщение от другого обычного аккаунта создаёт лида; следующие сообщения дополняют обращение. Сервисный аккаунт `777000` исключён. Проверяйте работу новым сообщением после того, как `/health` покажет `telegram_user_connected: true`.

## Render и диагностика

Файл [render.yaml](../render.yaml) описывает Docker Web Service, PostgreSQL и проверку `/health`. Значения с `sync: false` задаются вручную в Dashboard. Для Telegram-бота также нужны `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` и `PUBLIC_BASE_URL`.

`/health` показывает наличие настроек и состояние подключения без раскрытия секретов. `bot_configured: true` означает лишь наличие токена. `telegram_user_configured: true` означает наличие API ID, API hash и сессии; `telegram_user_connected: true` подтверждает активное подключение аккаунта. Окончательная проверка обоих каналов — реальное новое сообщение и появление лида в CRM.

На бесплатном Render Web Service засыпает после простоя. Пока он спит, MTProto-клиент не получает личные сообщения и такое сообщение само по себе не будит сервис. Для демонстрации сначала откройте CRM, дождитесь запуска и проверьте `/health`, затем отправьте сообщение. Для постоянного приёма нужен непрерывно работающий сервис. Подробнее: [ограничения Render Free](https://render.com/docs/free#spinning-down-on-idle).
