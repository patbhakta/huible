import 'dart:convert';

/// Parses NDJSON (newline-delimited JSON) or SSE (text/event-stream) chunks
/// and extracts the `delta` or `response` text fields.
///
/// NDJSON line format: `{"delta":"word "}` or `{"response":"..."}`.
/// SSE line format:    `data: {"delta":"word "}`.
class NdjsonParser {
  /// Parses a single raw line and returns the text delta, or null if the
  /// line is empty / a comment / cannot yield a delta.
  static String? parseLine(String line) {
    final trimmed = line.trim();
    if (trimmed.isEmpty || trimmed.startsWith(':')) return null;

    // Strip SSE "data: " prefix.
    final jsonStr = trimmed.startsWith('data: ') ? trimmed.substring(6) : trimmed;

    // SSE stream-end sentinel.
    if (jsonStr == '[DONE]') return null;

    try {
      final decoded = jsonDecode(jsonStr);
      if (decoded is Map<String, dynamic>) {
        final delta = decoded['delta'];
        if (delta is String) return delta;
        final response = decoded['response'];
        if (response is String) return response;
      }
    } catch (_) {
      // Not valid JSON — ignore.
    }
    return null;
  }

  /// Parses a complete NDJSON/SSE body string and returns all deltas.
  static List<String> parseBody(String body) {
    final lines = body.split('\n');
    final results = <String>[];
    for (final line in lines) {
      final delta = parseLine(line);
      if (delta != null) results.add(delta);
    }
    return results;
  }
}
