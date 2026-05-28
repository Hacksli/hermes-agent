---
name: vision-recognize
description: "Paid image recognition on the youself.io platform — describe, OCR, or answer questions about a photo the user sent."
version: 1.0.0
author: YouSelf
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [vision, image, ocr, youself, paid]
    related_skills: []
---

# Vision recognition (Claude Opus 4.7)

## Overview

YouSelf platform exposes a paid endpoint that runs your text prompt + an inbound user photo through Claude Opus 4.7 and returns a text answer. Use it when the user sends a photo and asks you to describe it, identify what's in it, transcribe text from it (OCR), translate signs, count objects, judge quality — anything that needs the model to actually look at the pixels.

## ⚠️ Build the prompt from conversation context — never blindly forward

**This is the most important rule for using this skill.** The `prompt` field you send is what *Claude Opus actually sees together with the image*. It steers the model: bad prompt → useless answer + wasted tokens.

You — the agent — must read the user's message, the recent conversation, the photo caption, and any prior context, then **synthesize a focused prompt that says what the user actually wants to learn from this image**. The user almost never writes a self-contained instruction; they assume you'll figure it out.

### Worked examples

| User sends                              | What user wants                          | **Prompt you should send** (NOT just paste user's text)                                                                       |
| --------------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Photo of a restaurant menu + "ціни?"    | Prices of dishes, in their language       | "Read the menu in this photo. List each dish with its price. Reply in the language of the menu (Ukrainian if menu is in UA)." |
| Photo of food + "це їстівне?"            | Safety judgment                          | "Identify the food in this photo. Is it safe to eat as shown? List any visible concerns (mold, raw meat, allergens)."         |
| Photo of receipt, no text               | Likely OCR + total                       | "Transcribe this receipt: vendor, date, line items with prices, total. Output as plain text in the receipt's original language." |
| Screenshot of an error message + "що це?" | Explanation of the error                 | "Read the error message in this screenshot and explain in 2–3 sentences what it means and the most likely cause."             |
| Selfie + "як я виглядаю?"               | Honest feedback on appearance/mood       | "Describe what you see in this photo (clothing, expression, lighting). Don't make up details. Reply in Ukrainian, briefly."   |
| Photo of a house plant + "що з нею?"    | Diagnosis (likely sick plant)            | "Identify this plant and describe its visible condition. Are there signs of disease, pests, over/under-watering? Reply in UA." |
| Meme + "поясни"                          | Cultural/humor explanation               | "Describe what's depicted in this image and explain the joke or reference, if there is one."                                  |
| Bare photo, no caption, no prior context | Brief description as default fallback    | "Describe the main subject of this photo in 1–2 sentences in the user's language (Ukrainian if unknown)."                     |

### Rules of thumb when crafting the prompt

1. **Look at the caption first**, then the last 1–2 user messages, then the conversation topic.
2. **State the output format you want** (list, JSON, single sentence, in language X).
3. **Anchor to the image** ("Read the menu in this photo", "Identify the plant in this image") — Claude responds more reliably when the prompt explicitly references that there *is* an image to look at.
4. **Don't pass the user's literal words** unless they happen to already be a complete instruction. "Що тут?" is not a prompt — it's a hint that the user wants you to figure out what's worth saying.
5. **When unsure, fall back to a short description** in the user's language (last row above). Never call with an empty or placeholder prompt — you'll pay the same and get vague output.

## When to Use

* User sends a photo in their Telegram and asks a question about it.
* You need to OCR a screenshot, document, or sign.
* User asks "what's wrong with this?" while attaching a picture.
* User sends a meme, painting, or chart and asks for an interpretation.

**Don't** use it for:

* Photos *you* generated (e.g. via image_gen) — you already know what they are.
* Asking the user to take an action — text is cheaper and immediate.
* Empty prompts (`{prompt: "?", file_url: ...}`) — write a real question, the prompt steers the model.

## How you call it — use the wrapper, NOT raw curl

The platform redacts the gateway token from any shell command you execute, so a direct `curl -H "Authorization: Bearer $YOUSELF_GATEWAY_TOKEN" ...` will fail with auth error. Instead, use the pre-installed wrapper that sources the env on your behalf:

```sh
youself-vision "<signed file_url from the /updates payload>" "<your focused prompt>"
```

Returns the same JSON as the underlying endpoint — see Response below.

If for some reason the wrapper is missing (very old VM), the underlying endpoint is `POST $YOUSELF_GATEWAY_URL/vision/recognize` (the `YOUSELF_GATEWAY_URL` env already ends with `/youself-gateway/v1`, so don't repeat that prefix) with body `{prompt, file_url, max_tokens?}` and Bearer auth — but on a current VM you should never hit that path. `file_url` is exactly the signed URL the gateway pushes to you in `/updates`; pass it through — the backend extracts the file ULID, fetches the bytes from Telegram, and base64-encodes for Claude.

### Response

```json
{
  "text":          "The image shows a black cat sitting on a windowsill, ...",
  "model":         "opus",
  "input_tokens":  1843,
  "output_tokens": 412,
  "charged_cents": 14
}
```

`text` is the answer to relay back to the user (use `send_message` or just include in your reply).

## Pricing

Billed per token at sell prices listed in `/price`. Input tokens include the image (Anthropic counts ≈1k–2k tokens per typical user photo) plus your text prompt. Output tokens are the answer.

Check the user's balance with the `youself-balance` wrapper before a large call if you're unsure they can afford it (never curl the gateway directly — the token is redacted from your shell). The vision call returns `402` with `{"error": {"code": "insufficient_quota"}}` when balance is too low — relay that to the user and stop.

## Example: full cycle

The user sends a photo of a restaurant menu and asks "what does the second dish cost?". The `/updates` push gives you:

```json
{
  "type": "message",
  "chat_id": 12345,
  "photo": [{"file_url": "https://api.youself.io/youself-gateway/v1/files/01HZ...?sig=...&exp=..."}],
  "caption": "what does the second dish cost?",
  ...
}
```

You call (using the wrapper — token never appears in your command surface):

```sh
youself-vision \
  "https://api.youself.io/youself-gateway/v1/files/01HZ...?sig=...&exp=..." \
  "Read the menu in this photo. What is the second dish listed and how much does it cost? Reply in the same language as the menu."
```

Output (stdout) — JSON. Parse `text`, relay back via `youself-send-text` or just include in your normal reply.

## Constraints and errors

| HTTP   | Meaning                                                      | What to do                                            |
| ------ | ------------------------------------------------------------ | ----------------------------------------------------- |
| `400`  | Missing `prompt` / `file_url`, or `file_url` not a ULID/URL  | Fix the request shape                                 |
| `402`  | `insufficient_quota` — wallet too low                        | Tell the user to `/topup`, do not retry               |
| `429`  | Per-VM concurrent vision call already in flight              | Wait a few seconds and retry, or queue                |
| `502`  | Upstream (Anthropic) error, or image too large (>5 MiB), or unsupported MIME | Tell the user (e.g. "this image is too big — please resend a smaller version") |
| `410`  | gateway_files row expired (photo was sent >1h ago)           | Ask user to resend the photo                          |

## Best practices

1. **Prompt = inferred user intent, not raw user text.** See the "Build the prompt from conversation context" section above — this is the single biggest factor in answer quality and token cost. Re-read it before every call until it's automatic.
2. **Honest failure.** If the call returns non-2xx, tell the user what went wrong. Don't fabricate an answer.
3. **Don't re-recognize.** If the user follows up with another question about the same photo within the same conversation, reuse the text you already got — don't pay for a second recognize call unless their question genuinely needs new visual analysis.
4. **OCR for handwriting** works but is less accurate than for printed text. Set expectations if needed.
5. **Privacy.** The image bytes leave the VM and go to Anthropic. Don't recognize photos the user explicitly marked private or that contain credentials/IDs unless they asked.
