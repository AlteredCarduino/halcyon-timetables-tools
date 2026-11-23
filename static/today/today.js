(function () {
  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function formatGenerated(iso, tz) {
    if (!iso) return '';
    try {
      var d = new Date(iso);
      return (
        'Updated ' +
        d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) +
        (tz ? ' · ' + tz : '')
      );
    } catch (e) {
      return iso;
    }
  }

  function setErr(msg) {
    var el = $('err');
    if (!el) return;
    if (msg) {
      el.textContent = msg;
      el.hidden = false;
    } else {
      el.hidden = true;
      el.textContent = '';
    }
  }

  function renderCalendar(sec) {
    var el = $('calendarBody');
    if (!sec || !sec.ok) {
      el.innerHTML =
        '<p class="today-err-inline">' + esc((sec && sec.error) || 'Calendar unavailable') + '</p>';
      return;
    }
    var events = sec.events || [];
    if (!events.length) {
      el.innerHTML = '<p class="today-empty">No school-wide calendar events for this date.</p>';
      return;
    }
    var html = '<ul class="today-list">';
    events.forEach(function (ev) {
      var when = ev.allDay
        ? 'All day'
        : (ev.start || '') + (ev.end ? ' – ' + ev.end : '');
      html +=
        '<li><strong>' +
        esc(ev.title) +
        '</strong><span class="today-li-meta">' +
        esc(when) +
        '</span></li>';
    });
    html += '</ul>';
    el.innerHTML = html;
  }

  function renderRegistration(sec) {
    var el = $('registrationBody');
    if (!sec || !sec.ok) {
      el.innerHTML =
        '<p class="today-err-inline">' + esc((sec && sec.error) || 'Registration unavailable') + '</p>';
      return;
    }
    var html =
      '<table class="today-periods-table"><thead><tr><th>Period</th><th>Status</th><th>Students on register</th></tr></thead><tbody>';
    (sec.periods || []).forEach(function (p) {
      var badge = p.started
        ? '<span class="today-badge">Started</span>'
        : '<span class="today-badge today-badge--wait">Not started</span>';
      var reg = p.registerReady === false ? 'Not ready' : p.registerRows != null ? String(p.registerRows) : '—';
      html +=
        '<tr><td>' +
        esc(p.label || p.name) +
        '</td><td>' +
        badge +
        '</td><td>' +
        esc(reg) +
        '</td></tr>';
    });
    html += '</tbody></table>';

    function pupilList(title, rows) {
      var h = '<p class="today-subhead">' + esc(title) + ' (' + (rows ? rows.length : 0) + ')</p>';
      if (!rows || !rows.length) {
        return h + '<p class="today-empty">None recorded for started periods.</p>';
      }
      h += '<ul class="today-list">';
      rows.forEach(function (r) {
        var meta = [];
        if (r.yearGroup) meta.push(r.yearGroup);
        if (r.name && r.schoolId && r.name !== r.schoolId) {
          meta.push('School ID ' + r.schoolId);
        }
        meta.push((r.periods || []).join(', '));
        h +=
          '<li><strong>' +
          esc(r.name || r.schoolId) +
          '</strong><span class="today-li-meta">' +
          esc(meta.filter(Boolean).join(' · ')) +
          '</span></li>';
      });
      h += '</ul>';
      return h;
    }
    html += pupilList('Out of school', sec.pupilsOutOfSchool);
    html += pupilList('Marked absent', sec.pupilsMarkedAbsent);
    el.innerHTML = html;
  }

  function renderStaff(sec) {
    var el = $('staffBody');
    if (!sec || !sec.ok) {
      el.innerHTML =
        '<p class="today-err-inline">' + esc((sec && sec.error) || 'Staff absences unavailable') + '</p>';
      return;
    }
    var html = '';
    var rows = sec.absentToday || [];
    if (!rows.length) {
      html += '<p class="today-empty">No HR absences overlapping this date.</p>';
    } else {
      html += '<ul class="today-list">';
      rows.forEach(function (r) {
        var detail = (r.absences || [])
          .map(function (a) {
            return [a.startDate, a.endDate].filter(Boolean).join(' → ');
          })
          .join('; ');
        html +=
          '<li><strong>' +
          esc(r.name) +
          '</strong><span class="today-li-meta">' +
          esc(detail) +
          '</span></li>';
      });
      html += '</ul>';
    }
    el.innerHTML = html;
  }

  function applySnapshot(data) {
    $('generatedMeta').textContent =
      formatGenerated(data.generatedAt, data.timezone) +
      (data.partial ? ' · Some sources failed' : '');
    renderCalendar(data.calendar);
    renderRegistration(data.registration);
    renderStaff(data.staffAbsences);
    $('todayBody').hidden = false;
    $('loading').hidden = true;
  }

  function load() {
    var picker = $('dayPicker');
    var day = picker && picker.value ? picker.value : '';
    var url = '/api/today' + (day ? '?day=' + encodeURIComponent(day) : '');
    setErr('');
    $('loading').hidden = false;
    $('todayBody').hidden = true;
    $('btnRefresh').disabled = true;
    fetch(url)
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.error || r.statusText);
          return j;
        });
      })
      .then(function (data) {
        if (picker && data.date && !picker.value) {
          picker.value = data.date;
        }
        applySnapshot(data);
      })
      .catch(function (e) {
        setErr(e.message || String(e));
        $('loading').hidden = true;
      })
      .finally(function () {
        $('btnRefresh').disabled = false;
      });
  }

  $('btnRefresh').addEventListener('click', load);
  $('dayPicker').addEventListener('change', load);
  load();
})();
