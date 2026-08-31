import '../models/chat_error.dart';

/// Abstract interface for streaming chat data sources.
abstract class ChatDataSource {
  /// Sends [message] and returns a stream of text deltas (chunks).
  /// Throws [ChatError] subclass on API errors.
  Stream<String> sendMessage({
    required String message,
    String? conversationId,
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
