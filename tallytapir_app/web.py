"""Flask web interface and REST API of Tally Tapir.

Two doors lead to the same sender and receiver:

* ``/`` with ``/ui/api/...`` is the web interface. Its optional login uses a
  form and a session cookie.
* ``/api/v1/...`` is the REST API for other programs. Its optional login is
  HTTP Basic.

Each door has its own switch and its own login in the access settings.
"""

from __future__ import annotations

import logging
import math
import secrets
import ssl
import threading
from datetime import timedelta

from flask import Blueprint, Flask, jsonify, redirect, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.serving import WSGIRequestHandler, make_server

from . import __version__, about, protocol, transport
from .access import MIN_PASSWORD_LENGTH, Access
from .engine import MAX_INTERVAL_MS, MIN_INTERVAL_MS, TslSender
from .receiver import TslReceiver

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SESSION_HOURS = 12
MAX_BODY_BYTES = 64 * 1024
CHANGING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy":
        "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}
API_ENDPOINTS = [
    {"method": "GET", "path": "/api/v1/", "does": "This list"},
    {"method": "GET", "path": "/api/v1/sender", "does": "Sender settings and status"},
    {"method": "PATCH", "path": "/api/v1/sender", "does": "Change sender settings"},
    {"method": "POST", "path": "/api/v1/sender/connect", "does": "Open the connection"},
    {"method": "POST", "path": "/api/v1/sender/disconnect", "does": "Close the connection"},
    {"method": "POST", "path": "/api/v1/sender/send", "does": "Send the current packet once"},
    {"method": "GET", "path": "/api/v1/receiver", "does": "Receiver settings, displays and log"},
    {"method": "PATCH", "path": "/api/v1/receiver", "does": "Change receiver settings"},
    {"method": "POST", "path": "/api/v1/receiver/listen", "does": "Start listening"},
    {"method": "POST", "path": "/api/v1/receiver/stop", "does": "Stop listening"},
    {"method": "POST", "path": "/api/v1/receiver/clear", "does": "Forget received packets"},
    {"method": "GET", "path": "/api/v1/serial-ports", "does": "Serial ports of this machine"},
]


LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


class _QuietHandler(WSGIRequestHandler):
    """Answers without telling which server and Python version run here."""

    def version_string(self) -> str:
        """The server name sent with every answer."""
        return "Tally-Tapir"


def is_loopback(host: str) -> bool:
    """True if a web server bound to ``host`` can be reached from this machine only."""
    return host in LOOPBACK_HOSTS


def _request_hostname() -> str:
    """The host name the caller asked for, without port and IPv6 brackets."""
    host = request.host
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.split(":", 1)[0]


def _client() -> str:
    """Network address of the caller, used by the login throttle."""
    return request.remote_addr or "unknown"


def _error(message: str, status: int, **headers: str):
    """A JSON error answer.

    Args:
        message: Text for the ``error`` field.
        status: HTTP status code.
        **headers: Extra headers. Underscores in the names become dashes.
    """
    response = jsonify(error=message)
    response.status_code = status
    for name, value in headers.items():
        response.headers[name.replace("_", "-")] = value
    return response


def _too_many(wait: float):
    """Answer 429 with the waiting time, also as ``Retry-After`` header."""
    seconds = max(1, math.ceil(wait))
    return _error(f"Too many failed logins. Try again in {seconds} seconds.", 429,
                  Retry_After=str(seconds))


def _json_object():
    """The request body as dict, or None if it is not a JSON object."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _data_blueprint(name: str, engine: TslSender, receiver: TslReceiver) -> Blueprint:
    """Sender and receiver routes. Mounted once for the web page, once as REST API."""
    bp = Blueprint(name, __name__)

    def sender_state(**extra):
        """The sender snapshot as JSON, with extra fields such as ``ok``."""
        data = engine.snapshot()
        data.update(extra)
        return jsonify(data)

    def receiver_state(**extra):
        """The receiver snapshot as JSON, with extra fields such as ``ok``."""
        data = receiver.snapshot()
        data.update(extra)
        return jsonify(data)

    @bp.get("/sender")
    def get_sender():
        """GET /sender: settings and status of the sender."""
        return sender_state()

    @bp.route("/sender", methods=["PATCH"])
    def patch_sender():
        """PATCH /sender: change settings. Answers 400 with the reason if a value is refused."""
        changes = _json_object()
        if changes is None:
            return _error("Body must be a JSON object", 400)
        try:
            engine.update(**changes)
        except ValueError as exc:
            data = engine.snapshot()
            data["error"] = str(exc)
            return jsonify(data), 400
        return sender_state()

    @bp.post("/sender/connect")
    def connect():
        """POST /sender/connect: open the connection. ``ok`` tells whether it worked."""
        return sender_state(ok=engine.connect())

    @bp.post("/sender/disconnect")
    def disconnect():
        """POST /sender/disconnect: close the connection."""
        engine.disconnect()
        return sender_state(ok=True)

    @bp.post("/sender/send")
    def send():
        """POST /sender/send: send the current packet once."""
        return sender_state(ok=engine.send())

    @bp.get("/receiver")
    def get_receiver():
        """GET /receiver: settings, displays seen and log of the receiver."""
        return receiver_state()

    @bp.route("/receiver", methods=["PATCH"])
    def patch_receiver():
        """PATCH /receiver: change settings. Answers 400 with the reason if a value is refused."""
        changes = _json_object()
        if changes is None:
            return _error("Body must be a JSON object", 400)
        try:
            receiver.update(**changes)
        except ValueError as exc:
            data = receiver.snapshot()
            data["error"] = str(exc)
            return jsonify(data), 400
        return receiver_state()

    @bp.post("/receiver/listen")
    def listen():
        """POST /receiver/listen: start listening. ``ok`` tells whether it worked."""
        return receiver_state(ok=receiver.listen())

    @bp.post("/receiver/stop")
    def stop():
        """POST /receiver/stop: stop listening."""
        receiver.stop_listening()
        return receiver_state(ok=True)

    @bp.post("/receiver/clear")
    def clear():
        """POST /receiver/clear: forget everything received so far."""
        receiver.clear()
        return receiver_state(ok=True)

    @bp.get("/serial-ports")
    def serial_ports():
        """GET /serial-ports: the serial ports of this machine."""
        return jsonify(available=transport.serial_available(), ports=transport.list_serial_ports())

    return bp


def create_app(
    engine: TslSender,
    receiver: TslReceiver | None = None,
    access: Access | None = None,
    local_only: bool = True,
    tls: bool = False,
) -> Flask:
    """Create the Flask app.

    With ``local_only`` the app answers only requests addressed to this
    machine by a loopback name, which blocks DNS rebinding from web pages.
    """
    app = Flask(__name__)
    app.json.sort_keys = False
    # New key at every start: sessions end when the program ends.
    app.secret_key = secrets.token_bytes(32)
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_BODY_BYTES,
        SESSION_COOKIE_NAME="tally_tapir_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=tls,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
    )
    if receiver is None:
        receiver = TslReceiver()
    if access is None:
        access = Access()

    def logged_in() -> bool:
        """True if this browser holds a session for the current web login."""
        return session.get("g") == access.generation("web") and session.get("in") is True

    def start_session() -> None:
        """Begin a fresh session for the current web login."""
        session.clear()
        session.permanent = True
        session["in"] = True
        session["g"] = access.generation("web")

    # -- guards ----------------------------------------------------------

    def guard_api():
        """Gate of the REST API: first the switch, then HTTP Basic with the throttle."""
        if not access.enabled("api"):
            return _error("The REST API is switched off", 403)
        if not access.login_required("api"):
            return None
        challenge = {"WWW_Authenticate": 'Basic realm="Tally Tapir API", charset="UTF-8"'}
        auth = request.authorization
        if auth is None or auth.type != "basic" or auth.username is None:
            return _error("Login required", 401, **challenge)
        ok, wait = access.attempt("api", _client(), auth.username, auth.password or "")
        if ok:
            return None
        if wait > 0:
            return _too_many(wait)
        return _error("User name or password is wrong", 401, **challenge)

    def guard_web():
        """Gate of the web interface: first the switch, then the session.

        The login page and the static files stay reachable without a session.
        """
        wants_json = request.path.startswith("/ui/api/")
        if not access.enabled("web"):
            if wants_json:
                return _error("The web interface is switched off", 403)
            return "The web interface is switched off.\n", 403, {"Content-Type": "text/plain"}
        if not access.login_required("web") or logged_in():
            return None
        if request.path in ("/login", "/ui/api/login") or request.path.startswith("/static/"):
            return None
        if wants_json:
            return _error("Login required", 401)
        return redirect("/login")

    @app.before_request
    def guard():
        """Runs before every request.

        It checks the host name, turns away requests that other web pages could
        trigger, and then asks the gate of the door the request goes to.
        """
        if local_only and _request_hostname() not in LOOPBACK_HOSTS:
            return _error("This interface only answers on localhost", 403)
        if request.method in CHANGING_METHODS:
            # A JSON content type cannot be sent by a plain cross-site form,
            # and browsers name the page a request comes from.
            if not request.is_json:
                return _error("Requests that change something must use Content-Type application/json", 415)
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return _error("Cross-site request refused", 403)
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return _error("Request from another origin refused", 403)
        if request.path.startswith("/api/"):
            return guard_api()
        return guard_web()

    @app.after_request
    def harden(response):
        """Add the security headers and forbid caching of everything but static files."""
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        """HTTP errors as JSON on API paths, as the usual page elsewhere."""
        if request.path.startswith(("/api/", "/ui/api/")):
            return _error(exc.description or exc.name, exc.code or 500)
        return exc

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        """Any other error: log it and answer 500 without details."""
        logging.getLogger("tallytapir_app.web").exception("Request failed")
        return _error("Internal error", 500)

    # -- pages -----------------------------------------------------------

    @app.get("/")
    def index():
        """GET /: the page with all views."""
        return render_template(
            "index.html",
            version=__version__,
            protocols=protocol.PROTOCOL_LABELS,
            rx_protocols=protocol.RX_PROTOCOL_LABELS,
            transports=transport.TRANSPORT_LABELS,
            baudrates=transport.BAUDRATES,
            bytesizes=transport.BYTESIZES,
            parities=transport.PARITIES,
            stopbits=transport.STOPBITS,
            max_text=protocol.MAX_TEXT_CHARS,
            min_interval=MIN_INTERVAL_MS,
            max_interval=MAX_INTERVAL_MS,
            min_password=MIN_PASSWORD_LENGTH,
            about=about,
        )

    @app.get("/login")
    def login_page():
        """GET /login: the login form, or a redirect to the page if no login is needed."""
        if not access.login_required("web") or logged_in():
            return redirect("/")
        return render_template("login.html", version=__version__)

    # -- web interface only: login and access settings -------------------

    def access_state() -> dict:
        """The access settings for the Settings view, plus what the page needs for its warnings."""
        data = access.public()
        data.update(
            logged_in=access.login_required("web") and logged_in(),
            exposed=not local_only,
            tls=tls,
            min_password=MIN_PASSWORD_LENGTH,
        )
        return data

    @app.post("/ui/api/login")
    def login():
        """POST /ui/api/login: check user and password and start a session."""
        if not access.login_required("web"):
            return jsonify(ok=True)
        data = _json_object() or {}
        user, password = data.get("user"), data.get("password")
        ok, wait = access.attempt(
            "web", _client(),
            user if isinstance(user, str) else "",
            password if isinstance(password, str) else "",
        )
        if not ok:
            if wait > 0:
                return _too_many(wait)
            return _error("User name or password is wrong", 401)
        start_session()
        return jsonify(ok=True)

    @app.post("/ui/api/logout")
    def logout():
        """POST /ui/api/logout: end the session."""
        session.clear()
        return jsonify(ok=True)

    @app.get("/ui/api/access")
    def get_access():
        """GET /ui/api/access: switches and logins, without the hashes."""
        return jsonify(access_state())

    @app.route("/ui/api/access", methods=["PATCH"])
    def patch_access():
        """PATCH /ui/api/access: change switches and logins.

        Without a web login only this machine may do that. With one, a change of
        the web login needs the current password in ``current_password``.
        """
        changes = _json_object()
        if changes is None:
            return _error("Body must be a JSON object", 400)
        if not access.login_required("web") and _client() not in LOCAL_CLIENTS:
            # Without a web login, only this machine may hand out logins and switches.
            # Otherwise anyone on the network could take the program over.
            return _error("Access settings can be changed from another machine "
                          "only when the web interface requires a login", 403)
        current = changes.pop("current_password", None)
        changes_web_login = any(key in changes for key in ("web_login", "web_user", "web_password"))
        if access.login_required("web") and changes_web_login:
            # Changing the web login needs the current password again.
            ok, wait = access.attempt(
                "web", _client(), access.settings.web_user,
                current if isinstance(current, str) else "",
            )
            if not ok:
                if wait > 0:
                    return _too_many(wait)
                return _error("Enter the current web password to change the web login", 403)
        try:
            access.update(**changes)
        except ValueError as exc:
            return _error(str(exc), 400)
        if access.login_required("web"):
            start_session()  # this browser made the change and stays logged in
        return jsonify(access_state())

    # -- REST API only ---------------------------------------------------

    @app.get("/api/v1/")
    def api_index():
        """GET /api/v1/: name, version and the list of endpoints."""
        return jsonify(name="Tally Tapir", version=__version__, endpoints=API_ENDPOINTS)

    app.register_blueprint(_data_blueprint("ui", engine, receiver), url_prefix="/ui/api")
    app.register_blueprint(_data_blueprint("v1", engine, receiver), url_prefix="/api/v1")
    return app


class WebServer:
    """Runs the Flask app in a background thread, as long as a door is open.

    The server listens only while the web interface or the REST API is
    switched on. Switching both off closes the port.
    """

    def __init__(
        self,
        engine: TslSender,
        receiver: TslReceiver | None = None,
        access: Access | None = None,
        host: str = "127.0.0.1",
        port: int = 8088,
        tls: tuple[str, str] | None = None,
    ):
        """Prepare the server. The port opens with :meth:`start`.

        Args:
            engine: The sender.
            receiver: The receiver. A fresh one is made if left out.
            access: The access settings. Defaults to both doors open, no logins.
            host: Address to listen on. Anything but a loopback address makes the
                server reachable from other machines.
            port: Port to listen on, 0 for a free one.
            tls: Certificate file and key file for HTTPS, or None for HTTP.
        """
        self.host = host
        self.port = port
        self.error = ""
        self.access = access if access is not None else Access()
        self._tls = tls
        # The pages poll several times a second: keep the request log quiet.
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        self._app = create_app(engine, receiver, self.access,
                               local_only=is_loopback(host), tls=tls is not None)
        self._lock = threading.Lock()
        self._server = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self.access.on_change(self._access_changed)

    @property
    def exposed(self) -> bool:
        """True if the server listens beyond this machine."""
        return not is_loopback(self.host)

    @property
    def tls(self) -> bool:
        """True if the server speaks HTTPS."""
        return self._tls is not None

    @property
    def wanted(self) -> bool:
        """True if at least one door is switched on."""
        return self.access.enabled("web") or self.access.enabled("api")

    @property
    def running(self) -> bool:
        """True while the port is open."""
        return self._server is not None

    @property
    def url(self) -> str:
        """Address of the web interface as a person would type it."""
        scheme = "https" if self._tls else "http"
        shown = "localhost" if self.host in ("0.0.0.0", "::") or is_loopback(self.host) else self.host
        return f"{scheme}://{shown}:{self.port}/"

    def start(self) -> bool:
        """Open the port if a door is switched on. Returns False if that failed."""
        self.apply()
        return not self.error

    def apply(self) -> None:
        """Open or close the port to match the access settings."""
        with self._lock:
            if self._closed:
                return
            if self.wanted and self._server is None:
                try:
                    server = make_server(self.host, self.port, self._app, threaded=True,
                                         ssl_context=self._tls, request_handler=_QuietHandler)
                except SystemExit:
                    # Werkzeug ends the program when it cannot bind. Keep running instead.
                    self.error = (f"Cannot serve on {self.host}:{self.port}: "
                                  "the port is in use or not allowed")
                    return
                except (OSError, ssl.SSLError, ValueError) as exc:
                    self.error = f"Cannot serve on {self.host}:{self.port}: {exc}"
                    return
                self.error = ""
                self.port = server.server_port
                self._server = server
                self._thread = threading.Thread(target=server.serve_forever, name="tsl-web", daemon=True)
                self._thread.start()
            elif not self.wanted and self._server is not None:
                self._shutdown()

    def _shutdown(self) -> None:
        """Close the port. Called with the lock held."""
        server, thread = self._server, self._thread
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
        # Only now: "running" must stay true until the port is really closed.
        self._server = None
        self._thread = None

    def _access_changed(self) -> None:
        """Listener for access changes: adjust the port a moment later."""
        # A moment later, so the answer to the request that made the change still goes out.
        timer = threading.Timer(0.3, self.apply)
        timer.daemon = True
        timer.start()

    def stop(self) -> None:
        """Close the port for good, at program end."""
        with self._lock:
            self._closed = True
            if self._server is not None:
                self._shutdown()
