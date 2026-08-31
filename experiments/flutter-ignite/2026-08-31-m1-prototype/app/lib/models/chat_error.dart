/// Represents different error states returned from the chat API.
sealed class ChatError {
  const ChatError();
}

/// HTTP 401 — AUTH_REQUIRED
class AuthRequiredError extends ChatError {
  final String message;
  const AuthRequiredError({this.message = 'Sign-in required — check your API key'});
}

/// HTTP 403 — FORBIDDEN
class ForbiddenError extends ChatError {
  final String message;
  const ForbiddenError({this.message = 'This key is not scoped to that persona'});
}

/// HTTP 409 — CONSENT_REQUIRED
class ConsentRequiredError extends ChatError {
  final String? cardTitle;
  final String? cardBody;
  const ConsentRequiredError({this.cardTitle, this.cardBody});
}

/// HTTP 503 — Service unavailable
class ServiceUnavailableError extends ChatError {
  const ServiceUnavailableError();
}

/// Generic / unexpected error
class GenericChatError extends ChatError {
  final String message;
  const GenericChatError(this.message);
}
