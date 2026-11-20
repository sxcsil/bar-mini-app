const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
const initData = tg?.initData || "";
const headers = { "X-Telegram-Init-Data": initData };
const $ = (selector) => document.querySelector(selector);
let adminRecipes = [];

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
    if (user.role === "admin") {
      document.querySelectorAll(".admin-only").forEach((element) => element.classList.add("visible"));
      loadAdmin();
    }
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

async function adminRequest(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { ...headers, "Content-Type": "application/json", ...(options.headers || {}) } });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function loadAdmin() {
  try {
    const data = await adminRequest("/api/admin/overview");
    adminRecipes = data.recipes;
    $("#admin-user-select").innerHTML = '<option value="">Выберите сотрудника</option>' + data.users.filter((user) => user.role !== "admin").map((user) => `<option value="${user.id}">${escapeHtml(user.full_name)}</option>`).join("");
    $("#admin-recipes").innerHTML = data.recipes.map((recipe) => `<div class="admin-row"><div><b>${escapeHtml(recipe.title)}</b><small>${escapeHtml(recipe.category)}</small></div><div class="row-actions"><button class="mini-button" data-edit-recipe="${recipe.id}">Изменить</button><button class="mini-button danger" data-delete-recipe="${recipe.id}">Удалить</button></div></div>`).join("") || '<p class="muted">Техкарт нет.</p>';
    $("#admin-requests").innerHTML = data.requests.map((request) => `<div class="admin-row"><div><b>${escapeHtml(request.full_name)}</b><small>${escapeHtml(request.request_date)} · ${escapeHtml(request.status)}</small></div>${request.status === "pending" ? `<div class="row-actions"><button class="mini-button" data-request="${request.id}" data-status="approved">Одобрить</button><button class="mini-button danger" data-request="${request.id}" data-status="rejected">Отклонить</button></div>` : ""}</div>`).join("") || '<p class="muted">Заявок нет.</p>';
    $("#admin-shifts").innerHTML = data.shifts.map((shift) => `<div class="admin-row"><div><b>${escapeHtml(shift.full_name)}</b><small>${escapeHtml(shift.report_date)} · наличные ${Number(shift.cash_amount).toFixed(2)} · безналичные ${Number(shift.card_amount).toFixed(2)}</small></div><select class="status-select" data-shift="${shift.id}"><option value="submitted" ${shift.status === "submitted" ? "selected" : ""}>Новый</option><option value="reviewed" ${shift.status === "reviewed" ? "selected" : ""}>Проверен</option><option value="needs_attention" ${shift.status === "needs_attention" ? "selected" : ""}>Есть замечания</option></select></div>`).join("") || '<p class="muted">Отчётов нет.</p>';
  } catch { $("#admin-result").textContent = "Не удалось загрузить админку."; }
}

$("#admin-refresh")?.addEventListener("click", loadAdmin);
$("#recipe-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await adminRequest("/api/admin/recipes", { method: "POST", body: JSON.stringify({ title: form.get("title"), category: form.get("category"), ingredients: String(form.get("ingredients")).split("\n"), recipe_text: form.get("recipe_text"), notes: form.get("notes") }) });
    event.currentTarget.reset(); $("#admin-result").textContent = "Техкарта добавлена."; loadRecipes(); loadAdmin();
  } catch { $("#admin-result").textContent = "Не удалось сохранить техкарту."; }
});

$("#schedule-form")?.addEventListener("submit", async (event) => {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  try { await adminRequest("/api/admin/schedule", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) }); event.currentTarget.reset(); $("#admin-result").textContent = "График сохранён."; loadAdmin(); }
  catch { $("#admin-result").textContent = "Не удалось сохранить график."; }
});

document.addEventListener("click", async (event) => {
  const target = event.target;
  if (target.dataset.request) {
    try { await adminRequest(`/api/admin/day-off/${target.dataset.request}`, { method: "PATCH", body: JSON.stringify({ status: target.dataset.status }) }); loadAdmin(); }
    catch { $("#admin-result").textContent = "Не удалось обработать заявку."; }
  }
  if (target.dataset.deleteRecipe) {
    try { await adminRequest(`/api/admin/recipes/${target.dataset.deleteRecipe}`, { method: "DELETE" }); loadRecipes(); loadAdmin(); }
    catch { $("#admin-result").textContent = "Не удалось удалить техкарту."; }
  }
  if (target.dataset.editRecipe) {
    const recipe = adminRecipes.find((item) => item.id === Number(target.dataset.editRecipe));
    if (!recipe) return;
    const title = window.prompt("Название техкарты", recipe.title);
    if (title === null) return;
    const ingredients = window.prompt("Ингредиенты, каждый с новой строки", recipe.ingredients.join("\n"));
    if (ingredients === null) return;
    const recipeText = window.prompt("Способ приготовления", recipe.recipe_text);
    if (recipeText === null) return;
    const notes = window.prompt("Заметки", recipe.notes || "");
    if (notes === null) return;
    try { await adminRequest(`/api/admin/recipes/${recipe.id}`, { method: "PUT", body: JSON.stringify({ title, category: recipe.category, ingredients: ingredients.split("\n"), recipe_text: recipeText, notes }) }); loadRecipes(); loadAdmin(); }
    catch { $("#admin-result").textContent = "Не удалось изменить техкарту."; }
  }
});

document.addEventListener("change", async (event) => {
  const target = event.target;
  if (target.dataset.shift) {
    try { await adminRequest(`/api/admin/shifts/${target.dataset.shift}`, { method: "PATCH", body: JSON.stringify({ status: target.value }) }); }
    catch { $("#admin-result").textContent = "Не удалось обновить отчёт."; }
  }
});
