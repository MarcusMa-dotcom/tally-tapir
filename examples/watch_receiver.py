"""Let Tally Tapir listen and print what its watched display shows.

    python watch_receiver.py --id 3
    python watch_receiver.py --id 3 --screen 0 --listen-port 9800

The script switches the receiver of a running Tally Tapir on, sets the filter
and then asks the REST API twice a second what the watched display shows. It
prints a line whenever text or lamps change, and every packet with a problem.
"""

from __future__ import annotations

import argparse
import time

from api_client import ApiError, TallyTapirApi

LAMP_NAMES = ("off", "red", "green", "amber")


def describe(display: dict) -> str:
    """One line of text for a display entry of the receiver."""
    text = display["text"].rstrip()
    if display["lh"] is None:  # V3.1 has no lamps
        on = [str(n) for n in range(1, 5) if display["tallies"] & (1 << (n - 1))]
        return f"{text!r}  tally {', '.join(on) or 'none'}"
    return (f"{text!r}  left {LAMP_NAMES[display['lh']]}, text {LAMP_NAMES[display['txt']]}, "
            f"right {LAMP_NAMES[display['rh']]}")


def main() -> None:
    """Start the receiver and print what the watched display shows until Ctrl+C."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://localhost:8088", help="address of Tally Tapir")
    parser.add_argument("--id", type=int, help="UMD ID to watch, default: any")
    parser.add_argument("--screen", type=int, help="V5.0 screen to watch, default: any")
    parser.add_argument("--listen-port", type=int, default=9800, help="UDP port the receiver listens on")
    args = parser.parse_args()

    api = TallyTapirApi(args.base)
    try:
        api.set_receiver(transport="udp", port=args.listen_port, protocol="auto",
                         watch_id=args.id, watch_screen=args.screen)
        if not api.listen():
            raise SystemExit("Receiver did not start: " + api.receiver()["status"]["error"])
    except ApiError as exc:
        raise SystemExit(f"Tally Tapir refused the request ({exc})") from None
    except OSError as exc:
        raise SystemExit(f"Tally Tapir is not reachable at {args.base}: {exc}") from None

    print(f"Listening on UDP port {args.listen_port}. Stop with Ctrl+C.")
    shown = None
    problems_seen = 0
    try:
        while True:
            status = api.receiver()["status"]
            watched = status["watched"]
            if watched is not None:
                line = describe(watched)
                if line != shown:
                    shown = line
                    print(f"{time.strftime('%H:%M:%S')}  ID {watched['index']}: {line}")
            if status["problem_count"] > problems_seen:
                problems_seen = status["problem_count"]
                for entry in status["log"]:
                    for problem in entry["problems"]:
                        print(f"{time.strftime('%H:%M:%S')}  problem from {entry['source']}: {problem}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        api.stop_listening()


if __name__ == "__main__":
    main()
