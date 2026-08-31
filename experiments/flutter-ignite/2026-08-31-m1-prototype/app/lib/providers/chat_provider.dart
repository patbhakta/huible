import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../data/chat_data_source.dart';
import '../data/fake_chat_data_source.dart';
import '../data/remote_chat_data_source.dart';
import '../models/chat_error.dart';
import '../models/message.dart';
import '../models/settings.dart';
import 'settings_provider.dart';

const _uuid = Uuid();

/// Provides the active [ChatDataSource] based on current settings.
final chatDataSourceProvider = Provider<ChatDataSource>((ref) {
  final settings = ref.watch(settingsProvider);
  final source = settings.dataSource == DataSourceType.fake
      ? FakeChatDataSource()
      : RemoteChatDataSource(settings: settings);
  ref.onDispose(source.dispose);
  return source;
});

/// Chat UI state.
class ChatState {
  final List<ChatMessage> messages;
  final bool isSending;
  final ChatError? error;
  final String? consentPendingPersonaId; // for the consent flow

  const ChatState({
    this.messages = const [],
    this.isSending = false,
    this.error,
    this.consentPendingPersonaId,
  });

  ChatState copyWith({
    List<ChatMessage>? messages,
    bool? isSending,
    ChatError? error,
    bool clearError = false,
    String? consentPendingPersonaId,
    bool clearConsent = false,
  }) {
    return ChatState(
      messages: messages ?? this.messages,
      isSending: isSending ?? this.isSending,
      error: clearError ? null : (error ?? this.error),
      consentPendingPersonaId: clearConsent
          ? null
          : (consentPendingPersonaId ?? this.consentPendingPersonaId),
    );
  }
}

class ChatNotifier extends StateNotifier<ChatState> {
  ChatNotifier(this._ref) : super(const ChatState());

  final Ref _ref;
  String? _conversationId;

  ChatDataSource get _dataSource => _ref.read(chatDataSourceProvider);

  Future<void> sendMessage(String text) async {
    if (text.trim().isEmpty || state.isSending) return;

    final userMsg = ChatMessage(
      id: _uuid.v4(),
      role: MessageRole.user,
      text: text.trim(),
    );

    // Add persona placeholder (streaming).
    final personaId = _uuid.v4();
    final personaMsg = ChatMessage(
      id: personaId,
      role: MessageRole.persona,
      text: '',
      isStreaming: true,
    );

    state = state.copyWith(
      messages: [...state.messages, userMsg, personaMsg],
      isSending: true,
      clearError: true,
    );

    await _streamReply(
      text: text.trim(),
      personaId: personaId,
    );
  }

  Future<void> _streamReply({
    required String text,
    required String personaId,
  }) async {
    try {
      final stream = _dataSource.sendMessage(
        message: text,
        conversationId: _conversationId,
      );

      final buffer = StringBuffer();
      await for (final chunk in stream) {
        buffer.write(chunk);
        _updatePersonaMsg(personaId, buffer.toString(), isStreaming: true);
      }

      // Finalise.
      _updatePersonaMsg(personaId, buffer.toString(), isStreaming: false);
      state = state.copyWith(isSending: false);
    } on AuthRequiredError catch (e) {
      _setError(personaId, e);
    } on ForbiddenError catch (e) {
      _setError(personaId, e);
    } on ConsentRequiredError catch (e) {
      _removePersonaMsg(personaId);
      state = state.copyWith(
        isSending: false,
        error: e,
        consentPendingPersonaId: personaId,
      );
    } on ServiceUnavailableError catch (e) {
      _setError(personaId, e);
    } on GenericChatError catch (e) {
      _setError(personaId, e);
    } catch (e) {
      _setError(personaId, GenericChatError(e.toString()));
    }
  }

  void _updatePersonaMsg(String id, String text, {required bool isStreaming}) {
    state = state.copyWith(
      messages: state.messages
          .map((m) =>
              m.id == id ? m.copyWith(text: text, isStreaming: isStreaming) : m)
          .toList(),
    );
  }

  void _removePersonaMsg(String id) {
    state = state.copyWith(
      messages: state.messages.where((m) => m.id != id).toList(),
    );
  }

  void _setError(String personaId, ChatError error) {
    _removePersonaMsg(personaId);
    state = state.copyWith(isSending: false, error: error);
  }

  void clearError() {
    state = state.copyWith(clearError: true, clearConsent: true);
  }

  /// Called after the user acknowledges the consent card.
  /// Re-tries the last user message.
  Future<void> acknowledgeConsent() async {
    // We re-send the last user message.
    final lastUser = state.messages.lastWhere(
      (m) => m.role == MessageRole.user,
      orElse: () => const ChatMessage(
          id: '', role: MessageRole.user, text: ''),
    );
    state = state.copyWith(clearError: true, clearConsent: true);
    if (lastUser.text.isNotEmpty) {
      await sendMessage(lastUser.text);
    }
  }
}

final chatProvider =
    StateNotifierProvider<ChatNotifier, ChatState>((ref) {
  return ChatNotifier(ref);
});
