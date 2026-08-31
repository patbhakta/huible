enum DataSourceType { fake, remote }

class AppSettings {
  final String baseUrl;
  final String personaId;
  final String apiKey;
  final DataSourceType dataSource;

  const AppSettings({
    this.baseUrl = 'http://localhost:8000',
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
