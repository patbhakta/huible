import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/chat_error.dart';
import '../models/settings.dart';
import 'chat_data_source.dart';
import 'fake_chat_data_source.dart';
import 'ndjson_parser.dart';

/// HTTP-based implementation of [ChatDataSource].
///
/// - Attaches Bearer token on every request.
/// - When the response content-type is `application/x-ndjson` or
///   `text/event-stream`, parses streaming chunks.
/// - When plain JSON (current server behaviour), emits the `response` field
///   via typewriter so the streaming render path is always exercised.
/// - Maps HTTP 401/403/409/503 to the appropriate [ChatError] subclasses.
///
/// Protocol note (M2): FastAPI wraps raised HTTP exceptions in a `detail` key.
/// The real 409 shape is: `{"detail":{"error":{"code":"CONSENT_REQUIRED",...}}}`.
/// The mapper unwraps `detail.error` first, then falls back to top-level `error`
/// for backward compatibility with M1-shaped bodies.
class RemoteChatDataSource implements ChatDataSource {
  final AppSettings settings;
  final http.Client _client;

  RemoteChatDataSource({
    required this.settings,
    http.Client? client,
  }) : _client = client ?? http.Client();

  @override
  Stream<String> sendMessage({
    required String message,
    String? conversationId,
  }) {
    return _stream(message: message, conversationId: conversationId);
  }

  Stream<String> _stream({
    required String message,
    String? conversationId,
  }) async* {
    final uri = Uri.parse(
      '${settings.baseUrl}/api/v1/chat/${settings.personaId}',
    );

    final body = <String, dynamic>{'message': message};
    if (conversationId != null) body['conversation_id'] = conversationId;

    final request = http.Request('POST', uri)
      ..headers['Content-Type'] = 'application/json'
      ..headers['Authorization'] = 'Bearer ${settings.apiKey}'
      ..body = jsonEncode(body);

    late http.StreamedResponse response;
    try {
      response = await _client.send(request);
    } catch (e) {
      throw GenericChatError('Network error: $e');
    }

    final statusCode = response.statusCode;

    if (statusCode != 200) {
      final errorBody = await response.stream.bytesToString();
      _throwMappedError(statusCode, errorBody);
    }

    final contentType = response.headers['content-type'] ?? '';

    if (contentType.contains('application/x-ndjson') ||
        contentType.contains('text/event-stream')) {
      // True streaming path.
      await for (final chunk in response.stream.transform(utf8.decoder)) {
        final lines = chunk.split('\n');
        for (final line in lines) {
          final delta = NdjsonParser.parseLine(line);
          if (delta != null) yield delta;
        }
      }
    } else {
      // Plain JSON fallback — parse and typewriter-emit.
      final rawBody = await response.stream.bytesToString();
      final decoded = jsonDecode(rawBody) as Map<String, dynamic>;
      final replyText = decoded['response'] as String? ?? '';
      yield* typewriterStream(replyText);
    }
  }

  /// Acknowledges the consent card by POSTing to [acknowledgeUrl] (or the
  /// default consent endpoint derived from [settings.personaId]).
  ///
  /// Returns `true` when the server confirms `data.acknowledged == true`.
  /// Throws a mapped [ChatError] on any HTTP error.
  @override
  Future<bool> acknowledgeConsent({
    required String conversationId,
    required int cardVersion,
    String? acknowledgeUrl,
  }) async {
    final path = acknowledgeUrl ??
        '/api/v1/chat/${settings.personaId}/consent';
    final uri = Uri.parse('${settings.baseUrl}$path');

    late http.Response response;
    try {
      response = await _client.post(
        uri,
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ${settings.apiKey}',
        },
        body: jsonEncode({
          'conversation_id': conversationId,
          'card_version': cardVersion,
        }),
      );
    } catch (e) {
      throw GenericChatError('Network error: $e');
    }

    if (response.statusCode != 200) {
      _throwMappedError(response.statusCode, response.body);
    }

    final decoded = jsonDecode(response.body) as Map<String, dynamic>;
    final data = decoded['data'] as Map<String, dynamic>?;
    return data?['acknowledged'] as bool? ?? false;
  }

  /// Maps HTTP error status codes to [ChatError] subclasses.
  ///
  /// Unwrap order (M2):
  ///   1. `body.detail.error`  — real FastAPI shape
  ///   2. `body.error`         — M1 / legacy shape
  ///   3. empty map            — unknown body
  Never _throwMappedError(int statusCode, String body) {
    Map<String, dynamic> errorJson = {};
    try {
      final decoded = jsonDecode(body) as Map<String, dynamic>;
      // Prefer the FastAPI detail wrapper if present.
      final detail = decoded['detail'];
      if (detail is Map<String, dynamic> && detail['error'] is Map) {
        errorJson = detail['error'] as Map<String, dynamic>;
      } else {
        // Fall back to top-level error key (M1 compat).
        errorJson = decoded['error'] as Map<String, dynamic>? ?? {};
      }
    } catch (_) {}

    switch (statusCode) {
      case 401:
        throw AuthRequiredError(
          message: errorJson['message'] as String? ??
              'Sign-in required — check your API key',
        );
      case 403:
        throw ForbiddenError(
          message: errorJson['message'] as String? ??
              'This key is not scoped to that persona',
        );
      case 409:
        final consentCard =
            errorJson['consent_card'] as Map<String, dynamic>?;
        throw ConsentRequiredError(
          conversationId: errorJson['conversation_id'] as String?,
          acknowledgeUrl: errorJson['acknowledge_url'] as String?,
          cardVersion: consentCard?['version'] as int?,
          cardTitle: consentCard?['title'] as String?,
          cardBody: consentCard?['body'] as String?,
          acknowledgeInstructions:
              consentCard?['acknowledge_instructions'] as String?,
        );
      case 503:
        throw const ServiceUnavailableError();
      default:
        throw GenericChatError(
          'Unexpected error $statusCode: ${errorJson['message'] ?? body}',
        );
    }
  }

  @override
  void dispose() => _client.close();
}
