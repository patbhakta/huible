import 'package:flutter/material.dart';
import '../models/chat_error.dart';

/// Renders a contextual error card based on the [ChatError] type.
class ErrorCard extends StatelessWidget {
  final ChatError error;
  final VoidCallback? onDismiss;
  final VoidCallback? onGoToSettings;
  final VoidCallback? onAcknowledgeConsent;

  const ErrorCard({
    super.key,
    required this.error,
    this.onDismiss,
    this.onGoToSettings,
    this.onAcknowledgeConsent,
  });

  @override
  Widget build(BuildContext context) {
    return switch (error) {
      AuthRequiredError(:final message) => _buildCard(
          context: context,
          icon: Icons.lock_outline,
          title: 'Sign-in required',
          body: message,
          actions: [
            if (onGoToSettings != null)
              TextButton(
                onPressed: onGoToSettings,
                child: const Text('Go to Settings'),
              ),
            if (onDismiss != null)
              TextButton(onPressed: onDismiss, child: const Text('Dismiss')),
          ],
        ),
      ForbiddenError(:final message) => _buildCard(
          context: context,
          icon: Icons.block,
          title: 'Access denied',
          body: message,
          actions: [
            if (onDismiss != null)
              TextButton(onPressed: onDismiss, child: const Text('Dismiss')),
          ],
        ),
      ConsentRequiredError(:final cardTitle, :final cardBody) => _buildCard(
          context: context,
          icon: Icons.policy_outlined,
          title: cardTitle ?? 'Consent required',
          body: cardBody ??
              'You must acknowledge the terms before continuing.',
          actions: [
            if (onAcknowledgeConsent != null)
              ElevatedButton(
                onPressed: onAcknowledgeConsent,
                child: const Text('Acknowledge'),
              ),
            if (onDismiss != null)
              TextButton(onPressed: onDismiss, child: const Text('Cancel')),
          ],
        ),
      ServiceUnavailableError() => _buildCard(
          context: context,
          icon: Icons.cloud_off,
          title: 'Service unavailable',
          body: 'The service is temporarily unavailable. Please try again later.',
          actions: [
            if (onDismiss != null)
              TextButton(onPressed: onDismiss, child: const Text('Dismiss')),
          ],
        ),
      GenericChatError(:final message) => _buildCard(
          context: context,
          icon: Icons.error_outline,
          title: 'Error',
          body: message,
          actions: [
            if (onDismiss != null)
              TextButton(onPressed: onDismiss, child: const Text('Dismiss')),
          ],
        ),
    };
  }

  Widget _buildCard({
    required BuildContext context,
    required IconData icon,
    required String title,
    required String body,
    required List<Widget> actions,
  }) {
    final colorScheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.all(12),
      color: colorScheme.errorContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(icon, color: colorScheme.error),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  title,
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    color: colorScheme.onErrorContainer,
                  ),
                ),
              ),
            ]),
            const SizedBox(height: 8),
            Text(body,
                style: TextStyle(color: colorScheme.onErrorContainer)),
            if (actions.isNotEmpty) ...[
              const SizedBox(height: 12),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: actions,
              ),
            ],
          ],
        ),
      ),
    );
  }
}
