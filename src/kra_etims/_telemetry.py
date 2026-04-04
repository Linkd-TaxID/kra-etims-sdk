"""
Optional OpenTelemetry instrumentation for kra-etims.

This module is imported unconditionally. When ``opentelemetry-api`` is not
installed the try/except falls through to no-op stubs so the SDK itself
has zero mandatory dependency on OTel — the standard pattern recommended
by the OpenTelemetry specification for library authors.

Install the extra to activate real spans:
    pip install kra-etims[otel]

OTel spec reference:
    https://opentelemetry.io/docs/specs/otel/library-guidelines/#instrumentation-best-practices
"""

from contextlib import contextmanager
from typing import Any, Generator, Optional

try:
    from opentelemetry import trace
    from opentelemetry.trace import StatusCode

    _tracer = trace.get_tracer("kra_etims", "0.2.0")
    _OTEL_AVAILABLE = True
except ImportError:
    _tracer = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False


@contextmanager
def span(
    name: str,
    attributes: Optional[dict[str, Any]] = None,
) -> Generator[Any, None, None]:
    """
    Context manager that wraps a block in an OTel span when the API is available.

    When ``opentelemetry-api`` is not installed this is a no-op — the context
    manager yields None and the library behaves as if OTel was never imported.

    Usage::

        with span("kra_etims.submit_sale", {"invoice.no": invoice.invcNo}):
            ...

    On exception the span is marked as ERROR and the exception is recorded before
    re-raising, so it always appears in traces regardless of the caller's handling.
    """
    if not _OTEL_AVAILABLE:
        yield None
        return

    with _tracer.start_as_current_span(name) as current_span:  # type: ignore[union-attr]
        if attributes:
            for k, v in attributes.items():
                current_span.set_attribute(k, v)
        try:
            yield current_span
        except Exception as exc:
            current_span.set_status(StatusCode.ERROR, str(exc))
            current_span.record_exception(exc)
            raise
