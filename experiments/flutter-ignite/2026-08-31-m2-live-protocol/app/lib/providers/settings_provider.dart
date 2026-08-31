import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/settings.dart';

class SettingsNotifier extends StateNotifier<AppSettings> {
  SettingsNotifier() : super(const AppSettings());

  void setBaseUrl(String url) => state = state.copyWith(baseUrl: url);
  void setPersonaId(String id) => state = state.copyWith(personaId: id);
  void setApiKey(String key) => state = state.copyWith(apiKey: key);
  void setDataSource(DataSourceType type) =>
      state = state.copyWith(dataSource: type);
}

final settingsProvider =
    StateNotifierProvider<SettingsNotifier, AppSettings>((ref) {
  return SettingsNotifier();
});
