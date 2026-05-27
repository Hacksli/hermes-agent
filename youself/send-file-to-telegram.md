# Надсилання файлів у Telegram користувачу

> Інструкція для агента (LLM, що працює всередині VM). Описує, як використати
> youself-gateway, щоб віддати користувачу файл — фото, голосове повідомлення,
> документ або відео — у тому ж Telegram-чаті, з якого прийшло звернення.

---

## Коли застосовувати

Користуйся цими endpoint'ами замість того, щоб писати посилання текстом або
просити користувача «зайти і завантажити», коли результат твоєї роботи —
файл, який зручніше отримати прямо в месенджері. Типові випадки:

| Сценарій | Який endpoint |
|---|---|
| Згенерував зображення (icon, schema, дашборд-скрін, AI-art) | `send_photo` |
| Записав / синтезував голосовий коментар (TTS, відповідь голосом) | `send_voice` |
| Згенерував PDF / CSV / xlsx / json / log / архів | `send_document` |
| Записав / завантажив відео-фрагмент (screen-record, відеооглядка) | `send_video` |

Якщо файл — це **зображення, яке має бути збережене з повною якістю**
(скріншот документа, креслення, фото для друку) — бери `send_document`, а не
`send_photo`: Telegram стискає photo-content.

---

## Доступ

Env-змінні вже доступні у твоєму середовищі (інжектуються cloud-init'ом у
`/etc/hermes/env` і експортуються OpenRC-сервісом):

```sh
YOUSELF_GATEWAY_URL=https://api.youself.io
YOUSELF_GATEWAY_TOKEN=youself_gateway_...
```

Аутентифікація — Bearer token. Чат користувача resolve'иться на сервері за
твоїм `YOUSELF_GATEWAY_TOKEN` (`vm_id → tg_chats.chat_id`), додатково
вказувати `chat_id` не потрібно і не можна.

---

## Endpoint каталог

| Endpoint | Multipart-поле | Підказка |
|---|---|---|
| `POST /youself-gateway/v1/messages/send_photo`    | `photo`    | JPEG/PNG/WebP, до 10 MB. Telegram стискає. |
| `POST /youself-gateway/v1/messages/send_voice`    | `voice`    | OGG/Opus. Відтворюється як голосове повідомлення (waveform, кружок). |
| `POST /youself-gateway/v1/messages/send_document` | `document` | Будь-який тип, до 50 MB. Без стиснення. |
| `POST /youself-gateway/v1/messages/send_video`    | `video`    | MP4 (H.264/AAC) до 50 MB. |

Усі приймають додаткове form-поле `caption` (текст під файлом, до 1024 символів).

### Відповідь

При успіху (HTTP 200) — JSON:

```json
{
  "message_id": "01HZ...",   // наш ULID (для подальшого edit_text, якщо треба)
  "chat_id": 123456789,
  "ts": "2026-05-27T12:34:56Z"
}
```

### Помилки

| HTTP | Що означає | Що робити |
|---|---|---|
| `400` | Не той `multipart`, відсутнє form-поле, файл порожній | Перебудуй запит. |
| `401` | Bearer-токен невалідний або відсутній | Перевір `YOUSELF_GATEWAY_TOKEN`; повідом користувачу, що щось зламано — нехай зверне на сервісі. |
| `424 Failed Dependency` (`no chat attached to vm`) | VM ще не прив'язана до жодного TG-чату | Чекай поки `vm.provision` дописав `tg_chats.attached_vm_id`; зазвичай це сталось уже на провіженінгу. |
| `502 Bad Gateway` (`tg send failed`) | Telegram повернув помилку (надто великий файл, проблема з форматом) | Зменши файл або зміни формат. |

Не намагайся ретраїти `424`/`401` — це конфігураційні стани, не транзієнт.

---

## Curl-рецепти

Для всіх прикладів припускаємо, що файл уже лежить локально у VM.

### Фото

```sh
curl -fsS -X POST \
  -H "Authorization: Bearer $YOUSELF_GATEWAY_TOKEN" \
  -F "photo=@/tmp/chart.png" \
  -F "caption=Графік продажів за травень" \
  "$YOUSELF_GATEWAY_URL/youself-gateway/v1/messages/send_photo"
```

### Голосове повідомлення

```sh
curl -fsS -X POST \
  -H "Authorization: Bearer $YOUSELF_GATEWAY_TOKEN" \
  -F "voice=@/tmp/reply.ogg" \
  "$YOUSELF_GATEWAY_URL/youself-gateway/v1/messages/send_voice"
```

> Для голосового — рекомендований формат OGG із Opus-кодеком. Перекодувати
> можна через `ffmpeg -i in.wav -c:a libopus -b:a 32k -application voip out.ogg`.

### Документ

```sh
curl -fsS -X POST \
  -H "Authorization: Bearer $YOUSELF_GATEWAY_TOKEN" \
  -F "document=@/tmp/invoice-2026-05.pdf" \
  -F "caption=Інвойс за травень" \
  "$YOUSELF_GATEWAY_URL/youself-gateway/v1/messages/send_document"
```

### Відео

```sh
curl -fsS -X POST \
  -H "Authorization: Bearer $YOUSELF_GATEWAY_TOKEN" \
  -F "video=@/tmp/demo.mp4" \
  -F "caption=Демо нової фічі" \
  "$YOUSELF_GATEWAY_URL/youself-gateway/v1/messages/send_video"
```

---

## Правила вибору й поведінки

1. **Не дублюй**: якщо вже відповів текстом — не шли ще й голосове з тим самим
   змістом. Файли — це додатковий канал, коли вони реально несуть інформацію,
   яку текстом не передати.
2. **Caption — короткий і інформативний**. Не повторюй у caption те, що видно
   на самому файлі (назва, очевидний контекст). Пиши те, що користувач не
   може прочитати з імені/прев'ю: «згенеровано за вашим брифом», «дата
   зйомки 2026-05-27».
3. **Розмірні ліміти Telegram**: photo ≤ 10 MB, document/video ≤ 50 MB. Якщо
   файл більший — спершу стисни (`pngquant`, `ffmpeg -crf 28`, `zip -9`) або
   віддай посилання на хмарне сховище окремим текстовим повідомленням.
4. **Ім'я файлу** = ім'я, що бачить користувач у Telegram. Назвай файл так,
   щоб він мав сенс через тиждень: `звіт-травень-2026.pdf`, а не `out.pdf`.
5. **Чутливі дані**: не шли паролі, ключі чи персональні дані у файлах, якщо
   користувач сам про це не попросив. Telegram-чат — це не приватний канал
   у криптографічному сенсі.
6. **Підтвердь дію**: після успішного `send_*` коротко повідом текстом
   («Надіслав звіт» або «Голосова відповідь нижче ⬇️»), щоб користувач
   зрозумів, що це від тебе, а не випадковий файл.
7. **Помилки повідомляй чесно**: якщо `curl` повернув не 200 — не вдавай, що
   все добре. Скажи користувачу, що не вдалось надіслати, і поясни чому
   (надто великий, формат не той, тощо).

---

## Приклад: повний цикл «зроби PDF-звіт і відправ»

```sh
# 1. Згенерував звіт у себе у VM
python /home/user/reports/build.py --month=2026-05 --out=/tmp/report.pdf

# 2. Перевір, що файл є й не порожній
[ -s /tmp/report.pdf ] || { echo "report missing"; exit 1; }

# 3. Відправ користувачу
RESP=$(curl -fsS -X POST \
  -H "Authorization: Bearer $YOUSELF_GATEWAY_TOKEN" \
  -F "document=@/tmp/report.pdf" \
  -F "caption=Звіт за травень 2026" \
  "$YOUSELF_GATEWAY_URL/youself-gateway/v1/messages/send_document")

# 4. Перевір відповідь (на 200 backend повертає JSON із message_id)
echo "$RESP" | grep -q '"message_id"' || { echo "send failed: $RESP"; exit 1; }
```

Після цього коротко напиши користувачу текстом: «Звіт за травень готовий ⬆️».
