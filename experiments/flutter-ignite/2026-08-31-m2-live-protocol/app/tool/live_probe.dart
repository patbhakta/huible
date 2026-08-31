// M2 live end-to-end probe: exercises RemoteChatDataSource against the REAL
// FastAPI server (huible-app, 127.0.0.1:8000). Run from app/ with `dart run`.
// Sequence: sendMessage (expect 409) -> acknowledgeConsent -> retry (expect 200).

// ignore_for_file: avoid_print — this is a CLI diagnostic harness; printing is
// its entire output contract.
import 'dart:async';

import 'package:huible_m1/models/chat_error.dart';
import 'package:huible_m1/models/settings.dart';
import 'package:huible_m1/data/remote_chat_data_source.dart';

Future<void> main() async {
  final settings = AppSettings(
    baseUrl: 'http://127.0.0.1:8000',
    personaId: 'fdc3a44b-4c0f-565d-b671-4ed0e3bc7894',
    apiKey: 'demo-pat-8c810186d97c224d',
    dataSource: DataSourceType.remote,
  );
  final source = RemoteChatDataSource(settings: settings);

  String? adoptedConversationId;
  int? cardVersion;
  String? ackUrl;

  // 1. First turn on a fresh session -> expect ConsentRequiredError (409).
  try {
    await source
        .sendMessage(message: 'M2 live probe from the Dart client.')
        .drain();
    print('FAIL: expected ConsentRequiredError, got a reply');
    return;
  } on ConsentRequiredError catch (e) {
    adoptedConversationId = e.conversationId;
    cardVersion = e.cardVersion;
    ackUrl = e.acknowledgeUrl;
    print('409 parsed OK: conversationId=$adoptedConversationId');
    print('  cardVersion=$cardVersion title="${e.cardTitle}"');
    print('  acknowledgeUrl=$ackUrl');
    print('  cardBody length=${e.cardBody?.length}');
    if (adoptedConversationId == null || cardVersion == null) {
      print('FAIL: missing conversation id or card version');
      return;
    }
  }

  // 2. Acknowledge consent with the adopted id.
  final ackOk = await source.acknowledgeConsent(
    conversationId: adoptedConversationId,
    cardVersion: cardVersion,
    acknowledgeUrl: ackUrl,
  );
  print('consent ack accepted=$ackOk');
  if (!ackOk) {
    print('FAIL: ack not accepted');
    return;
  }

  // 3. Retry through the same data source, with the adopted conversation id.
  final buffer = StringBuffer();
  var chunks = 0;
  final sub = source.sendMessage(
    message:
        'M2 live probe from the Dart client — one turn only, please reply with one short sentence.',
    conversationId: adoptedConversationId,
  );
  await for (final chunk in sub) {
    buffer.write(chunk);
    chunks++;
  }
  print('reply chunks=$chunks text="${buffer.toString().trim()}"');
  print(
    buffer.isEmpty ? 'FAIL: empty reply' : 'LIVE END-TO-END PASS (409->ack->turn)',
  );
  source.dispose();
}
