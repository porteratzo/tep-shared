"""Flask-free helpers for the standard API envelope ``{"success", "data", "error"}``.

The Flask response builders live in :mod:`shared.core.api_response`; this module holds only what HTTP
*clients* need, so importing it never pulls in Flask.
"""

from __future__ import annotations

from typing import Any


def unwrap_data(body: Any) -> Any:
    """Return the payload of a standard envelope, or ``body`` unchanged if it isn't one.

    Clients of the envelope routes call this on ``resp.json()`` instead of reading
    top-level keys. The pass-through for non-envelope bodies exists only for the
    few tep_ai diagnostic routes (``/api/anomaly_dino_v3/*``) that still return a
    bare payload.
    """
    if isinstance(body, dict) and "success" in body and "data" in body:
        return body["data"]
    return body
