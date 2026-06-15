# Hermes API Server (OpenAI-сумісний HTTP-шлюз)

> Джерело істини: `gateway/platforms/api_server.py`. Цей документ описує поведінку,
> закодовану в адаптері `APIServerAdapter`, та спосіб його запуску через gateway.

## 1. Що це таке

API Server — це **один із платформ-адаптерів** Hermes gateway, який піднімає
HTTP-сервер на `aiohttp` і надає **OpenAI-сумісний REST API** до того самого
`AIAgent`, що обслуговує месенджери (Telegram, Slack тощо).

Архітектурно він рівноправний з іншими адаптерами: усі вони — нащадки
`BasePlatformAdapter` під керуванням одного `GatewayRunner`. Тобто HTTP-клієнти і
чат-платформи проходять крізь однакову логіку сесій, інструментів та агента.

Будь-який OpenAI-сумісний фронтенд (Open WebUI, LobeChat, LibreChat, AnythingLLM,
NextChat, ChatBox тощо) підключається, вказавши `base_url` = `http://localhost:8642/v1`.

- **Тип**: HTTP/REST сервер (не вебхук-приймач і не проксі).
- **Виконання інструментів**: серверне — інструменти виконуються на хості API-сервера
  (`runtime.tool_execution = "server"`, `split_runtime = false`).
- **Реалізація**: `aiohttp.web` (`web.AppRunner` + `web.TCPSite`).

---

## 2. Швидкий старт

### 2.1. Увімкнення

API Server вимкнено за замовчуванням (`enabled: false`). Увімкнути можна двома способами.

**Через `~/.hermes/config.yaml`:**

```yaml
platforms:
  api_server:
    enabled: true
    host: 127.0.0.1        # за замовчуванням 127.0.0.1
    port: 8642             # за замовчуванням 8642
    key: ""               # Bearer-ключ; порожній = без автентифікації (тільки локально)
    cors_origins: ""      # CSV-список дозволених origin або "*"
    model_name: ""        # назва моделі для /v1/models (опційно)
```

**Через змінні оточення:**

| Змінна | За замовчуванням | Призначення |
|---|---|---|
| `API_SERVER_HOST` | `127.0.0.1` | Адреса прив'язки |
| `API_SERVER_PORT` | `8642` | Порт (некоректне значення → fallback на 8642) |
| `API_SERVER_KEY` | *(порожньо)* | Bearer-ключ для автентифікації |
| `API_SERVER_CORS_ORIGINS` | *(порожньо)* | CSV дозволених браузерних origin або `*` |
| `API_SERVER_MODEL_NAME` | *(порожньо)* | Перевизначення назви моделі в `/v1/models` |
| `HERMES_MAX_ITERATIONS` | `90` | Ліміт ітерацій агента на запит |

> Пріоритет: значення з `config.extra` (тобто з `config.yaml`) переважає над env-змінною.

### 2.2. Запуск gateway

```bash
python -m gateway.run        # запустити gateway з усіма увімкненими платформами
# або
python cli.py --gateway
```

При успішному старті в логах з'явиться:

```
[api_server] API server listening on http://127.0.0.1:8642 (model: hermes-agent)
```

### 2.3. Перевірка

```bash
curl http://127.0.0.1:8642/health
curl http://127.0.0.1:8642/v1/models
```

---

## 3. Автентифікація та безпека

### 3.1. Bearer-токен

Автентифікація — за заголовком `Authorization: Bearer <API_SERVER_KEY>`.
Порівняння токена стале за часом (`hmac.compare_digest`).

- Якщо `API_SERVER_KEY` **не заданий** → усі запити приймаються без автентифікації
  (передбачено лише для локального використання).
- Якщо ключ заданий, а токен невірний/відсутній → `401 invalid_api_key`.

```bash
curl http://127.0.0.1:8642/v1/models \
  -H "Authorization: Bearer $API_SERVER_KEY"
```

### 3.2. Захисні запобіжники при старті

Адаптер **відмовляється стартувати**, якщо:

1. Прив'язка до мережево-доступної адреси (не localhost) **без** `API_SERVER_KEY`.
2. Прив'язка до мережево-доступної адреси з **placeholder-ключем** (ключ коротший
   за 8 символів / нерозпізнаваний як справжній секрет).
3. Порт уже зайнятий (fail-fast із підказкою змінити `platforms.api_server.port`).

> Генеруйте реальний секрет, напр. `openssl rand -hex 32`, перш ніж відкривати
> сервер у мережу.

### 3.3. CORS

Без `cors_origins` браузерні крос-origin запити не отримують CORS-заголовків
(не-браузерні клієнти працюють завжди). Значення `*` дозволяє всі origin;
інакше — лише точний збіг із переліком. Дозволені заголовки запиту:
`Authorization, Content-Type, Idempotency-Key`.

---

## 4. Сесії та довготривала пам'ять (заголовки `X-Hermes-*`)

За замовчуванням `/v1/chat/completions` — **stateless**. Контекст між запитами
вмикається опційно двома незалежними заголовками:

| Заголовок | Призначення |
|---|---|
| `X-Hermes-Session-Id` | Продовження конкретного транскрипту (session continuity) |
| `X-Hermes-Session-Key` | Стабільний ідентифікатор каналу, що скоупить довготривалу пам'ять (напр. Honcho) між транскриптами |

Особливості:

- Обидва заголовки **незалежні**: можна слати один, обидва або жоден.
- Обидва **вимагають увімкненої автентифікації** (`API_SERVER_KEY`). Без ключа
  передача цих заголовків відхиляється — щоб неавтентифікований клієнт на
  локальному сервері не міг втрутитися в чужий скоуп пам'яті чи сесію.
- М'який ліміт довжини значення — 256 символів.
- У відповіді сервер повертає `X-Hermes-Session-Id` (і за наявності
  `X-Hermes-Session-Key`), щоб клієнт продовжив діалог наступним запитом.

```bash
curl http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Hermes-Session-Id: my-conversation-1" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Привіт!"}]}'
```

---

## 5. Перелік ендпоінтів

Базовий префікс: `http://<host>:<port>` (за замовчуванням `http://127.0.0.1:8642`).

### 5.1. Здоров'я та метадані

| Маршрут | Метод | Опис |
|---|---|---|
| `/health` | GET | Базова перевірка живучості |
| `/health/detailed` | GET | Розширений статус для дашбордів / cross-container проб |
| `/v1/health` | GET | Аліас `/health` у стилі OpenAI |
| `/v1/models` | GET | Список моделей (повертає налаштовану назву моделі) |
| `/v1/capabilities` | GET | Машиночитний контракт API (фічі, авторизація, runtime) |

### 5.2. Чат / генерація

| Маршрут | Метод | Опис |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI Chat Completions. Stateless; сесійність — через `X-Hermes-Session-Id` |
| `/v1/responses` | POST | OpenAI Responses API. Stateful через `previous_response_id` |
| `/v1/responses/{response_id}` | GET | Отримати збережену відповідь |
| `/v1/responses/{response_id}` | DELETE | Видалити збережену відповідь |

### 5.3. Сесії (крос-платформні чати)

| Маршрут | Метод | Опис |
|---|---|---|
| `/v1/sessions` | GET | Список усіх збережених сесій (Telegram, Slack, Web/API…) з `state.db`. Параметри: `source`, `limit`, `offset` |
| `/v1/sessions/{session_id}/messages` | GET | Транскрипт сесії у форматі `role`/`content` |

Продовжити будь-яку сесію (зокрема Telegram) можна через `POST /v1/chat/completions` із заголовком `X-Hermes-Session-Id: <session_id>`, надсилаючи лише новий хід у `messages` — сервер сам підвантажить історію з `state.db`.

### 5.4. Асинхронні запуски (Runs)

| Маршрут | Метод | Опис |
|---|---|---|
| `/v1/runs` | POST | Стартувати run; одразу повертає `run_id` (202) |
| `/v1/runs/{run_id}` | GET | Поточний статус run (для polling) |
| `/v1/runs/{run_id}/events` | GET | SSE-потік структурованих подій життєвого циклу |
| `/v1/runs/{run_id}/approval` | POST | Підтвердити/відхилити запит на approval |
| `/v1/runs/{run_id}/stop` | POST | Перервати агента, що виконується |

### 5.4. Cron-джоби

| Маршрут | Метод | Опис |
|---|---|---|
| `/api/jobs` | GET / POST | Список / створення джоб |
| `/api/jobs/{job_id}` | GET / PATCH / DELETE | Отримати / оновити / видалити джобу |
| `/api/jobs/{job_id}/pause` | POST | Призупинити |
| `/api/jobs/{job_id}/resume` | POST | Відновити |
| `/api/jobs/{job_id}/run` | POST | Запустити негайно |

> Усі захищені ендпоінти проходять `_check_auth` (див. розділ 3).

---

## 6. Деталі ключових ендпоінтів

### 6.1. `POST /v1/chat/completions`

OpenAI-сумісний формат. Підтримує:

- `messages` зі `content` як рядком **або** масивом типізованих частин
  (`{"type":"text","text":"..."}`) — масив автоматично «сплющується» в рядок.
- `stream: true|false` (булеві значення приймаються і як справжні JSON-boolean,
  і як рядки `"true"/"false"` для сумісних фронтендів).
- Стрімінг — через SSE; keepalive раз на 30 с.

**Не-стрімінговий приклад:**

```bash
curl http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "hermes-agent",
        "messages": [{"role": "user", "content": "Скільки буде 2+2?"}],
        "stream": false
      }'
```

**Стрімінговий приклад:**

```bash
curl -N http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Напиши хайку"}],"stream":true}'
```

### 6.2. `POST /v1/responses`

OpenAI Responses API зі стейтом на боці сервера:

- Ланцюжок діалогу через `previous_response_id`.
- `conversation` і `previous_response_id` — **взаємовиключні** (інакше `400`).
- Відповіді зберігаються в `ResponseStore` (ліміт — останні 100 відповідей),
  доступні через `GET /v1/responses/{id}` поки не витіснені.
- Стрімінг подій (delta, function_call / function_call_output) через SSE з точною
  кореляцією `tool_call_id`.

### 6.3. Асинхронні Runs

Патерн для довгих задач і зовнішніх control-plane UI:

1. `POST /v1/runs` → `202` з `run_id`.
2. Підписка на `GET /v1/runs/{run_id}/events` (SSE) — події прогресу інструментів,
   запити approval, завершення.
3. За потреби: `POST /v1/runs/{run_id}/approval` для підтвердження дій,
   `POST /v1/runs/{run_id}/stop` для переривання.
4. Альтернатива SSE — polling `GET /v1/runs/{run_id}`.

Осиротілі run-стріми періодично прибираються фоновим sweep-таском за TTL.

### 6.4. `GET /v1/capabilities`

Повертає машиночитний контракт, напр.:

```json
{
  "object": "hermes.api_server.capabilities",
  "platform": "hermes-agent",
  "model": "hermes-agent",
  "auth": { "type": "bearer", "required": true },
  "runtime": {
    "mode": "server_agent",
    "tool_execution": "server",
    "split_runtime": false
  },
  "features": {
    "chat_completions": true,
    "chat_completions_streaming": true,
    "responses_api": true,
    "responses_streaming": true,
    "run_submission": true,
    "run_status": true,
    "run_events_sse": true,
    "run_stop": true,
    "run_approval_response": true,
    "tool_progress_events": true,
    "approval_events": true,
    "session_continuity_header": "X-Hermes-Session-Id",
    "session_key_header": "X-Hermes-Session-Key",
    "cors": false
  }
}
```

Зовнішні UI використовують цей ендпоінт, щоб виявити доступні можливості без
припущень про конкретну версію Hermes.

---

## 7. Назва моделі (`/v1/models`)

Назва, яку рекламує сервер, визначається за пріоритетом:

1. Явне перевизначення (`config.extra.model_name` або `API_SERVER_MODEL_NAME`).
2. Назва активного профілю (якщо це не `default` / `custom`) — щоб кожен профіль
   рекламував окрему «модель».
3. Fallback: `hermes-agent`.

---

## 8. Обмеження та константи

| Параметр | Значення | Опис |
|---|---|---|
| `DEFAULT_HOST` | `127.0.0.1` | Адреса за замовчуванням |
| `DEFAULT_PORT` | `8642` | Порт за замовчуванням |
| `MAX_REQUEST_BYTES` | 10 MB | Максимальний розмір запиту (довгі діалоги з tool calls) |
| `MAX_STORED_RESPONSES` | 100 | Скільки відповідей тримає `ResponseStore` |
| `MAX_NORMALIZED_TEXT_LENGTH` | 64 KB | Ліміт нормалізованого текстового контенту |
| `MAX_CONTENT_LIST_SIZE` | 1000 | Макс. елементів у масиві `content` |
| `_MAX_SESSION_HEADER_LEN` | 256 | Ліміт довжини значень `X-Hermes-*` заголовків |
| SSE keepalive | 30 с | Інтервал keepalive для chat completions SSE |

---

## 9. Підключення зовнішнього UI (приклад Open WebUI)

1. Запустити gateway з увімкненим `api_server` і заданим `API_SERVER_KEY`.
2. У налаштуваннях UI вказати OpenAI-сумісне з'єднання:
   - **Base URL**: `http://127.0.0.1:8642/v1`
   - **API Key**: значення `API_SERVER_KEY`
3. Обрати модель зі списку `/v1/models` (за замовчуванням `hermes-agent`).

---

## 10. Усунення несправностей

| Симптом | Причина / рішення |
|---|---|
| Сервер не стартує, лог про `requires API_SERVER_KEY` | Прив'язка не до localhost без ключа — задайте `API_SERVER_KEY` або поверніть `127.0.0.1` |
| Старт відхилено через placeholder-ключ | Згенеруйте справжній секрет (`openssl rand -hex 32`) |
| `Port already in use` | Порт зайнятий — змініть `platforms.api_server.port` |
| `401 invalid_api_key` | Невірний/відсутній `Authorization: Bearer` |
| Заголовки `X-Hermes-*` ігноруються/відхиляються | Потрібен увімкнений `API_SERVER_KEY` |
| CORS-помилки в браузері | Задайте `API_SERVER_CORS_ORIGINS` (точний origin або `*`) |

---

## 11. Довідник за кодом

| Що | Файл:рядок |
|---|---|
| Документація ендпоінтів (docstring) | `gateway/platforms/api_server.py:1` |
| Реєстрація маршрутів | `gateway/platforms/api_server.py:3490` |
| Конфіг / env-змінні адаптера | `gateway/platforms/api_server.py:667` |
| Перевірка автентифікації | `gateway/platforms/api_server.py:822` |
| Парсинг `X-Hermes-Session-Key` | `gateway/platforms/api_server.py:861` |
| `/v1/capabilities` | `gateway/platforms/api_server.py:1048` |
| Запобіжники старту (мережа/ключ/порт) | `gateway/platforms/api_server.py:3523` |
| Дефолти й константи | `gateway/platforms/api_server.py:57` |
| Enum платформи / прапор `enabled` | `gateway/config.py:120`, `gateway/config.py:283` |
