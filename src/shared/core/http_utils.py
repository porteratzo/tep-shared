"""Shared helpers for outbound HTTP calls between services.

``correlation_headers()`` propagates the current request's correlation ID
(set by ``shared.core.log_setup.add_correlation_id_middleware``) to downstream
service calls, so a single inbound request can be traced across service
boundaries in the logs.
"""

from shared.core.log_setup import request_id_var


def correlation_headers() -> dict[str, str]:
    """Return an ``X-Request-ID`` header dict for propagating the current
    correlation ID downstream. Empty when no request is in flight."""
    rid = request_id_var.get("-")
    return {"X-Request-ID": rid} if rid != "-" else {}
