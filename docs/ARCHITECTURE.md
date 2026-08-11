# Health Hub MCP Server — Architecture & Contracts

> Для переписывания Android companion app. Описывает серверную сторону: что принимает, как хранит, что отдаёт.

## Обзор

```
Android 1.4.0 ──POST /ingest/health──────▶ ┌──────────────────┐
              ──POST /ingest/screentime──▶ │   Health Hub     │──MCP──▶ LLM
                                            │   SQLite + WAL   │
                    браузер ──GET /debug──▶ └──────────────────┘
```

Сервер принимает сырые данные Health Connect, нормализует, дедуплицирует, строит дневные сводки и отдаёт их LLM-клиентам по MCP. Не требует миграций — SQLite в WAL-режиме, идемпотентная запись.

---

## 1. Входной контракт — что шлёт приложение

### Health payload: `POST /ingest/health`

**Заголовки:**
```
Authorization: Bearer <DEVICE_TOKEN>
X-Signature: sha256=<hex>      (опционально, если сервер требует HMAC)
Content-Type: application/json; charset=utf-8
```

**Тело — верхний уровень (обязательные поля):**
```json
{
  "timestamp": "2026-08-10T12:00:00Z",
  "app_version": "1.4.0",
  "source": "health_connect"
}
```

**Ключи типов данных — каждый опционален (отсутствует при 0 записей):**
```json
{
  "steps":     [{"count": 1234, "start_time": "...", "end_time": "..."}],
  "heart_rate":[{"bpm": 72, "time": "..."}],
  "sleep":     [{"session_end_time": "...", "duration_seconds": 28800,
                  "stages": [{"stage": "deep", "start_time": "...",
                              "end_time": "...", "duration_seconds": 7200}]}]
}
```

**Полный список 25 типов:**

| JSON-ключ | Вид | Поле значения | Кумулятивный |
|---|---|---|---|
| `steps` | INTERVAL | `count` (int) | да |
| `distance` | INTERVAL | `meters` (float) | да |
| `active_calories` | INTERVAL | `calories` (float) | да |
| `total_calories` | INTERVAL | `calories` (float) | да |
| `exercise` | INTERVAL | `type` (str), `duration_seconds` (int) | нет |
| `hydration` | INTERVAL | `liters` (float) | да |
| `nutrition` | INTERVAL | `calories`, `protein_grams`, `carbs_grams`, `fat_grams` — опциональны | да |
| `mindfulness` | INTERVAL | `title` (str/null), `duration_seconds` | нет |
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

**Виды записей (RecordKind):**
- `INTERVAL` — есть `start_time` / `end_time`, значение суммируется (шаги, дистанция, калории)
- `INSTANT` — только `time`, точечные замеры (пульс, вес, SpO2)
- `SESSION` — особый разбор (только сон — со стадиями)

**Блок `_diagnostics`:**
```json
{
  "_diagnostics": {
    "steps": {
      "permission_granted": true, "page_count": 3,
      "raw_record_count": 2841, "filtered_record_count": 1000,
      "min_time": "...", "max_time": "...",
      "last_sync": "...", "error": null
    }
  }
}
```
По одной записи на каждый тип. `permission_granted` — есть ли разрешение. `raw_record_count` — всего записей в Health Connect. `filtered_record_count` — сколько прочитано за этот вызов. `last_sync` — время последнего чтения (курсор).

**Важные правила:**
- Массивы с 0 записей **отсутствуют в JSON целиком** — не `[]`, не `null`, просто ключа нет
- Поле `device` **отсутствует** в health-payload (только в screen-time)
- У записей **нет идентификаторов** из Health Connect — дедупликация контент-хешем
- У сна **нет `start_time`** — вычисляется: `start = session_end_time − duration_seconds`

### Screen time: `POST /ingest/screentime`

```json
{
  "timestamp": "...", "app_version": "1.4.0",
  "device": "Google Pixel 8",
  "source": "screen_time",
  "screen_time": [{
    "date": "2026-08-09",
    "total_screen_time_minutes": 180,
    "apps": [{"package": "com.example", "name": "App", "minutes": 45, "last_used": "..."}]
  }]
}
```

Приходят всегда последние 7 дней целиком → пишется через upsert по `date`, список приложений заменяется полностью.

### Echo: `POST /ingest/echo`

Принимает любое тело. Без аутентификации, без парсинга. Всегда `200`. Нужен для отладки сырого формата.

---

## 2. Ответы сервера

### Успех: `200`

```json
{
  "payload_id": 42,
  "received": 801,        // сколько записей прислано
  "stored": 798,           // сколько новых (не дубликатов)
  "duplicates": 3,         // сколько дубликатов (уже были)
  "errors": [],            // ошибки валидации отдельных записей
  "rollups_updated": ["2026-08-10"]  // даты с пересчитанными сводками
}
```

**Критично:** `200` означает "всё записано, курсор можно двигать". Приложение **не ретраит** `200`, курсор двигается вперёд, и эти данные больше никогда не придут. Если запись в БД не удалась — сервер **никогда не отдаёт `200`**.

### Ошибки:

| Ситуация | Код | Тело | Ретрай? |
|----------|-----|------|---------|
| Неверный токен | `401` | `{"error":"unauthorized"}` | нет |
| Неверная подпись | `401` | `{"error":"invalid_signature","payload_id":N}` | нет |
| Тело не JSON | `400` | `{"error":"invalid_json","payload_id":N}` | нет |
| Неверный конверт | `400` | `{"error":"invalid_envelope","detail":"..."}` | нет |
| Ошибка записи в БД | `500` | `{"error":"db_error","detail":"..."}` | **да** |
| Внутренняя ошибка | `500` | `{"error":"internal_error","detail":"..."}` | **да** |

Ретрай: 3 попытки, экспоненциальный backoff, таймаут 10с. `408`, `429`, `5xx`, сетевые ошибки — ретраятся. `4xx` — постоянная ошибка, не ретраится.

### Частичный успех

Ошибка отдельной записи не отменяет остальные. Сервер нормализует с per-record error collection:

```json
{
  "payload_id": 42,
  "received": 801,
  "stored": 798,
  "duplicates": 3,
  "errors": [
    {"type": "steps", "index": 5, "error": "missing start_time"}
  ],
  "rollups_updated": ["2026-08-10"]
}
```

---

## 3. Пайплайн обработки (порядок строгий)

1. **Прочитать сырое тело** — `request.body()` как bytes
2. **Проверить токен** — `Authorization: Bearer <DEVICE_TOKEN>`. Неверный → `401`, без записи в БД
3. **Проверить подпись** — `X-Signature: sha256=<hex>`, HMAC-SHA256 от сырого тела. Флаг `signature_valid` вычисляется
4. **Записать L0** — `raw_payloads` (append-only, **до любой валидации**). Даже нераспарсенное тело. `signature_valid` флаг из шага 3
5. **Проверить подпись** — если невалидна → `401`, но строка в raw_payloads уже есть (для форензики)
6. **Распарсить JSON** — не JSON → `400`, parse_error в raw_payloads
7. **Валидировать конверт** — timestamp, app_version, source обязательны. Нет → `400`
8. **Нормализовать** — чистые функции, per-record ошибки в `errors[]`, не падают
9. **Записать L1** — events (ON CONFLICT DO NOTHING), sleep_stages, sync_diagnostics, daily_rollups — **в одной транзакции**
10. **Ответить** — `200` только после успешного коммита всей транзакции

---

## 4. Слои данных

### L0 — raw_payloads (append-only, никогда не удаляется)
```sql
raw_payloads(id, received_at, source, app_version, device, payload_timestamp,
             signature_valid, byte_size, body_json, parse_error)
```

### L0b — sync_diagnostics (диагностика приложения)
```sql
sync_diagnostics(payload_id, data_type, permission_granted, page_count,
                 raw_record_count, filtered_record_count, min_time, max_time,
                 last_sync, error)
```

### L1 — events + sleep_stages (нормализованные, идемпотентные)
```sql
events(event_id TEXT PK,     -- SHA-256 контент-хеш → идемпотентность
       data_type, start_utc, end_utc, value_num, value_json, origin,
       payload_id, first_seen_at)

sleep_stages(session_event_id, stage, start_utc, end_utc, seconds)
```

### L2 — daily_rollups (дневные сводки)
```sql
daily_rollups(local_date TEXT PK, steps, distance_m, active_kcal, total_kcal,
              sleep_in_bed_s, sleep_asleep_s, hr_avg, hr_min, hr_max,
              hr_resting, weight_kg, coverage_json, computed_at)
```

Сводки считаются по **локальной дате** с учётом `TIMEZONE` и `DAY_BOUNDARY_HOUR`. Сон атрибутируется по `end_utc`.

---

## 5. Агрегация и дедупликация

### Дедупликация (dedup.py)

Проблема: если шаги пишут и телефон, и часы — записи дублируются. Наивная сумма завышает в 1.5–3×.

Алгоритм `sum_non_overlapping`:
1. Сортировать по `start_utc`
2. Группировать пересекающиеся по времени интервалы
3. Из каждой группы взять **максимум** значения (не сумму)
4. Сложить максимумы групп

Применяется к кумулятивным типам: steps, distance, active_calories, total_calories, hydration, nutrition.

### Дневная сводка (rollup.py)

- `steps` / `distance_m` / `active_kcal` / `total_kcal` — через `sum_non_overlapping`
- `active_kcal` и `total_kcal` — **раздельные поля**, никогда не смешиваются
- `sleep_in_bed_s` — сумма `duration_seconds` сессий
- `sleep_asleep_s` — `in_bed` минус стадии `awake` + `awake_in_bed`
- `hr_avg` / `hr_min` / `hr_max` — из `heart_rate`
- `hr_resting` — **только** из `resting_heart_rate`, не вычисляется из семплов пульса
- `weight_kg` — последнее измерение за день
- `coverage_json` — по каждой метрике: `ok`, `absent`, или `partial` (разрыв >6ч)

---

## 6. MCP-инструменты для LLM

**5 инструментов, все через `POST /mcp` с `Authorization: Bearer <MCP_TOKEN>`:**

| Инструмент | Параметры | Что возвращает |
|---|---|---|
| `daily_summary` | `date_from`, `date_to`, `metrics[]?` | Дневные сводки: шаги, калории, сон, пульс, вес + coverage |
| `metric_series` | `metric`, `date_from`, `date_to`, `bucket?` | Временной ряд одной метрики (день/неделя) |
| `sleep_detail` | `date_from`, `date_to` | Сессии сна со стадиями (deep/REM/light/awake) |
| `get_sync_health` | `days?` | Здоровье синхронизации: coverage, пропуски, ошибки |
| `inspect_last_payload` | — | Метаданные + диагностика последнего payload |

**Обязательные требования:**
- Корень ответа — `object` (не массив)
- Поле `caveats: string[]` в каждом ответе — человекочитаемые предупреждения
- Лимит 400 объектов в ответе
- `NULL` → не возвращается (не `0`!)

---

## 7. Конфигурация

| Переменная | Обязательна | Умолчание | Назначение |
|---|---|---|---|
| `DEVICE_TOKEN` | да | — | Токен для /ingest/* |
| `MCP_TOKEN` | да | — | Токен для /mcp и /debug |
| `ALLOWED_HOSTS` | да | — | Список доменов для MCP transport security |
| `WEBHOOK_HMAC_SECRET` | нет | `""` | Секрет для X-Signature (пустой = проверка отключена) |
| `DATABASE_PATH` | нет | `data/healthhub.sqlite3` | Путь к БД |
| `TIMEZONE` | нет | `UTC` | Часовой пояс для агрегации |
| `DAY_BOUNDARY_HOUR` | нет | `0` | Час начала локальных суток (4 = 04:00) |
| `LOG_LEVEL` | нет | `INFO` | Уровень логирования |
| `PORT` | нет | `8000` | Порт uvicorn |

---

## 8. Что улучшить в Android-приложении

На основе реальной эксплуатации (30 payloads, 26K событий, 9 типов данных):

1. **Пейджинг неэффективен.** 200 записей за синхронизацию — для heart_rate (25K записей) нужно 125 ручных тапов. Нужен либо авто-ретрей (ретраить 5xx/408/429 с backoff), либо batch-режим: читать несколько страниц за один POST.

2. **Курсоры типов независимы.** Каждый тип шлётся отдельно — это 25 параллельных курсоров. Нужно либо объединить в один payload (все типы, у которых есть новые данные), либо пакетная отправка: последовательно читать все типы за один тап.

3. **active_calories = 0 записей.** Health Connect не хранит active_calories на многих устройствах — только total_calories. Не надо слать пустой ключ (по спеке он отсутствует, но диагностика всё равно показывает raw=0).

4. **Нет идентификаторов записей.** Текущая дедупликация — контент-хеш. Если приложение сможет добавить `client_record_id` к каждой записи, дедупликация станет точнее и быстрее.

5. **`last_sync` в диагностике.** Приложение уже шлёт `_diagnostics.last_sync` — это хорошо. Но для автоматического ретрея можно добавить `cursor_position` (сколько всего прочитано / сколько осталось).

6. **Один webhook URL на категорию.** Сейчас приложение требует ровно один URL для health и один для screentime. Если добавить fallback URL или health-check перед отправкой — надёжнее.

7. **Размер пакета.** Сейчас 200 записей × ~200 байт = ~40KB на POST. Можно увеличить до 1000 без проблем — сервер принимает до нескольких MB.

8. **Пробелы в данных (реальные).** После синхронизации двумя клиентами (companion app + HC Webhook, 155K событий, 34 типа):
   - **Нет данных:** вес, рост, % жира, температура тела, bone_mass, lean_body_mass, body_water_mass, basal_metabolic_rate, blood_pressure, blood_glucose, vo2_max, hydration, nutrition, mindfulness — Health Connect не хранит вообще
   - **active_calories:** 0 записей в Health Connect. Другие приложения показывают «активные калории» потому что считают сами, не читают из Health Connect. Серверу нужно либо принимать эти данные из другого источника, либо вычислять: `total_calories − оценочный BMR`
   - **BMR (basal_metabolic_rate):** нет данных. Для расчёта активных калорий нужно либо вводить вручную (вес/рост/пол/возраст → формула Миффлина), либо интегрировать с внешним API
   - **HC Webhook vs companion app:** HC Webhook выгружает всё одним POST (20MB, 128K событий), companion app — по 200 записей за раз. Разница в покрытии: ×5 по heart_rate, ×17 по total_calories, +3 недели исторических данных
