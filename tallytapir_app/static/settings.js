"use strict";

// Settings view: switches and logins for web interface and REST API.
// Uses $ and showNotice from app.js.

const ACCESS_FLAGS = ["web_enabled", "web_login", "api_enabled", "api_login"];
const ACCESS_USERS = ["web_user", "api_user"];
const ACCESS_PASSWORDS = ["web_password", "api_password", "current_password"];

let accessState = null;

// Warnings about doors that stand open to the network, as a list of texts.
function accessWarnings(data) {
  const warnings = [];
  if (data.exposed && data.web_enabled && !data.web_login) {
    warnings.push("The web interface can be reached from the network without a login.");
  }
  if (data.exposed && data.api_enabled && !data.api_login) {
    warnings.push("The REST API can be reached from the network without a login.");
  }
  if (data.exposed && !data.tls && (data.web_login || data.api_login)) {
    warnings.push("Without TLS, passwords cross the network unencrypted. Start with --tls-cert and --tls-key.");
  }
  return warnings;
}

// Draw the Settings view.
//   data:     access settings from the server, without password hashes
//   fillForm: also reset the form fields, which empties the password fields
function renderAccess(data, fillForm) {
  accessState = data;
  if (fillForm) {
    ACCESS_FLAGS.forEach((key) => { $(`acc_${key}`).checked = data[key]; });
    ACCESS_USERS.forEach((key) => { $(`acc_${key}`).value = data[key]; });
    ACCESS_PASSWORDS.forEach((key) => { $(`acc_${key}`).value = ""; });
  }
  for (const area of ["web", "api"]) {
    $(`acc-${area}-password-state`).textContent = data[`${area}_password_set`]
      ? "A password is set."
      : "No password set yet.";
  }
  $("acc-current-row").hidden = !data.web_login;
  $("acc-api-base").textContent = `${location.origin}/api/v1/`;
  $("logout").hidden = !data.logged_in;
  const list = $("acc-warnings");
  list.replaceChildren();
  accessWarnings(data).forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    list.append(item);
  });
}

// Fetch the access settings and fill the form with them.
async function loadAccess() {
  try {
    const response = await fetch("/ui/api/access");
    if (response.ok) renderAccess(await response.json(), true);
  } catch (error) {
    // app.js reports an unreachable server.
  }
}

// Send what the form differs in from the stored settings.
// Unchanged fields stay out, so the server only asks for the current web password
// when the web login itself changes.
async function applyAccess(event) {
  event.preventDefault();
  if (!accessState) return;
  const message = $("acc-message");
  const changes = {};
  ACCESS_FLAGS.forEach((key) => {
    if ($(`acc_${key}`).checked !== accessState[key]) changes[key] = $(`acc_${key}`).checked;
  });
  ACCESS_USERS.forEach((key) => {
    if ($(`acc_${key}`).value.trim() !== accessState[key]) changes[key] = $(`acc_${key}`).value;
  });
  ACCESS_PASSWORDS.forEach((key) => {
    if ($(`acc_${key}`).value !== "") changes[key] = $(`acc_${key}`).value;
  });
  if (Object.keys(changes).every((key) => key === "current_password")) {
    message.textContent = "Nothing to change.";
    return;
  }
  if (changes.web_enabled === false
      && !confirm("Switch the web interface off? This page stops working until it is switched on again in the desktop window or with --web on.")) {
    return;
  }
  message.textContent = "";
  try {
    const response = await fetch("/ui/api/access", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    });
    const data = await response.json();
    if (!response.ok) {
      message.textContent = data.error || `Not saved (${response.status})`;
      message.className = "error";
      return;
    }
    renderAccess(data, true);
    message.className = "hint";
    message.textContent = data.web_enabled ? "Saved." : "Saved. The web interface is switched off now.";
  } catch (error) {
    message.className = "error";
    message.textContent = "Tally Tapir is not reachable.";
  }
}

$("view-settings").addEventListener("submit", applyAccess);
$("logout").addEventListener("click", async () => {
  await fetch("/ui/api/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  location.href = "/login";
});

loadAccess();
