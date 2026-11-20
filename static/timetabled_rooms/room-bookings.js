(function () {
  "use strict";

  const BOOKING_MINUTES = 15;
  const API = "/api/room-bookings";
  const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const STAFF_EMAIL_KEY = "roomBookingStaffEmail";
  const STAFF_NAME_KEY = "roomBookingStaffName";

  let yearRange = null;

  function timetableData() {
    // Inline page script uses `const TIMETABLE_DATA` (global lexical, not window.*).
    if (typeof TIMETABLE_DATA !== "undefined") return TIMETABLE_DATA;
    return window.TIMETABLE_DATA || null;
  }

  function schoolCalendarTerms() {
    const data = timetableData();
    const cal = data && data.schoolCalendar;
    if (!cal || !cal.loaded || !Array.isArray(cal.terms) || !cal.terms.length) {
      return null;
    }
    return cal.terms;
  }

  function isBookableSchoolDay(dayDate) {
    const terms = schoolCalendarTerms();
    if (!terms) return true;
    const iso = String(dayDate || "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return false;
    const d = new Date(`${iso}T12:00:00`);
    const wd = d.getDay();
    if (wd === 0 || wd === 6) return false;
    return terms.some((term) => iso >= term.start && iso <= term.end);
  }

  const accessCache = new Map();

  function isRestrictedRoom(room) {
    const data = timetableData();
    return Boolean(data && data.restrictedRooms && data.restrictedRooms.includes(room));
  }

  async function canBookRoom(room) {
    if (!isRestrictedRoom(room)) return true;
    const email = (localStorage.getItem(STAFF_EMAIL_KEY) || "").trim().toLowerCase();
    if (!email) return false;
    const key = `${room}\0${email}`;
    if (accessCache.has(key)) return accessCache.get(key);
    const res = await fetch(
      `${API}/check-access?room=${encodeURIComponent(room)}&email=${encodeURIComponent(email)}`
    );
    const data = await res.json().catch(() => ({}));
    const allowed = res.ok && Boolean(data.allowed);
    accessCache.set(key, allowed);
    return allowed;
  }

  function parseTime(value) {
    const match = String(value).trim().match(/(\d{1,2}):(\d{2})/);
    if (!match) return NaN;
    return Number(match[1]) * 60 + Number(match[2]);
  }

  function formatTimeValue(value) {
    const match = String(value).trim().match(/(\d{1,2}):(\d{2})/);
    if (!match) return String(value || "");
    return String(Number(match[1])).padStart(2, "0") + ":" + match[2];
  }

  function formatTime(minutes) {
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
  }

  function alignQuarterHour(minutes) {
    const remainder = minutes % BOOKING_MINUTES;
    return remainder === 0 ? minutes : minutes + (BOOKING_MINUTES - remainder);
  }

  function rangeClashes(start, end, existing) {
    return existing.some((b) => {
      const bStart = parseTime(b.start_time);
      const bEnd = parseTime(b.end_time);
      return start < bEnd && bStart < end;
    });
  }

  function quarterHourEnds(period, existing, startTimeStr) {
    const pEnd = parseTime(period.end);
    const start = parseTime(startTimeStr);
    const slots = [];
    for (let t = start + BOOKING_MINUTES; t <= pEnd; t += BOOKING_MINUTES) {
      if (!rangeClashes(start, t, existing)) {
        slots.push(formatTime(t));
      }
    }
    return slots;
  }

  function quarterHourStarts(period, existing) {
    const pStart = parseTime(period.start);
    const pEnd = parseTime(period.end);
    const slots = [];
    let t = pStart % BOOKING_MINUTES === 0 ? pStart : alignQuarterHour(pStart);
    for (; t + BOOKING_MINUTES <= pEnd; t += BOOKING_MINUTES) {
      if (quarterHourEnds(period, existing, formatTime(t)).length) {
        slots.push(formatTime(t));
      }
    }
    return slots;
  }

  function periodForRow(row) {
    if (!row) return null;
    const start = row.dataset.periodStart;
    const end = row.dataset.periodEnd;
    if (!start || !end) return null;
    return {
      order: Number(row.dataset.periodOrder),
      name: row.querySelector(".period-name")?.textContent?.trim() || "",
      start,
      end,
    };
  }

  function bookingStartsInPeriod(booking, period) {
    const bStart = parseTime(booking.start_time);
    const pStart = parseTime(period.start);
    const pEnd = parseTime(period.end);
    if (Number.isNaN(bStart) || Number.isNaN(pStart) || Number.isNaN(pEnd)) return false;
    return bStart >= pStart && bStart < pEnd;
  }

  function bookingMatchesPeriodRow(booking, period) {
    const order = booking.period_order;
    if (order != null && order !== "") {
      return Number(order) === Number(period.order);
    }
    return bookingStartsInPeriod(booking, period);
  }

  function visibleDateRange() {
    const ids = Array.from(document.querySelectorAll(".day-grid[id]")).map((el) => el.id);
    if (!ids.length) return null;
    ids.sort();
    return { from: ids[0], to: ids[ids.length - 1] };
  }

  function bookingsForCell(bookings, dayDate, room, period) {
    if (!period) return [];
    const pStart = parseTime(period.start);
    const pEnd = parseTime(period.end);
    return bookings.filter((b) => {
      if (b.day_date !== dayDate || b.room !== room) return false;
      const bStart = parseTime(b.start_time);
      const bEnd = parseTime(b.end_time);
      return bStart < pEnd && pStart < bEnd;
    });
  }

  function bookingsForCellDisplay(bookings, dayDate, room, period) {
    if (!period) return [];
    return bookings.filter(
      (b) =>
        b.day_date === dayDate &&
        b.room === room &&
        bookingMatchesPeriodRow(b, period)
    );
  }

  function bookingLayout(booking, period) {
    const pStart = parseTime(period.start);
    const pEnd = parseTime(period.end);
    const bStart = parseTime(booking.start_time);
    const bEnd = parseTime(booking.end_time);
    const periodMinutes = pEnd - pStart;
    if (periodMinutes <= 0 || Number.isNaN(bStart) || Number.isNaN(bEnd)) {
      return { top: 0, height: 100 };
    }
    const clipStart = Math.max(bStart, pStart);
    const clipEnd = Math.min(bEnd, pEnd);
    if (clipEnd <= clipStart) {
      return { top: 0, height: 0 };
    }
    return {
      top: ((clipStart - pStart) / periodMinutes) * 100,
      height: ((clipEnd - clipStart) / periodMinutes) * 100,
    };
  }

  function applyChipLayout(chip, booking, period) {
    const layout = bookingLayout(booking, period);
    chip.style.top = `${layout.top}%`;
    chip.style.height = `${layout.height}%`;
  }

  function slotStarts(period, existing) {
    return quarterHourStarts(period, existing);
  }

  function populateEndSelect(period, existing, startTime) {
    const endSelect = modalEl && modalEl.querySelector("#room-booking-end");
    if (!endSelect) return;
    endSelect.innerHTML = "";
    if (!startTime) {
      endSelect.innerHTML = '<option value="">Select start time first</option>';
      return;
    }
    const ends = quarterHourEnds(period, existing, startTime);
    if (!ends.length) {
      endSelect.innerHTML = '<option value="">No free end times</option>';
    } else {
      ends.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t;
        opt.textContent = t;
        endSelect.appendChild(opt);
      });
    }
  }

  function updateEndOptions() {
    if (!pending || !modalEl) return;
    const start = modalEl.querySelector("#room-booking-start").value;
    const existing = bookingsForCell(bookingsCache, pending.dayDate, pending.room, pending.period);
    populateEndSelect(pending.period, existing, start);
  }

  let bookingsCache = [];
  let blocksCache = [];

  async function loadBlocks() {
    const range = visibleDateRange();
    if (!range) {
      blocksCache = [];
      return;
    }
    const res = await fetch(
      `/api/room-blocks?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`
    );
    if (!res.ok) throw new Error("Could not load room blocks");
    const data = await res.json();
    blocksCache = data.blocks || [];
  }

  function jsWeekdayMon0(dayDate) {
    const day = new Date(dayDate + "T12:00:00");
    return (day.getDay() + 6) % 7;
  }

  function weekdayName(dayDate) {
    return DAY_NAMES[jsWeekdayMon0(dayDate)] || "";
  }

  async function loadYearRange() {
    if (yearRange) return yearRange;
    try {
      const res = await fetch("/api/room-bookings/year-range");
      if (!res.ok) return null;
      yearRange = await res.json();
    } catch (err) {
      console.warn("Year range:", err);
    }
    return yearRange;
  }

  function updateRepeatSummary() {
    if (!modalEl || !pending) return;
    const summary = modalEl.querySelector("#room-booking-repeat-summary");
    const repeatEndWrap = modalEl.querySelector("#room-booking-repeat-end-wrap");
    const repeatSelect = modalEl.querySelector("#room-booking-repeat");
    if (!summary || !repeatSelect) return;

    const mode = repeatSelect.value;
    if (repeatEndWrap) {
      repeatEndWrap.hidden = mode !== "custom";
    }

    if (mode === "none") {
      summary.textContent = "";
      summary.hidden = true;
      return;
    }

    const dayName = weekdayName(pending.dayDate);
    if (mode === "academic_year") {
      const end = yearRange && yearRange.end_date ? yearRange.end_date : "end of school year";
      summary.textContent = `Repeats every ${dayName} from ${pending.dayDate} through ${end}.`;
    } else if (mode === "custom") {
      const endDate = modalEl.querySelector("#room-booking-repeat-end")?.value || "…";
      summary.textContent = `Repeats every ${dayName} from ${pending.dayDate} through ${endDate}.`;
    }
    summary.hidden = false;
  }

  function clearAvailableHighlight(cell) {
    if (!cell) return;
    cell.classList.remove("available-now");
    const room = cell.dataset.room;
    const grid = cell.closest(".day-grid");
    if (!grid || !room) return;
    const header = grid.querySelector(`.room-head[data-room="${CSS.escape(room)}"]`);
    if (header) header.classList.remove("available-now-header");
  }

  function stripAvailableFromBlockedCells() {
    document.querySelectorAll("td.empty.room-blocked").forEach(clearAvailableHighlight);
  }

  function watchBlockedHighlightConflicts() {
    if (watchBlockedHighlightConflicts.started) return;
    watchBlockedHighlightConflicts.started = true;
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type !== "attributes" || mutation.attributeName !== "class") return;
        const cell = mutation.target;
        if (!(cell instanceof HTMLElement)) return;
        if (!cell.matches("td.empty.room-blocked.available-now")) return;
        clearAvailableHighlight(cell);
      });
    });
    document.querySelectorAll(".day-grid").forEach((grid) => {
      observer.observe(grid, { attributes: true, attributeFilter: ["class"], subtree: true });
    });
  }

  window.roomBookingsIsCellBlocked = function (dayDate, room, periodStart, periodEnd) {
    return blocksCache.some(
      (block) => block.room === room && blockCovers(block, dayDate, periodStart, periodEnd)
    );
  };

  function blockCovers(block, dayDate, periodStart, periodEnd) {
    if (block.room !== undefined && block.room === "") return false;
    if (dayDate < block.start_date || dayDate > block.end_date) return false;
    if (block.weekdays_raw) {
      const allowed = block.weekdays_raw.split(",").map((v) => Number(v.trim()));
      if (!allowed.includes(jsWeekdayMon0(dayDate))) return false;
    }
    const pStart = parseTime(periodStart);
    const pEnd = parseTime(periodEnd);
    const bStart = parseTime(block.start_time);
    const bEnd = parseTime(block.end_time);
    if (Number.isNaN(pStart) || Number.isNaN(pEnd) || Number.isNaN(bStart) || Number.isNaN(bEnd)) {
      return false;
    }
    return pStart < bEnd && bStart < pEnd;
  }

  function blockForCell(dayDate, room, period) {
    return blocksCache.find(
      (block) => block.room === room && blockCovers(block, dayDate, period.start, period.end)
    );
  }

  function isCellBlocked(dayDate, room, period) {
    return Boolean(blockForCell(dayDate, room, period));
  }

  async function loadBookings() {
    const range = visibleDateRange();
    if (!range) {
      bookingsCache = [];
      return;
    }
    const res = await fetch(`${API}?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`);
    if (!res.ok) throw new Error("Could not load bookings");
    const data = await res.json();
    bookingsCache = data.bookings || [];
  }

  function clearBookingChips() {
    document.querySelectorAll(".room-booking-chip, .room-block-chip").forEach((el) => el.remove());
    document.querySelectorAll("td.empty.has-bookings").forEach((el) => {
      el.classList.remove("has-bookings");
    });
    document.querySelectorAll(".empty-slot.has-booking-content, .empty-slot.has-block-content").forEach((el) => {
      el.classList.remove("has-booking-content", "has-block-content");
    });
    document.querySelectorAll("td.empty.room-blocked").forEach((el) => {
      el.classList.remove("room-blocked");
      el.removeAttribute("title");
    });
    document.querySelectorAll("td.empty.room-restricted").forEach((el) => {
      el.classList.remove("room-restricted");
    });
  }

  function renderBookingChips() {
    clearBookingChips();
    document.querySelectorAll(".day-grid").forEach((grid) => {
      const dayDate = grid.id;
      if (grid.classList.contains("day-grid-closed") || !isBookableSchoolDay(dayDate)) {
        return;
      }
      grid.querySelectorAll("tr[data-period-order]").forEach((row) => {
        const period = periodForRow(row);
        if (!period) return;
        row.querySelectorAll("td.empty").forEach((cell) => {
          const room = cell.dataset.room;
          if (!room) return;
          const block = blockForCell(dayDate, room, period);
          if (block) {
            clearAvailableHighlight(cell);
            cell.classList.add("room-blocked");
            const slot = cell.querySelector(".empty-slot");
            const chip = document.createElement("div");
            chip.className = "room-block-chip";
            chip.title = "Room blocked — booking not available";
            chip.textContent = block.title || "Blocked";
            applyChipLayout(chip, block, period);
            (slot || cell).appendChild(chip);
            if (slot) slot.classList.add("has-block-content");
            return;
          }
          const items = bookingsForCellDisplay(bookingsCache, dayDate, room, period);
          const slot = cell.querySelector(".empty-slot");
          items.forEach((b) => {
            const chip = document.createElement("div");
            chip.className = "room-booking-chip";
            chip.title = "Click to remove booking";
            chip.dataset.bookingId = b.id;
            chip.innerHTML = `<strong>${escapeHtml(b.title)}</strong>${escapeHtml(b.staff_name)} · ${escapeHtml(formatTimeValue(b.start_time))}–${escapeHtml(formatTimeValue(b.end_time))}`;
            applyChipLayout(chip, b, period);
            chip.addEventListener("click", (ev) => {
              ev.stopPropagation();
              removeBooking(b);
            });
            (slot || cell).appendChild(chip);
          });
          if (items.length) {
            cell.classList.add("has-bookings");
            if (slot) slot.classList.add("has-booking-content");
          }
          cell.classList.add("bookable");
          if (isRestrictedRoom(room)) {
            cell.classList.add("room-restricted");
            cell.title = "Restricted room — authorised staff only";
          }
        });
      });
    });
    stripAvailableFromBlockedCells();
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function refreshBookings() {
    try {
      await Promise.all([loadBookings(), loadBlocks()]);
      renderBookingChips();
    } catch (err) {
      console.warn("Room bookings:", err);
    }
  }

  async function removeBooking(booking) {
    const staffEmail = localStorage.getItem(STAFF_EMAIL_KEY) || "";
    const staffName = localStorage.getItem(STAFF_NAME_KEY) || "";
    if (!staffEmail) {
      alert("Enter your school email in the booking form first so you can manage your bookings.");
      return;
    }

    let scope = "single";
    if (booking.series_id) {
      const seriesCount = bookingsCache.filter((b) => b.series_id === booking.series_id).length;
      const deleteAll = confirm(
        `This booking is part of a recurring series (${seriesCount} dates).\n\nOK = delete the entire series\nCancel = choose whether to delete just this day`
      );
      if (deleteAll) {
        scope = "series";
      } else if (!confirm("Delete only this day's booking?")) {
        return;
      }
    } else if (!confirm("Remove this room booking?")) {
      return;
    }

    const params = new URLSearchParams({
      scope,
      staff_email: staffEmail,
      staff_name: staffName,
    });
    const res = await fetch(`${API}/${encodeURIComponent(booking.id)}?${params}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(data.detail || data.error || "Could not remove booking");
      return;
    }
    await refreshBookings();
    if (scope === "series" && (data.removed || 0) > 1) {
      alert(`Removed ${data.removed} bookings from the series.`);
    }
  }

  let modalEl = null;

  function ensureModal() {
    if (modalEl && !modalEl.querySelector("#room-booking-staff-email")) {
      modalEl.remove();
      modalEl = null;
    }
    if (modalEl) return modalEl;
    modalEl = document.createElement("div");
    modalEl.className = "room-booking-modal-backdrop";
    modalEl.hidden = true;
    modalEl.innerHTML = `
      <div class="room-booking-modal" role="dialog" aria-labelledby="room-booking-title">
        <h2 id="room-booking-title">Book room</h2>
        <p class="meta" id="room-booking-meta"></p>
        <p class="room-booking-hint">Times in 15-minute slots on the hour and quarter past (:00, :15, :30, :45).</p>
        <label for="room-booking-start">Start time</label>
        <select id="room-booking-start"></select>
        <label for="room-booking-end">End time</label>
        <select id="room-booking-end"></select>
        <label for="room-booking-repeat">Repeat</label>
        <select id="room-booking-repeat">
          <option value="none">This day only</option>
          <option value="academic_year">Every week for rest of academic year</option>
          <option value="custom">Every week until custom date</option>
        </select>
        <div id="room-booking-repeat-end-wrap" hidden>
          <label for="room-booking-repeat-end">Repeat until</label>
          <input id="room-booking-repeat-end" type="date" />
        </div>
        <p class="room-booking-repeat-summary" id="room-booking-repeat-summary" hidden></p>
        <label for="room-booking-title-input">Title / subject</label>
        <input id="room-booking-title-input" type="text" maxlength="120" placeholder="e.g. Maths support" />
        <label for="room-booking-staff">Staff name</label>
        <input id="room-booking-staff" type="text" maxlength="80" placeholder="Your name" />
        <label for="room-booking-staff-email">School email</label>
        <input id="room-booking-staff-email" type="email" maxlength="120" placeholder="you@halcyonlondon.org" autocomplete="email" />
        <p class="room-booking-hint">Your email is saved in this browser and required to delete your bookings later.</p>
        <p class="room-booking-error" id="room-booking-error" hidden></p>
        <div class="room-booking-modal-actions">
          <button type="button" class="secondary" data-action="cancel">Cancel</button>
          <button type="button" class="primary" data-action="save">Book room</button>
        </div>
      </div>`;
    document.body.appendChild(modalEl);
    modalEl.addEventListener("click", (ev) => {
      if (ev.target === modalEl) closeModal();
    });
    modalEl.querySelector('[data-action="cancel"]').addEventListener("click", closeModal);
    modalEl.querySelector("#room-booking-repeat").addEventListener("change", updateRepeatSummary);
    modalEl.querySelector("#room-booking-repeat-end").addEventListener("change", updateRepeatSummary);
    modalEl.querySelector("#room-booking-repeat-end").addEventListener("input", updateRepeatSummary);
    return modalEl;
  }

  let pending = null;

  function closeModal() {
    if (!modalEl) return;
    modalEl.hidden = true;
    pending = null;
    const err = modalEl.querySelector("#room-booking-error");
    if (err) err.hidden = true;
  }

  function openModal(ctx) {
    ensureModal();
    pending = ctx;
    const period = ctx.period;
    const existing = bookingsForCell(bookingsCache, ctx.dayDate, ctx.room, period);
    const starts = slotStarts(period, existing);
    const startSelect = modalEl.querySelector("#room-booking-start");
    startSelect.innerHTML = "";
    if (!starts.length) {
      startSelect.innerHTML = '<option value="">No free start times</option>';
      populateEndSelect(period, existing, "");
    } else {
      starts.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t;
        opt.textContent = t;
        startSelect.appendChild(opt);
      });
      populateEndSelect(period, existing, starts[0]);
    }
    startSelect.onchange = updateEndOptions;
    modalEl.querySelector("#room-booking-meta").textContent =
      `${ctx.room} · ${ctx.dayDate} · ${period.name} (${period.start}–${period.end})`;
    modalEl.querySelector("#room-booking-title-input").value = "";
    modalEl.querySelector("#room-booking-staff").value = localStorage.getItem(STAFF_NAME_KEY) || "";
    modalEl.querySelector("#room-booking-staff-email").value = localStorage.getItem(STAFF_EMAIL_KEY) || "";
    modalEl.querySelector("#room-booking-repeat").value = "none";
    const repeatEnd = modalEl.querySelector("#room-booking-repeat-end");
    repeatEnd.value = yearRange && yearRange.end_date ? yearRange.end_date : ctx.dayDate;
    repeatEnd.min = ctx.dayDate;
    updateRepeatSummary();
    modalEl.hidden = false;
    modalEl.querySelector("#room-booking-title-input").focus();

    modalEl.querySelector('[data-action="save"]').onclick = saveBooking;
  }

  async function saveBooking() {
    if (!pending) return;
    const errEl = modalEl.querySelector("#room-booking-error");
    errEl.hidden = true;
    const start_time = modalEl.querySelector("#room-booking-start").value;
    const end_time = modalEl.querySelector("#room-booking-end").value;
    const title = modalEl.querySelector("#room-booking-title-input").value.trim();
    const staff_name = modalEl.querySelector("#room-booking-staff").value.trim();
    const staff_email = modalEl.querySelector("#room-booking-staff-email").value.trim();
    const repeat = modalEl.querySelector("#room-booking-repeat").value;
    const repeat_end_date = modalEl.querySelector("#room-booking-repeat-end").value;
    if (!start_time) {
      errEl.textContent = "Choose a start time.";
      errEl.hidden = false;
      return;
    }
    if (!end_time) {
      errEl.textContent = "Choose an end time.";
      errEl.hidden = false;
      return;
    }
    if (!staff_name) {
      errEl.textContent = "Enter your name.";
      errEl.hidden = false;
      return;
    }
    if (!staff_email || !staff_email.includes("@")) {
      errEl.textContent = "Enter your school email.";
      errEl.hidden = false;
      return;
    }
    if (repeat === "custom") {
      if (!repeat_end_date) {
        errEl.textContent = "Choose a repeat end date.";
        errEl.hidden = false;
        return;
      }
      if (repeat_end_date < pending.dayDate) {
        errEl.textContent = "Repeat end date must be on or after the first booking date.";
        errEl.hidden = false;
        return;
      }
    }
    const body = {
      room: pending.room,
      day_date: pending.dayDate,
      start_time,
      end_time,
      title,
      staff_name,
      staff_email,
      period_order: pending.period.order,
      period_start: pending.period.start,
      period_end: pending.period.end,
      repeat,
    };
    if (repeat === "custom") body.repeat_end_date = repeat_end_date;

    const res = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      errEl.textContent = data.detail || data.error || "Booking failed";
      errEl.hidden = false;
      return;
    }
    closeModal();
    localStorage.setItem(STAFF_EMAIL_KEY, staff_email.toLowerCase());
    localStorage.setItem(STAFF_NAME_KEY, staff_name);
    await refreshBookings();
    const count = data.count || (data.booking ? 1 : 0);
    const skipped = data.skipped || [];
    if (count > 1 || skipped.length) {
      alert(formatRecurringResultMessage(count, skipped, data.skip_summary));
    }
  }

  function formatRecurringResultMessage(count, skipped, skipSummary) {
    let msg = count === 1 ? "1 booking created." : `${count} bookings created.`;
    if (!skipped.length) return msg;

    const summary = skipSummary || {};
    const parts = [];
    if (summary.timetable) parts.push(`${summary.timetable} timetabled lesson${summary.timetable === 1 ? "" : "s"}`);
    if (summary.booking) parts.push(`${summary.booking} existing booking${summary.booking === 1 ? "" : "s"}`);
    if (summary.blocked) parts.push(`${summary.blocked} blocked slot${summary.blocked === 1 ? "" : "s"}`);
    if (summary.other) parts.push(`${summary.other} other`);
    if (parts.length) {
      msg += `\n\nSkipped ${skipped.length} date${skipped.length === 1 ? "" : "s"}: ${parts.join(", ")}.`;
    } else {
      msg += `\n\nSkipped ${skipped.length} date${skipped.length === 1 ? "" : "s"}.`;
    }

    const sample = skipped.slice(0, 8).map((item) => `• ${item.day_date}: ${item.error}`);
    if (sample.length) {
      msg += `\n\n${sample.join("\n")}`;
      if (skipped.length > sample.length) {
        msg += `\n…and ${skipped.length - sample.length} more.`;
      }
    }
    return msg;
  }

  async function onGridClick(ev) {
    const cell = ev.target.closest("td.empty");
    if (!cell || ev.target.closest(".room-booking-chip") || ev.target.closest(".room-block-chip") || cell.classList.contains("room-blocked")) return;
    const row = cell.closest("tr[data-period-order]");
    const grid = cell.closest(".day-grid");
    if (!row || !grid) return;
    if (grid.classList.contains("day-grid-closed") || !isBookableSchoolDay(grid.id)) {
      return;
    }
    const period = periodForRow(row);
    if (!period) return;
    const room = cell.dataset.room;
    const data = timetableData();
    if (!room || (data && data.excludedRooms && data.excludedRooms.includes(room))) return;
    if (isRestrictedRoom(room)) {
      const email = (localStorage.getItem(STAFF_EMAIL_KEY) || "").trim();
      if (!email) {
        alert(
          `${room} can only be booked by authorised staff. Enter your school email when booking another room, or ask admin to add you.`
        );
        return;
      }
      const allowed = await canBookRoom(room);
      if (!allowed) {
        alert(`You are not authorised to book ${room}. Ask admin to add your school email on Room Bookings Admin.`);
        return;
      }
    }
    openModal({ room, dayDate: grid.id, period });
  }

  function hookDatePicker() {
    const picker = document.getElementById("date-picker");
    if (picker) {
      picker.addEventListener("change", () => setTimeout(refreshBookings, 50));
    }
    document.getElementById("prev-day")?.addEventListener("click", () => setTimeout(refreshBookings, 50));
    document.getElementById("next-day")?.addEventListener("click", () => setTimeout(refreshBookings, 50));
  }

  function init() {
    if (!document.querySelector(".timetable-grid")) return;
    document.body.addEventListener("click", onGridClick);
    hookDatePicker();
    watchBlockedHighlightConflicts();
    loadYearRange();
    refreshBookings();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
