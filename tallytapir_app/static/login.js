"use strict";

// Login page: sends user name and password as JSON and opens the page on success.

const form = document.getElementById("login-form");
const errorLine = document.getElementById("login-error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorLine.textContent = "";
  try {
    const response = await fetch("/ui/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user: document.getElementById("login-user").value,
        password: document.getElementById("login-password").value,
      }),
    });
    const data = await response.json();
    if (response.ok) {
      location.href = "/";
      return;
    }
    errorLine.textContent = data.error || `Login failed (${response.status})`;
    document.getElementById("login-password").value = "";
  } catch (error) {
    errorLine.textContent = "Tally Tapir is not reachable.";
  }
});

document.getElementById("login-user").focus();
