/**
 * Staff-only pages (Registration, Registration Attendance, Fire Drill).
 * Unlock with HALCYON_STAFF_PAGE_CODE (default 7272) for the browser session.
 */
(function () {
  var STORAGE_KEY = 'halcyon_staff_pages_unlocked';
  var DEFAULT_CODE = '7272';

  function expectedCode() {
    if (typeof window.HALCYON_STAFF_PAGE_CODE === 'string' && window.HALCYON_STAFF_PAGE_CODE) {
      return window.HALCYON_STAFF_PAGE_CODE;
    }
    return DEFAULT_CODE;
  }

  function isUnlocked() {
    return sessionStorage.getItem(STORAGE_KEY) === '1';
  }

  function unlock() {
    sessionStorage.setItem(STORAGE_KEY, '1');
  }

  function staffHeaders(extra) {
    var h = extra ? Object.assign({}, extra) : {};
    if (isUnlocked()) {
      h['X-Halcyon-Staff-Code'] = expectedCode();
    }
    return h;
  }

  window.HalcyonStaffGateUnlocked = isUnlocked;
  window.HalcyonStaffGateHeaders = staffHeaders;

  /** Prompt for staff code once per session (e.g. before bulk sign-out). Calls done(true) if OK. */
  window.HalcyonStaffGatePrompt = function (done) {
    if (typeof done !== 'function') return;
    if (isUnlocked()) {
      done(true);
      return;
    }
    var existing = document.getElementById('halcyonStaffGatePrompt');
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

    var overlay = document.createElement('div');
    overlay.id = 'halcyonStaffGatePrompt';
    overlay.className = 'halcyon-staff-gate halcyon-staff-gate--prompt';
    overlay.innerHTML =
      '<div class="halcyon-staff-gate-card" role="dialog" aria-labelledby="halcyonStaffPromptTitle">' +
      '<h1 id="halcyonStaffPromptTitle">Staff code</h1>' +
      '<p class="halcyon-staff-gate-hint">Enter the staff code to continue.</p>' +
      '<input type="password" inputmode="numeric" autocomplete="off" ' +
      'class="halcyon-staff-gate-input" id="halcyonStaffPromptInput" aria-label="Staff code" />' +
      '<p class="halcyon-staff-gate-error" id="halcyonStaffPromptError" hidden></p>' +
      '<div class="halcyon-staff-gate-prompt-actions">' +
      '<button type="button" class="halcyon-btn halcyon-staff-gate-btn" id="halcyonStaffPromptOk">Continue</button>' +
      '<button type="button" class="halcyon-btn halcyon-btn-secondary" id="halcyonStaffPromptCancel">Cancel</button>' +
      '</div></div>';
    document.body.appendChild(overlay);

    var input = document.getElementById('halcyonStaffPromptInput');
    var err = document.getElementById('halcyonStaffPromptError');
    var finish = function (ok) {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      done(!!ok);
    };
    document.getElementById('halcyonStaffPromptOk').addEventListener('click', function () {
      if (tryUnlock(input, err)) finish(true);
    });
    document.getElementById('halcyonStaffPromptCancel').addEventListener('click', function () {
      finish(false);
    });
    if (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (tryUnlock(input, err)) finish(true);
        }
        if (e.key === 'Escape') finish(false);
      });
      input.focus();
    }
  };

  function hideAppUntilUnlock() {
    var app = document.querySelector('.halcyon-app');
    if (app) app.setAttribute('hidden', 'hidden');
  }

  function showApp() {
    var app = document.querySelector('.halcyon-app');
    if (app) app.removeAttribute('hidden');
    var overlay = document.getElementById('halcyonStaffGate');
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
  }

  function tryUnlock(inputEl, errEl) {
    var val = String((inputEl && inputEl.value) || '').trim();
    if (val === expectedCode()) {
      unlock();
      showApp();
      window.dispatchEvent(new CustomEvent('halcyon-staff-unlocked'));
      return true;
    }
    if (errEl) {
      errEl.textContent = 'Incorrect code.';
      errEl.hidden = false;
    }
    if (inputEl) inputEl.select();
    return false;
  }

  function mountGate() {
    if (isUnlocked()) return;
    hideAppUntilUnlock();
    var overlay = document.createElement('div');
    overlay.id = 'halcyonStaffGate';
    overlay.className = 'halcyon-staff-gate';
    overlay.innerHTML =
      '<div class="halcyon-staff-gate-card" role="dialog" aria-labelledby="halcyonStaffGateTitle">' +
      '<h1 id="halcyonStaffGateTitle">Staff access</h1>' +
      '<p class="halcyon-staff-gate-hint">Enter the staff code to open this page.</p>' +
      '<input type="password" inputmode="numeric" autocomplete="off" ' +
      'class="halcyon-staff-gate-input" id="halcyonStaffGateInput" aria-label="Staff code" />' +
      '<p class="halcyon-staff-gate-error" id="halcyonStaffGateError" hidden></p>' +
      '<button type="button" class="halcyon-btn halcyon-staff-gate-btn" id="halcyonStaffGateBtn">Continue</button>' +
      '<a class="halcyon-staff-gate-back" href="/">← Sign In Home</a>' +
      '</div>';
    document.body.appendChild(overlay);

    var input = document.getElementById('halcyonStaffGateInput');
    var err = document.getElementById('halcyonStaffGateError');
    var btn = document.getElementById('halcyonStaffGateBtn');
    if (btn) {
      btn.addEventListener('click', function () {
        tryUnlock(input, err);
      });
    }
    if (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          tryUnlock(input, err);
        }
      });
      input.focus();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountGate);
  } else {
    mountGate();
  }
})();
