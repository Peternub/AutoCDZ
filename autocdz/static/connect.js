"use strict";
// Секрет ссылки остаётся во фрагменте и не попадает в URL серверного запроса.
let ticket = window.location.hash.slice(1);
window.history.replaceState(null, "", "/connect");
const form = document.getElementById("connect-form");
const result = document.getElementById("result");
const submit = document.getElementById("submit");
if (!ticket) {
  submit.disabled = true;
  result.textContent = "Открой новую ссылку подключения из бота.";
}
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submit.disabled = true;
  result.textContent = "Проверяю сессию…";
  try {
    const response = await fetch("/connect", {
      method: "POST",
      credentials: "omit",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        ticket,
        token: document.getElementById("token").value,
        profile_id: Number(document.getElementById("profile").value),
        consent: document.getElementById("consent").checked,
        ai_consent: document.getElementById("ai-consent").checked
      })
    });
    const data = await response.json();
    result.textContent = data.message || "Не удалось подключиться. Создай новую ссылку в боте.";
  } catch {
    result.textContent = "Нет подтверждения подключения. Проверь раздел МЭШ в боте.";
  } finally {
    document.getElementById("token").value = "";
    ticket = "";
  }
});
