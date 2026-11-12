# Mercator Analytics

Сервис аналитики проекта **Mercator**. Отвечает за детекцию рыночных событий (снижение/рост цен) по собранным данным и отдачу результатов расчёта по REST API.
Mercator состоит из нескольких независимых сервисов:

- **Task Manager** (ядро, Java/Spring) — оркестрирует сбор данных, хранит карточки/сеты в Postgres (`tm-postgres`) и замеры в ClickHouse (`tm-clickhouse`).
- **Парсер Ozon** (Python/Selenium) — наполняет данные ядра.
- **Analytics** (этот репозиторий) — читает данные ядра, считает рыночные события двумя алгоритмами (EWMA, MACD), хранит результаты в своей базе `analytics` и отдаёт их по API.
- **Frontend** (React, разрабатывается отдельно) — единственный потребитель этого API.

Analytics **не хранит исходные данные сама** — только результаты расчётов (снепшоты, события, участники событий) в собственной базе Postgres, изолированной от базы ядра.

Источник данных переключается без изменения кода, через `CONNECTOR_KIND` в `.env`:
- `mock` — данные читаются из дампов в `data/backups` (текущий режим, для разработки без доступа к боевому серверу);
- `real` — прямое подключение к `tm-postgres`/`tm-clickhouse` под read-only ролью (реализовано, но проверено пока только на Postgres-части — ClickHouse протестировать можно только рядом с реальным сервером).

Расчёт выполняется **батч-джобом по расписанию** (APScheduler, дважды в сутки — 00:45 и 12:45), не на лету. API только отдаёт уже посчитанные и сохранённые результаты.

## Стек технологий

- Python 3.12
- FastAPI + Uvicorn
- SQLAlchemy 2.0 + Alembic (миграции)
- psycopg 3 — подключение к Postgres
- clickhouse-connect — подключение к ClickHouse
- pandas, numpy — расчёт алгоритмов
- matplotlib — отладочные графики
- APScheduler — планировщик батч-расчёта
- bcrypt — хеширование паролей
- PyJWT — выдача и проверка токенов
- pydantic / pydantic-settings — конфигурация и валидация
- pytest, pytest-cov — тесты
- Docker / Docker Compose

## Структура проекта

```
mercator_analytics/
├── app/
│   ├── analytics/
│   │   ├── algorithms/       # common.py (общие инструменты), ewma.py, macd.py
│   │   ├── service.py        # оркестратор: все уровни, все алгоритмы, логирование
│   │   ├── runner.py         # запуск полного расчёта, запись в базу
│   │   └── selection.py      # отбор карточек в выборку под алгоритм
│   ├── api/
│   │   ├── analytics.py      # /catalog, /levels, /snapshot, /chart.png
│   │   ├── auth.py           # /auth/login, /auth/me
│   │   ├── cards.py          # /cards/{sku}/measurements
│   │   ├── health.py         # /health
│   │   └── subscriptions.py  # /subscriptions
│   ├── assignments/          # чтение CSV-заданий на сбор (represent_set_*.csv)
│   ├── config/                # settings.py, policies.py, logging_setup.py
│   ├── connectors/            # base.py, factory.py, mock.py, real.py, schema.py
│   ├── db/                    # base.py, models.py, repository.py, session.py
│   ├── etl/                   # regularize.py — регуляризация замеров к сетке
│   ├── migrations/            # alembic: versions/, env.py
│   ├── users/                 # models.py (User, UserSubscription), security.py, seed.py
│   └── main.py
├── data/
│   └── backups/                # дампы: pg_*.sql, ch_measurement_*.csv, represent_set_*.csv
├── tests/
│   ├── conftest.py             # подгружает .env.test до импорта app.*
│   ├── data_files.py           # поиск дампа ядра по маске
│   ├── unit/                   # test_security.py, test_find_participants.py, test_schema.py
│   └── integration/            # conftest.py + test_seed/test_auth_api/test_subscriptions_api/
│                                # test_repository/test_cards_api/test_real_connector_pg
├── docker-compose.yml           # an-app + an-postgres (боевой/локальный контур)
├── docker-compose.test.yml      # test-postgres (только для прогона тестов)
├── Dockerfile
├── requirements.txt
├── alembic.ini
├── pytest.ini
├── .env
└── .env.test
```

## Swagger

FastAPI генерирует спецификацию автоматически из кода:

- `http://localhost:8020/docs` — интерактивный Swagger UI;
- `http://localhost:8020/openapi.json` — сырой OpenAPI-файл (пригодится для автогенерации клиента на фронтенде).


## Эндпоинты

| Метод | Путь | Авторизация | Описание |
|---|---|---|---|
| GET | `/health` | нет | проверка работоспособности сервиса |
| POST | `/api/v1/auth/login` | нет | логин по username/password, возвращает JWT |
| GET | `/api/v1/auth/me` | Bearer JWT | данные текущего пользователя |
| GET | `/api/v1/subscriptions` | Bearer JWT | сохранённые сеты пользователя (категория+маркетплейс+гео) |
| PUT | `/api/v1/subscriptions` | Bearer JWT | заменяет весь список подписок, с дедупликацией |
| GET | `/api/v1/catalog` | нет | доступные сочетания категория+гео+маркетплейс |
| GET | `/api/v1/levels` | нет | какие снепшоты уровня посчитаны, признак sufficient/insufficient_reason |
| GET | `/api/v1/snapshot` | нет | снепшот целиком: метаданные + события + кривая рынка |
| GET | `/api/v1/chart.png` | нет | PNG-график снепшота (для отчётов и отладки) |
| GET | `/api/v1/events/{event_id}/participants` | нет | карточки-участники конкретного события |
| GET | `/api/v1/cards/{sku}/measurements` | нет | реальные замеры цены карточки за последние N суток (`days`, по умолчанию 30) |

## Тесты

Тесты есть, разделены на юнит и интеграционные.

- `tests/unit/` — чистая логика без базы и файлов (хеширование пароля, JWT, поиск участников события, приведение канонической схемы). Запускаются без Docker.
- `tests/integration/` — всё, что реально пишет в Postgres или ходит в файлы дампов: сидирование пользователя, `/auth`, `/subscriptions`, `save_run` и остальной `repository.py`, `/cards/*/measurements`, SQL-запросы `RealConnector` против восстановленного дампа ядра.

### Как запускать тесты

Юнит-тесты — без каких-либо зависимостей:
```bash
pytest tests/unit -v
```

Интеграционные тесты требуют отдельный тестовый Postgres (не боевой `an-postgres`, отдельный контейнер):
```bash
docker compose -f docker-compose.test.yml up -d
pytest tests/integration -v
```
Настройки для тестового прогона лежат в `.env.test` и подхватываются автоматически через `tests/conftest.py` (переопределяет переменные окружения до первого импорта `app.*`).

Тесты `test_real_connector_pg.py` дополнительно восстанавливают дамп ядра в тестовую базу — файл ищется по маске `pg_*.sql` в `data/backups` (см. `tests/data_files.py`): подкладываешь свежий дамп в папку — тесты сами подхватят его, ничего в тестах менять не нужно.

Часть `RealConnector`, отвечающая за ClickHouse (`get_measurements`), тестами не покрыта — проверяется только после развёртывания рядом с реальным ClickHouse ядра, локально сети до него нет.

Прогнать всё разом:
```bash
pytest
```
С покрытием:
```bash
pytest --cov=app
```

## Развёртывание

1. Клонировать репозиторий.
2. Подготовить `.env` (по образцу `.env.example`): реальные пароли БД, свой `JWT_SECRET_KEY`:
```bash
   python -c "import secrets; print(secrets.token_hex(32))"
```
3. Убедиться, что внешняя docker-сеть ядра (`CORE_NETWORK` в `.env`) уже существует — `docker-compose.yml` подключается к ней как к внешней (`external: true`). Сеть создаётся при первом старте docker-compose ядра (Task Manager) — его нужно поднять хотя бы раз до этого шага.
4. Поднять сервис:
```bash
   docker compose up --build
```
   Поднимутся `an-app` (порт 8020) и `an-postgres` (порт из `ANALYTICS_PG_BIND`).
5. Накатить миграции — **не выполняется автоматически** при старте контейнера. Делается вручную с хоста, с портом, вынесенным на loopback (имя контейнера `an-postgres` резолвится только внутри docker-сети, не на хосте):
```bash
   ANALYTICS_PG_HOST=127.0.0.1 ANALYTICS_PG_PORT=<ANALYTICS_PG_BIND-порт> alembic upgrade head
```
6. При первом старте автоматически создаётся тестовый пользователь `user`/`1234` (сидирование идемпотентно — повторные перезапуски контейнера дубль не создадут).
7. Проверить: `http://localhost:8020/docs` — должен открыться Swagger со всеми эндпоинтами.

По умолчанию `CONNECTOR_KIND=mock` — данные читаются из `data/backups`. Переключение на `real` (при развёртывании рядом с ядром на сервере) — только смена значения в `.env`, код не требует правок.

CORS сейчас разрешён для одного origin — `http://localhost:5173` (Vite dev-сервер фронтенда). При другом адресе фронтенда — поменять `allow_origins` в `app/main.py`.