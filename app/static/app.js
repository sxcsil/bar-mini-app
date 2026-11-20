const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
const initData = tg?.initData || "";
const headers = { "X-Telegram-Init-Data": initData };
const $ = (selector) => document.querySelector(selector);

function toast(message) {
  if (tg?.showPopup) tg.showPopup({ message });
  else window.alert(message);
}

document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === button.dataset.page));
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.page === button.dataset.page));
  window.scrollTo({ top: 0, behavior: "smooth" });
}));

async function loadMe() {
  try {
    const response = await fetch("/api/me", { headers });
    if (!response.ok) throw new Error("auth");
    const user = await response.json();
    $("#greeting").textContent = `Привет, ${user.full_name.split(" ")[0]}`;
  } catch { $("#greeting").textContent = "Барная смена"; }
}

async function loadRecipes(query = "") {
  const response = await fetch(`/api/recipes?q=${encodeURIComponent(query)}`, { headers });
  if (!response.ok) return;
  const recipes = await response.json();
  $("#recipe-list").innerHTML = recipes.map((recipe) => `<article><p class="eyebrow">${escapeHtml(recipe.category)}</p><h3>${escapeHtml(recipe.title)}</h3><div>${recipe.ingredients.map((item) => `<span class="ingredient">${escapeHtml(item)}</span>`).join("")}</div><p class="muted" style="margin:12px 0 0">${escapeHtml(recipe.recipe_text)}</p>${recipe.notes ? `<p class="muted">${escapeHtml(recipe.notes)}</p>` : ""}</article>`).join("") || '<p class="muted">Ничего не найдено</p>';
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

$("#recipe-search").addEventListener("input", (event) => loadRecipes(event.target.value));

$("#shift-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const files = [...form.querySelectorAll('input[type="file"]')].flatMap((input) => [...input.files]);
  if (!files.length) { toast("Добавьте фотографии закрытия смены через камеру."); return; }
  const data = new FormData(form);
  data.delete("photos");
  files.forEach((file) => data.append("photos", file));
  data.append("photo_types", JSON.stringify(["bar", "fridge", "cash"].slice(0, files.length)));
  const button = form.querySelector("button[type=submit]");
  button.disabled = true; button.textContent = "Отправляем…";
  try {
    const response = await fetch("/api/shifts", { method: "POST", headers, body: data });
    if (!response.ok) throw new Error("send");
    $("#shift-result").textContent = "Отчёт сохранён. Подключите токен бота и ID администратора, чтобы отправлять его в Telegram.";
    form.reset();
  } catch { $("#shift-result").textContent = "Не удалось отправить отчёт. Проверьте соединение и попробуйте ещё раз."; }
  finally { button.disabled = false; button.textContent = "Отправить отчёт"; }
});

async function loadSchedule() {
  const response = await fetch("/api/schedule", { headers });
  if (!response.ok) return;
  const shifts = await response.json();
  $("#schedule-list").innerHTML = shifts.map((shift) => `<div class="schedule-item"><span>${new Date(`${shift.work_date}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" })}</span><b>${shift.shift_type === "off" ? "Выходной" : shift.shift_type}</b></div>`).join("") || '<p class="muted">Расписание пока не опубликовано.</p>';
}

$("#day-off-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = new FormData(event.currentTarget);
  const response = await fetch("/api/day-off", { method: "POST", headers, body });
  $("#day-off-result").textContent = response.ok ? "Заявка отправлена администратору." : "Не удалось отправить заявку.";
  if (response.ok) event.currentTarget.reset();
});

loadMe();
loadRecipes();
loadSchedule();
