"""
shared/core/api_response.py — Standard API response helpers for all tep services.

Defines the canonical response envelope:
    {"success": bool, "data": <any> | null, "error": <str> | null}

Usage:
    from shared.core.api_response import success_response, error_response

    @app.route("/api/thing")
    def get_thing():
        try:
            result = do_work()
            return success_response(result)
        except ValueError as e:
            return error_response(str(e), 400)
        except Exception as e:
            return error_response(str(e), 500)
"""

from __future__ import annotations

from typing import Any, Tuple

from flask import Response, jsonify

# ``unwrap_data`` is Flask-free and lives in ``shared.core.envelope`` so client libraries (e.g. the
# pipeline_maker engine) can use it without importing Flask; re-exported here for existing callers.
from shared.core.envelope import unwrap_data  # noqa: F401


def success_response(data: Any = None, status_code: int = 200) -> Tuple[Response, int]:
    """Return a standard success envelope with HTTP 200 (or specified code)."""
    return jsonify({"success": True, "data": data, "error": None}), status_code


def error_response(message: str, status_code: int = 500) -> Tuple[Response, int]:
    """Return a standard error envelope with the given HTTP status code."""
    return jsonify({"success": False, "data": None, "error": message}), status_code
