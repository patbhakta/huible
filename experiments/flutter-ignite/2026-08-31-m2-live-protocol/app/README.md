# Huible M1 — Text-Chat Flutter Web Prototype

A Flutter web prototype of the Huible M1 text-chat client built as a velocity test.

## What it is
Chat UI with streaming persona replies (word-by-word typewriter effect), auth-error handling (401/403/409/503), and offline demo mode.

## How to run

**Dev (Chrome):**
```bash
flutter run -d chrome
```

**Serve release build:**
```bash
flutter build web --release
# then serve app/build/web/ via any static HTTP server, e.g.:
python3 -m http.server 8080 --directory build/web
```

## Settings

Open the ⚙ icon → Connection Settings:
- **Data source**: `Fake (offline)` streams a fixed demo reply; `Remote` hits the real API.
- **Base URL**: FastAPI server root (default `http://localhost:8000`).
- **Persona UUID**: UUID of the target persona.
- **API Key**: Bearer token (stored in memory only, not persisted).
