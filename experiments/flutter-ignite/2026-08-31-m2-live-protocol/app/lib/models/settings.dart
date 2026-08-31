enum DataSourceType { fake, remote }

/// Compile-time API base default (build with:
/// `flutter build web --release --dart-define=API_BASE_URL=https://api.huible.com`).
/// Falls back to localhost for `flutter run` against a local server.
const String _kDefaultApiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://localhost:8000',
);

class AppSettings {
  final String baseUrl;
  final String personaId;
  final String apiKey;
  final DataSourceType dataSource;

  const AppSettings({
    this.baseUrl = _kDefaultApiBaseUrl,
    this.personaId = '',
    this.apiKey = '',
    this.dataSource = DataSourceType.fake,
  });

  AppSettings copyWith({
    String? baseUrl,
    String? personaId,
    String? apiKey,
    DataSourceType? dataSource,
  }) {
    return AppSettings(
      baseUrl: baseUrl ?? this.baseUrl,
      personaId: personaId ?? this.personaId,
      apiKey: apiKey ?? this.apiKey,
      dataSource: dataSource ?? this.dataSource,
    );
  }
}
