import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/settings.dart';
import '../providers/settings_provider.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  late TextEditingController _baseUrlCtrl;
  late TextEditingController _personaIdCtrl;
  late TextEditingController _apiKeyCtrl;

  @override
  void initState() {
    super.initState();
    final s = ref.read(settingsProvider);
    _baseUrlCtrl = TextEditingController(text: s.baseUrl);
    _personaIdCtrl = TextEditingController(text: s.personaId);
    _apiKeyCtrl = TextEditingController(text: s.apiKey);
  }

  @override
  void dispose() {
    _baseUrlCtrl.dispose();
    _personaIdCtrl.dispose();
    _apiKeyCtrl.dispose();
    super.dispose();
  }

  void _save() {
    final notifier = ref.read(settingsProvider.notifier);
    notifier.setBaseUrl(_baseUrlCtrl.text.trim());
    notifier.setPersonaId(_personaIdCtrl.text.trim());
    notifier.setApiKey(_apiKeyCtrl.text.trim());
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Settings saved')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(settingsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Connection Settings')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Data source toggle.
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Data source',
                      style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 8),
                  SegmentedButton<DataSourceType>(
                    segments: const [
                      ButtonSegment(
                        value: DataSourceType.fake,
                        label: Text('Fake (offline)'),
                        icon: Icon(Icons.wifi_off),
                      ),
                      ButtonSegment(
                        value: DataSourceType.remote,
                        label: Text('Remote'),
                        icon: Icon(Icons.cloud),
                      ),
                    ],
                    selected: {settings.dataSource},
                    onSelectionChanged: (s) => ref
                        .read(settingsProvider.notifier)
                        .setDataSource(s.first),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          // Remote settings (only relevant when remote selected).
          AnimatedOpacity(
            opacity:
                settings.dataSource == DataSourceType.remote ? 1.0 : 0.4,
            duration: const Duration(milliseconds: 200),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  children: [
                    TextField(
                      controller: _baseUrlCtrl,
                      enabled: settings.dataSource == DataSourceType.remote,
                      decoration: const InputDecoration(
                        labelText: 'Base URL',
                        hintText: 'http://localhost:8000',
                        prefixIcon: Icon(Icons.link),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _personaIdCtrl,
                      enabled: settings.dataSource == DataSourceType.remote,
                      decoration: const InputDecoration(
                        labelText: 'Persona UUID',
                        prefixIcon: Icon(Icons.person_outline),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _apiKeyCtrl,
                      enabled: settings.dataSource == DataSourceType.remote,
                      obscureText: true,
                      decoration: const InputDecoration(
                        labelText: 'API Key',
                        prefixIcon: Icon(Icons.key),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: 20),
          FilledButton.icon(
            icon: const Icon(Icons.save),
            label: const Text('Save'),
            onPressed: _save,
          ),
        ],
      ),
    );
  }
}
