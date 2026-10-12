# SPEC — Health Hub MCP Server (полная переписка с нуля)

## Implementation target update (2026-10-12)

The current user request extends the completed server to expose all 34 stored
registry types through MCP. This update supersedes the historical 25-type,
five-tool surface and fixed file list below for this extension. It does not
restart the original implementation/deployment phases.

The MCP server advertises `health-connect-mcp`. Add `list_data_types` for
registry metadata and schemas, and `health_records` for explicit, typed,
paginated access. Generate input enums and discriminated output models from
the registry. Documented value fields have their actual units; fields without
a defined contract remain JSON, including nested content, with caveats.

The endpoint is only `/mcp`, using MCP Python SDK 2.x and protocol revision
`2026-07-28` only. Reject earlier revisions and remove `/mcp-health`. Every
request declares its version and capabilities in `_meta` with matching HTTP
headers. Use `server/discover`; do not use the historical `initialize`
handshake or session IDs. This supersedes the transport/session instructions
in the original phases below. `mcp_server/transport.py` enforces the revision
after authentication; the SDK validates the request envelope.

Record queries require one type and inclusive local dates using configured
timezone/day boundary. Instants use measurement time, sleep uses session end,
and intervals intersect the half-open UTC window without clipping. Pages
default to 100 records and have a maximum of 400. Query-bound cursors continue
in chronological start-time/event-ID order. Invalid arguments are MCP errors.
Missing fields remain unknown; records have content-hash deduplication and
no suppression of overlapping sources. Bearer authentication applies to all
MCP transport methods and tool calls. Verify every type using synthetic data.

The extension adds `contract/values.py`, `mcp_server/schemas_records.py`,
`mcp_server/records_query.py`, `mcp_server/tools_records.py`, and focused MCP
test modules. Existing rollup tools cover their documented aggregate subset.

### Storage and aggregation cleanup

Individual records are the primary MCP interface. Aggregate tools remain
additional derived views and never modify stored values. This update
supersedes conflicting calculation and connection instructions below.

`db/store.py` owns separate committed-read and serialized-write connections
to a SQLite file. One ingestion pipeline owns each complete write unit;
raw input commits separately, while events, stages, diagnostics and rollups
commit atomically. Failed or cancelled requests roll back before releasing
the writer. All time-window comparisons use indexed canonical UTC instants.

`aggregate/sleep.py` supplies both detail and rollup calculations. Time in
bed is the session duration. Time asleep sums sleeping/light/deep/rem and
excludes awake/awake_in_bed/out_of_bed. Missing, unknown, overlapping,
incomplete or inconsistent stages leave time asleep unknown.

Daily summaries and metric series enforce 400 output buckets. Weekly
bucketing must be requested explicitly; unknown metrics are MCP errors.
Partial recorded coverage remains partial even when a metric is present.
Sleep detail caps sessions at 400 and directs pagination to health_records.
Debug coverage and rollup comparisons use configured local day attribution.

`python -m health_hub --rebuild-rollups` atomically replaces the optional
summary cache using stored events and current timezone settings. Run it
with the service stopped after changing aggregation rules. It does not
rewrite raw payloads or individual records. Unused schemas.py and Event.stages
are removed; output schemas live with the record tools.

Версия спеки: 2026-08-09. Это **единственный источник истины** по требованиям.
Если код расходится со спекой — прав документ. Если реальность расходится со спекой — остановись и спроси.

---

## 0. Что строим и зачем

Персональный сервер, который принимает данные Health Connect с Android-приложения
[life-dashboard-companion-app](https://github.com/owen282000/life-dashboard-companion-app) **v1.4.0**
и отдаёт их LLM-клиентам (Claude, Perplexity, OpenCode) по протоколу MCP.

```
Android 1.4.0 ──POST /ingest/health──────▶ ┌──────────────────┐
              ──POST /ingest/screentime──▶ │   Health Hub     │──MCP──▶ LLM
                                            │   SQLite         │
                    браузер ──GET /debug──▶ └──────────────────┘
```

Проблема прошлых попыток: сервер писали под контракт, которого не существует, и он отдавал LLM
сырые записи, из которых модель делала неверные выводы. Эта спека фиксирует **реальный** контракт
(вычитанный из исходников приложения) и требует слоя агрегации между сырьём и LLM.

**Состояние старта:** чистый лист. Старую БД не мигрируем. Деплой заменяет живой сервер
на `health.example.com`.

**Проект публичный.** Репозиторий выкладывается на GitHub под MIT, чтобы связку мог поднять любой
владелец Android-телефона. Из этого следуют требования, обязательные с первой строки кода,
а не прикрученные в конце:

- Ни одного личного значения в коде. Домен, токены, часовой пояс, путь к БД — только из окружения.
  Строка `health.example.com` не должна встречаться нигде, кроме `.env` (в `.gitignore`)
  и примеров в документации.
- Никаких секретов в репозитории. `.env`, `data/`, `creds.txt` — в `.gitignore` с первого коммита.
- Поднятие у постороннего человека — три команды и не больше пятнадцати минут. Проверяется
  в Фазе 8 на чистой машине.
- Английский язык в коде, комментариях, README и сообщениях об ошибках. Русский допустим только
  в этой спеке.

---

## 1. Жёсткие правила для исполнителя

Нарушение любого из них = работа не принята.

1. **Не выдумывай поля.** Контракт входящих данных — в §3. Если поля нет в §3, его не существует.
   Особенно: **нет** `device` в health-payload, **нет** `client_record_id`, **нет** `record_id`,
   **нет** `origin`, у сессии сна **нет** `start_time`.
2. **Один файл — одна ответственность.** Файловая структура §4 обязательна к соблюдению буквально.
   Не создавай файлов вне неё. Не создавай `utils.py`, `helpers.py`, `common.py`.
3. **Файл длиннее 200 строк — разбей.** Функция длиннее 50 строк — разбей.
4. **Чистые функции отдельно от I/O.** Модули `contract/`, `ingest/normalize.py`,
   `ingest/identity.py`, `aggregate/` **не имеют права** импортировать `aiosqlite`, обращаться
   к сети или файлам. Они принимают данные и возвращают данные. Это главное требование
   к архитектуре — оно делает всё тестируемым и чинибельным.
5. **SQL только параметризованный** (`?`-плейсхолдеры). Никогда не форматируй строки для SQL.
6. **Фазы §9 выполняются по порядку.** После каждой фазы остановись, выполни команду проверки
   из раздела «Приёмка» этой фазы, покажи её вывод и жди подтверждения. Не начинай следующую
   фазу самостоятельно.
7. **Тесты пишутся в той же фазе, что и код.** Фаза без зелёных тестов не закрыта.
8. **Ничего не удаляй из `raw_payloads`.** Никогда.
9. Если чего-то не хватает для выполнения — **спроси**, не угадывай и не заглушай `pass`-ом.
10. **Ни одного личного значения в коде.** Домены, токены, часовой пояс, пути — только через
    переменные окружения. Перед сдачей каждой фазы: `grep -ri "example\.com\|192\.0\.2\.1" --exclude-dir=.git .`
    должен возвращать пустоту (кроме `docs/` и `.env.example`, где допустим `example.com`).
11. **Английский в репозитории.** Код, комментарии, докстринги, README, тексты ошибок и логов —
    по-английски. Проект публичный.

---

## 2. Технологический стек

Строго этот, без замен и без добавления библиотек:

| Что | Чем |
|---|---|
| Python | 3.12 |
| ASGI-сервер | `uvicorn[standard]` |
| HTTP-каркас | `starlette` |
| MCP | `mcp[cli]` (FastMCP + Streamable HTTP) |
| БД | `aiosqlite` (SQLite, WAL) |
| Валидация | `pydantic` v2 |
| Тесты | `pytest`, `pytest-asyncio`, `httpx` |
| Упаковка | Docker + docker-compose |
| Прокси | Caddy |

Никаких ORM, никаких Alembic, никакого Postgres, никакого фронтенд-фреймворка.
Страница `/debug` — один самодостаточный HTML-файл с ванильным JS.

---

## 3. Контракт входящих данных (источник истины)

Вычитано из `HealthSyncManager.buildJsonPayload()` приложения v1.4.0.

### 3.1 Общее

- `POST`, `Content-Type: application/json; charset=utf-8`.
- Заголовок `Authorization: Bearer <DEVICE_TOKEN>` (настраивается в приложении как custom header).
- Заголовок `X-Signature: sha256=<hex>` — HMAC-SHA256 от **сырого тела** запроса с общим секретом.
  Проверять **до** парсинга JSON.
- Клиент ретраит: 3 попытки, экспоненциальный backoff, таймаут 10 с.
  Ретраятся `408`, `429`, `5xx` и сетевые ошибки. `4xx` считается постоянной ошибкой и **не ретраится**.
- **Массивы с нулём записей отсутствуют в JSON целиком**, а не приходят пустыми. Читать всё через
  `payload.get(key) or []`.

### 3.2 Health payload → `POST /ingest/health`

```json
{
  "timestamp": "2026-08-09T12:00:00Z",
  "app_version": "1.4.0",
  "source": "health_connect",
  "steps":     [{"count": 1234, "start_time": "...", "end_time": "..."}],
  "heart_rate":[{"bpm": 72, "time": "..."}],
  "sleep":     [{"session_end_time": "...", "duration_seconds": 28800,
                 "stages": [{"stage": "deep", "start_time": "...", "end_time": "...",
                             "duration_seconds": 7200}]}],
  "_diagnostics": {
    "steps": {"permission_granted": true, "page_count": 3,
              "raw_record_count": 2841, "filtered_record_count": 1000,
              "min_time": "...", "max_time": "...", "last_sync": "...", "error": null}
  }
}
```

Верхний уровень: только `timestamp`, `app_version`, `source`, ключи типов и `_diagnostics`.
**Поля `device` здесь нет.**

### 3.3 Все 25 типов

Колонка «вид» определяет обработку: `INTERVAL` — есть `start_time`/`end_time`;
`INSTANT` — есть только `time`; `SESSION` — особый разбор.

| JSON-ключ | Вид | Поля значения | Кумулятивный |
|---|---|---|---|
| `steps` | INTERVAL | `count` (int) | да |
| `distance` | INTERVAL | `meters` (float) | да |
| `active_calories` | INTERVAL | `calories` (float) | да |
| `total_calories` | INTERVAL | `calories` (float) | да |
| `exercise` | INTERVAL | `type` (str), `duration_seconds` (int) | нет |
| `hydration` | INTERVAL | `liters` (float) | да |
| `nutrition` | INTERVAL | `calories`, `protein_grams`, `carbs_grams`, `fat_grams` — **все опциональны** | да |
| `mindfulness` | INTERVAL | `title` (str, может быть `null` или отсутствовать), `duration_seconds` | нет |
| `menstruation_period` | INTERVAL | — | нет |
| `sleep` | SESSION | `session_end_time`, `duration_seconds`, `stages[]` | нет |
| `heart_rate` | INSTANT | `bpm` (int) | нет |
| `resting_heart_rate` | INSTANT | `bpm` (int) | нет |
| `heart_rate_variability` | INSTANT | `heart_rate_variability_millis` (float) | нет |
| `blood_pressure` | INSTANT | `systolic` (float), `diastolic` (float) | нет |
| `blood_glucose` | INSTANT | `mmol_per_liter` (float) | нет |
| `oxygen_saturation` | INSTANT | `percentage` (float) | нет |
| `respiratory_rate` | INSTANT | `rate` (float) | нет |
| `body_temperature` | INSTANT | `celsius` (float) | нет |
| `weight` | INSTANT | `kilograms` (float) | нет |
| `height` | INSTANT | `meters` (float) | нет |
| `body_fat` | INSTANT | `percentage` (float) | нет |
| `lean_body_mass` | INSTANT | `kilograms` (float) | нет |
| `bone_mass` | INSTANT | `kilograms` (float) | нет |
| `body_water_mass` | INSTANT | `kilograms` (float) | нет |
| `menstruation_flow` | INSTANT | `flow` (`light`/`medium`/`heavy`/`unknown`) | нет |

Значения `stage` у сна: `unknown`, `awake`, `sleeping`, `out_of_bed`, `light`, `deep`, `rem`,
`awake_in_bed`.

### 3.4 Три ловушки, на которых ломались прошлые версии

1. **`heart_rate` — это плоский список семплов, а не записей.** Каждый элемент = один замер.
   За сутки их могут быть сотни.
2. **У сессии сна нет времени начала.** Вычислять: `start = session_end_time − duration_seconds`.
   `duration_seconds` — это **время в кровати**, включая стадии `awake` и `awake_in_bed`.
3. **Ни у одной записи нет идентификатора.** Дедупликация возможна только контент-хешем (§5.2).

### 3.5 Screen time → `POST /ingest/screentime`

```json
{
  "timestamp": "...", "app_version": "1.4.0",
  "device": "Google Pixel 8",
  "source": "screen_time",
  "screen_time": [{"date": "2026-08-09", "total_screen_time_minutes": 180,
                   "apps": [{"package": "...", "name": "...", "minutes": 45, "last_used": "..."}]}]
}
```

Здесь `device` **есть**. Приходят всегда последние 7 дней целиком → писать через upsert по `date`,
список приложений за день заменять полностью.

---

## 4. Файловая структура (обязательна буквально)

```
health_hub/
  __init__.py
  config.py                 # Settings из env, падает при отсутствии обязательных
  logging_setup.py          # настройка логирования, %-форматирование

  app.py                    # композиционный корень: сборка Starlette + lifespan
  __main__.py               # точка входа для uvicorn

  db/
    __init__.py
    schema.sql              # весь DDL, идемпотентный
    connection.py           # открытие соединения, WAL, PRAGMA, get_db()
    migrations.py           # применение schema.sql, таблица schema_version

  contract/                 # ЧИСТЫЙ модуль: без I/O, без aiosqlite
    __init__.py
    kinds.py                # enum RecordKind: INTERVAL / INSTANT / SESSION
    registry.py             # TYPE_REGISTRY: 25 описаний типов. Точка расширения.
    payload.py              # pydantic-модели верхнего уровня HealthPayload/ScreenTimePayload

  ingest/                   # normalize.py и identity.py — ЧИСТЫЕ
    __init__.py
    identity.py             # event_id = контент-хеш
    normalize.py            # dict payload -> list[Event] (чистая функция)
    raw_store.py            # L0: запись сырого payload (I/O)
    diagnostics.py          # L0b: разбор _diagnostics в строки (I/O)
    writer.py               # L1: upsert events / sleep_stages / screen_time (I/O)

  aggregate/                # ЧИСТЫЙ модуль: без I/O
    __init__.py
    timewindow.py           # локальная дата с настраиваемой границей суток
    dedup.py                # разрешение перекрытий для кумулятивных типов
    rollup.py               # вычисление дневной сводки из списка событий

  rollup_service.py         # I/O-обёртка над aggregate/: читает БД, пишет daily_rollups

  api/
    __init__.py
    auth.py                 # Bearer + HMAC по сырому телу
    routes_health.py        # GET /health
    routes_ingest.py        # POST /ingest/health, /ingest/screentime, /ingest/echo
    routes_debug.py         # GET /debug + /debug/api/*
    debug.html              # одностраничный интерфейс отладки

  mcp_server/
    __init__.py
    server.py               # FastMCP instance + TransportSecuritySettings
    schemas.py              # выходные JSON-схемы инструментов
    tools_summary.py        # daily_summary, metric_series
    tools_sleep.py          # sleep_detail
    tools_ops.py            # get_sync_health, inspect_last_payload

tests/
  conftest.py
  fixtures/
    payload_health_full.json
    payload_health_sparse.json     # половина ключей отсутствует
    payload_screentime.json
    payload_real.json              # реальный экспорт с телефона, кладёт пользователь
  test_registry.py
  test_normalize.py
  test_identity.py
  test_timewindow.py
  test_dedup.py
  test_rollup.py
  test_auth.py
  test_routes_ingest.py
  test_routes_debug.py
  test_mcp_tools.py

scripts/
  verify_deploy.sh

docs/
  SETUP.md                  # пошаговая установка для постороннего человека
  ANDROID.md                # настройка приложения-компаньона
  API.md                    # контракт ингеста и список MCP-инструментов
  TROUBLESHOOTING.md        # что смотреть на /debug, когда данные не сходятся

.github/
  workflows/ci.yml          # ruff + pytest на push и PR
  ISSUE_TEMPLATE/bug_report.md

Dockerfile
docker-compose.yml
Caddyfile.example           # с плейсхолдером домена, не с личным
requirements.txt
.env.example
.gitignore
README.md
LICENSE                     # MIT
CONTRIBUTING.md
SECURITY.md
```

`.gitignore` создаётся **в первом же коммите** и содержит минимум:
`.env`, `data/`, `*.sqlite3`, `*.db`, `__pycache__/`, `.venv/`, `.pytest_cache/`, `creds.txt`,
`tests/fixtures/payload_real.json`.

Последняя строка важна: фикстура с реальным экспортом с телефона содержит персональные данные
о здоровье и в публичный репозиторий попасть не должна. В репозитории лежат только синтетические
фикстуры.

**Направление зависимостей строгое:**
`api/` и `mcp_server/` → `ingest/`, `rollup_service.py` → `aggregate/`, `contract/`, `db/` → `config.py`.
Обратных импортов нет. `contract/` и `aggregate/` не импортируют вообще ничего из проекта,
кроме друг друга и стандартной библиотеки.

---

## 5. Данные

### 5.1 Схема БД (`db/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- L0: сырьё, append-only, никогда не удаляется
CREATE TABLE IF NOT EXISTS raw_payloads (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at       TEXT NOT NULL,        -- ISO8601 UTC, время сервера
  source            TEXT NOT NULL,        -- health_connect | screen_time | echo
  app_version       TEXT,
  device            TEXT,                 -- только для screen_time
  payload_timestamp TEXT,
  signature_valid   INTEGER NOT NULL,     -- 0/1
  byte_size         INTEGER NOT NULL,
  body_json         TEXT NOT NULL,        -- ровно то, что пришло
  parse_error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_received ON raw_payloads(received_at DESC);

-- L0b: разобранный блок _diagnostics
CREATE TABLE IF NOT EXISTS sync_diagnostics (
  payload_id            INTEGER NOT NULL REFERENCES raw_payloads(id),
  data_type             TEXT NOT NULL,
  permission_granted    INTEGER,
  page_count            INTEGER,
  raw_record_count      INTEGER,
  filtered_record_count INTEGER,
  min_time              TEXT,
  max_time              TEXT,
  last_sync             TEXT,
  error                 TEXT,
  PRIMARY KEY (payload_id, data_type)
);

-- L1: нормализованные события
CREATE TABLE IF NOT EXISTS events (
  event_id      TEXT PRIMARY KEY,         -- sha256 hex, см. 5.2
  data_type     TEXT NOT NULL,
  start_utc     TEXT NOT NULL,            -- ISO8601 UTC
  end_utc       TEXT NOT NULL,            -- для INSTANT равен start_utc
  value_num     REAL,                     -- основная скалярная величина
  value_json    TEXT NOT NULL,            -- полная запись как пришла
  origin        TEXT,                     -- всегда NULL: приложение не отдаёт источник
  payload_id    INTEGER NOT NULL REFERENCES raw_payloads(id),
  first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type_start ON events(data_type, start_utc);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_utc);

CREATE TABLE IF NOT EXISTS sleep_stages (
  session_event_id TEXT NOT NULL REFERENCES events(event_id),
  stage            TEXT NOT NULL,
  start_utc        TEXT NOT NULL,
  end_utc          TEXT NOT NULL,
  seconds          INTEGER NOT NULL,
  PRIMARY KEY (session_event_id, stage, start_utc)
);

CREATE TABLE IF NOT EXISTS screen_time_days (
  local_date    TEXT PRIMARY KEY,
  total_minutes INTEGER NOT NULL,
  payload_id    INTEGER NOT NULL REFERENCES raw_payloads(id),
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screen_time_apps (
  local_date TEXT NOT NULL,
  package    TEXT NOT NULL,
  name       TEXT,
  minutes    INTEGER NOT NULL,
  last_used  TEXT,
  PRIMARY KEY (local_date, package)
);

-- L2: дневные сводки по ЛОКАЛЬНОЙ дате
CREATE TABLE IF NOT EXISTS daily_rollups (
  local_date      TEXT PRIMARY KEY,
  steps           INTEGER,
  distance_m      REAL,
  active_kcal     REAL,
  total_kcal      REAL,
  sleep_in_bed_s  INTEGER,
  sleep_asleep_s  INTEGER,
  hr_avg          REAL,
  hr_min          INTEGER,
  hr_max          INTEGER,
  hr_resting      INTEGER,
  weight_kg       REAL,
  coverage_json   TEXT NOT NULL,          -- {"steps":"ok","sleep":"absent",...}
  computed_at     TEXT NOT NULL
);
```

### 5.2 Идентичность события (`ingest/identity.py`)

Приложение не отдаёт ID записей, поэтому идентичность вычисляется из содержимого:

```python
def event_id(data_type: str, start_utc: str, end_utc: str, value_json: str) -> str:
    """SHA-256 от канонического представления записи.

    value_json канонизируется: json.dumps(obj, sort_keys=True, separators=(",", ":"),
    ensure_ascii=False). Одинаковая запись, присланная повторно, даёт тот же id.
    """
```

Запись в `events` — `INSERT ... ON CONFLICT(event_id) DO NOTHING`. Это делает повторную
доставку одного и того же payload полностью идемпотентной.

---

## 6. Модуль `contract/registry.py` — единственная точка расширения

```python
@dataclass(frozen=True)
class TypeSpec:
    json_key: str            # ключ в payload, напр. "steps"
    data_type: str           # как хранится в БД, совпадает с json_key
    kind: RecordKind         # INTERVAL | INSTANT | SESSION
    value_field: str | None  # поле основной величины, напр. "count"; None для sleep
    unit: str                # "count" | "m" | "kcal" | "bpm" | "kg" | ...
    cumulative: bool         # суммируется ли за день

TYPE_REGISTRY: dict[str, TypeSpec] = { ... }   # ровно 25 записей из §3.3
```

Добавление нового типа Health Connect = добавление одной строки сюда. Ни один другой модуль
не должен содержать `if data_type == "steps"`. `normalize.py` работает только через реестр.
Единственное допустимое исключение — сон (`kind=SESSION`) в отдельной ветке `normalize.py`.

---

## 7. Логика агрегации (`aggregate/`, чистые функции)

### 7.1 `timewindow.py`

```python
def local_date_of(instant_utc: str, tz: str, day_boundary_hour: int) -> str:
    """UTC-момент -> локальная дата 'YYYY-MM-DD' с учётом границы суток.

    day_boundary_hour=4 означает: 02:30 локального времени относится к ПРЕДЫДУЩЕЙ дате.
    """

def day_bounds_utc(local_date: str, tz: str, day_boundary_hour: int) -> tuple[str, str]:
    """Локальная дата -> [начало, конец) в UTC."""
```

Дневные сводки считаются **только** по локальной дате. Группировка по UTC-дате запрещена.

### 7.2 `dedup.py` — против двойного счёта

Приложение читает сырые записи Health Connect и не отдаёт источник. Если у пользователя пишут
шаги и телефон, и часы — записи дублируются и наивная сумма завышает результат в 1.5–3 раза.

```python
def sum_non_overlapping(events: list[Event]) -> float:
    """Сумма значений кумулятивного типа с подавлением перекрытий.

    Алгоритм:
    1. Отсортировать по start_utc.
    2. Идти по списку, накапливая группы пересекающихся по времени интервалов.
    3. Из каждой группы взять МАКСИМУМ значения, не сумму.
    4. Сложить максимумы групп.
    """
```

Применяется ко всем типам с `cumulative=True`. Для некумулятивных — обычные min/avg/max.
В `coverage_json` соответствующего дня выставить признак `"overlap_detected": true`,
если хотя бы одна группа содержала больше одного интервала.

### 7.3 `rollup.py`

```python
def compute_rollup(local_date: str, events: list[Event],
                   stages: list[SleepStage]) -> DailyRollup:
```

Правила:

- `steps` / `distance_m` / `active_kcal` / `total_kcal` — через `sum_non_overlapping`.
- `active_kcal` и `total_kcal` — **раздельные поля**. Никогда не складывать одно с другим
  и не подставлять одно вместо другого. `total` включает основной обмен, `active` — нет.
- `sleep_in_bed_s` = сумма `duration_seconds` сессий, отнесённых к этой дате
  (сессия относится к дате по своему **времени окончания**).
- `sleep_asleep_s` = `sleep_in_bed_s` − сумма стадий `awake` и `awake_in_bed`.
  Обе величины отдаются наружу раздельно и подписываются.
- `hr_avg` / `hr_min` / `hr_max` — по событиям `heart_rate`.
- `hr_resting` — **только** из `resting_heart_rate`. Не вычислять из семплов пульса.
- `weight_kg` — последнее измерение за день.
- `coverage_json` — по каждой метрике одно из: `"ok"` (данные есть),
  `"absent"` (событий нет вовсе), `"partial"` (есть разрыв больше 6 часов внутри дня).

Пересчёт запускается после каждого успешного ингеста **только для затронутых локальных дат**,
не для всей истории.

---

## 8. HTTP API

### 8.1 `GET /health`
Без авторизации. `200 {"status":"ok","version":"<app version>","db":"ok"}`.

### 8.2 `POST /ingest/health` и `POST /ingest/screentime`

Порядок обработки строго такой:

1. Прочитать **сырое тело** запроса.
2. Проверить `Authorization: Bearer` → при несовпадении `401`.
3. Проверить `X-Signature` HMAC от сырого тела → при несовпадении `401`,
   **но строку в `raw_payloads` всё равно записать** с `signature_valid = 0`.
   Если `WEBHOOK_HMAC_SECRET` не задан — проверку пропустить, писать `signature_valid = 1`.
4. Записать в `raw_payloads` **до** любой валидации. Даже нераспарсенное тело.
5. Распарсить и нормализовать. Ошибка отдельной записи не отменяет остальные:
   собирать список ошибок, а не падать.
6. Записать `events`, `sleep_stages`, `sync_diagnostics`.
7. Пересчитать `daily_rollups` для затронутых дат.
8. Ответить.

Коды ответа — критично, от них зависит поведение курсора в приложении:

| Ситуация | Код | Почему |
|---|---|---|
| Всё записано | `200` | приложение двигает курсор |
| Часть записей невалидна, остальное записано | `200` + `errors[]` в теле | лучше принять частично, чем потерять всё |
| Плохой токен или подпись | `401` | приложение не ретраит, это верно |
| Тело не JSON | `400` | ретрай не поможет |
| Ошибка записи в БД | `500` | приложение ретратит, курсор **не двигается** |

**Никогда не отдавай `200`, если запись в БД не удалась.** Приложение подвинет курсор
и эти данные не придут больше никогда.

Тело успешного ответа:
```json
{"payload_id": 42, "received": 1247, "stored": 1203, "duplicates": 44,
 "errors": [], "rollups_updated": ["2026-08-08", "2026-08-09"]}
```

### 8.3 `POST /ingest/echo` — эхо-режим

Принимает что угодно, ничего не валидирует, пишет в `raw_payloads` с `source='echo'`,
всегда отвечает `200 {"ok": true, "payload_id": N, "byte_size": N}`.
Нужен, чтобы направить на него приложение и посмотреть сырой формат, ничего не сломав.

### 8.4 `GET /debug` — инспектор

Авторизация: `?token=<MCP_TOKEN>`. При неверном токене — `401` без тела.
Отдаёт `api/debug.html`, JS внутри дёргает JSON-эндпоинты с тем же токеном.

JSON-эндпоинты:

| Путь | Отдаёт |
|---|---|
| `GET /debug/api/payloads?limit=20` | список последних payload: id, время, source, app_version, размер, `signature_valid`, число событий |
| `GET /debug/api/payload/{id}` | сырое тело целиком + результат разбора |
| `GET /debug/api/diagnostics?payload_id=` | таблица `_diagnostics` по типам |
| `GET /debug/api/coverage?days=30` | матрица день × тип: число событий |
| `GET /debug/api/rollup/{local_date}` | сводка за день + сырые суммы **до** дедупликации, рядом |
| `GET /debug/api/stats` | всего событий по типам, диапазон дат, время последнего ингеста |

Панели на странице `/debug` (в этом порядке сверху вниз):

1. **Статус.** Время последнего ингеста, `app_version`, всего событий, диапазон дат.
   Красная плашка, если последний ингест был больше 3 часов назад.
2. **Последние payload.** Таблица; клик по строке разворачивает сырой JSON с подсветкой.
3. **Диагностика приложения.** Разобранный `_diagnostics` последнего health-payload:
   по каждому типу `permission_granted`, `page_count`, `raw`/`filtered`, `min_time`/`max_time`,
   `last_sync`, `error`. Подсветить красным строки с `permission_granted=false` или непустым `error`.
4. **Покрытие.** Сетка 30 дней × типы. Ячейка = число событий; пусто — красным.
   Это главный инструмент поиска «непонятных отрезков».
5. **Сверка агрегации.** Для выбранного дня две колонки рядом: наивная сумма всех записей
   и результат `sum_non_overlapping`. Их отношение — прямая мера двойного счёта источников.
6. **Эхо-лог.** Последние тела, пришедшие на `/ingest/echo`.

Требования к странице: один файл, ванильный JS, без CDN и внешних запросов, работает
с телефона (адаптивная вёрстка), автообновление раз в 60 секунд.

---

## 9. MCP-слой

### 9.1 Инструменты

| Инструмент | Параметры | Возвращает |
|---|---|---|
| `daily_summary` | `date_from`, `date_to`, `metrics[]` (опц.) | `{"days":[...], "coverage":{...}, "caveats":[...]}` |
| `metric_series` | `metric`, `date_from`, `date_to`, `bucket` (`day`/`week`) | `{"points":[...], "unit":"...", "caveats":[...]}` |
| `sleep_detail` | `date_from`, `date_to` | сессии, стадии, `time_in_bed_s` и `time_asleep_s` раздельно |
| `get_sync_health` | `days` | по типу: последнее событие, покрытие, пропуски, последняя ошибка из `_diagnostics` |
| `inspect_last_payload` | — | метаданные последнего payload и его `_diagnostics` |

### 9.2 Обязательные требования

- Корень `outputSchema` каждого инструмента — **`object`**. Голый массив MCP не примет.
- В каждом ответе поле `caveats: string[]`. Заполнять человекочитаемыми предупреждениями:
  `"за 2026-08-03 данных нет"`, `"обнаружены перекрывающиеся записи шагов, возможен двойной счёт"`,
  `"resting_heart_rate отсутствует за весь период"`.
  Без этого модель уверенно выдумает недостающее.
- Жёсткий лимит: не более 400 объектов в ответе. При превышении — укрупнить бакет
  и добавить это в `caveats`.
- Сырые события наружу по умолчанию **не отдаются**. Инструменты работают с `daily_rollups`.
- `daily_summary` не должен возвращать нули за дни без данных. Только `null` + пометка
  в `coverage` и `caveats`.

### 9.3 Ловушки MCP SDK (проверены на живом сервере, не переоткрывай их заново)

1. `TransportSecuritySettings(allowed_hosts=...)` заполняется из переменной `ALLOWED_HOSTS`
   (список через запятую, включая вариант с `:*`). **Не `["*"]`** — SDK сравнивает строки точно,
   иначе каждый запрос получит `421 Misdirected Request`. Домен в коде не хардкодить.
2. При монтировании через `Mount("/mcp", app=mcp.streamable_http_app())` встроенный lifespan
   не работает. Нужно вручную:
   ```python
   @asynccontextmanager
   async def lifespan(app):
       async with mcp.session_manager.run():
           yield
   ```
   Без этого первый же запрос повиснет без внятной ошибки.
3. **`--workers 1`, всегда.** FastMCP держит состояние сессий в памяти процесса. С несколькими
   воркерами `initialize` попадёт в один процесс, а `tools/call` — в другой, и клиент получит
   `404 session not found`. Проявляется как «коннектор подключён, но чат говорит недоступен».
4. Использовать `streamable_http_app()`, не `sse_app()` — SSE-транспорт устарел.
5. Caddy обязан отдавать `/mcp` с `flush_interval -1`, иначе стриминг зависает до закрытия соединения.

---

## 10. Конфигурация

`.env.example`:

```
# ---- Required. The server refuses to start without these. ----
# Generate with: openssl rand -hex 32
DEVICE_TOKEN=
MCP_TOKEN=

# Public hostname(s) this server is reached at. Comma-separated.
# The MCP SDK does exact string matching — wildcards do NOT work.
# Include the ":*" variant to allow non-default ports.
ALLOWED_HOSTS=health.example.com,health.example.com:*

# ---- Optional ----
# Shared secret for the X-Signature HMAC header. Leave empty to disable verification.
WEBHOOK_HMAC_SECRET=
DATABASE_PATH=/data/healthhub.sqlite3
# IANA timezone used for all daily aggregation.
TIMEZONE=UTC
# Hour (0-23) at which the local day starts. Set to 4 if you want
# post-midnight activity to count towards the previous day.
DAY_BOUNDARY_HOUR=0
LOG_LEVEL=INFO
PORT=8000
```

`config.py`: обязательные читать через `os.environ["..."]` — при отсутствии падать с `KeyError`
на старте с внятным английским сообщением, какой переменной не хватает и как её сгенерировать.
Опциональные — через `.get()` с умолчанием. **Никаких умолчаний для секретов и для `ALLOWED_HOSTS`.**

Умолчания подобраны так, чтобы были нейтральны для постороннего: `TIMEZONE=UTC`,
`DAY_BOUNDARY_HOUR=0`, домен-плейсхолдер `example.com`. Личные значения живут только в `.env`.

`Caddyfile.example`:
```
# Copy to Caddyfile and replace the domain.
health.example.com {
    handle /mcp* {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1   # required: streaming hangs without it
        }
    }
    handle {
        reverse_proxy 127.0.0.1:8000
    }
}
```

---

## 11. Фазы работы

Каждая фаза заканчивается остановкой и показом вывода команды приёмки.

### Фаза 1 — Каркас

Создать: структуру каталогов, `config.py`, `logging_setup.py`, `db/schema.sql`,
`db/connection.py`, `db/migrations.py`, `api/routes_health.py`, `app.py`, `__main__.py`,
`Dockerfile`, `docker-compose.yml`, `Caddyfile.example`, `requirements.txt`, `.env.example`,
`.gitignore`, `LICENSE` (MIT), заготовку `README.md`.
MCP и ингест пока не подключать.

`.gitignore` и `LICENSE` — именно в этой фазе, до первого коммита с данными.

**Приёмка:**
```bash
docker compose up -d --build
curl -s localhost:8001/health        # -> {"status":"ok",...}
sqlite3 data/healthhub.sqlite3 ".tables"   # -> все таблицы из §5.1
grep -ri "example\.com\|192\.0\.2\.1" --exclude-dir=.git . ; echo "exit=$?"  # ничего не найдено
git status --porcelain | grep -E "\.env$|\.sqlite3$" ; echo "exit=$?"          # ничего не найдено
```

### Фаза 2 — Контракт и нормализация (чистый код + тесты)

Создать: `contract/kinds.py`, `contract/registry.py` (все 25 типов), `contract/payload.py`,
`ingest/identity.py`, `ingest/normalize.py`.
Создать фикстуры `tests/fixtures/payload_health_full.json` (все 25 типов, минимум по 2 записи)
и `payload_health_sparse.json` (только `steps` и `sleep`, остальные ключи отсутствуют).
Тесты: `test_registry.py`, `test_identity.py`, `test_normalize.py`.

Тесты обязаны покрывать:
- все 25 типов дают события;
- отсутствующие ключи не вызывают исключений;
- `heart_rate` из 100 семплов даёт 100 событий;
- у сна `start_utc == end_utc − duration_seconds`;
- одна и та же запись дважды даёт одинаковый `event_id`;
- запись с изменённым значением даёт другой `event_id`.

**Приёмка:** `pytest tests/ -v` — зелёный, покрытие `normalize.py` не ниже 90 %.

### Фаза 3 — Приём данных

Создать: `api/auth.py`, `ingest/raw_store.py`, `ingest/diagnostics.py`, `ingest/writer.py`,
`api/routes_ingest.py`. Подключить к `app.py`.
Тесты: `test_auth.py`, `test_routes_ingest.py`.

**Приёмка:**
```bash
curl -X POST localhost:8001/ingest/health \
  -H "Authorization: Bearer $DEVICE_TOKEN" -H "Content-Type: application/json" \
  --data-binary @tests/fixtures/payload_health_full.json
# повторить ту же команду ещё раз
sqlite3 data/healthhub.sqlite3 "SELECT count(*) FROM events;"
```
Число событий после второго запроса **не изменилось** — идемпотентность работает.
Плохой токен даёт `401`, но строка в `raw_payloads` появилась с `signature_valid=0`.

### Фаза 4 — Инспектор `/debug`

Создать: `api/routes_debug.py`, `api/debug.html`. Тест `test_routes_debug.py`.

**Приёмка:** открыть `http://localhost:8001/debug?token=$MCP_TOKEN` в браузере,
все шесть панелей §8.4 отрисованы и наполнены данными из загруженной фикстуры.
Показать скриншот или вывод `curl` по каждому из шести JSON-эндпоинтов.

### Фаза 5 — Агрегация

Создать: `aggregate/timewindow.py`, `aggregate/dedup.py`, `aggregate/rollup.py`,
`rollup_service.py`. Подключить пересчёт к ингесту.
Тесты: `test_timewindow.py`, `test_dedup.py`, `test_rollup.py`.

Тесты обязаны покрывать:
- `DAY_BOUNDARY_HOUR=4`: событие в 02:30 локального времени попадает в предыдущую дату;
- два полностью перекрывающихся интервала по 5000 шагов дают 5000, а не 10000;
- два непересекающихся интервала по 5000 дают 10000;
- сон 8 часов с двумя часами `awake` даёт `in_bed=28800`, `asleep=21600`;
- день без событий даёт `coverage: absent`, а не нули.

**Приёмка:** `pytest tests/ -v` зелёный + `GET /debug/api/rollup/<дата>` показывает
обе колонки — наивную сумму и дедуплицированную.

### Фаза 6 — MCP

Создать: `mcp_server/server.py`, `schemas.py`, `tools_summary.py`, `tools_sleep.py`,
`tools_ops.py`. Смонтировать в `app.py`. Тест `test_mcp_tools.py`.

**Приёмка:** локально
```bash
curl -X POST localhost:8001/mcp -H "Authorization: Bearer $MCP_TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
```
возвращает результат; `tools/list` показывает все пять инструментов; `daily_summary`
за день с данными возвращает непустой ответ с непустым `caveats`.

### Фаза 7 — Деплой

Развернуть на боевом домене. Домен берётся из `.env` и `Caddyfile`, в коде его быть не должно.

1. Сохранить копию старой БД: `cp /opt/health-hub/data/health.db ~/health-db-archive-$(date +%F).db`.
   Не мигрировать, но и не удалять.
2. Остановить старый сервис.
3. `cp Caddyfile.example Caddyfile`, подставить реальный домен, развернуть через docker-compose.
4. `scripts/verify_deploy.sh <base-url>` — принимает URL параметром, ничего не хардкодит.
   Проверяет: `/health`, `401` без токена, MCP `initialize`, `tools/list`, доступность `/debug`.
5. Обновить URL вебхуков в приложении: `/ingest/health` и `/ingest/screentime`.
   В приложении должен быть настроен **ровно один** webhook URL на категорию.

**Приёмка:** `./scripts/verify_deploy.sh https://<домен>` зелёный; после реального синка
с телефона на `/debug` видны свежий payload и заполненная панель диагностики.

### Фаза 8 — Публикация на GitHub

Цель: посторонний человек с Android-телефоном и VPS поднимает связку, не задавая вопросов.

**8.1 `README.md`** — по-английски, в этом порядке:

- Одно предложение: что это и зачем.
- Схема потока данных (ASCII, как в §0).
- «Requirements»: Android 14+, приложение-компаньон v1.4.0+, любой хост с Docker, домен с TLS.
- «Quick start» — ровно три команды:
  ```bash
  cp .env.example .env && openssl rand -hex 32   # заполнить DEVICE_TOKEN и MCP_TOKEN
  docker compose up -d --build
  curl -s localhost:8000/health
  ```
- «Connect your phone» — ссылка на `docs/ANDROID.md`.
- «Connect an LLM» — готовый блок конфига для Claude Desktop с плейсхолдерами.
- «What you get» — список MCP-инструментов одной таблицей.
- Скриншот страницы `/debug` (единственная картинка, кладётся в `docs/`).
- Секция **«Known limitations»**, честная и обязательная:
  - у записей нет идентификаторов и источника — дедупликация делается контент-хешем;
  - приложение читает сырые записи, а не агрегаты Health Connect, поэтому при нескольких
    источниках данных кумулятивные метрики могут быть завышены; сервер подавляет перекрытия,
    но это эвристика — смотрите панель сверки на `/debug`;
  - курсор синхронизации в приложении двигается только вперёд, поэтому записи, попавшие
    в Health Connect задним числом, могут не дойти;
  - Health Connect не отдаёт данные старше 30 дней без отдельного разрешения — сервер является
    единственным архивом, бэкапьте `data/`.
- Лицензия и ссылка на репозиторий приложения-компаньона.

**8.2 `docs/`**

- `SETUP.md` — установка с нуля: VPS, Docker, Caddy, TLS, генерация токенов, systemd (опционально).
- `ANDROID.md` — установка компаньона, выдача разрешений Health Connect, настройка вебхуков,
  заголовков, HMAC-секрета. **Отдельно предупредить: ровно один webhook URL на категорию**,
  с объяснением почему (курсор двигается при успехе хотя бы одного адресата).
- `API.md` — полный контракт ингеста (§3) и описание всех MCP-инструментов с примерами
  запроса и ответа.
- `TROUBLESHOOTING.md` — сценарии: «нет данных вообще», «данные отстают на дни»,
  «цифры не совпадают с телефоном», «MCP-клиент пишет unavailable». По каждому — что смотреть
  на `/debug` и какой вывод из этого следует.

**8.3 `CONTRIBUTING.md`** — как запустить тесты, требования к PR, правило «один файл — одна
ответственность», как добавить новый тип Health Connect (одна строка в `contract/registry.py`
плюс тест).

**8.4 `SECURITY.md`** — модель угроз в двух абзацах: сервер хранит персональные данные о здоровье,
обязателен HTTPS, токены генерируются пользователем, HMAC защищает от подделки отправителя,
`/debug` отдаёт сырые данные и защищён тем же токеном. Куда сообщать об уязвимостях.

**8.5 CI** — `.github/workflows/ci.yml`: на `push` и `pull_request` поднимает Python 3.12,
ставит зависимости, гоняет `ruff check` и `pytest`. Бейдж статуса в README.

**8.6 Гигиена репозитория**

- `git log` не содержит ни одного коммита с `.env`, `*.sqlite3`, реальными токенами или
  персональными данными. Если такой коммит был — историю переписать до публикации.
- Проверить: `grep -rniE "example\.com|192\.0\.2\.1" --exclude-dir=.git .`
- `tests/fixtures/payload_real.json` отсутствует в индексе.

**Приёмка Фазы 8 — тест «чистой машины».** В пустом каталоге:

```bash
git clone <repo> hh && cd hh
cp .env.example .env
# заполнить два токена и ALLOWED_HOSTS
docker compose up -d --build
curl -s localhost:8000/health
curl -X POST localhost:8000/ingest/health \
  -H "Authorization: Bearer $DEVICE_TOKEN" -H "Content-Type: application/json" \
  --data-binary @tests/fixtures/payload_health_full.json
```

Всё должно заработать без единой правки исходников. Показать вывод целиком.
Дополнительно: `git grep -iE "example\.com|192\.0\.2\.1"` пуст, CI зелёный.

---

## 12. Чего делать нельзя

- Придумывать поля, которых нет в §3.
- Класть бизнес-логику в `api/` или `mcp_server/` — там только транспорт.
- Импортировать `aiosqlite` в `contract/`, `aggregate/`, `ingest/normalize.py`, `ingest/identity.py`.
- Писать `if data_type == "..."` где-либо, кроме ветки сна в `normalize.py`.
- Отдавать `200`, если запись в БД не удалась.
- Отдавать LLM сырые массивы событий по умолчанию.
- Отдавать нули за дни без данных.
- Запускать uvicorn с числом воркеров больше одного.
- Ставить `allowed_hosts=["*"]`.
- Удалять что-либо из `raw_payloads`.
- Добавлять зависимости сверх списка §2.
- Переходить к следующей фазе без показанной приёмки предыдущей.
- Хардкодить домен, токен, часовой пояс или путь к БД в исходниках.
- Коммитить `.env`, файлы БД, реальные токены или `tests/fixtures/payload_real.json`.
- Писать код, комментарии, README или тексты ошибок по-русски.
