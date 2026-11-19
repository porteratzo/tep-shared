"""shared.core.auth: the role gate every Flask service uses, and the password comparison.

``make_role_required`` was at 0 % coverage in this module (other modules' route tests exercise it only
through their own apps, where it is often replaced by a pass-through lambda). It is the only RBAC code in
the system, so its truth table is pinned here directly on a minimal Flask app.
"""

from typing import Any, Callable, Optional

import pytest
from flask import Flask
from flask import session as flask_session

from shared.core.auth import ROLE_LEVELS, make_role_required, passwords_match

pytestmark = pytest.mark.unit

ROLES = ["operator", "maintenance", "admin"]
RANK = {"operator": 0, "maintenance": 1, "admin": 2}  # written out here, not read from the code under test


def _app(login_url: Any = "/login") -> Flask:
    app = Flask(__name__)
    app.secret_key = "test-secret"
    calls: list = []

    def make_view(min_role: str, name: str) -> Callable[..., Any]:
        @make_role_required(min_role, login_url=login_url)
        def view(*args: Any, **kwargs: Any) -> Any:
            calls.append((name, args, kwargs))
            return {"ok": name}

        view.__name__ = f"view_{name}"
        return view

    for role in ROLES:
        app.add_url_rule(f"/page/{role}", f"page_{role}", make_view(role, f"page_{role}"))
        app.add_url_rule(f"/api/{role}", f"api_{role}", make_view(role, f"api_{role}"))
    app.add_url_rule("/api/item/<int:item_id>", "item", make_view("maintenance", "item"))
    app.config["calls"] = calls

    @app.route("/_set_role/<role>")
    def set_role(role: str) -> str:
        flask_session["role"] = role
        return "set"

    return app


def _client(app: Flask, role: Optional[str]) -> Any:
    c = app.test_client()
    if role is not None:
        assert c.get(f"/_set_role/{role}").status_code == 200
    return c


# ---------------------------------------------------------------------------
# the role truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_role", ROLES)
@pytest.mark.parametrize("min_role", ROLES)
def test_api_access_follows_the_role_hierarchy(session_role: str, min_role: str) -> None:
    app = _app()
    resp = _client(app, session_role).get(f"/api/{min_role}")
    if RANK[session_role] >= RANK[min_role]:
        assert resp.status_code == 200 and resp.get_json() == {"ok": f"api_{min_role}"}
    else:
        assert resp.status_code == 403
        assert [c[0] for c in app.config["calls"]] == []  # the view body never ran


def test_role_levels_are_strictly_ordered() -> None:
    assert ROLE_LEVELS["operator"] < ROLE_LEVELS["maintenance"] < ROLE_LEVELS["admin"]


def test_no_session_role_means_operator() -> None:
    app = _app()
    c = _client(app, None)
    assert c.get("/api/operator").status_code == 200
    assert c.get("/api/maintenance").status_code == 403


def test_an_unknown_role_in_the_session_is_treated_as_operator_not_as_admin() -> None:
    """A tampered/legacy session value must never grant more than the lowest level (fail closed)."""
    app = _app()
    c = _client(app, "superuser")
    assert c.get("/api/operator").status_code == 200
    assert c.get("/api/maintenance").status_code == 403
    assert c.get("/api/admin").status_code == 403


def test_role_names_are_case_sensitive() -> None:
    assert _client(_app(), "ADMIN").get("/api/admin").status_code == 403


# ---------------------------------------------------------------------------
# denial responses: JSON for /api/*, redirect for pages
# ---------------------------------------------------------------------------


def test_denied_api_request_gets_the_error_envelope_naming_the_required_role() -> None:
    resp = _client(_app(), "operator").get("/api/admin")
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["success"] is False and "admin" in body["error"]


def test_denied_page_request_redirects_to_the_default_login_page() -> None:
    resp = _client(_app(), "operator").get("/page/maintenance")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")


def test_login_url_can_be_another_services_absolute_url() -> None:
    resp = _client(_app(login_url="http://main.local:5000/login"), None).get("/page/admin")
    assert resp.status_code == 302 and resp.headers["Location"] == "http://main.local:5000/login"


def test_login_url_callable_is_evaluated_per_request() -> None:
    seen: list = []

    def resolve() -> str:
        seen.append(len(seen))
        return f"/login?n={len(seen)}"

    app = _app(login_url=resolve)
    c = _client(app, None)
    assert c.get("/page/admin").headers["Location"].endswith("/login?n=1")
    assert c.get("/page/admin").headers["Location"].endswith("/login?n=2")
    c.get("/api/admin")  # an API denial never needs the login URL
    assert len(seen) == 2


def test_an_allowed_request_does_not_resolve_the_login_url() -> None:
    def boom() -> str:
        raise AssertionError("login_url must not be resolved when access is allowed")

    app = _app(login_url=boom)
    assert _client(app, "admin").get("/page/admin").status_code == 200


# ---------------------------------------------------------------------------
# decorator mechanics
# ---------------------------------------------------------------------------


def test_view_arguments_and_return_value_pass_through() -> None:
    app = _app()
    resp = _client(app, "admin").get("/api/item/7")
    assert resp.status_code == 200
    ((name, args, kwargs),) = app.config["calls"]
    assert name == "item" and kwargs == {"item_id": 7}


def test_wraps_preserves_the_view_name_so_flask_endpoints_do_not_collide() -> None:
    decorator = make_role_required("admin")

    @decorator
    def my_view() -> str:
        """doc"""
        return "x"

    assert my_view.__name__ == "my_view" and my_view.__doc__ == "doc"


def test_an_unknown_min_role_is_rejected_when_the_route_is_declared_not_on_the_first_request() -> None:
    """A typo such as ``make_role_required("admn")`` used to be a KeyError (HTTP 500) on every request of
    that route; it must fail at import/registration time instead."""
    with pytest.raises(ValueError, match="admn"):
        make_role_required("admn")


# ---------------------------------------------------------------------------
# passwords_match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "supplied, expected, result",
    [
        ("secret", "secret", True),
        ("secret", "Secret", False),
        ("secret ", "secret", False),
        ("", "secret", False),
        ("secret", "", False),
        ("", "", False),  # TS-1: a blank configured password matches nothing, not even a blank submission
        ("   ", "   ", False),
        ("x", "  ", False),
        ("pässwörd-密码", "pässwörd-密码", True),
        ("pässwörd", "passwörd", False),
        ("a" * 10_000, "a" * 10_000, True),
    ],
)
def test_passwords_match(supplied: str, expected: str, result: bool) -> None:
    assert passwords_match(supplied, expected) is result


@pytest.mark.parametrize("hostile", [None, 123, 1.5, b"secret", ["secret"], {"p": "secret"}, True])
def test_non_string_input_is_a_wrong_password_not_an_exception(hostile: Any) -> None:
    """compare_digest raises TypeError on these; a login route turned that into a 500."""
    assert passwords_match(hostile, "secret") is False


def test_comparison_is_constant_time_via_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    import hmac

    seen: list = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr("shared.core.auth.hmac.compare_digest", spy)
    assert passwords_match("pw", "pw") is True
    assert seen == [(b"pw", b"pw")]  # compared as UTF-8 bytes
