class KRAeTIMSError(Exception):
    """Base exception for all KRAeTIMS SDK errors."""
    pass

class KRAConnectivityTimeoutError(KRAeTIMSError):
    """
    Triggered when the 24-hour VSCU offline ceiling is breached (HTTP 503).
    This indicates that the middleware cannot reach the KRA endpoint and
    the local VSCU cache has expired.
    """
    def __init__(self, message="KRA connectivity timeout: VSCU offline ceiling breached (HTTP 503)."):
        super().__init__(message)

class KRAeTIMSAuthError(KRAeTIMSError):
    """Raised when authentication fails or token refresh fails."""
    pass

class KRAeTIMSValidationError(KRAeTIMSError):
    """Raised when the payload does not match the KRA v2.0 spec."""
    pass

class TIaaSUnavailableError(KRAeTIMSError):
    """
    Raised when the Railway instance is sleeping or down.
    Maps to requests.exceptions.ConnectionError.
    """
    def __init__(self, message="TIaaS Service Unavailable: The Railway instance is unreachable."):
        super().__init__(message)

class TIaaSAmbiguousStateError(KRAeTIMSError):
    """
    Raised when a network interruption occurs after a request was sent, 
    but before a response was received. The state of the invoice on the 
    KRA/TIaaS side is unknown (Schrödinger's Invoice).
    """
    def __init__(self, message="TIaaS Ambiguous State: Request sent but connection was dropped before response. Submit again with the same idempotency key."):
        super().__init__(message)
