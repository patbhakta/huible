import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:huible_m1/data/fake_chat_data_source.dart';
import 'package:huible_m1/models/settings.dart';
import 'package:huible_m1/providers/chat_provider.dart';
import 'package:huible_m1/providers/settings_provider.dart';
import 'package:huible_m1/screens/chat_screen.dart';

Widget buildTestApp({Duration chunkDelay = Duration.zero}) {
  final router = GoRouter(
    initialLocation: '/',
    routes: [
      GoRoute(path: '/', builder: (_, __) => const ChatScreen()),
    ],
  );

  final fakeSource = FakeChatDataSource(chunkDelay: chunkDelay);

  return ProviderScope(
    overrides: [
      settingsProvider.overrideWith(
        (ref) => SettingsNotifier()
          ..state = const AppSettings(dataSource: DataSourceType.fake),
      ),
      chatDataSourceProvider.overrideWithValue(fakeSource),
    ],
    child: MaterialApp.router(routerConfig: router),
  );
}

void main() {
  group('ChatScreen widget tests', () {
    testWidgets('sending a message shows user bubble and streamed persona reply',
        (tester) async {
      await tester.pumpWidget(buildTestApp());
      await tester.pumpAndSettle();

      // Type a message.
      final input = find.byType(TextField);
      await tester.enterText(input, 'Hello persona');

      // Tap send button.
      final sendBtn = find.byIcon(Icons.send);
      await tester.tap(sendBtn);
      await tester.pump();

      // User bubble should appear immediately.
      expect(find.text('Hello persona'), findsOneWidget);

      // Let all streaming chunks complete.
      await tester.pumpAndSettle(const Duration(seconds: 10));

      // Persona bubble should contain the full fake reply text.
      expect(
        find.textContaining('Hello! I am your Huible persona.'),
        findsOneWidget,
      );

      // Input should be re-enabled.
      final textField = tester.widget<TextField>(find.byType(TextField));
      expect(textField.enabled, isTrue);
    });

    testWidgets('send button is replaced by spinner while streaming',
        (tester) async {
      // Use a delay long enough that streaming doesn't finish immediately.
      await tester.pumpWidget(
        buildTestApp(chunkDelay: const Duration(milliseconds: 500)),
      );
      await tester.pumpAndSettle();

      final input = find.byType(TextField);
      await tester.enterText(input, 'Test');

      final sendBtn = find.byIcon(Icons.send);
      await tester.tap(sendBtn);

      // Pump enough to process the state update but not finish streaming.
      await tester.pump(const Duration(milliseconds: 50));

      // While streaming: CircularProgressIndicator visible, send icon gone.
      expect(find.byType(CircularProgressIndicator), findsOneWidget);

      // Wait for all chunks and AnimatedSwitcher animation to complete.
      await tester.pumpAndSettle(const Duration(seconds: 30));

      // Send button returns after streaming completes.
      expect(find.byIcon(Icons.send), findsOneWidget);
    });
  });
}
