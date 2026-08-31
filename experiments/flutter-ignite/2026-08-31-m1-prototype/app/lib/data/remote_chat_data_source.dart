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

  /// Maps HTTP error status codes to [ChatError] subclasses.
  Never _throwMappedError(int statusCode, String body) {
    Map<String, dynamic> errorJson = {};
    try {
      final decoded = jsonDecode(body) as Map<String, dynamic>;
      errorJson = decoded['error'] as Map<String, dynamic>? ?? {};
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
          cardTitle: consentCard?['title'] as String?,
          cardBody: consentCard?['body'] as String?,
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
