(function () {
  "use strict";

  /* Same key as room-bookings-admin.js so one unlock covers both Admin pages. */
  const TOKEN_KEY = "roomBookingsAdminToken";

  function token() {
    try {
      return String(sessionStorage.getItem(TOKEN_KEY) || "").trim();
    } catch (e) {
      return "";
    }
  }

  function setToken(value) {
    try {
      sessionStorage.setItem(TOKEN_KEY, String(value || "").trim());
    } catch (e) {
      /* ignore */
    }
  }

  function clearToken() {
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  function formatApiError(data, res, text) {
    const d = data && data.detail;
    if (typeof d === "string" && d) return d;
    if (Array.isArray(d) && d.length) {
      return d
        .map(function (item) {
          if (typeof item === "string") return item;
          if (item && item.msg) return String(item.msg);
          return JSON.stringify(item);
        })
        .join("; ");
    }
    if (data && data.error) return String(data.error);
    if (text && text.trim().charAt(0) === "<") {
      return (
        "Server returned a web page instead of JSON (HTTP " +
        res.status +
        "). Redeploy the app so /api/registration-attendance/admin/* exists."
      );
    }
    return (text && text.slice(0, 180)) || res.statusText || "Request failed";
  }

  async function api(path, options) {
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {}, {
      "X-Admin-Token": token(),
    });
    if (opts.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, Object.assign({}, opts, { headers, credentials: "same-origin" }));
    const text = await res.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (e) {
      data = {};
    }
    if (!res.ok) {
      const err = new Error(formatApiError(data, res, text));
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function showMsg(el, text, isError) {
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    el.classList.toggle("rba-error", !!isError);
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function refreshList() {
    const body = document.getElementById("viewer-body");
    const envSummary = document.getElementById("env-summary");
    const data = await api("/api/registration-attendance/admin/viewers");
    const rows = data.access || [];
    if (envSummary) {
      const envEmails = data.env_emails || [];
      if (envEmails.length) {
        envSummary.textContent =
          envEmails.length +
          " email(s) also allowed via REGISTRATION_ATTENDANCE_ALLOWED_EMAILS: " +
          envEmails.join(", ");
      } else {
        envSummary.textContent = "No REGISTRATION_ATTENDANCE_ALLOWED_EMAILS set on the server.";
      }
    }
    if (!body) return;
    if (!rows.length) {
      body.innerHTML =
        '<tr><td colspan="3" class="rba-muted">No emails in the database allowlist yet.</td></tr>';
      return;
    }
    body.innerHTML = rows
      .map(function (row) {
        return (
          "<tr>" +
          "<td>" +
          escapeHtml(row.staff_email) +
          "</td>" +
          "<td>" +
          escapeHtml(String(row.created_at || "").slice(0, 19).replace("T", " ")) +
          "</td>" +
          '<td><button type="button" class="rba-btn secondary" data-del="' +
          escapeHtml(row.id) +
          '">Remove</button></td>' +
          "</tr>"
        );
      })
      .join("");
    body.querySelectorAll("[data-del]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const id = btn.getAttribute("data-del");
        if (!id) return;
        try {
          await api("/api/registration-attendance/admin/viewers/" + encodeURIComponent(id), {
            method: "DELETE",
          });
          await refreshList();
        } catch (err) {
          showMsg(document.getElementById("viewer-msg"), String(err.message || err), true);
        }
      });
    });
  }

  async function unlock() {
    const input = document.getElementById("admin-token");
    const authErr = document.getElementById("auth-error");
    const panel = document.getElementById("admin-panel");
    const authCard = document.getElementById("auth-card");
    showMsg(authErr, "", false);
    setToken(input ? input.value : "");
    try {
      await api("/api/registration-attendance/admin/ping");
      if (authCard) authCard.hidden = true;
      if (panel) panel.hidden = false;
      try {
        await refreshList();
      } catch (listErr) {
        showMsg(
          document.getElementById("viewer-msg"),
          "Unlocked, but could not load viewers: " + String(listErr.message || listErr),
          true
        );
      }
    } catch (err) {
      clearToken();
      if (panel) panel.hidden = true;
      if (authCard) authCard.hidden = false;
      showMsg(authErr, String(err.message || err), true);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const input = document.getElementById("admin-token");
    const saved = token();
    if (saved && input) input.value = saved;

    document.getElementById("save-token-btn").addEventListener("click", unlock);
    if (input) {
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          unlock();
        }
      });
    }

    document.getElementById("viewer-form").addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const msg = document.getElementById("viewer-msg");
      const emailEl = document.getElementById("viewer-email");
      const email = emailEl ? String(emailEl.value || "").trim() : "";
      showMsg(msg, "", false);
      try {
        await api("/api/registration-attendance/admin/viewers", {
          method: "POST",
          body: JSON.stringify({ staff_email: email }),
        });
        if (emailEl) emailEl.value = "";
        showMsg(msg, "Added " + email, false);
        await refreshList();
      } catch (err) {
        showMsg(msg, String(err.message || err), true);
      }
    });

    if (saved) unlock();
  });
})();
