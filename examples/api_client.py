"""Control Tally Tapir from Python through its REST API.

Needs nothing but the standard library. Run it against a started Tally Tapir:

    python api_client.py --text "CAM 3" --id 3 --right red
    python api_client.py --host 192.168.1.50 --target-port 9800 --text "PGM" --left green --right green
    python api_client.py --base https://tapir.example:8088 --user robot --text "ON AIR" --right red

With ``--user`` the password is asked for, or read from TALLY_TAPIR_API_PASSWORD.

The class ``TallyTapirApi`` below is small enough to copy into your own program.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import urllib.error
import urllib.request

LAMPS = {"off": 0, "red": 1, "green": 2, "amber": 3}


class ApiError(Exception):
    """The API answered with an error. ``status`` is the HTTP status code."""

    def __init__(self, status: int, message: str):
        """Keep the HTTP status code next to the message."""
        super().__init__(f"{status}: {message}")
        self.status = status


class TallyTapirApi:
    """A thin client for the REST API of Tally Tapir."""

    def __init__(self, base: str = "http://localhost:8088", user: str | None = None,
                 password: str | None = None, timeout: float = 5.0):
        """Remember where Tally Tapir runs and, if needed, the API login.

        Args:
            base: Address of Tally Tapir, for example ``http://localhost:8088``.
            user: API user name, if the API requires a login.
            password: Password of that user.
            timeout: Seconds to wait for an answer.

        Raises:
            ValueError: ``base`` is not an http or https address.
        """
        if not base.startswith(("http://", "https://")):
            raise ValueError("base must start with http:// or https://")
        self.base = base.rstrip("/") + "/api/v1"
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if user is not None:
            token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
            self._headers["Authorization"] = f"Basic {token}"

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        """Send one request and return the JSON answer.

        Raises:
            ApiError: Tally Tapir answered with an error status.
            OSError: Tally Tapir is not reachable.
        """
        # Every request that changes something needs a JSON body, an empty one is fine.
        data = None if method == "GET" else json.dumps(body or {}).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = json.load(exc).get("error", exc.reason)
            except ValueError:
                message = exc.reason
            raise ApiError(exc.code, message) from None

    # -- sender ----------------------------------------------------------

    def sender(self) -> dict:
        """Settings and status of the sender."""
        return self._call("GET", "/sender")

    def set_sender(self, **settings) -> dict:
        """Change sender settings, for example ``text="CAM 1", tally_bits=1``."""
        return self._call("PATCH", "/sender", settings)

    def connect(self) -> bool:
        """Open the connection of the sender. Returns True if that worked."""
        return self._call("POST", "/sender/connect")["ok"]

    def disconnect(self) -> None:
        """Close the connection of the sender."""
        self._call("POST", "/sender/disconnect")

    def send(self) -> bool:
        """Send the current packet once."""
        return self._call("POST", "/sender/send")["ok"]

    # -- receiver --------------------------------------------------------

    def receiver(self) -> dict:
        """Settings of the receiver, the displays it has seen and its log."""
        return self._call("GET", "/receiver")

    def set_receiver(self, **settings) -> dict:
        """Change receiver settings, for example ``port=9800, watch_id=3``."""
        return self._call("PATCH", "/receiver", settings)

    def listen(self) -> bool:
        """Let the receiver listen. Returns True if that worked."""
        return self._call("POST", "/receiver/listen")["ok"]

    def stop_listening(self) -> None:
        """Stop the receiver."""
        self._call("POST", "/receiver/stop")


def lamp_bits(left: str = "off", text: str = "off", right: str = "off") -> int:
    """The ``tally_bits`` value for three lamp colours: two bits per lamp."""
    return LAMPS[right] | (LAMPS[text] << 2) | (LAMPS[left] << 4)


def main() -> None:
    """Read the command line, set the display and send it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://localhost:8088", help="address of Tally Tapir")
    parser.add_argument("--user", help="API user name, if the API requires a login")
    parser.add_argument("--host", help="where the TSL packets go, default: keep the setting")
    parser.add_argument("--target-port", type=int, help="port the TSL packets go to")
    parser.add_argument("--protocol", choices=("v3.1", "v4.0", "v5.0", "v5.0w"))
    parser.add_argument("--id", type=int, default=1, help="UMD ID")
    parser.add_argument("--text", default="CAM 1")
    parser.add_argument("--left", choices=LAMPS, default="off")
    parser.add_argument("--text-tally", choices=LAMPS, default="off")
    parser.add_argument("--right", choices=LAMPS, default="off")
    args = parser.parse_args()

    password = None
    if args.user:
        password = os.environ.get("TALLY_TAPIR_API_PASSWORD") or getpass.getpass("API password: ")
    api = TallyTapirApi(args.base, args.user, password)

    settings = {"umd_id": args.id, "text": args.text,
                "tally_bits": lamp_bits(args.left, args.text_tally, args.right)}
    for name, value in (("host", args.host), ("port", args.target_port), ("protocol", args.protocol)):
        if value is not None:
            settings[name] = value
    try:
        state = api.set_sender(**settings)
        if not state["status"]["connected"] and not api.connect():
            raise SystemExit("Could not connect: " + api.sender()["status"]["error"])
        api.send()  # TSL carries the whole state, so sending once more does no harm
        status = api.sender()["status"]
    except ApiError as exc:
        raise SystemExit(f"Tally Tapir refused the request ({exc})") from None
    except OSError as exc:
        raise SystemExit(f"Tally Tapir is not reachable at {args.base}: {exc}") from None
    print(f"Sent to {status['connection']}: {status['packet_hex']}")


if __name__ == "__main__":
    main()
