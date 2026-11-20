/**
 * Gate Registration Attendance behind a school-email allowlist.
 * Admin manages emails at /registration-attendance-admin.
 */
(function () {
  var STORAGE_KEY = 'halcyonRegAttendanceEmail:v1';

  function savedEmail() {
    try {
      return String(sessionStorage.getItem(STORAGE_KEY) || '').trim().toLowerCase();
    } catch (e) {
      return '';
    }
  }

  function saveEmail(email) {
    try {
      sessionStorage.setItem(STORAGE_KEY, String(email || '').trim().toLowerCase());
    } catch (e) {
      /* ignore */
    }
  }

  function clearEmail() {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  function ensureStyles() {
    if (document.getElementById('halcyonRegAttendanceGateCss')) return;
    var style = document.createElement('style');
    style.id = 'halcyonRegAttendanceGateCss';
    style.textContent =
      '.halcyon-ra-gate{position:fixed;inset:0;z-index:5000;display:flex;align-items:center;justify-content:center;' +
      'padding:1.25rem;background:rgba(12,18,36,.72);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}' +
      '.halcyon-ra-gate-card{width:min(92vw,420px);border-radius:16px;padding:1.35rem 1.4rem 1.2rem;' +
      'background:rgba(255,255,255,.96);color:#1a1d26;box-shadow:0 18px 40px rgba(0,0,0,.28);}' +
      '.halcyon-ra-gate-card h1{margin:0 0 .45rem;font-size:1.2rem;font-weight:650;}' +
      '.halcyon-ra-gate-hint{margin:0 0 1rem;font-size:.92rem;line-height:1.45;opacity:.88;}' +
      '.halcyon-ra-gate-input{width:100%;box-sizing:border-box;min-height:46px;border-radius:12px;' +
      'border:1px solid rgba(30,32,70,.22);padding:.55rem .85rem;font:inherit;font-size:1rem;}' +
      '.halcyon-ra-gate-error{margin:.65rem 0 0;color:#9b1c1c;font-size:.88rem;font-weight:600;}' +
      '.halcyon-ra-gate-actions{display:flex;gap:.65rem;margin-top:1rem;}' +
      '.halcyon-ra-gate-btn{flex:1;min-height:46px;border:none;border-radius:12px;font:inherit;font-weight:600;' +
      'cursor:pointer;background:#1e2046;color:#fff;}' +
      '.halcyon-ra-gate-back{display:inline-block;margin-top:.9rem;font-size:.9rem;color:#1e2046;}';
    document.head.appendChild(style);
  }

  function showGate(prefill, errText) {
    ensureStyles();
    var existing = document.getElementById('halcyonRaGate');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.id = 'halcyonRaGate';
    overlay.className = 'halcyon-ra-gate';
    overlay.innerHTML =
      '<div class="halcyon-ra-gate-card" role="dialog" aria-labelledby="halcyonRaGateTitle">' +
      '<h1 id="halcyonRaGateTitle">Registration Attendance</h1>' +
      '<p class="halcyon-ra-gate-hint">Enter your school email. Only staff added by an admin can open this page.</p>' +
      '<input type="email" class="halcyon-ra-gate-input" id="halcyonRaGateInput" ' +
      'placeholder="you@halcyonlondon.org" autocomplete="email" />' +
      '<p class="halcyon-ra-gate-error" id="halcyonRaGateError"' +
      (errText ? '' : ' hidden') +
      '>' +
      (errText || '') +
      '</p>' +
      '<div class="halcyon-ra-gate-actions">' +
      '<button type="button" class="halcyon-ra-gate-btn" id="halcyonRaGateBtn">Continue</button>' +
      '</div>' +
      '<a class="halcyon-ra-gate-back" href="/">← Sign In Home</a>' +
      '</div>';
    document.body.appendChild(overlay);

    var input = document.getElementById('halcyonRaGateInput');
    var err = document.getElementById('halcyonRaGateError');
    var btn = document.getElementById('halcyonRaGateBtn');
    if (prefill && input) input.value = prefill;

    function fail(msg) {
      if (!err) return;
      err.hidden = false;
      err.textContent = msg || 'Access denied.';
    }

    function submit() {
      var email = input ? String(input.value || '').trim() : '';
      if (!email || email.indexOf('@') === -1) {
        fail('Enter a valid school email.');
        return;
      }
      btn.disabled = true;
      fetch('/api/registration-attendance/check-access?email=' + encodeURIComponent(email), {
        credentials: 'same-origin'
      })
        .then(function (r) {
          return r.json().then(function (j) {
            return { okHttp: r.ok, j: j };
          });
        })
        .then(function (res) {
          btn.disabled = false;
          if (!res.okHttp || !res.j || !res.j.allowed) {
            clearEmail();
            fail((res.j && res.j.error) || 'Your email is not authorised to view this page.');
            return;
          }
          saveEmail(email);
          overlay.remove();
        })
        .catch(function () {
          btn.disabled = false;
          fail('Could not verify access. Try again.');
        });
    }

    if (btn) btn.addEventListener('click', submit);
    if (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          submit();
        }
      });
      setTimeout(function () {
        input.focus();
      }, 50);
    }
  }

  function tryUnlock() {
    var email = savedEmail();
    if (!email) {
      showGate('', '');
      return;
    }
    fetch('/api/registration-attendance/check-access?email=' + encodeURIComponent(email), {
      credentials: 'same-origin'
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { okHttp: r.ok, j: j };
        });
      })
      .then(function (res) {
        if (res.okHttp && res.j && res.j.allowed) return;
        clearEmail();
        showGate(email, (res.j && res.j.error) || 'Your email is no longer authorised.');
      })
      .catch(function () {
        showGate(email, 'Could not verify access. Try again.');
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryUnlock);
  } else {
    tryUnlock();
  }
})();
