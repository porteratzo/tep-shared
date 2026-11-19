"""Role-based access control shared by every service's Flask app.

Config-password + signed-session-cookie auth: a route declares the minimum
role it needs via :func:`make_role_required`, and ``flask.session["role"]``
(set by whichever service's login page the operator used) carries the role.
There is no User DB model — this intentionally stays that lightweight.
"""

from __future__ import annotations

import functools
import hmac
from typing import Any, Callable, Union

from flask import redirect, request
from flask import session as flask_session

from .api_response import error_response

ROLE_LEVELS = {"operator": 0, "maintenance": 1, "admin": 2}


def passwords_match(supplied: Any, expected: str) -> bool:
    """Constant-time password check that never raises on hostile input.

    ``hmac.compare_digest`` raises ``TypeError`` for a non-string or a non-ASCII ``str``, which a
    login route turned into a 500. Anything that isn't a string is simply a wrong password, and
    both sides are compared as UTF-8 bytes so non-ASCII passwords work.

    Fails closed (TS-1): a blank or whitespace-only *expected* password (a role whose
    ``ADMIN_PASSWORD`` / ``MAINTENANCE_PASSWORD`` was set empty) matches nothing, not even an empty
    submission, so such a role can never log in. A blank *supplied* password never matches either.
    """
    if not isinstance(supplied, str) or not isinstance(expected, str):
        return False
    if not expected.strip() or not supplied.strip():
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def make_role_required(
    min_role: str, login_url: Union[str, Callable[[], str]] = "/login"
) -> Callable[..., Any]:
    """Return a decorator that enforces a minimum role on a route.

    ``login_url`` is where an unauthenticated non-API request gets redirected —
    the caller's own `/login` page by default, or another service's login page
    (e.g. pipeline_maker redirecting to the main dashboard's `/login`) when this
    service has no login page of its own. Pass a zero-arg callable instead of a
    string when the URL depends on per-request state (e.g. the browser's host).

    Raises ``ValueError`` for a ``min_role`` that isn't a known role, so a typo fails when the route is
    declared rather than as a ``KeyError`` (HTTP 500) on every request to it.
    """
    if min_role not in ROLE_LEVELS:
        raise ValueError(f"Unknown role {min_role!r}; expected one of {sorted(ROLE_LEVELS)}")

    def decorator(f: Any) -> Any:
        @functools.wraps(f)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            role = flask_session.get("role", "operator")
            if ROLE_LEVELS.get(role, 0) < ROLE_LEVELS[min_role]:
                if request.path.startswith("/api/"):
                    return error_response(f"Insufficient permissions — required: {min_role}", 403)
                resolved_login_url = login_url() if callable(login_url) else login_url
                return redirect(resolved_login_url)
            return f(*args, **kwargs)

        return wrapped

    return decorator
