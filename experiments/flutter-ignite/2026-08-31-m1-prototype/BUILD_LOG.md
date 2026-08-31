# BUILD_LOG — FlutterIgnite M1 Prototype

**Model:** Claude Sonnet 4.6 (Thinking) via Antigravity  
**Date:** 2026-08-31  
**Task:** Huible M1 text-chat Flutter web prototype — velocity measurement  
**Start time:** ~02:01 UTC  
**End time:** ~02:10 UTC  
**Total wall time:** ~9 minutes

---

## Step 1 — Environment check [02:01 UTC, ~30s]

- Read TASK.md.
- Checked for `/opt/flutter-dl/FLUTTER_VERSION.txt` → contained `INSTALL_OK` ✓
- Flutter already installed; ran `flutter --version` → **Flutter 3.32.2, Dart 3.8.1** ✓
- Fixed `git config --global --add safe.directory /opt/flutter` (git ownership warning).

```
flutter --version
→ Flutter 3.32.2 • channel stable
→ Dart 3.8.1 • DevTools 2.45.1
```

---

## Step 2 — Flutter project creation [02:02 UTC, ~5s]

```bash
flutter create --project-name huible_m1 --platforms web,android app
```
**Outcome:** ✓ 42 files written, dependencies resolved.

---

## Step 3 — pubspec.yaml — add dependencies [02:02 UTC, ~15s]

Added to `dependencies`:
- `flutter_riverpod: ^2.6.1`
- `go_router: ^14.8.1`
- `http: ^1.2.2`
- `uuid: ^4.5.1`

Ran `flutter pub get` → resolved 13 new packages ✓

---

## Step 4 — Source code written [02:02–02:05 UTC, ~3 min]

Files created:

| File | Purpose |
|---|---|
| `lib/main.dart` | Entry point, `ProviderScope`, `MaterialApp.router` |
| `lib/router.dart` | `GoRouter` with `/` and `/settings` routes |
| `lib/models/message.dart` | `ChatMessage` model (id, role, text, isStreaming) |
| `lib/models/settings.dart` | `AppSettings` (baseUrl, personaId, apiKey, dataSource) |
| `lib/models/chat_error.dart` | Sealed class hierarchy for 401/403/409/503/generic errors |
| `lib/data/chat_data_source.dart` | Abstract `ChatDataSource` interface |
| `lib/data/fake_chat_data_source.dart` | Offline word-by-word streaming with configurable delay |
| `lib/data/remote_chat_data_source.dart` | HTTP impl with Bearer auth, NDJSON/SSE + typewriter fallback |
| `lib/data/ndjson_parser.dart` | NDJSON/SSE line parser (`delta` / `response` fields) |
| `lib/providers/settings_provider.dart` | `StateNotifier` for `AppSettings` |
| `lib/providers/chat_provider.dart` | `StateNotifier` for chat state (messages, isSending, error) |
| `lib/widgets/message_bubble.dart` | User-right / persona-left chat bubbles |
| `lib/widgets/typing_indicator.dart` | Animated 3-dot indicator during streaming |
| `lib/widgets/error_card.dart` | Contextual error cards (auth/forbidden/consent/503/generic) |
| `lib/screens/chat_screen.dart` | Main chat UI with auto-scroll, input bar, error card |
| `lib/screens/settings_screen.dart` | Settings UI with segmented toggle + remote fields |

---

## Step 5 — Test files written [02:05 UTC, ~1 min]

- `test/unit_test.dart` — 16 unit tests:
  - `NdjsonParser`: delta, response, SSE prefix, empty/comment/[DONE]/invalid JSON, `parseBody`
  - `typewriterStream`: word-by-word, single word, trailing spaces
  - `RemoteChatDataSource` error mapping: 401→`AuthRequiredError`, 403→`ForbiddenError`, 409→`ConsentRequiredError`, 503→`ServiceUnavailableError`, 200→typewriter chunks
- `test/widget_test.dart` — 2 widget tests:
  - Send message → user bubble + full streamed persona reply
  - Spinner visible during streaming, send button returns after done

---

## Step 6 — flutter analyze (round 1) [02:05 UTC]

```bash
flutter analyze
→ 5 issues found
```

**Issues and fixes:**
1. `lib/router.dart:1` — `unused_import` (`flutter/material.dart`) → **removed**
2. `test/unit_test.dart:83,90` — `no_leading_underscores_for_local_identifiers` (`_settings`, `_mockClient`) → **renamed to `settings()`, `mockClient()`**
3. `test/widget_test.dart:15` — `unused_element` (`_testChatDataSourceProvider`) → **removed**
4. `test/widget_test.dart:34` — `undefined_identifier` (`chatDataSourceProvider`) → **added correct import from `providers/chat_provider.dart`**

---

## Step 7 — flutter analyze (round 2) [02:07 UTC]

```bash
flutter analyze
→ 1 issue found
```

**Issue:** residual `unused_import` in `widget_test.dart` (`data/chat_data_source.dart` no longer needed) → **removed**

---

## Step 8 — flutter analyze (round 3) [02:07 UTC]

```bash
flutter analyze
→ No issues found! ✓
```

---

## Step 9 — flutter test (round 1) [02:07 UTC]

```bash
flutter test --reporter expanded
→ 17/18 passed, 1 failure
```

**Failure:** "send button is disabled while streaming" — `find.byIcon(Icons.send), findsNothing` failed.  
**Root cause:** `FakeChatDataSource(chunkDelay: Duration.zero)` completes the entire stream in the same event loop turn as the first `pump()`, so the send button was already restored by the time the assertion ran.  
**Fix:** Used `chunkDelay: Duration(milliseconds: 500)` for the spinner test and `pump(Duration(milliseconds: 50))` to catch the in-progress state before stream completion.

---

## Step 10 — flutter test (round 2) [02:08 UTC]

```bash
flutter test --reporter expanded
→ All 18 tests passed! ✓
```

---

## Step 11 — flutter build web --release [02:08–02:09 UTC, ~37s]

```bash
flutter build web --release
→ ✓ Built build/web (24 MB total)
```

Font tree-shaking: CupertinoIcons 257KB→1.4KB (99.4%), MaterialIcons 1.6MB→9.4KB (99.4%).

---

## Verification Summary

| Command | Result |
|---|---|
| `flutter analyze` | ✅ No issues found |
| `flutter test` | ✅ All 18 tests passed |
| `flutter build web --release` | ✅ build/web produced (24 MB) |

---

## Friction log (velocity data)

| Friction | Time lost |
|---|---|
| `git safe.directory` warning for `/opt/flutter` (running as root) | ~10s |
| Round 1 analyze: `unused_import` in router.dart, naming convention in tests, undefined `chatDataSourceProvider` | ~2 min to fix 5 issues |
| Round 2 analyze: residual unused import | ~20s |
| Widget test timing: zero-delay fake source completed before spinner could be observed | ~2 min to diagnose and fix |
| **Total friction** | **~4.5 min of rework out of ~9 min wall time** |

## Architecture notes

- `ChatDataSource` is abstract; the active implementation is selected by `chatDataSourceProvider` based on `settingsProvider`. Both are riverpod providers — easy to swap or override in tests.
- `RemoteChatDataSource` follows the task contract exactly: NDJSON/SSE → streaming deltas; plain JSON → typewriter emitter. Both paths hit the same incremental render logic in `ChatNotifier`.
- `ChatError` is a sealed class — exhaustive `switch` in `ErrorCard.build()` → compile-time safety on error states.
- `StateNotifier` (not codegen) as required by the task house pattern.
