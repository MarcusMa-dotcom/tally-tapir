"""Access settings: switches for web interface and REST API, optional logins.

Passwords are never stored. The file holds a salted scrypt hash (PBKDF2 where
scrypt is missing), made and checked with Werkzeug's password functions.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable

from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger("tallytapir_app.access")

AREAS = ("web", "api")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_USER_LENGTH = 64

MAX_FAILURES = 5          # failed logins from one address before it has to wait
LOCK_SECONDS = 30         # first waiting time, doubles with every further failure
MAX_LOCK_SECONDS = 900
THROTTLE_ENTRIES = 2000
VERIFIED_CACHE_ENTRIES = 16


@dataclass
class AccessSettings:
    """What the access file stores.

    Attributes:
        web_enabled: The web interface is switched on.
        api_enabled: The REST API is switched on.
        web_login: The web interface requires a login.
        web_user: User name of the web login.
        web_hash: Salted hash of the web password, empty if none is set.
        api_login: The REST API requires a login.
        api_user: User name of the API login.
        api_hash: Salted hash of the API password, empty if none is set.
    """
    web_enabled: bool = True
    api_enabled: bool = True
    web_login: bool = False
    web_user: str = ""
    web_hash: str = ""
    api_login: bool = False
    api_user: str = ""
    api_hash: str = ""


def hash_password(password: str) -> str:
    """Return a salted hash. Every call uses a new random salt."""
    try:
        return generate_password_hash(password, method="scrypt", salt_length=16)
    except (ValueError, AttributeError):  # this Python was built without scrypt
        return generate_password_hash(password, method="pbkdf2:sha256:600000", salt_length=16)


def check_user(name: Any) -> str:
    """Validate a user name and return it without surrounding spaces.

    A name has 1 to 64 characters. A colon is refused because HTTP Basic splits
    user and password at it, control characters because they hide in logs.

    Raises:
        ValueError: The name breaks one of these rules.
    """
    if not isinstance(name, str):
        raise ValueError("User name must be text")
    name = name.strip()
    if not 1 <= len(name) <= MAX_USER_LENGTH:
        raise ValueError(f"User name must have 1 to {MAX_USER_LENGTH} characters")
    if ":" in name or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ValueError("User name must not contain a colon or control characters")
    return name


def check_password(password: Any) -> str:
    """Validate a new password: text of 8 to 128 characters.

    Raises:
        ValueError: The password is too short, too long or not text.
    """
    if not isinstance(password, str):
        raise ValueError("Password must be text")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must have {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters")
    return password


class Throttle:
    """Slows down password guessing: after a few failures an address has to wait."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        """Create an empty throttle.

        Args:
            clock: Returns the time in seconds. Tests replace it to move time.
        """
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], tuple[int, float]] = {}

    def wait_seconds(self, area: str, address: str) -> float:
        """Seconds this address still has to wait, 0 if it may try."""
        with self._lock:
            _, until = self._entries.get((area, address), (0, 0.0))
            return max(0.0, until - self._clock())

    def failed(self, area: str, address: str) -> None:
        """Count a failed login of an address.

        From the fifth failure in a row the address is locked: first for 30
        seconds, then twice as long with every further failure, up to 15 minutes.
        """
        with self._lock:
            now = self._clock()
            if len(self._entries) >= THROTTLE_ENTRIES:
                self._entries = {k: v for k, v in self._entries.items() if v[1] > now}
                if len(self._entries) >= THROTTLE_ENTRIES:
                    self._entries.clear()
            count = self._entries.get((area, address), (0, 0.0))[0] + 1
            until = 0.0
            if count >= MAX_FAILURES:
                until = now + min(LOCK_SECONDS * 2 ** (count - MAX_FAILURES), MAX_LOCK_SECONDS)
            self._entries[(area, address)] = (count, until)

    def succeeded(self, area: str, address: str) -> None:
        """Forget the failures of an address after a good login."""
        with self._lock:
            self._entries.pop((area, address), None)


class Access:
    """Holds the access settings, checks logins and announces changes.

    One object is shared by the window, the web server and the command line.
    All methods may be called from any thread.

    Args:
        config_path: File to load the settings from and save them to. Without
            it nothing is stored, which the tests use.
    """
    def __init__(self, config_path: Path | str | None = None):
        """See the class description for the argument."""
        self._lock = threading.RLock()
        self._config_path = Path(config_path) if config_path else None
        self.settings = self._load() if self._config_path else AccessSettings()
        self.throttle = Throttle()
        self._generation = {area: 0 for area in AREAS}
        self._listeners: list[Callable[[], None]] = []
        self._cache_key = secrets.token_bytes(32)
        self._verified: dict[str, set[bytes]] = {area: set() for area in AREAS}
        # Checked when the user name is wrong, so both cases take the same time.
        self._dummy_hash = hash_password(secrets.token_hex(16))

    # -- reading ---------------------------------------------------------

    def enabled(self, area: str) -> bool:
        """True if the door ``area`` (``"web"`` or ``"api"``) is switched on."""
        return getattr(self.settings, f"{area}_enabled")

    def login_required(self, area: str) -> bool:
        """True if the door ``area`` (``"web"`` or ``"api"``) requires a login."""
        return getattr(self.settings, f"{area}_login")

    def generation(self, area: str) -> int:
        """Changes whenever the login of an area changes: old sessions end."""
        return self._generation[area]

    def public(self) -> dict:
        """The settings without the hashes, safe to show."""
        with self._lock:
            s = self.settings
            return {
                "web_enabled": s.web_enabled,
                "api_enabled": s.api_enabled,
                "web_login": s.web_login,
                "web_user": s.web_user,
                "web_password_set": bool(s.web_hash),
                "api_login": s.api_login,
                "api_user": s.api_user,
                "api_password_set": bool(s.api_hash),
            }

    def on_change(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` after every change of the settings.

        The web server uses this to open or close its port.
        """
        self._listeners.append(listener)

    # -- changing --------------------------------------------------------

    def update(self, **changes: Any) -> dict:
        """Apply changes. Passwords arrive as ``web_password`` / ``api_password``.

        Raises ValueError on bad input and changes nothing in that case.
        """
        allowed = {"web_enabled", "api_enabled", "web_login", "api_login",
                   "web_user", "api_user", "web_password", "api_password"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown setting {sorted(unknown)[0]!r}")

        with self._lock:
            new = AccessSettings(**asdict(self.settings))
            for name in ("web_enabled", "api_enabled", "web_login", "api_login"):
                if name in changes:
                    if not isinstance(changes[name], bool):
                        raise ValueError(f"{name} must be true or false")
                    setattr(new, name, changes[name])
            for area in AREAS:
                if f"{area}_user" in changes:
                    value = changes[f"{area}_user"]
                    blank = isinstance(value, str) and not value.strip()
                    setattr(new, f"{area}_user", "" if blank else check_user(value))
                password = changes.get(f"{area}_password")
                if password not in (None, ""):
                    setattr(new, f"{area}_hash", hash_password(check_password(password)))
                if getattr(new, f"{area}_login"):
                    label = "web interface" if area == "web" else "REST API"
                    if not getattr(new, f"{area}_user") or not getattr(new, f"{area}_hash"):
                        raise ValueError(
                            f"Set a user name and a password before requiring a login for the {label}")

            for area in AREAS:
                keys = (f"{area}_login", f"{area}_user", f"{area}_hash")
                if any(getattr(new, key) != getattr(self.settings, key) for key in keys):
                    self._generation[area] += 1
                    self._verified[area].clear()
            changed = new != self.settings
            self.settings = new
            if changed:
                self._save()
        if changed:
            for listener in list(self._listeners):
                listener()
        return self.public()

    # -- checking --------------------------------------------------------

    def verify(self, area: str, user: str, password: str) -> bool:
        """True if user and password are right for the area. Takes constant time per case."""
        if not isinstance(user, str) or not isinstance(password, str):
            return False
        if len(user) > MAX_USER_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
            return False
        with self._lock:
            expected_user = getattr(self.settings, f"{area}_user")
            stored = getattr(self.settings, f"{area}_hash")
            generation = self._generation[area]
            token = hmac.new(self._cache_key, f"{user}\0{password}".encode(), "sha256").digest()
            if token in self._verified[area]:
                return True
        user_ok = hmac.compare_digest(user.encode(), expected_user.encode())
        hash_ok = check_password_hash(stored or self._dummy_hash, password)  # slow on purpose
        ok = bool(stored) and bool(expected_user) and user_ok and hash_ok
        if ok:
            with self._lock:
                if generation == self._generation[area]:
                    if len(self._verified[area]) >= VERIFIED_CACHE_ENTRIES:
                        self._verified[area].clear()
                    # Remembers a keyed digest of the login, not the password itself.
                    self._verified[area].add(token)
        return ok

    def attempt(self, area: str, address: str, user: str, password: str) -> tuple[bool, float]:
        """Check a login coming from ``address``. Returns (ok, seconds to wait)."""
        wait = self.throttle.wait_seconds(area, address)
        if wait > 0:
            return False, wait
        if self.verify(area, user, password):
            self.throttle.succeeded(area, address)
            return True, 0.0
        self.throttle.failed(area, address)
        log.warning("Failed %s login from %s", area, address)
        return False, self.throttle.wait_seconds(area, address)

    # -- persistence -----------------------------------------------------

    def _load(self) -> AccessSettings:
        """Read the access file.

        Anything missing or of the wrong type keeps its default, so a damaged file
        cannot stop the program.
        """
        settings = AccessSettings()
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if not isinstance(data, dict):
            return settings
        for item in fields(AccessSettings):
            value = data.get(item.name)
            if isinstance(value, type(getattr(settings, item.name))):
                setattr(settings, item.name, value)
        for area in AREAS:  # a login without user or hash would lock nobody out, so drop it
            if not getattr(settings, f"{area}_user") or not getattr(settings, f"{area}_hash"):
                setattr(settings, f"{area}_login", False)
        return settings

    def _save(self) -> None:
        """Write the access file so that only its owner can read it."""
        if self._config_path is None:
            return
        try:
            path = self._config_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            # Owner only: the file holds the password hashes.
            handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(asdict(self.settings), stream, indent=2)
            os.replace(temp, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        except OSError as exc:
            log.warning("Access settings could not be saved: %s", exc)
