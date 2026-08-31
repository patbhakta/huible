import 'package:flutter_test/flutter_test.dart';
import 'package:huible_m1/data/ndjson_parser.dart';
import 'package:huible_m1/data/fake_chat_data_source.dart';
import 'package:huible_m1/data/remote_chat_data_source.dart';
import 'package:huible_m1/models/chat_error.dart';
import 'package:huible_m1/models/settings.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  // ─── NDJSON Parser ───────────────────────────────────────────────────────

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

  // ─── typewriterStream (word-chunk emitter) ────────────────────────────────

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

  // ─── RemoteChatDataSource — error mapping ─────────────────────────────────

  group('RemoteChatDataSource error mapping', () {
    AppSettings settings() => const AppSettings(
          baseUrl: 'http://test',
          personaId: 'p1',
          apiKey: 'key',
          dataSource: DataSourceType.remote,
        );

    http.Client mockClient(int status, String body,
        {String contentType = 'application/json'}) {
      return MockClient((request) async {
        return http.Response(body, status,
            headers: {'content-type': contentType});
      });
    }

    test('401 throws AuthRequiredError', () async {
      final client = mockClient(
        401,
        '{"error":{"code":"AUTH_REQUIRED","message":"Bad key"}}',
      );
      final ds = RemoteChatDataSource(settings: settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<AuthRequiredError>()),
      );
    });

    test('403 throws ForbiddenError', () async {
      final client = mockClient(
        403,
        '{"error":{"code":"FORBIDDEN","message":"Not scoped"}}',
      );
      final ds = RemoteChatDataSource(settings: settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<ForbiddenError>()),
      );
    });

    test('409 throws ConsentRequiredError with card fields', () async {
      final client = mockClient(
        409,
        '{"error":{"code":"CONSENT_REQUIRED",'
        '"consent_card":{"title":"Terms","body":"Please agree"}}}',
      );
      final ds = RemoteChatDataSource(settings: settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<ConsentRequiredError>()),
      );
    });

    test('503 throws ServiceUnavailableError', () async {
      final client = mockClient(503, '{}');
      final ds = RemoteChatDataSource(settings: settings(), client: client);
      expect(
        () => ds.sendMessage(message: 'hi').toList(),
        throwsA(isA<ServiceUnavailableError>()),
      );
    });

    test('200 plain JSON emits typewriter chunks', () async {
      final client = mockClient(
        200,
        '{"response":"hello world"}',
        contentType: 'application/json',
      );
      final ds = RemoteChatDataSource(settings: settings(), client: client);
      final chunks = await ds
          .sendMessage(message: 'hi')
          .toList();
      expect(chunks.join(), 'hello world');
    });
  });
}
