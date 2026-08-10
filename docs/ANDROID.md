# Android Companion App Setup

This guide walks through installing the companion app, granting Health Connect permissions, and pointing it at your Health Hub server.

## 1. Install the app

Download the latest APK (v1.4.0 or newer) from the [life-dashboard-companion-app releases page](https://github.com/owen282000/life-dashboard-companion-app/releases).

Install it on a phone running Android 14 or later:

```bash
adb install life-dashboard-companion-v1.4.0.apk
```

## 2. Grant Health Connect permissions

Open the app and grant read access to **all** health categories you want to sync. The app reads from Health Connect, not directly from sensors, so every category you want must be explicitly permitted.

Categories include: steps, distance, active/total calories, exercise, hydration, nutrition, mindfulness, sleep, heart rate, resting heart rate, heart rate variability, blood pressure, blood glucose, oxygen saturation, respiratory rate, body temperature, weight, height, body fat, lean body mass, bone mass, body water mass, menstruation.

Any category left ungranted will show `permission_granted: false` in the `/debug` diagnostics panel and produce no data.

## 3. Configure the webhook URL

In the app's sync settings, set the webhook endpoint for **health** data:

```
URL:    https://your-domain.example.com/ingest/health
```

For **screen time** data, set a separate webhook:

```
URL:    https://your-domain.example.com/ingest/screentime
```

## 4. Configure authentication headers

The server requires a bearer token on every ingest request. In the app, add a custom header:

```
Authorization: Bearer <YOUR_DEVICE_TOKEN>
```

Use the `DEVICE_TOKEN` value from your `.env` file. The `Bearer ` prefix is required.

## 5. Set the HMAC signature (recommended)

To prevent request forgery, set a shared HMAC secret in both the app and your server's `WEBHOOK_HMAC_SECRET`:

1. Generate a secret: `openssl rand -hex 32`
2. Put it in `.env` as `WEBHOOK_HMAC_SECRET=...`
3. Put the same secret in the app's signature settings

The app will send an `X-Signature: sha256=<hex>` header computed over the raw request body. The server verifies it **before** parsing JSON. If verification is active and fails, the raw payload is still stored (with `signature_valid = 0`) but the request returns `401` and the app does **not** advance its cursor.

If `WEBHOOK_HMAC_SECRET` is empty on the server, signature verification is skipped.

## 6. ⚠️ Exactly one webhook URL per category

This is the most important rule in this guide. Violating it silently loses data.

> **Each category (health, screen time) must have exactly one webhook URL.** Do not add multiple target URLs for redundancy or fan-out.

**Why:** The app advances its sync cursor as soon as **any one** target returns success. If you list two URLs and the first succeeds, the cursor moves forward. If the second target was unreachable, that payload is never retried — it is gone. The app does not track per-target cursors.

**What this means in practice:**
- One URL for `/ingest/health`, one URL for `/ingest/screentime`. No more.
- If you want a backup, run it as a separate reverse-proxy fan-out **behind** Health Hub, not as a second app target. The proxy can retry without advancing the app's cursor.
- If you migrate servers, change the single URL — do not add the new one alongside the old one.

## 7. Trigger a sync

In the app, tap "Sync now" (or wait for the scheduled sync). The first sync pulls up to 1000 records per type per page and may take a minute.

Open the debug inspector in a browser:

```
https://your-domain.example.com/debug?token=<YOUR_MCP_TOKEN>
```

You should see:
- A green status panel with the last ingest time
- A new row in the "Recent payloads" table
- The diagnostics panel populated with per-type `raw_record_count`, `filtered_record_count`, and `last_sync`

If the diagnostics panel shows `permission_granted: false` for a type, revisit step 2. If it shows a non-empty `error`, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## How the cursor works

The app reads Health Connect in pages (1000 records per page) and tracks a high-water mark per type. Each successful `POST /ingest/health` (HTTP 200) advances the cursor to the last record in that batch. A `4xx` response is treated as permanent and **not** retried. A `5xx` or network error is retried with exponential backoff (3 attempts, 10 s timeout) and the cursor does **not** move.

This is why the server returns `500` (not `200`) when the database write fails — so the app retries instead of skipping the batch.
