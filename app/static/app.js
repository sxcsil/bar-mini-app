const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const initData = tg?.initData || "";
const headers = { "X-Telegram-Init-Data": initData };
const $ = (selector) => document.querySelector(selector);
let adminRecipes = [];
let visibleRecipes = [];
let adminUsers = [];
let isAdmin = false;
let calendarDate = new Date();
let selectedCalendarDate = "";

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const notify = (message) => tg?.showPopup ? tg.showPopup({ message }) : window.alert(message);

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
    if (user.role === "admin") { isAdmin = true; document.querySelectorAll(".admin-only").forEach((element) => element.classList.add("visible")); const addButton = $("#new-recipe"); if (addButton) $("#recipes .section-head").append(addButton); loadAdmin(); }
  } catch { $("#greeting").textContent = "Барная смена"; }
}

async function loadRecipes(query = "") {
  const response = await fetch(`/api/recipes?q=${encodeURIComponent(query)}`, { headers });
  if (!response.ok) return;
  const recipes = await response.json();
  visibleRecipes = recipes;
  $("#recipe-list").innerHTML = recipes.map((recipe) => `<article class="recipe-tile" data-recipe-id="${recipe.id}"><div class="recipe-tile-head"><div><p class="eyebrow">${escapeHtml(recipe.category)}</p><h3>${escapeHtml(recipe.title)}</h3></div>${adminRecipes.some((item) => item.id === recipe.id) ? `<button class="pencil admin-only visible" data-edit-recipe="${recipe.id}" aria-label="Изменить техкарту">✎</button>` : ""}</div><div>${recipe.ingredients.map((item) => `<span class="ingredient">${escapeHtml(item)}</span>`).join("")}</div><p class="muted">Нажмите, чтобы открыть техкарту</p></article>`).join("") || '<p class="muted">Ничего не найдено</p>';
}

function showRecipe(recipe) {
  const detail = $("#recipe-detail");
  detail.hidden = false;
  detail.innerHTML = `<button class="secondary" id="close-recipe" type="button">← К списку</button>${recipe.photo_url ? `<img class="recipe-photo" src="${escapeHtml(recipe.photo_url)}" alt="${escapeHtml(recipe.title)}">` : ""}<p class="eyebrow">${escapeHtml(recipe.category)}</p><div class="detail-title-row"><h2>${escapeHtml(recipe.title)}</h2>${isAdmin ? `<button class="pencil" id="detail-edit-recipe" aria-label="Изменить техкарту">✎</button>` : ""}</div><div class="detail-ingredients">${recipe.ingredients.map((item) => `<span class="ingredient">${escapeHtml(item)}</span>`).join("")}</div><p>${escapeHtml(recipe.recipe_text)}</p>${recipe.notes ? `<p class="muted">${escapeHtml(recipe.notes)}</p>` : ""}`;
  $("#recipe-list").hidden = true;
  $("#close-recipe").addEventListener("click", () => { detail.hidden = true; $("#recipe-list").hidden = false; });
  $("#detail-edit-recipe")?.addEventListener("click", () => renderRecipeEditor(recipe));
}

function renderRecipeEditor(recipe) {
  const detail = $("#recipe-detail");
  detail.innerHTML = `<button class="secondary" id="cancel-recipe-edit" type="button">← К техкарте</button><form id="recipe-edit-form" class="form recipe-editor"><input name="title" value="${escapeHtml(recipe.title)}" required><input name="category" value="${escapeHtml(recipe.category)}" required><textarea name="ingredients" required>${escapeHtml(recipe.ingredients.join("\n"))}</textarea><textarea name="recipe_text" required>${escapeHtml(recipe.recipe_text)}</textarea><textarea name="notes">${escapeHtml(recipe.notes || "")}</textarea><input name="photo_url" value="${escapeHtml(recipe.photo_url || "")}" placeholder="URL фотографии"><button class="primary" type="submit">Сохранить изменения</button></form>`;
  $("#cancel-recipe-edit").addEventListener("click", () => showRecipe(recipe));
  $("#recipe-edit-form").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); try { await adminRequest(`/api/admin/recipes/${recipe.id}`, { method: "PUT", body: JSON.stringify({ title: form.get("title"), category: form.get("category"), ingredients: String(form.get("ingredients")).split("\n"), recipe_text: form.get("recipe_text"), notes: form.get("notes"), photo_url: form.get("photo_url") }) }); const updated = { ...recipe, title: form.get("title"), category: form.get("category"), ingredients: String(form.get("ingredients")).split("\n"), recipe_text: form.get("recipe_text"), notes: form.get("notes"), photo_url: form.get("photo_url") }; adminRecipes = adminRecipes.map((item) => item.id === recipe.id ? updated : item); visibleRecipes = visibleRecipes.map((item) => item.id === recipe.id ? updated : item); showRecipe(updated); loadAdmin(); } catch { notify("Не удалось сохранить техкарту."); } });
}

$("#recipe-search").addEventListener("input", (event) => loadRecipes(event.target.value));
$("#recipe-list").addEventListener("click", (event) => {
  const edit = event.target.closest("[data-edit-recipe]");
  if (edit) { event.stopPropagation(); editRecipe(Number(edit.dataset.editRecipe)); return; }
  const tile = event.target.closest("[data-recipe-id]");
  if (tile) { const recipe = visibleRecipes.find((item) => item.id === Number(tile.dataset.recipeId)); if (recipe) showRecipe(recipe); }
});

$("#shift-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const inputs = [...form.querySelectorAll('input[type="file"]')];
  const files = inputs.flatMap((input) => [...input.files]);
  if (files.length !== inputs.length) { notify("Сделайте все семь фотографий закрытия смены."); return; }
  const data = new FormData(); data.append("comment", form.comment.value); data.append("photo_types", JSON.stringify(inputs.map((input) => input.dataset.photoType))); files.forEach((file) => data.append("photos", file));
  const button = form.querySelector("button[type=submit]"); button.disabled = true; button.textContent = "Отправляем…";
  try { const response = await fetch("/api/shifts", { method: "POST", headers, body: data }); if (!response.ok) throw new Error(); $("#shift-result").textContent = "Отчёт сохранён и отправлен администратору."; form.reset(); document.querySelectorAll(".photo-slot").forEach((slot) => slot.classList.remove("has-photo")); }
  catch { $("#shift-result").textContent = "Не удалось отправить отчёт."; } finally { button.disabled = false; button.textContent = "Отправить отчёт"; }
});
document.querySelectorAll('#shift-form input[type="file"]').forEach((input) => input.addEventListener("change", () => input.closest(".photo-slot").classList.toggle("has-photo", input.files.length > 0)));

async function loadSchedule() {
  const response = await fetch("/api/schedule", { headers }); if (!response.ok) return;
  const rows = await response.json(); $("#schedule-list").innerHTML = rows.map((row) => `<div class="schedule-item"><span>${new Date(`${row.work_date}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" })}</span><b>${row.shift_type === "off" ? "Выходной" : "Рабочая смена"}</b></div>`).join("") || '<p class="muted">Расписание пока не опубликовано.</p>';
  const staffResponse = await fetch("/api/staff", { headers }); if (staffResponse.ok) { const staff = await staffResponse.json(); $("#swap-user").innerHTML = '<option value="">Выберите сотрудника</option>' + staff.map((user) => `<option value="${user.id}">${escapeHtml(user.full_name)}</option>`).join(""); }
}
$("#day-off-form").addEventListener("submit", async (event) => { event.preventDefault(); const response = await fetch("/api/day-off", { method: "POST", headers, body: new FormData(event.currentTarget) }); $("#day-off-result").textContent = response.ok ? "Заявка отправлена администратору." : "Не удалось отправить заявку."; if (response.ok) event.currentTarget.reset(); });
$("#swap-form").addEventListener("submit", async (event) => { event.preventDefault(); const response = await fetch("/api/shift-swap", { method: "POST", headers, body: new FormData(event.currentTarget) }); $("#swap-result").textContent = response.ok ? "Заявка на обмен отправлена." : "Не удалось отправить заявку."; if (response.ok) event.currentTarget.reset(); });

async function adminRequest(url, options = {}) { const response = await fetch(url, { ...options, headers: { ...headers, "Content-Type": "application/json", ...(options.headers || {}) } }); if (!response.ok) throw new Error(await response.text()); return response.json(); }

async function loadAdmin() {
  try {
    const data = await adminRequest("/api/admin/overview"); adminRecipes = data.recipes; adminUsers = data.users.filter((user) => user.role !== "admin");
    $("#admin-requests").innerHTML = data.requests.map((request) => `<div class="admin-row"><div><b>${escapeHtml(request.full_name)}</b><small>${escapeHtml(request.request_date)} · ${escapeHtml(request.status)}</small></div>${request.status === "pending" ? `<div class="row-actions"><button class="mini-button" data-request="${request.id}" data-status="approved">Одобрить</button><button class="mini-button danger" data-request="${request.id}" data-status="rejected">Отклонить</button></div>` : ""}</div>`).join("") || '<p class="muted">Заявок нет.</p>';
    $("#admin-swaps").innerHTML = data.swaps.map((swap) => `<div class="admin-row"><div><b>${escapeHtml(swap.requester_name)}</b><small>${escapeHtml(swap.request_date)}${swap.target_name ? ` · обмен с ${escapeHtml(swap.target_name)}` : ""} · ${escapeHtml(swap.status)}</small></div></div>`).join("") || '<p class="muted">Заявок на обмен пока нет.</p>';
    $("#admin-shifts").innerHTML = data.shifts.map((shift) => `<div class="admin-row"><div><b>${escapeHtml(shift.full_name)}</b><small>${escapeHtml(shift.report_date)} · ${escapeHtml(shift.status)}</small></div><select class="status-select" data-shift="${shift.id}"><option value="submitted" ${shift.status === "submitted" ? "selected" : ""}>Новый</option><option value="reviewed" ${shift.status === "reviewed" ? "selected" : ""}>Проверен</option><option value="needs_attention" ${shift.status === "needs_attention" ? "selected" : ""}>Есть замечания</option></select></div>`).join("") || '<p class="muted">Отчётов нет.</p>';
    renderCalendar(await adminRequest(`/api/admin/calendar?month=${monthKey()}`));
    loadRecipes($("#recipe-search").value);
  } catch { $("#admin-result").textContent = "Не удалось загрузить админку."; }
}

function monthKey() { return `${calendarDate.getFullYear()}-${String(calendarDate.getMonth() + 1).padStart(2, "0")}`; }
function renderCalendar(schedules = []) {
  const year = calendarDate.getFullYear(); const month = calendarDate.getMonth(); const first = new Date(year, month, 1); const days = new Date(year, month + 1, 0).getDate();
  $("#calendar-month").textContent = calendarDate.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  const byDate = schedules.reduce((map, row) => ((map[row.work_date] ||= []).push(row), map), {});
  let html = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((day) => `<span class="calendar-weekday">${day}</span>`).join("");
  const offset = (first.getDay() + 6) % 7; for (let i = 0; i < offset; i += 1) html += '<span class="calendar-empty"></span>';
  for (let day = 1; day <= days; day += 1) { const key = `${monthKey()}-${String(day).padStart(2, "0")}`; html += `<button class="calendar-day ${byDate[key]?.length ? "has-shifts" : ""} ${selectedCalendarDate === key ? "selected" : ""}" data-calendar-date="${key}"><b>${day}</b><small>${byDate[key]?.length || 0} чел.</small></button>`; }
  $("#admin-calendar").innerHTML = html;
}
async function openCalendarDay(day) { selectedCalendarDate = day; const schedules = await adminRequest(`/api/admin/calendar?month=${monthKey()}`); renderCalendar(schedules); const assigned = new Set(schedules.filter((row) => row.work_date === day).map((row) => row.user_id)); $("#calendar-editor").hidden = false; $("#calendar-selected-date").textContent = new Date(`${day}T00:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }); $("#calendar-user-list").innerHTML = adminUsers.map((user) => `<label class="user-check"><input type="checkbox" value="${user.id}" ${assigned.has(user.id) ? "checked" : ""}><span>${escapeHtml(user.full_name)}</span></label>`).join(""); }

$("#admin-calendar").addEventListener("click", (event) => { const day = event.target.closest("[data-calendar-date]"); if (day) openCalendarDay(day.dataset.calendarDate); });
$("#calendar-prev").addEventListener("click", () => { calendarDate.setMonth(calendarDate.getMonth() - 1); loadAdmin(); });
$("#calendar-next").addEventListener("click", () => { calendarDate.setMonth(calendarDate.getMonth() + 1); loadAdmin(); });
$("#calendar-save").addEventListener("click", async () => { const user_ids = [...document.querySelectorAll("#calendar-user-list input:checked")].map((input) => Number(input.value)); try { await adminRequest("/api/admin/calendar/day", { method: "POST", body: JSON.stringify({ work_date: selectedCalendarDate, user_ids }) }); $("#admin-result").textContent = "График сохранён."; $("#calendar-editor").hidden = true; loadAdmin(); } catch { $("#admin-result").textContent = "Не удалось сохранить график."; } });

async function editRecipe(id) { const recipe = adminRecipes.find((item) => item.id === id); if (!recipe) return; showRecipe(recipe); $("#recipe-list").hidden = true; $("#recipe-detail").hidden = false; renderRecipeEditor(recipe); }

$("#admin-refresh").addEventListener("click", loadAdmin);
document.addEventListener("click", async (event) => { const target = event.target.closest("button"); if (!target) return; if (target.dataset.request) { try { await adminRequest(`/api/admin/day-off/${target.dataset.request}`, { method: "PATCH", body: JSON.stringify({ status: target.dataset.status }) }); loadAdmin(); } catch { $("#admin-result").textContent = "Не удалось обработать заявку."; } } if (target.dataset.deleteRecipe) { try { await adminRequest(`/api/admin/recipes/${target.dataset.deleteRecipe}`, { method: "DELETE" }); loadAdmin(); loadRecipes(); } catch { $("#admin-result").textContent = "Не удалось удалить техкарту."; } } if (target.dataset.editRecipe) editRecipe(Number(target.dataset.editRecipe)); });
document.addEventListener("change", async (event) => { const target = event.target; if (target.dataset.shift) { try { await adminRequest(`/api/admin/shifts/${target.dataset.shift}`, { method: "PATCH", body: JSON.stringify({ status: target.value }) }); } catch { $("#admin-result").textContent = "Не удалось обновить отчёт."; } } });

loadMe(); loadRecipes(); loadSchedule();
