<h1 align="center">Leadroom</h1>

Мини-CRM для заявок агентства. Обращения из Telegram-бота и личных сообщений подключённого аккаунта попадают в общий список; менеджер может добавить лида вручную, назначить теги и отслеживать работу по статусам.

## Технологии

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&amp;logoColor=white" alt="Python 3.13"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&amp;logoColor=white" alt="FastAPI 0.115+"></a>
  <a href="https://jinja.palletsprojects.com/"><img src="https://img.shields.io/badge/Jinja-3.1+-B41717?logo=jinja&amp;logoColor=white" alt="Jinja 3.1+"></a>
  <a href="https://www.sqlalchemy.org/"><img src="https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00?logo=sqlalchemy&amp;logoColor=white" alt="SQLAlchemy 2.0+"></a>
  <a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&amp;logoColor=white" alt="PostgreSQL 17"></a>
  <a href="https://docs.telethon.dev/"><img src="https://img.shields.io/badge/Telethon-1.40+-26A5E4?logo=telegram&amp;logoColor=white" alt="Telethon 1.40+"></a>
  <a href="https://docs.pytest.org/"><img src="https://img.shields.io/badge/Pytest-8.3+-0A9EDC?logo=pytest&amp;logoColor=white" alt="Pytest 8.3+"></a>
  <a href="https://www.docker.com/"><img src="https://img.shields.io/badge/Docker-containerized-2496ED?logo=docker&amp;logoColor=white" alt="Docker"></a>
  <a href="https://render.com/"><img src="https://img.shields.io/badge/Render-deployed-000000?logo=render&amp;logoColor=white" alt="Render"></a>
</p>

**[Открыть работающую CRM](https://agency-leads-crm.onrender.com/)** · [Проверить состояние сервиса](https://agency-leads-crm.onrender.com/health)

**Telegram-бот:** [@leadroom_agency_bot](https://t.me/leadroom_agency_bot)<br>**Telegram-аккаунт:** [@leadroom_agency_crm](https://t.me/leadroom_agency_crm)

> На бесплатном Render первый запуск после простоя может занять около минуты. Сначала дождитесь открытия CRM, затем проверяйте получение новых сообщений из обычного Telegram: фоновый клиент работает только пока сервис активен.

<p align="center">
  <img src="docs/assets/crm_screen.png" alt="Список лидов Leadroom со статусами, поиском, фильтрами по источнику и тегам" width="960">
</p>

## Как проходит заявка

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/lead-flow-dark.svg">
  <img src="docs/assets/lead-flow-light.svg" alt="Telegram-бот собирает имя, контакт и запрос; личное сообщение или ручной ввод тоже создают лида; все заявки попадают в CRM, где менеджер назначает теги и фильтрует список." width="960">
</picture>

Бот спрашивает имя, контакт и запрос. После последнего ответа лид появляется в CRM. Для проверки полного сценария откройте [бота](https://t.me/leadroom_agency_bot), отправьте `/start`, ответьте на три вопроса, затем найдите нового лида в CRM, добавьте ему тег и включите фильтр по этому тегу. Заявки в публичной демоверсии общие для всех посетителей.

## Что умеет MVP

| Канал | Результат |
| --- | --- |
| Telegram-бот | Пошаговая заявка с защитой webhook и повторной доставки сообщения |
| Обычный Telegram | Первое личное сообщение создаёт лида; следующие дополняют обращение |
| CRM | Ручное создание, изменение, удаление, статусы, теги, поиск и фильтры |

Теги помогают группировать заявки; статус показывает этап обработки. Фильтр по нескольким тегам находит лидов с **любым** из выбранных тегов.

## Запуск локально

Нужны [uv](https://docs.astral.sh/uv/) и Docker. Настройки берутся из `.env.example`; реальные ключи и Telegram-сессию храните только в локальном `.env` или в секретах хостинга.

Установите зависимости:

```bash
uv sync
```

Создайте настройки:

```bash
cp .env.example .env
```

Поднимите PostgreSQL:

```bash
docker compose up -d db
```

Запустите приложение:

```bash
uv run uvicorn app.main:app --reload --env-file .env
```

CRM откроется по адресу [http://127.0.0.1:8000](http://127.0.0.1:8000). Проверка:

```bash
uv run pytest -q
```

Проверка: автоматические тесты покрывают создание и изменение лидов, теги, фильтры, сценарии Telegram-бота и подключённого обычного Telegram-аккаунта, а также защиту от повторной обработки сообщений. Для приёмки по заданию важен отдельный проход в живой версии: отправьте новое сообщение [боту](https://t.me/leadroom_agency_bot) и отдельное личное сообщение [подключённому Telegram-аккаунту](https://t.me/leadroom_agency_crm), затем убедитесь, что лиды появились в CRM.

Подключение Telegram-бота, обычного аккаунта и развёртывание описаны в [руководстве по настройке](docs/operations.md).

Исходники приложения находятся в `app/`, сценарии проверок — в `tests/`, конфигурация локальной БД — в `compose.yaml`, публикация на Render — в `render.yaml`.
