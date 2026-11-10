# Security audit of Tally Tapir 0.1.0

Date: 10 November 2026. Checked were the app (`tallytapir_app`, this
repository) and the library (`tallytapir`,
[tally-tapir-lib](https://github.com/MarcusMa-dotcom/tally-tapir-lib)) in the
state of release 0.1.0.

**What this is:** a self-review by the author of the code. It is not an
independent audit and not a penetration test by a third party. Where strangers
can read or send on the network, it does not replace an independent review.

## Result in short

- No open finding rated high or medium. Twelve findings came up during the
  audit and were fixed. Eleven are covered by a test, F11 was checked by hand
  on a fresh installation.
- Five risks remain on purpose. They are listed below with a recommendation.
- As delivered, the program can be reached from its own machine only and
  therefore needs no login. Once it is opened to the network
  (`--host 0.0.0.0`), logins and HTTPS belong with it. The program warns then.

## What was checked

| Check | Tool or method | Result |
|---|---|---|
| Known vulnerabilities of the dependencies | pip-audit against the tested versions | None known |
| Static code analysis | bandit over 4 400 lines of app, library, examples and packaging script | 13 hints, none high: 3 false alarms, 10 deliberate choices (see below) |
| Dangerous patterns | Search for `eval`, `exec`, `pickle`, `subprocess`, `shell=True`, `innerHTML`, inline scripts, debug mode | No hits |
| Input from outside | 22 prepared inputs, 9 wrong device names, broken JSON bodies, oversized bodies | All refused, state unchanged |
| Received packets | 40 000 random and damaged packets, 3 000 random blocks per protocol as a stream | No crash. One fault found and fixed (F8) |
| Logins | Wrong logins, broken headers, forged cookies, response times | As intended, times equal (83 ms and 84 ms) |
| Attacks from other web pages | Foreign origin, foreign host name, forms without JSON | Refused |
| The running program | Installation, start options, HTTPS, file permissions | As intended (see "Checked on the running program") |

Tested versions: Flask 3.1.3, Werkzeug 3.1.9, Jinja2 3.1.6, MarkupSafe 3.0.4,
itsdangerous 2.2.0, click 8.5.1, blinker 1.9.0, pyserial 3.5, Python 3.15.2.
The list is in `requirements-tested.txt`.

## Where the program can be attacked

| Surface | Who can reach it | What is possible there |
|---|---|---|
| Web server (web interface and REST API) | By default the local machine only. With `--host 0.0.0.0` the network | Control sender and receiver, change the access settings |
| Receiver (UDP, TCP, serial) | After "Listen" everyone on the network who can send packets | Deliver packets, which are displayed |
| Settings files | The logged-in user of the machine | Settings and password hashes |
| Other web pages in the user's browser | Every page visited | Trigger requests to `localhost` |

## Findings that were fixed

| No. | Rating | Finding | Fix |
|---|---|---|---|
| F1 | high | With the port taken, the web server ended the whole program including the window instead of reporting an error | The case is caught, the program keeps running and shows the error |
| F2 | medium | A prepared host name (null byte) left the sender stuck in "connecting" for good | Host names are checked, every connection error ends cleanly |
| F3 | medium | A prepared listen address caused an unhandled error in the receiver | All errors on opening come back as a message |
| F4 | medium | With the web interface reachable from the network without login, anyone there could set a login or switch the API login off and take the program over | Without a web login, only the local machine may change the access settings |
| F5 | medium | Other web pages could trigger actions on the local program through the browser, for example "Send" | Changing requests need the JSON content type, foreign origins are refused |
| F6 | low | The serial port field accepted any file path, and the error message told whether the file exists | Device names such as `/dev/ttyUSB0` or `COM3` only |
| F7 | low | The server named the versions of Werkzeug and Python in every answer | The banner is "Tally-Tapir" only |
| F8 | low | A V3.1 packet that was cut short stayed in the buffer and spoiled the next packet | Cut-short packets are reported and dropped |
| F9 | low | The receiver accepted any number of TCP connections | At most 32 at a time |
| F10 | low | Headers against embedding in foreign pages and against caching were missing | `Content-Security-Policy`, `X-Frame-Options`, `nosniff`, `no-store` |
| F11 | low | Installing the app would have looked the library up by its name in a package index. Whoever registers that name there could have slipped code in | The app installs the library from its repository at a fixed tag, never by name |
| F12 | low | A faulty callback ended the receiving of the library | The error is reported, receiving continues |

The ratings are the author's own judgement for a test tool in a broadcast
network: "high" means the program fails without anyone's doing, "medium" a
takeover or blockade by someone with network access, "low" a leak of
information or a hardening step.

## Passwords and logins

| Point | How it is done |
|---|---|
| Storage | As a hash only: scrypt (N = 32768, r = 8, p = 1) with a random salt of 16 characters per password. If the Python installation lacks scrypt, PBKDF2-SHA256 with 600 000 rounds |
| File | `settings.access.json`, readable by the owner only (0600). On Windows the permissions of the user profile apply |
| Rules | 8 to 128 characters, user name 1 to 64 characters without colon |
| Entry | In the windows, by a prompt in the terminal, or by an environment variable. Never as a start option, so the password does not appear in the process list |
| Output | Hashes and passwords appear in no answer and in no log |
| Guessing | After 5 failed logins from one address, 30 seconds of waiting, twice as long with every further failure, at most 15 minutes. Separate for web interface and API |
| Response time | An unknown user and a wrong password cost the same computing time |
| Web interface | Login form, session cookie with `HttpOnly` and `SameSite=Strict`, with HTTPS also `Secure`. Sessions end after 12 hours, on logout, on every change of the login and on restart |
| REST API | HTTP Basic. A successful check is remembered until the next change as a keyed fingerprint, not the password itself |
| Changing the web login | Needs the current password again |

Web interface and REST API have separate switches and separate logins. With
both switched off, the program closes the port.

## Risks that remain

| No. | Risk | Recommendation |
|---|---|---|
| R1 | Without HTTPS, password and session cookie cross the network unencrypted. Whoever listens can log in | On a network always start with `--tls-cert` and `--tls-key`. Checked: TLS 1.3, TLS 1.1 is refused |
| R2 | The built-in web server is a simple development server. It has no protection against very many or deliberately slow connections | Run it on a trusted network only, otherwise put a reverse proxy in front. Then switch the web login on, because the proxy looks like the local machine to the program |
| R3 | Whoever has access to web interface or API can make the machine send TSL packets to any address on the network. That is what the tool is for | Limit access to the people who may do that (login) |
| R4 | TSL itself has neither login nor encryption. The receiver shows what arrives, forged packets included | Do not use it as proof of the state of an installation where strangers can send on the network |
| R5 | The dependencies are not locked to fixed versions | For permanent installations use `requirements-tested.txt` and repeat the check with pip-audit on updates |

Further notes that need no action:

- The guard against password guessing counts per network address. Behind a
  shared address (NAT, proxy) an attacker also locks out the other users for
  the waiting time.
- Cookies belong to the host name, not to the port. Another service on the
  same machine gets the session cookie sent by the browser.
- A password in an environment variable can be read by other programs of the
  same user. The prompt in the terminal avoids that.
- After "Listen" the receiver listens on all network interfaces by default
  (`0.0.0.0`). For a receiving tool that is intended.
- The packaged Mac app is signed ad hoc, not with an Apple Developer ID, and
  it is not notarized. macOS asks for an explicit permission on first start.

## Hints of the static analysis

| Hint | Count | Assessment |
|---|---|---|
| B105 "hardcoded password" | 2 | False alarm: these are the names of the environment variables |
| B104 "binds to all interfaces" | 8 | 7 deliberate (default of the receiver, in app, library and three examples), 1 false alarm (a text comparison) |
| B110 "error is swallowed" | 2 | Deliberate: a faulty callback while reporting an error must not end the receiving |
| B310 "URL open" | 1 | In the example client. It accepts `http://` and `https://` addresses only |

## Checked on the running program

- Installing the app into a fresh environment: the library comes from its
  repository, there is no lookup of its name on a package index.
- Start with `--web off --api-login robot` and the password from the
  environment: API without login 401, with login 200, web interface 403.
- Access file with permissions `-rw-------`, no plain password in it, the hash
  begins with `scrypt:32768:8:1$`.
- Both doors off and no window: the program ends with a message.
- HTTPS with a self-signed certificate: TLS 1.3, plain HTTP on the same port
  and TLS 1.1 are refused.
- Login, logout and a wrong password in the browser, without messages from
  the Content Security Policy.
- The packaged Mac app in an empty environment: it depends on nothing outside
  its own bundle.

## Not checked

- Running on Windows and Linux. File permissions and network behaviour differ
  there.
- Behaviour under high load and deliberate overloading of the web server.
- The serial line with real hardware.
- The source code of the dependencies. They were only checked against the
  database of known vulnerabilities.

## Tests to repeat the checks

The security tests are in `tests/test_security.py`, `tests/test_access.py` and
`tests/test_web.py`, the random tests in `tests/test_library.py` of the
library. Together with the other tests there are 167 for the app and 21 for
the library.
