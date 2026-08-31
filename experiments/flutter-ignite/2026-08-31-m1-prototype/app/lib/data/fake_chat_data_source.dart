import 'chat_data_source.dart';

/// Fixed multi-sentence offline persona reply, streamed word-by-word.
const String _kFakeReply =
    'Hello! I am your Huible persona. '
    'I can answer your questions and chat with you. '
    'This is a deterministic offline demo response. '
    'Feel free to send another message!';

/// Deterministic offline data source used for tests and offline demo.
class FakeChatDataSource implements ChatDataSource {
  /// Delay between each word chunk (adjustable for tests).
  final Duration chunkDelay;

  const FakeChatDataSource({this.chunkDelay = const Duration(milliseconds: 60)});

  @override
  Stream<String> sendMessage({
    required String message,
    String? conversationId,
  }) async* {
    final words = _kFakeReply.split(' ');
    for (var i = 0; i < words.length; i++) {
      await Future<void>.delayed(chunkDelay);
      // Emit each word followed by a space (except the last).
      yield i < words.length - 1 ? '${words[i]} ' : words[i];
    }
  }

  @override
  void dispose() {}
}

/// Streams a plain text string word-by-word (typewriter effect).
/// Used by [RemoteChatDataSource] when the server returns plain JSON.
Stream<String> typewriterStream(String text, {Duration chunkDelay = const Duration(milliseconds: 60)}) async* {
  final words = text.split(' ');
  for (var i = 0; i < words.length; i++) {
    await Future<void>.delayed(chunkDelay);
    yield i < words.length - 1 ? '${words[i]} ' : words[i];
  }
}
