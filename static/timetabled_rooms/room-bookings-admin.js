(function () {
  "use strict";

  const TOKEN_KEY = "roomBookingsAdminToken";
  const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  let rooms = [];
  let yearRange = null;
  let periods = [];
  let restrictedRooms = [];
  let schoolCalendar = null;

  function token() {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  }

  function setToken(value) {
    sessionStorage.setItem(TOKEN_KEY, value.trim());
  }

  function headers() {
    return {
      "Content-Type": "application/json",
      "X-Admin-Token": token(),
    };
  }

  async function api(path, options) {
    const res = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { ...headers(), ...(options && options.headers) },
    });
    const text = await res.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (err) {
      throw new Error(res.ok ? "Invalid JSON" : text.slice(0, 200) || res.statusText);
    }
    if (!res.ok) {
      throw new Error(data.error || data.detail || res.statusText);
    }
    return data;
  }

  function todayIso() {
    return new Date().toISOString().slice(0, 10);
  }

  function addDays(iso, days) {
    const d = new Date(iso + "T12:00:00");
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function weekdayCheckboxes() {
    return Array.from(document.querySelectorAll("#block-weekdays input[type=checkbox]"));
  }

  function selectedWeekdays() {
    return weekdayCheckboxes()
      .filter((cb) => cb.checked)
      .map((cb) => Number(cb.value));
  }

  function setWeekdaySelection(values) {
    const set = new Set(values.map(Number));
    weekdayCheckboxes().forEach((cb) => {
      cb.checked = set.has(Number(cb.value));
    });
    updateBlockSummary();
  }

  function repeatLabel(weekdays) {
    if (!weekdays || !weekdays.length) return "no days selected";
    if (weekdays.length === 7) return "every day";
    if (weekdays.length === 5 && weekdays.join() === "0,1,2,3,4") return "weekdays (Mon–Fri)";
    if (weekdays.length === 2 && weekdays.join() === "5,6") return "weekends (Sat–Sun)";
    if (weekdays.length === 1) return `every ${DAY_NAMES[weekdays[0]]}`;
    return weekdays.map((d) => DAY_SHORT[d]).join(", ");
  }

  function periodCheckboxes() {
    return Array.from(document.querySelectorAll("#block-periods input[type=checkbox]"));
  }

  function selectedPeriods() {
    return periodCheckboxes()
      .filter((cb) => cb.checked)
      .map((cb) => Number(cb.value));
  }

  function setPeriodSelection(values) {
    const set = new Set(values.map(Number));
    periodCheckboxes().forEach((cb) => {
      cb.checked = set.has(Number(cb.value));
    });
    updateBlockSummary();
  }

  function periodLabel(start, end) {
    const match = periods.find((p) => p.start === start && p.end === end);
    if (match) return `${match.name} (${start}–${end})`;
    return `${start}–${end}`;
  }

  function selectedPeriodsLabel() {
    const orders = selectedPeriods().sort((a, b) => a - b);
    if (!orders.length) return "no periods selected";
    if (orders.length === periods.length && periods.length) return "all periods";
    return orders
      .map((order) => {
        const p = periods.find((row) => Number(row.order) === order);
        return p ? p.name : `Period ${order}`;
      })
      .join(", ");
  }

  function renderPeriodCheckboxes() {
    const container = document.getElementById("block-periods");
    if (!container) return;
    if (!periods.length) {
      container.innerHTML =
        '<p class="rba-muted">No timetable periods found. Run Refresh from iSAMS on the timetabled rooms page first.</p>';
      return;
    }
    container.innerHTML = periods
      .map(
        (p) => `<label class="rba-period">
          <input type="checkbox" value="${escapeHtml(p.order)}" checked />
          <span class="rba-period-text">
            <span class="rba-period-name">${escapeHtml(p.name)}</span>
            <span class="rba-period-time">${escapeHtml(p.start)}–${escapeHtml(p.end)}</span>
          </span>
        </label>`
      )
      .join("");
    periodCheckboxes().forEach((cb) => {
      cb.addEventListener("change", updateBlockSummary);
    });
    updateBlockSummary();
  }

  async function loadPeriods() {
    const data = await api("/api/room-bookings/periods");
    periods = (data.periods || []).slice().sort((a, b) => Number(a.order) - Number(b.order));
    renderPeriodCheckboxes();
  }

  function updateBlockSummary() {
    const el = document.getElementById("block-summary");
    if (!el) return;
    const room = document.getElementById("block-room").value || "…";
    const from = document.getElementById("block-from").value || "…";
    const to = document.getElementById("block-to").value || "…";
    const days = selectedWeekdays();
    el.textContent = `Will block ${room}: ${repeatLabel(days)}, ${selectedPeriodsLabel()}, from ${from} to ${to}.`;
  }

  function fillRoomSelects() {
    ["create-room", "clear-room", "block-room", "filter-room"].forEach((id) => {
      const sel = document.getElementById(id);
      if (!sel) return;
      sel.innerHTML = id === "filter-room" ? '<option value="">All rooms</option>' : "";
      rooms.forEach((room) => {
        const opt = document.createElement("option");
        opt.value = room;
        opt.textContent = room;
        sel.appendChild(opt);
      });
    });
  }

  function showMsg(el, text, ok) {
    el.textContent = text;
    el.hidden = !text;
    el.className = "rba-msg " + (ok ? "ok" : "err");
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadRooms() {
    const data = await api("/api/room-bookings/rooms");
    rooms = data.rooms || [];
    fillRoomSelects();
    updateBlockSummary();
  }

  async function loadYearRange() {
    yearRange = await api("/api/room-bookings/admin/year-range");
    if (yearRange && yearRange.start_date) {
      document.getElementById("block-from").value = yearRange.start_date;
      document.getElementById("block-to").value = yearRange.end_date;
      updateBlockSummary();
    }
  }

  async function loadSchoolCalendar() {
    try {
      const res = await fetch("/api/room-bookings/school-calendar", { credentials: "same-origin" });
      if (!res.ok) return;
      schoolCalendar = await res.json();
    } catch (err) {
      schoolCalendar = null;
    }
    const meta = document.getElementById("school-calendar-meta");
    if (!meta) return;
    if (schoolCalendar && schoolCalendar.loaded && schoolCalendar.terms && schoolCalendar.terms.length) {
      meta.hidden = false;
      meta.textContent =
        "School term dates from iSAMS apply: ad-hoc bookings are hidden and blocked outside term (refresh timetabled rooms to update).";
    } else {
      meta.hidden = false;
      meta.textContent =
        "School term calendar not loaded — run Refresh from iSAMS on timetabled rooms to enable term booking rules.";
    }
  }

  function isBookableSchoolDay(iso) {
    if (!schoolCalendar || !schoolCalendar.loaded || !schoolCalendar.terms || !schoolCalendar.terms.length) {
      return true;
    }
    const day = String(iso || "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return false;
    const d = new Date(`${day}T12:00:00`);
    const wd = d.getDay();
    if (wd === 0 || wd === 6) return false;
    return schoolCalendar.terms.some((term) => day >= term.start && day <= term.end);
  }

  function assertBookableDate(iso, label) {
    if (isBookableSchoolDay(iso)) return null;
    return `${label} falls outside iSAMS school term dates — bookings cannot be created for that day on the public grid.`;
  }

  async function loadSettings() {
    const summary = document.getElementById("allowed-emails-summary");
    const list = document.getElementById("allowed-emails-list");
    if (!summary || !list) return;
    try {
      const data = await api("/api/room-bookings/admin/settings");
      restrictedRooms = data.restricted_rooms || [];
      fillAccessRoomSelect();
      if (!data.email_restrictions_enabled) {
        summary.textContent = "Any staff school email can book most rooms (no global allowlist configured). Restricted rooms still use the list below.";
        list.innerHTML = "";
        return;
      }
      summary.textContent = `${data.allowed_count} globally authorised email${data.allowed_count === 1 ? "" : "s"} configured on the server (ROOM_BOOKINGS_ALLOWED_EMAILS).`;
      const emails = data.allowed_emails || [];
      list.innerHTML = emails.length
        ? emails.map((email) => `<li>${escapeHtml(email)}</li>`).join("")
        : '<li class="rba-muted">Allowlist enabled but empty — no one can book until emails are added.</li>';
    } catch (err) {
      summary.textContent = "Could not load booker settings.";
      list.innerHTML = "";
    }
  }

  function fillAccessRoomSelect() {
    const sel = document.getElementById("access-room");
    if (!sel) return;
    sel.innerHTML = "";
    restrictedRooms.forEach((room) => {
      const opt = document.createElement("option");
      opt.value = room;
      opt.textContent = room;
      sel.appendChild(opt);
    });
    if (!restrictedRooms.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No restricted rooms configured";
      sel.appendChild(opt);
      sel.disabled = true;
    } else {
      sel.disabled = false;
    }
  }

  async function loadRoomAccess() {
    const tbody = document.getElementById("room-access-body");
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="3" class="rba-muted">Loading…</td></tr>';
    try {
      const data = await api("/api/room-bookings/admin/room-access");
      const rows = data.access || [];
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="rba-muted">No authorised emails yet for restricted rooms.</td></tr>';
        return;
      }
      tbody.innerHTML = rows
        .map(
          (row) => `<tr>
            <td>${escapeHtml(row.room)}</td>
            <td>${escapeHtml(row.staff_email)}</td>
            <td><button type="button" class="rba-btn danger" data-delete-access="${escapeHtml(row.id)}">Remove</button></td>
          </tr>`
        )
        .join("");
      tbody.querySelectorAll("[data-delete-access]").forEach((btn) => {
        btn.addEventListener("click", () => deleteRoomAccess(btn.dataset.deleteAccess));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="3" class="rba-muted">${escapeHtml(err.message || "Could not load room access")}</td></tr>`;
    }
  }

  async function deleteRoomAccess(id) {
    if (!confirm("Remove this email from the room allowlist?")) return;
    await api(`/api/room-bookings/admin/room-access/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadRoomAccess();
  }

  async function onRoomAccess(ev) {
    ev.preventDefault();
    const msg = document.getElementById("room-access-msg");
    const room = document.getElementById("access-room").value;
    const staff_email = document.getElementById("access-email").value.trim();
    if (!room) {
      showMsg(msg, "No restricted room selected.", false);
      return;
    }
    try {
      const data = await api("/api/room-bookings/admin/room-access", {
        method: "POST",
        body: JSON.stringify({ room, staff_email }),
      });
      showMsg(msg, `Added ${data.access.staff_email} for ${data.access.room}.`, true);
      document.getElementById("access-email").value = "";
      await loadRoomAccess();
    } catch (err) {
      showMsg(msg, err.message || "Could not add room access", false);
    }
  }

  async function loadBlocks() {
    const from = document.getElementById("filter-from").value;
    const to = document.getElementById("filter-to").value;
    const tbody = document.getElementById("blocks-body");
    tbody.innerHTML = '<tr><td colspan="6" class="rba-muted">Loading…</td></tr>';
    const data = await api(`/api/room-bookings/admin/blocks?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    const blocks = data.blocks || [];
    if (!blocks.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="rba-muted">No blocks in this range.</td></tr>';
      return;
    }
    tbody.innerHTML = blocks
      .map(
        (b) => `<tr>
          <td>${escapeHtml(b.room)}</td>
          <td>${escapeHtml(b.start_date)} → ${escapeHtml(b.end_date)}</td>
          <td>${escapeHtml(b.weekdays)}</td>
          <td>${escapeHtml(periodLabel(b.start_time, b.end_time))}</td>
          <td>${escapeHtml(b.title)}</td>
          <td><button type="button" class="rba-btn danger" data-delete-block="${escapeHtml(b.id)}">Delete</button></td>
        </tr>`
      )
      .join("");
    tbody.querySelectorAll("[data-delete-block]").forEach((btn) => {
      btn.addEventListener("click", () => deleteBlock(btn.dataset.deleteBlock));
    });
  }

  async function loadBookings() {
    const from = document.getElementById("filter-from").value;
    const to = document.getElementById("filter-to").value;
    const room = document.getElementById("filter-room").value;
    const tbody = document.getElementById("bookings-body");
    tbody.innerHTML = '<tr><td colspan="6" class="rba-muted">Loading…</td></tr>';
    const params = new URLSearchParams({ from, to });
    if (room) params.set("room", room);
    const data = await api(`/api/room-bookings/admin?${params}`);
    const bookings = data.bookings || [];
    if (!bookings.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="rba-muted">No visible bookings in this range.</td></tr>';
      return;
    }
    tbody.innerHTML = bookings
      .map(
        (b) => `<tr>
          <td>${escapeHtml(b.day_date)}</td>
          <td>${escapeHtml(b.room)}</td>
          <td>${escapeHtml(b.start_time)}–${escapeHtml(b.end_time)}</td>
          <td>${escapeHtml(b.title)}</td>
          <td>${escapeHtml(b.staff_name)}</td>
          <td><button type="button" class="rba-btn danger" data-delete="${escapeHtml(b.id)}">Delete</button></td>
        </tr>`
      )
      .join("");
    tbody.querySelectorAll("[data-delete]").forEach((btn) => {
      btn.addEventListener("click", () => deleteBooking(btn.dataset.delete));
    });
  }

  async function refreshLists() {
    await Promise.all([loadBlocks(), loadBookings()]);
  }

  async function deleteBooking(id) {
    if (!confirm("Delete this visible booking?")) return;
    await api(`/api/room-bookings/admin/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadBookings();
  }

  async function deleteBlock(id) {
    if (!confirm("Delete this room block? Staff will be able to book again.")) return;
    await api(`/api/room-bookings/admin/blocks/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadBlocks();
  }

  async function unlock() {
    const input = document.getElementById("admin-token");
    const errEl = document.getElementById("auth-error");
    setToken(input.value);
    errEl.hidden = true;
    try {
      await api("/api/room-bookings/admin/ping");
      document.getElementById("auth-card").hidden = true;
      document.getElementById("admin-panel").hidden = false;
      await loadRooms();
      await loadYearRange();
      await loadSchoolCalendar();
      await loadPeriods();
      setWeekdaySelection([0, 1, 2, 3, 4]);
      setPeriodSelection(periods.map((p) => Number(p.order)));
      await Promise.all([refreshLists(), loadSettings(), loadRoomAccess()]);
    } catch (err) {
      errEl.textContent = err.message || "Invalid token";
      errEl.hidden = false;
      sessionStorage.removeItem(TOKEN_KEY);
    }
  }

  function buildBlockPayload() {
    const weekdays = selectedWeekdays().sort((a, b) => a - b);
    const period_orders = selectedPeriods().sort((a, b) => a - b);
    return {
      room: document.getElementById("block-room").value,
      start_date: document.getElementById("block-from").value,
      end_date: document.getElementById("block-to").value,
      period_orders,
      title: document.getElementById("block-title").value.trim(),
      staff_name: document.getElementById("block-staff").value.trim(),
      clear_existing_bookings: document.getElementById("block-clear-bookings").checked,
      repeat: "custom",
      weekdays,
    };
  }

  async function onBlock(ev) {
    ev.preventDefault();
    const msg = document.getElementById("block-msg");
    const body = buildBlockPayload();
    if (!body.weekdays.length) {
      showMsg(msg, "Select at least one day to block.", false);
      return;
    }
    if (!body.period_orders.length) {
      showMsg(msg, "Select at least one period to block.", false);
      return;
    }
    try {
      const data = await api("/api/room-bookings/admin/blocks", {
        method: "POST",
        body: JSON.stringify(body),
      });
      showMsg(msg, data.message || "Block created.", true);
      await refreshLists();
    } catch (err) {
      showMsg(msg, err.message || "Could not create block", false);
    }
  }

  async function onCreate(ev) {
    ev.preventDefault();
    const msg = document.getElementById("create-msg");
    const dayDate = document.getElementById("create-date").value;
    const termErr = assertBookableDate(dayDate, "Booking date");
    if (termErr) {
      showMsg(msg, termErr, false);
      return;
    }
    const body = {
      room: document.getElementById("create-room").value,
      day_date: dayDate,
      start_time: document.getElementById("create-start").value.slice(0, 5),
      end_time: document.getElementById("create-end").value.slice(0, 5),
      title: document.getElementById("create-title").value.trim(),
      staff_name: document.getElementById("create-staff").value.trim(),
      replace_existing: document.getElementById("create-replace").checked,
    };
    try {
      const data = await api("/api/room-bookings/admin", {
        method: "POST",
        body: JSON.stringify(body),
      });
      showMsg(msg, data.message || "Booking created.", true);
      await loadBookings();
    } catch (err) {
      showMsg(msg, err.message || "Could not create booking", false);
    }
  }

  async function onClear(ev) {
    ev.preventDefault();
    const room = document.getElementById("clear-room").value;
    const day = document.getElementById("clear-date").value;
    if (!confirm(`Remove all visible bookings for ${room} on ${day}?`)) return;
    const msg = document.getElementById("clear-msg");
    try {
      const data = await api("/api/room-bookings/admin/clear-day", {
        method: "POST",
        body: JSON.stringify({ room, day_date: day }),
      });
      showMsg(msg, `Removed ${data.removed || 0} booking(s).`, true);
      await loadBookings();
    } catch (err) {
      showMsg(msg, err.message || "Could not clear bookings", false);
    }
  }

  function initDates() {
    const today = todayIso();
    document.getElementById("create-date").value = today;
    document.getElementById("clear-date").value = today;
    document.getElementById("filter-from").value = today;
    document.getElementById("filter-to").value = addDays(today, 28);
  }

  function init() {
    initDates();
    loadSchoolCalendar();
    const saved = token();
    if (saved) document.getElementById("admin-token").value = saved;

    document.getElementById("save-token-btn").addEventListener("click", unlock);
    document.getElementById("admin-token").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") unlock();
    });
    document.getElementById("block-form").addEventListener("submit", onBlock);
    document.getElementById("create-form").addEventListener("submit", onCreate);
    document.getElementById("clear-form").addEventListener("submit", onClear);
    document.getElementById("room-access-form").addEventListener("submit", onRoomAccess);
    document.getElementById("refresh-list-btn").addEventListener("click", loadBookings);
    document.getElementById("refresh-blocks-btn").addEventListener("click", loadBlocks);
    document.getElementById("apply-filter-btn").addEventListener("click", refreshLists);

    weekdayCheckboxes().forEach((cb) => {
      cb.addEventListener("change", updateBlockSummary);
    });

    ["block-room", "block-from", "block-to"].forEach((id) => {
      document.getElementById(id).addEventListener("change", updateBlockSummary);
      document.getElementById(id).addEventListener("input", updateBlockSummary);
    });

    document.getElementById("block-periods-all").addEventListener("click", () => {
      setPeriodSelection(periods.map((p) => Number(p.order)));
    });
    document.getElementById("block-periods-none").addEventListener("click", () => {
      setPeriodSelection([]);
    });

    document.getElementById("block-year-btn").addEventListener("click", async () => {
      if (!yearRange) await loadYearRange();
      if (yearRange) {
        document.getElementById("block-from").value = yearRange.start_date;
        document.getElementById("block-to").value = yearRange.end_date;
        updateBlockSummary();
      }
    });

    if (saved) unlock();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
