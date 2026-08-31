import '../models/chat_error.dart';

/// Abstract interface for streaming chat data sources.
abstract class ChatDataSource {
  /// Sends [message] and returns a stream of text deltas (chunks).
  /// Throws [ChatError] subclass on API errors.
  Stream<String> sendMessage({
    required String message,
    String? conversationId,
  });

  /// Acknowledges the consent card and returns true on success.
  /// Throws [ChatError] on failure.
  Future<bool> acknowledgeConsent({
    required String conversationId,
    required int cardVersion,
    String? acknowledgeUrl,
  });

  /// Optionally dispose resources.
  void dispose() {}
}

/// Result type that wraps either streaming chunks or an error.
sealed class ChatSendResult {
  const ChatSendResult();
}

class ChatStreamResult extends ChatSendResult {
  final Stream<String> chunks;
  const ChatStreamResult(this.chunks);
}

class ChatErrorResult extends ChatSendResult {
  final ChatError error;
  const ChatErrorResult(this.error);
}
