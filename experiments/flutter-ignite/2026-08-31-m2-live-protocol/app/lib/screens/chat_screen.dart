import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../models/message.dart';
import '../providers/chat_provider.dart';
import '../widgets/error_card.dart';
import '../widgets/message_bubble.dart';
import '../widgets/typing_indicator.dart';

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  final _focusNode = FocusNode();

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    _controller.clear();
    await ref.read(chatProvider.notifier).sendMessage(text);
    _scrollToBottom();
  }

  @override
  Widget build(BuildContext context) {
    final chatState = ref.watch(chatProvider);
    final isSending = chatState.isSending;
    final messages = chatState.messages;
    final error = chatState.error;

    // Auto-scroll on new content.
    ref.listen(chatProvider, (prev, next) {
      if (next.messages.length != (prev?.messages.length ?? 0) ||
          next.isSending != prev?.isSending) {
        _scrollToBottom();
      }
    });

    return Scaffold(
      appBar: AppBar(
        title: const Text('Huible M1'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Settings',
            onPressed: () => context.push('/settings'),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              padding: const EdgeInsets.only(top: 8, bottom: 8),
              itemCount: messages.length + (isSending ? 1 : 0),
              itemBuilder: (context, index) {
                // Typing indicator as the last item while sending.
                if (isSending && index == messages.length) {
                  // Only show typing indicator if last message has no text yet.
                  final lastMsg =
                      messages.isNotEmpty ? messages.last : null;
                  if (lastMsg == null ||
                      lastMsg.role != MessageRole.persona ||
                      lastMsg.text.isEmpty) {
                    return const TypingIndicator();
                  }
                  return const SizedBox.shrink();
                }
                return MessageBubble(message: messages[index]);
              },
            ),
          ),
          if (error != null)
            ErrorCard(
              error: error,
              onDismiss: () =>
                  ref.read(chatProvider.notifier).clearError(),
              onGoToSettings: () => context.push('/settings'),
              onAcknowledgeConsent: () async {
                await ref
                    .read(chatProvider.notifier)
                    .acknowledgeConsent();
              },
            ),
          _buildInputBar(isSending),
        ],
      ),
    );
  }

  Widget _buildInputBar(bool isSending) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 6, 12, 10),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _controller,
                focusNode: _focusNode,
                enabled: !isSending,
                minLines: 1,
                maxLines: 4,
                textInputAction: TextInputAction.send,
                onSubmitted: isSending ? null : (_) => _send(),
                decoration: InputDecoration(
                  hintText: 'Type a message…',
                  filled: true,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: BorderSide.none,
                  ),
                  contentPadding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 10),
                ),
              ),
            ),
            const SizedBox(width: 8),
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 200),
              child: isSending
                  ? const SizedBox(
                      key: ValueKey('sending'),
                      width: 44,
                      height: 44,
                      child: Padding(
                        padding: EdgeInsets.all(10),
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                    )
                  : IconButton.filled(
                      key: const ValueKey('send'),
                      icon: const Icon(Icons.send),
                      onPressed: _send,
                      tooltip: 'Send',
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
