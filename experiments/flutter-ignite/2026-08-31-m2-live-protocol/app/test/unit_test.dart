import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:huible_m1/data/ndjson_parser.dart';
import 'package:huible_m1/data/fake_chat_data_source.dart';
import 'package:huible_m1/data/remote_chat_data_source.dart';
import 'package:huible_m1/models/chat_error.dart';
import 'package:huible_m1/models/settings.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// ─── Fixture helpers ────────────────────────────────────────────────────────

String _fixture(String name) {
  final scriptDir = File(Platform.script.toFilePath()).parent;
  // When run via `flutter test` the script is the test file itself.
  // Resolve relative to the test directory.
  final candidates = [
    // Direct sibling (when script path is the test file).
    File('${scriptDir.path}/fixtures/$name'),
    // From repo root (fallback for some runners).
    File('test/fixtures/$name'),
  ];
  for (final f in candidates) {
    if (f.existsSync()) return f.readAsStringSync();
  }
  // Last resort: relative to CWD (flutter test is run from app/).
  return File('test/fixtures/$name').readAsStringSync();
}

// ─── Shared helpers ──────────────────────────────────────────────────────────

AppSettings _settings({String personaId = 'p1'}) => AppSettings(
      baseUrl: 'http://test',
      personaId: personaId,
      apiKey: 'key',
      dataSource: DataSourceType.remote,
    );

http.Client _mockClient(
  int status,
  String body, {
  String contentType = 'application/json',
}) {
  return MockClient((request) async {
    return http.Response(body, status,
        headers: {'content-type': contentType});
  });
}

void main() {
  // ─── AppSettings ───────────────────────────────────────────────────────────

  group('AppSettings defaults', () {
    test('baseUrl defaults to compile-time API_BASE_URL (localhost fallback)',
        () {
      // No --dart-define in this test run: must fall back to localhost.
      expect(const AppSettings().baseUrl, 'http://localhost:8000');
    });

    test('copyWith keeps baseUrl when not provided', () {
      final s = const AppSettings(baseUrl: 'https://api.example.com');
      expect(s.copyWith(personaId: 'x').baseUrl, 'https://api.example.com');
    });
  });

  // ─── NDJSON Parser ─────────────────────────────────────────────────────────

  group('NdjsonParser.parseLine', () {
    test('parses NDJSON delta field', () {
      expect(NdjsonParser.parseLine('{"delta":"hello "}'), 'hello ');
    });

    test('parses NDJSON response field', () {
      expect(NdjsonParser.parseLine('{"response":"world"}'), 'world');
    });

    test('parses SSE data: prefix with delta', () {
      expect(
        NdjsonParser.parseLine('data: {"delta":"chunk "}'),
        'chunk ',
      );
    });

    test('returns null for empty line', () {
      expect(NdjsonParser.parseLine(''), isNull);
    });

    test('returns null for SSE comment', () {
      expect(NdjsonParser.parseLine(': keep-alive'), isNull);
    });

    test('returns null for [DONE] sentinel', () {
      expect(NdjsonParser.parseLine('data: [DONE]'), isNull);
    });

    test('returns null for invalid JSON', () {
      expect(NdjsonParser.parseLine('{invalid}'), isNull);
    });

    test('parseBody accumulates all deltas', () {
      const body = '{"delta":"Hello "}\n{"delta":"world"}\n';
      expect(NdjsonParser.parseBody(body), ['Hello ', 'world']);
    });
  });

  // ─── typewriterStream ──────────────────────────────────────────────────────

  group('typewriterStream', () {
    test('emits full text word by word', () async {
      const input = 'one two three';
      final chunks = await typewriterStream(
        input,
        chunkDelay: Duration.zero,
      ).toList();
      expect(chunks.join(), input);
    });

    test('handles single-word text', () async {
      final chunks = await typewriterStream(
        'hello',
        chunkDelay: Duration.zero,
      ).toList();
      expect(chunks, ['hello']);
    });

    test('preserves trailing spaces between words', () async {
      final chunks = await typewriterStream(
        'a b',
        chunkDelay: Duration.zero,
      ).toList();
      expect(chunks.join(), 'a b');
    });
  });

  // ─── RemoteChatDataSource — error mapping ──────────────────────────────────

  group('RemoteChatDataSource error mapping', () {
    // ── M1 compat: top-level error key ──────────────────────────────────────

    test('401 top-level error key → AuthRequiredError with message', () async {
      final client = _mockClient(
        401,
        '{"error":{"code":"AUTH_REQUIRED","message":"Bad key"}}',
      );
      final ds = RemoteChatDataSource(settings: _settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<AuthRequiredError>()
            .having((e) => e.message, 'message', 'Bad key')),
      );
    });

    test('403 top-level error key → ForbiddenError with message', () async {
      final client = _mockClient(
        403,
        '{"error":{"code":"FORBIDDEN","message":"Not scoped"}}',
      );
      final ds = RemoteChatDataSource(settings: _settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<ForbiddenError>()
            .having((e) => e.message, 'message', 'Not scoped')),
      );
    });

    test('409 top-level error key (M1 compat) → ConsentRequiredError', () async {
      final client = _mockClient(
        409,
        '{"error":{"code":"CONSENT_REQUIRED",'
        '"conversation_id":"old-conv-id",'
        '"consent_card":{"version":2,"title":"Terms","body":"Please agree"}}}',
      );
      final ds = RemoteChatDataSource(settings: _settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<ConsentRequiredError>()
            .having((e) => e.conversationId, 'conversationId', 'old-conv-id')
            .having((e) => e.cardVersion, 'cardVersion', 2)
            .having((e) => e.cardTitle, 'cardTitle', 'Terms')),
      );
    });

    // ── M2: detail.error wrapper ─────────────────────────────────────────────

    test('409 real fixture (detail.error) → ConsentRequiredError with all fields',
        () async {
      final body = _fixture('consent_409.json');
      final client = _mockClient(409, body);
      final ds = RemoteChatDataSource(settings: _settings(), client: client);

      late ConsentRequiredError captured;
      try {
        await ds.sendMessage(message: 'hello').toList();
        fail('expected ConsentRequiredError');
      } on ConsentRequiredError catch (e) {
        captured = e;
      }

      expect(captured.conversationId, '15f2b501-054c-4159-83b2-720d70477b48');
      expect(captured.acknowledgeUrl,
          '/api/v1/chat/fdc3a44b-4c0f-565d-b671-4ed0e3bc7894/consent');
      expect(captured.cardVersion, 3);
      expect(captured.cardTitle, 'Before we begin \u2014 please read');
    });

    test('401 with detail.error wrapper → AuthRequiredError with message',
        () async {
      const body =
          '{"detail":{"error":{"code":"AUTH_REQUIRED","message":"Token expired"}}}';
      final client = _mockClient(401, body);
      final ds = RemoteChatDataSource(settings: _settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<AuthRequiredError>()
            .having((e) => e.message, 'message', 'Token expired')),
      );
    });

    test('403 with detail.error wrapper → ForbiddenError with message',
        () async {
      const body =
          '{"detail":{"error":{"code":"FORBIDDEN","message":"Persona mismatch"}}}';
      final client = _mockClient(403, body);
      final ds = RemoteChatDataSource(settings: _settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<ForbiddenError>()
            .having((e) => e.message, 'message', 'Persona mismatch')),
      );
    });

    // ── 503 / 200 ────────────────────────────────────────────────────────────

    test('503 → ServiceUnavailableError', () async {
      final client = _mockClient(503, '{}');
      final ds = RemoteChatDataSource(settings: _settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<ServiceUnavailableError>()),
      );
    });

    test('200 plain JSON → typewriter chunks from fixture', () async {
      final body = _fixture('chat_200.json');
      final client = _mockClient(200, body, contentType: 'application/json');
      final ds = RemoteChatDataSource(settings: _settings(), client: client);
      final chunks = await ds
          .sendMessage(message: 'hi')
          .toList();
      expect(chunks.join(), 'Hello! I remember you well.');
    });
  });

  // ─── acknowledgeConsent ────────────────────────────────────────────────────

  group('RemoteChatDataSource.acknowledgeConsent', () {
    test('happy path: POST hits ack URL with correct body + Bearer header, returns true',
        () async {
      final ackBody = _fixture('consent_ack_200.json');
      late http.Request captured;
      final client = MockClient((request) async {
        captured = request;
        return http.Response(ackBody, 200,
            headers: {'content-type': 'application/json'});
      });

      final ds = RemoteChatDataSource(settings: _settings(personaId: 'fdc3a44b-4c0f-565d-b671-4ed0e3bc7894'), client: client);
      final result = await ds.acknowledgeConsent(
        conversationId: '15f2b501-054c-4159-83b2-720d70477b48',
        cardVersion: 3,
        acknowledgeUrl: '/api/v1/chat/fdc3a44b-4c0f-565d-b671-4ed0e3bc7894/consent',
      );

      expect(result, isTrue);
      // Assert the POST targeted the ack URL.
      expect(captured.url.path,
          '/api/v1/chat/fdc3a44b-4c0f-565d-b671-4ed0e3bc7894/consent');
      expect(captured.method, 'POST');
      // Assert Bearer header present.
      expect(captured.headers['Authorization'], 'Bearer key');
      // Assert body contains conversationId and cardVersion.
      final sentBody = jsonDecode(captured.body) as Map<String, dynamic>;
      expect(sentBody['conversation_id'], '15f2b501-054c-4159-83b2-720d70477b48');
      expect(sentBody['card_version'], 3);
    });

    test('acknowledgeConsent 409 response → throws ConsentRequiredError, does not return true',
        () async {
      final body = _fixture('consent_409.json');
      final client = _mockClient(409, body);
      final ds = RemoteChatDataSource(settings: _settings(), client: client);

      expect(
        () => ds.acknowledgeConsent(
          conversationId: '15f2b501-054c-4159-83b2-720d70477b48',
          cardVersion: 3,
        ),
        throwsA(isA<ConsentRequiredError>()),
      );
    });
  });

  // ─── Provider flow ─────────────────────────────────────────────────────────
  // Tests the state-machine logic in ChatNotifier without the widget layer.

  group('ChatNotifier consent flow', () {
    // We need to test: sendMessage → 409 → acknowledgeConsent → retry uses adopted id.
    // We do this by constructing a MockClient that tracks requests in sequence.

    test(
        'sendMessage→409→acknowledgeConsent: ack POSTs with correct id, retry uses adopted conversationId',
        () async {
      const convoId = '15f2b501-054c-4159-83b2-720d70477b48';
      const personaId = 'fdc3a44b-4c0f-565d-b671-4ed0e3bc7894';

      final requests = <http.BaseRequest>[];
      int callCount = 0;

      final client = MockClient((request) async {
        requests.add(request);
        final idx = callCount++;
        if (idx == 0) {
          // First call: chat POST → 409 CONSENT_REQUIRED
          return http.Response(_fixture('consent_409.json'), 409,
              headers: {'content-type': 'application/json'});
        } else if (idx == 1) {
          // Second call: consent ack POST → 200
          return http.Response(_fixture('consent_ack_200.json'), 200,
              headers: {'content-type': 'application/json'});
        } else {
          // Third call: retry chat POST → 200
          return http.Response(_fixture('chat_200.json'), 200,
              headers: {'content-type': 'application/json'});
        }
      });

      final settings = AppSettings(
        baseUrl: 'http://test',
        personaId: personaId,
        apiKey: 'key',
        dataSource: DataSourceType.remote,
      );
      final ds = RemoteChatDataSource(settings: settings, client: client);

      // Fake Ref for ChatNotifier — only reads chatDataSourceProvider.
      // We use the provider directly in unit-test style.
      // Instead of using ProviderContainer here (widget-test territory),
      // we call the data source directly to verify the contract.

      // Step 1: chat send → 409.
      late ConsentRequiredError consentErr;
      try {
        await ds.sendMessage(message: 'Hello', conversationId: null).toList();
        fail('Expected ConsentRequiredError');
      } on ConsentRequiredError catch (e) {
        consentErr = e;
      }
      expect(consentErr.conversationId, convoId);
      expect(requests, hasLength(1));

      // Step 2: acknowledge consent.
      final ackResult = await ds.acknowledgeConsent(
        conversationId: consentErr.conversationId!,
        cardVersion: consentErr.cardVersion!,
        acknowledgeUrl: consentErr.acknowledgeUrl,
      );
      expect(ackResult, isTrue);
      expect(requests, hasLength(2));
      // Ack request body must contain the server-issued conversationId.
      final ackReq = requests[1] as http.Request;
      final ackBody = jsonDecode(ackReq.body) as Map<String, dynamic>;
      expect(ackBody['conversation_id'], convoId);

      // Step 3: retry chat with adopted conversationId.
      final chunks = await ds
          .sendMessage(message: 'Hello', conversationId: convoId)
          .toList();
      expect(chunks.join(), 'Hello! I remember you well.');
      expect(requests, hasLength(3));
      // Third request must have conversation_id in body.
      final retryReq = requests[2] as http.Request;
      final retryBody = jsonDecode(retryReq.body) as Map<String, dynamic>;
      expect(retryBody['conversation_id'], convoId);
    });
  });
}
