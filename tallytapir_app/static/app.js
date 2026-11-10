"use strict";

// Send view of the web interface, and the helpers the other views share.
//
// The page keeps no state of its own. It shows what the server reports and sends every
// input to it. A poll every 400 ms keeps it in step with the desktop window and with
// other browsers that control the same Tally Tapir.

const POLL_MS = 400;
const LAMPS = ["off", "red", "green", "amber"];

const $ = (id) => document.getElementById(id);
const radios = (name) => Array.from(document.querySelectorAll(`input[name="${name}"]`));
const tallyBoxes = Array.from(document.querySelectorAll("input.tally"));

let state = null;
let lastRevision = -1;
let instance = null;
let portsLoaded = false;
// Settings the user changed here whose answer from the server is still pending.
// A count per setting, because several changes of one setting can be under way.
const pendingCounts = new Map();
const pending = {
  has: (key) => pendingCounts.has(key),
  add: (key) => pendingCounts.set(key, (pendingCounts.get(key) || 0) + 1),
  remove: (key) => {
    const left = (pendingCounts.get(key) || 0) - 1;
    if (left > 0) pendingCounts.set(key, left);
    else pendingCounts.delete(key);
  },
};
// Requests that change something go out one after another, so the server
// sees them in the order they were made.
let queue = Promise.resolve();
// Run a task after all tasks that were queued before it.
function enqueue(task) {
  queue = queue.then(task, task);
  return queue;
}

// Call the server and return its JSON answer. An error text in the answer is shown.
// A 401 means the session ended: go to the login page.
//   path:   address on this server
//   body:   object to send as JSON, leave out for a plain GET
//   method: HTTP method for requests with a body, POST by default
async function api(path, body, method) {
  const options = body === undefined
    ? {}
    : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (response.status === 401) {
    location.href = "/login";  // the session ended
    throw new Error("login required");
  }
  const data = await response.json();
  showNotice(response.ok ? "" : (data.error || `Request failed (${response.status})`));
  return data;
}

// Show a message above the page. An empty text hides the line.
function showNotice(message) {
  const notice = $("notice");
  notice.textContent = message;
  notice.hidden = !message;
}

// Send changed settings to the sender and draw the answer.
// The changed fields count as pending until the answer is in, so no poll overwrites them.
function change(changes) {
  const keys = Object.keys(changes);
  keys.forEach((key) => pending.add(key));
  return enqueue(async () => {
    try {
      const data = await api("/ui/api/sender", changes, "PATCH");
      keys.forEach((key) => pending.remove(key));
      if (data.settings) render(data, true);
    } catch (error) {
      keys.forEach((key) => pending.remove(key));
      showNotice("Tally Tapir is not reachable.");
    }
  });
}

// Trigger an action of the sender (connect, disconnect, send) and draw the answer.
function action(path) {
  return enqueue(async () => {
    try {
      const data = await api(path, {});
      if (data.settings) render(data, true);
    } catch (error) {
      showNotice("Tally Tapir is not reachable.");
    }
  });
}

// Write a value into a control unless the user is editing it right now.
function put(id, value) {
  const element = $(id);
  if (pending.has(id)) return;
  if (element.type === "checkbox") {
    element.checked = Boolean(value);
    return;
  }
  const typing = document.activeElement === element && element.tagName === "INPUT" && !element.readOnly;
  if (!typing && element.value !== String(value)) element.value = String(value);
}

// Check the radio button of a group that carries the value.
function putRadio(name, value) {
  if (pending.has(name)) return;
  radios(name).forEach((radio) => { radio.checked = radio.value === value; });
}

// Time of day for a timestamp in seconds, or "never" for none.
function clockTime(epochSeconds) {
  if (!epochSeconds) return "never";
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour12: false });
}

// Draw the whole Send view from a snapshot of the sender.
//   data:  the answer of the server: settings and status
//   force: draw even if nothing changed, used after an own request
function render(data, force) {
  if (data.instance !== instance) {
    // The server was restarted, its revision counter starts again.
    instance = data.instance;
    lastRevision = -1;
  }
  // Answers can arrive out of order: never draw an older state over a newer one.
  if (data.revision < lastRevision) return;
  if (!force && data.revision === lastRevision) return;
  lastRevision = data.revision;
  state = data;
  const s = data.settings;
  const status = data.status;
  const isV5 = s.protocol === "v5.0" || s.protocol === "v5.0w";
  const isV3 = s.protocol === "v3.1";
  const isSerial = s.transport === "serial";

  putRadio("transport", s.transport);
  putRadio("protocol", s.protocol);
  ["host", "port", "umd_id", "screen", "text", "interval_ms", "baudrate", "bytesize", "parity"]
    .forEach((id) => put(id, s[id]));
  put("stopbits", s.stopbits);
  ["utf16", "clock", "clock_tenths", "interval_enabled", "tally_toggle", "send_on_change", "hex_braces"]
    .forEach((id) => put(id, s[id]));
  put("brightness", s.brightness);
  $("brightness-value").textContent = s.brightness;

  $("ip-fields").hidden = isSerial;
  $("serial-fields").hidden = !isSerial;
  if (isSerial && !portsLoaded) loadPorts();
  if (isSerial) selectPort(s.serial_port);

  $("screen-field").hidden = !isV5;
  $("utf16-field").hidden = !isV5;
  $("text").readOnly = s.clock;
  $("clock_tenths").disabled = !s.clock;
  $("tally_toggle").disabled = !s.interval_enabled;
  $("umd_id").max = isV5 ? 65535 : 126;
  $("protocol-hint").textContent = isV5
    ? (s.protocol === "v5.0w"
      ? "V5.0W adds the DLE/STX wrapper for TCP. UMD ID 0 to 65535."
      : "V5.0 without wrapper, as used over UDP. UMD ID 0 to 65535.")
    : `${isV3 ? "V3.1" : "V4.0"} sends 16 characters of text. UMD ID 0 to 126.`;

  if (!pending.has("tally_bits")) {
    tallyBoxes.forEach((box) => { box.checked = Boolean(s.tally_bits & (1 << Number(box.dataset.bit))); });
  }
  $("tally-text-group").disabled = isV3;
  const v3On = [1, 2, 3, 4].filter((n) => status.v3_tallies & (1 << (n - 1)));
  $("tally-hint").textContent = isV3
    ? `V3.1: boxes 1, 2, 5 and 6 set tally 1 to 4. On now: ${v3On.length ? v3On.join(", ") : "none"}.`
    : (s.protocol === "v4.0"
      ? "V4.0: the lamps go into XDATA, boxes 1, 2, 5 and 6 also set tally 1 to 4."
      : "Two bits per lamp: first box red, second box green, both amber.");
  for (const [key, label] of [["lh", "Left"], ["txt", "Text"], ["rh", "Right"]]) {
    const name = LAMPS[status.lamps[key]];
    const lamp = $(`lamp-${key}`);
    lamp.className = `lamp ${name}`;
    lamp.lastElementChild.textContent = name;
  }

  const connectButton = $("connect");
  connectButton.textContent = status.connecting ? "Connecting..." : (status.connected ? "Disconnect" : "Connect");
  connectButton.disabled = status.connecting;
  $("send").disabled = !status.connected;
  const connState = $("conn-state");
  connState.textContent = status.connected ? `Connected: ${status.connection}` : "Disconnected";
  connState.classList.toggle("on", status.connected);
  $("error").textContent = status.error;

  const hex = $("packet-hex");
  hex.textContent = status.packet_error || status.packet_hex;
  hex.classList.toggle("invalid", Boolean(status.packet_error));
  $("packet-length").textContent = `${status.packet_length} bytes`;
  $("sent-info").textContent =
    `Sent: ${status.sent_count} packet${status.sent_count === 1 ? "" : "s"}. Last: ${clockTime(status.last_sent)}`;

  const log = $("log");
  log.replaceChildren();
  if (status.log.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "Nothing sent yet.";
    log.append(empty);
  }
  status.log.slice().reverse().forEach((entry) => {
    const item = document.createElement("li");
    for (const [cls, text] of [["when", clockTime(entry.time)], ["size", `${entry.length} B`], ["bytes", entry.hex]]) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      item.append(span);
    }
    log.append(item);
  });
}

// Select a serial port in the list. A port the list does not know is added.
function selectPort(device) {
  const select = $("serial_port");
  if (pending.has("serial_port")) return;
  if (device && !Array.from(select.options).some((option) => option.value === device)) {
    select.add(new Option(device, device));
  }
  select.value = device;
}

// Fill the list of serial ports with what the machine running Tally Tapir has.
async function loadPorts() {
  portsLoaded = true;
  try {
    const data = await api("/ui/api/serial-ports");
    const select = $("serial_port");
    select.replaceChildren(new Option("Select a port", ""));
    data.ports.forEach((port) => {
      const label = port.description && port.description !== port.device
        ? `${port.device} (${port.description})`
        : port.device;
      select.add(new Option(label, port.device));
    });
    $("serial-note").hidden = data.available;
    if (state) selectPort(state.settings.serial_port);
  } catch (error) {
    portsLoaded = false;
  }
}

// Send a change shortly after the last keystroke. The field counts as pending
// from the first keystroke on, so no answer from the server overwrites it.
function changeSoon(key, read, delay) {
  let timer = null;
  return () => {
    if (timer === null) pending.add(key);
    clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      change({ [key]: read() });
      pending.remove(key);
    }, delay);
  };
}

// Connect every control with its handler. Runs once when the page loads.
function bind() {
  for (const name of ["transport", "protocol"]) {
    radios(name).forEach((radio) => radio.addEventListener("change", () => change({ [name]: radio.value })));
  }
  $("text").addEventListener("input", changeSoon("text", () => $("text").value, 120));
  $("host").addEventListener("change", () => change({ host: $("host").value }));
  for (const id of ["port", "umd_id", "screen", "interval_ms"]) {
    $(id).addEventListener("change", () => {
      if ($(id).value !== "") change({ [id]: Number($(id).value) });
    });
  }
  for (const id of ["baudrate", "bytesize"]) {
    $(id).addEventListener("change", () => change({ [id]: Number($(id).value) }));
  }
  for (const id of ["parity", "serial_port"]) {
    $(id).addEventListener("change", () => change({ [id]: $(id).value }));
  }
  $("stopbits").addEventListener("change", () => change({ stopbits: Number($("stopbits").value) }));
  for (const id of ["utf16", "clock", "clock_tenths", "interval_enabled", "tally_toggle", "send_on_change", "hex_braces"]) {
    $(id).addEventListener("change", () => change({ [id]: $(id).checked }));
  }
  $("brightness").addEventListener("input", () => {
    $("brightness-value").textContent = $("brightness").value;
    change({ brightness: Number($("brightness").value) });
  });
  tallyBoxes.forEach((box) => box.addEventListener("change", () => {
    const bits = tallyBoxes.reduce((sum, item) => sum | (item.checked ? 1 << Number(item.dataset.bit) : 0), 0);
    change({ tally_bits: bits });
  }));
  $("connect").addEventListener("click", () => {
    action(state && state.status.connected ? "/ui/api/sender/disconnect" : "/ui/api/sender/connect");
  });
  $("send").addEventListener("click", () => action("/ui/api/sender/send"));
  $("refresh-ports").addEventListener("click", loadPorts);
}

// Ask the server for the state of the sender and draw it if it changed.
async function poll() {
  try {
    const response = await fetch("/ui/api/sender");
    if (response.status === 401) {
      location.href = "/login";
      return;
    }
    const data = await response.json();
    if (!response.ok) {
      showNotice(data.error || `Request failed (${response.status})`);
      return;
    }
    render(data, false);
    if ($("notice").textContent === "Tally Tapir is not reachable.") showNotice("");
  } catch (error) {
    showNotice("Tally Tapir is not reachable.");
  }
}

bind();
poll();
setInterval(poll, POLL_MS);
