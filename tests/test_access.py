import json
import os
import stat
import sys

import pytest

from tallytapir_app.access import (
    LOCK_SECONDS,
    MAX_FAILURES,
    Access,
    Throttle,
    hash_password,
)

PASSWORD = "correct horse battery"


def test_hash_is_salted_and_not_reversible_text():
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)
    assert first != second                      # a new random salt every time
    assert PASSWORD not in first
    assert first.startswith(("scrypt:", "pbkdf2:sha256:"))
    assert first.count("$") == 2                # method $ salt $ hash


def test_verify_accepts_only_the_right_user_and_password():
    access = Access()
    access.update(api_user="robot", api_password=PASSWORD, api_login=True)
    assert access.verify("api", "robot", PASSWORD)
    assert not access.verify("api", "robot", PASSWORD + "x")
    assert not access.verify("api", "Robot", PASSWORD)
    assert not access.verify("web", "robot", PASSWORD)      # the web login is a different one
    assert not access.verify("api", "robot", "")
    assert not access.verify("api", "robot", "x" * 10_000)  # oversized input is refused early


def test_no_login_possible_without_a_password_set():
    access = Access()
    assert not access.verify("web", "", "")
    assert not access.verify("web", "anyone", "anything")


def test_file_holds_hash_only_and_is_private(tmp_path):
    path = tmp_path / "access.json"
    access = Access(path)
    access.update(web_user="operator", web_password=PASSWORD, web_login=True)
    text = path.read_text(encoding="utf-8")
    assert PASSWORD not in text
    stored = json.loads(text)
    assert stored["web_hash"].startswith(("scrypt:", "pbkdf2:"))
    assert "web_password" not in stored
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_settings_survive_a_restart(tmp_path):
    path = tmp_path / "access.json"
    Access(path).update(api_enabled=False, web_user="operator", web_password=PASSWORD, web_login=True)
    again = Access(path)
    assert again.public()["api_enabled"] is False
    assert again.login_required("web")
    assert again.verify("web", "operator", PASSWORD)


def test_login_flag_without_hash_in_file_is_dropped(tmp_path):
    path = tmp_path / "access.json"
    path.write_text('{"web_login": true, "web_user": "operator", "web_hash": ""}', encoding="utf-8")
    assert not Access(path).login_required("web")


def test_password_and_user_rules():
    access = Access()
    with pytest.raises(ValueError, match="8 to 128"):
        access.update(web_user="operator", web_password="short")
    with pytest.raises(ValueError, match="colon"):
        access.update(web_user="mar:cus")
    with pytest.raises(ValueError, match="before requiring a login"):
        access.update(web_login=True)
    with pytest.raises(ValueError, match="Unknown setting"):
        access.update(web_hash="scrypt:1:1:1$x$y")      # hashes cannot be set from outside
    with pytest.raises(ValueError):
        access.update(web_enabled="yes")
    assert access.public()["web_login"] is False        # nothing was applied


def test_public_view_has_no_hash():
    access = Access()
    access.update(web_user="operator", web_password=PASSWORD)
    view = access.public()
    assert view["web_password_set"] is True
    assert not any("hash" in key for key in view)
    assert PASSWORD not in json.dumps(view)


def test_changing_the_password_ends_the_old_one():
    access = Access()
    access.update(api_user="robot", api_password=PASSWORD, api_login=True)
    assert access.verify("api", "robot", PASSWORD)      # now remembered as verified
    before = access.generation("api")
    access.update(api_password="a completely new one")
    assert access.generation("api") == before + 1
    assert not access.verify("api", "robot", PASSWORD)
    assert access.verify("api", "robot", "a completely new one")


def test_login_can_be_switched_off_and_on_again():
    access = Access()
    access.update(web_user="operator", web_password=PASSWORD, web_login=True)
    access.update(web_login=False)
    assert not access.login_required("web")
    access.update(web_login=True)                        # the stored password still counts
    assert access.verify("web", "operator", PASSWORD)


def test_throttle_locks_after_failures_and_releases():
    now = [1000.0]
    throttle = Throttle(clock=lambda: now[0])
    for _ in range(MAX_FAILURES - 1):
        throttle.failed("api", "10.0.0.5")
    assert throttle.wait_seconds("api", "10.0.0.5") == 0
    throttle.failed("api", "10.0.0.5")
    assert throttle.wait_seconds("api", "10.0.0.5") == LOCK_SECONDS
    assert throttle.wait_seconds("api", "10.0.0.6") == 0      # other addresses are not affected
    assert throttle.wait_seconds("web", "10.0.0.5") == 0      # nor the other login
    throttle.failed("api", "10.0.0.5")
    assert throttle.wait_seconds("api", "10.0.0.5") == LOCK_SECONDS * 2
    now[0] += LOCK_SECONDS * 2
    assert throttle.wait_seconds("api", "10.0.0.5") == 0
    throttle.succeeded("api", "10.0.0.5")
    throttle.failed("api", "10.0.0.5")
    assert throttle.wait_seconds("api", "10.0.0.5") == 0      # the count started again


def test_attempt_refuses_even_the_right_password_while_locked():
    access = Access()
    access.update(api_user="robot", api_password=PASSWORD, api_login=True)
    for _ in range(MAX_FAILURES):
        ok, _ = access.attempt("api", "10.0.0.5", "robot", "wrong password")
        assert not ok
    ok, wait = access.attempt("api", "10.0.0.5", "robot", PASSWORD)
    assert not ok and wait > 0
    ok, wait = access.attempt("api", "10.0.0.9", "robot", PASSWORD)
    assert ok and wait == 0


def test_listeners_hear_changes():
    access = Access()
    heard = []
    access.on_change(lambda: heard.append(access.public()["api_enabled"]))
    access.update(api_enabled=False)
    access.update(api_enabled=False)        # no change, no call
    assert heard == [False]
