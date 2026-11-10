"use strict";

// Receive view. Uses $, api, enqueue, showNotice, clockTime and LAMPS from app.js.

const RX_PROTOCOL_NAMES = { "v3.1": "V3.1", "v4.0": "V4.0", "v5.0": "V5.0", "v5.0w": "V5.0W", junk: "?" };

let rxState = null;
let rxRevision = -1;
let rxInstance = null;
let rxPortsLoaded = false;
const rxPending = new Map();

const rxRadios = () => Array.from(document.querySelectorAll('input[name="rx_transport"]'));
const rxHeld = (key) => rxPending.has(key);

// Send changed settings to the receiver and draw the answer.
// As in the Send view, changed fields count as pending until the answer is in.
function rxChange(changes) {
  const keys = Object.keys(changes);
  keys.forEach((key) => rxPending.set(key, (rxPending.get(key) || 0) + 1));
  const release = () => keys.forEach((key) => {
    const left = rxPending.get(key) - 1;
    if (left > 0) rxPending.set(key, left);
    else rxPending.delete(key);
  });
  return enqueue(async () => {
    try {
      const data = await api("/ui/api/receiver", changes, "PATCH");
      release();
      if (data.settings) rxRender(data, true);
    } catch (error) {
      release();
      showNotice("Tally Tapir is not reachable.");
    }
  });
}

// Trigger an action of the receiver (listen, stop, clear) and draw the answer.
function rxAction(path) {
  return enqueue(async () => {
    try {
      const data = await api(path, {});
      if (data.settings) rxRender(data, true);
    } catch (error) {
      showNotice("Tally Tapir is not reachable.");
    }
  });
}

// Write a receiver setting into its control, unless the user is editing it.
// An empty filter arrives as null and shows as an empty field.
function rxPut(key, value) {
  const element = $(`rx_${key}`);
  if (rxHeld(key)) return;
  if (element.type === "checkbox") {
    element.checked = Boolean(value);
    return;
  }
  const text = value === null ? "" : String(value);
  const typing = document.activeElement === element && element.tagName === "INPUT";
  if (!typing && element.value !== text) element.value = text;
}

// A table cell for a lamp: a colour patch and the colour name, or a dash without lamp.
function lampCell(value) {
  const cell = document.createElement("td");
  if (value === null) {
    cell.textContent = "-";
    return cell;
  }
  const swatch = document.createElement("span");
  swatch.className = `swatch ${LAMPS[value]}`;
  cell.append(swatch, LAMPS[value]);
  return cell;
}

// The tallies that are on, as text. A dash where the version has none.
function tallyList(bits) {
  if (bits === null) return "-";
  const on = [1, 2, 3, 4].filter((n) => bits & (1 << (n - 1)));
  return on.length ? on.join(", ") : "none";
}

// True if a row of the table is the display the watched display shows.
function isWatched(display, watched) {
  return watched && display.screen === watched.screen && display.index === watched.index
    && display.protocol === watched.protocol;
}

// Draw the whole Receive view from a snapshot of the receiver.
//   data:  the answer of the server: settings and status
//   force: draw even if nothing changed, used after an own request
function rxRender(data, force) {
  if (data.instance !== rxInstance) {
    rxInstance = data.instance;
    rxRevision = -1;
  }
  if (data.revision < rxRevision) return;
  if (!force && data.revision === rxRevision) return;
  rxRevision = data.revision;
  rxState = data;
  const s = data.settings;
  const status = data.status;
  const isSerial = s.transport === "serial";

  if (!rxHeld("transport")) rxRadios().forEach((radio) => { radio.checked = radio.value === s.transport; });
  ["bind", "port", "baudrate", "bytesize", "parity", "stopbits", "protocol", "watch_screen", "watch_id",
    "hex_braces"].forEach((key) => rxPut(key, s[key]));
  $("rx-ip-fields").hidden = isSerial;
  $("rx-serial-fields").hidden = !isSerial;
  if (isSerial && !rxPortsLoaded) rxLoadPorts();
  if (isSerial) rxSelectPort(s.serial_port);

  const listenButton = $("rx-listen");
  listenButton.textContent = status.listening ? "Stop listening" : "Listen";
  const stateLabel = $("rx-state");
  let label = status.listening ? `Listening: ${status.description}` : "Not listening";
  if (status.listening && s.transport === "tcp") label += `, ${status.clients} client${status.clients === 1 ? "" : "s"}`;
  stateLabel.textContent = label;
  stateLabel.classList.toggle("on", status.listening);
  $("rx-error").textContent = status.error;

  // Watched display
  const watched = status.watched;
  const display = $("rx-display");
  const text = $("rx-text");
  const lampName = (value) => (value === null || value === undefined ? "" : LAMPS[value]);
  display.className = `umd-display${watched ? ` dim-${watched.brightness}` : ""}`;
  $("rx-lamp-lh").className = `umd-lamp ${watched ? lampName(watched.lh) : ""}`;
  $("rx-lamp-rh").className = `umd-lamp ${watched ? lampName(watched.rh) : ""}`;
  text.className = `umd-text ${watched ? lampName(watched.txt) : ""}`;
  text.textContent = watched ? watched.text : "";
  if (watched) {
    const parts = [RX_PROTOCOL_NAMES[watched.protocol]];
    if (watched.screen !== null) parts.push(`screen ${watched.screen}`);
    parts.push(`ID ${watched.index}`);
    if (watched.lh !== null) {
      parts.push(`left ${LAMPS[watched.lh]}, text ${LAMPS[watched.txt]}, right ${LAMPS[watched.rh]}`);
    }
    if (watched.tallies !== null) parts.push(`tally ${tallyList(watched.tallies)}`);
    parts.push(`brightness ${watched.brightness}`);
    parts.push(`${watched.count} packet${watched.count === 1 ? "" : "s"}, last ${clockTime(watched.time)}`);
    $("rx-display-info").textContent = parts.join(" · ");
  } else {
    $("rx-display-info").textContent = "Nothing received for this filter yet.";
  }

  // Displays seen
  const body = $("rx-displays").tBodies[0];
  body.replaceChildren();
  status.displays.forEach((d) => {
    const row = document.createElement("tr");
    if (isWatched(d, watched)) row.className = "watched";
    const cell = (value, cls) => {
      const td = document.createElement("td");
      if (cls) td.className = cls;
      td.textContent = value;
      return td;
    };
    row.append(
      cell(d.screen === null ? "-" : d.screen),
      cell(d.index),
      cell(d.text, "text"),
      lampCell(d.lh),
      lampCell(d.txt),
      lampCell(d.rh),
      cell(tallyList(d.tallies)),
      cell(d.brightness),
      cell(RX_PROTOCOL_NAMES[d.protocol]),
      cell(d.count),
      cell(clockTime(d.time)),
    );
    body.append(row);
  });
  $("rx-displays-note").textContent = status.display_count === 0
    ? "No display seen yet."
    : (status.display_count > status.displays.length
      ? `Showing ${status.displays.length} of ${status.display_count} displays.`
      : "The watched display is shown in bold.");

  // Log
  $("rx-count").textContent = status.packet_count;
  $("rx-problems").textContent = status.problem_count;
  const log = $("rx-log");
  log.replaceChildren();
  if (status.log.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "Nothing received yet.";
    log.append(empty);
  }
  status.log.slice().reverse().forEach((entry) => {
    const item = document.createElement("li");
    if (!entry.matched) item.className = "other";
    const add = (cls, value) => {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = value;
      item.append(span);
    };
    add("when", clockTime(entry.time));
    add("size", `${entry.source}  ${RX_PROTOCOL_NAMES[entry.protocol] || entry.protocol}  ${entry.length} B`);
    add("bytes", entry.hex);
    if (entry.summary) add("summary", entry.matched ? entry.summary : `${entry.summary} (not watched)`);
    entry.problems.forEach((problem) => add("problem", `Problem: ${problem}`));
    log.append(item);
  });
}

// Select a serial port in the list. A port the list does not know is added.
function rxSelectPort(device) {
  const select = $("rx_serial_port");
  if (rxHeld("serial_port")) return;
  if (device && !Array.from(select.options).some((option) => option.value === device)) {
    select.add(new Option(device, device));
  }
  select.value = device;
}

// Fill the list of serial ports with what the machine running Tally Tapir has.
async function rxLoadPorts() {
  rxPortsLoaded = true;
  try {
    const data = await api("/ui/api/serial-ports");
    const select = $("rx_serial_port");
    select.replaceChildren(new Option("Select a port", ""));
    data.ports.forEach((port) => {
      const label = port.description && port.description !== port.device
        ? `${port.device} (${port.description})`
        : port.device;
      select.add(new Option(label, port.device));
    });
    $("rx-serial-note").hidden = data.available;
    if (rxState) rxSelectPort(rxState.settings.serial_port);
  } catch (error) {
    rxPortsLoaded = false;
  }
}

// Connect every control of the Receive view with its handler. Runs once.
function rxBind() {
  rxRadios().forEach((radio) => radio.addEventListener("change", () => rxChange({ transport: radio.value })));
  $("rx_bind").addEventListener("change", () => rxChange({ bind: $("rx_bind").value }));
  $("rx_port").addEventListener("change", () => {
    if ($("rx_port").value !== "") rxChange({ port: Number($("rx_port").value) });
  });
  for (const key of ["watch_screen", "watch_id"]) {
    // "input" so the display follows while typing; empty means any.
    $(`rx_${key}`).addEventListener("input", () => {
      const value = $(`rx_${key}`).value;
      rxChange({ [key]: value === "" ? null : Number(value) });
    });
  }
  for (const key of ["baudrate", "bytesize", "stopbits"]) {
    $(`rx_${key}`).addEventListener("change", () => rxChange({ [key]: Number($(`rx_${key}`).value) }));
  }
  for (const key of ["parity", "serial_port", "protocol"]) {
    $(`rx_${key}`).addEventListener("change", () => rxChange({ [key]: $(`rx_${key}`).value }));
  }
  $("rx_hex_braces").addEventListener("change", () => rxChange({ hex_braces: $("rx_hex_braces").checked }));
  $("rx-listen").addEventListener("click", () => {
    rxAction(rxState && rxState.status.listening ? "/ui/api/receiver/stop" : "/ui/api/receiver/listen");
  });
  $("rx-clear").addEventListener("click", () => rxAction("/ui/api/receiver/clear"));
  $("rx-refresh-ports").addEventListener("click", rxLoadPorts);
}

// Ask the server for the state of the receiver and draw it if it changed.
async function rxPoll() {
  try {
    const response = await fetch("/ui/api/receiver");
    if (response.ok) rxRender(await response.json(), false);
  } catch (error) {
    // app.js already reports an unreachable server.
  }
}

const VIEWS = ["send", "receive", "settings", "about"];

// Show one of the views and mark its tab. The address keeps the choice, so a reload
// and the back button return to the same view.
function showView(name) {
  if (!VIEWS.includes(name)) name = "send";
  VIEWS.forEach((view) => {
    $(`view-${view}`).hidden = view !== name;
    $(`tab-${view}`).setAttribute("aria-pressed", String(view === name));
  });
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "settings") loadAccess();
}

const viewFromAddress = () => showView(location.hash.slice(1));
VIEWS.forEach((view) => $(`tab-${view}`).addEventListener("click", () => showView(view)));
window.addEventListener("hashchange", viewFromAddress);
viewFromAddress();

rxBind();
rxPoll();
setInterval(rxPoll, POLL_MS);
