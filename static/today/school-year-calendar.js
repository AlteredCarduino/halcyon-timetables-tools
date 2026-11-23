(function () {
  var PAGE_MODE =
    window.SYC_PAGE_MODE === 'list' ? 'list' : 'days';

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  var MONTHS = [
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December'
  ];

  function formatGenerated(iso, tz, label) {
    if (!iso) return '';
    try {
      var d = new Date(iso);
      var when =
        'Updated ' +
        d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
      return when + (label ? ' · ' + label : '') + (tz ? ' · ' + tz : '');
    } catch (e) {
      return iso;
    }
  }

  function parseIso(iso) {
    if (!iso || iso.length < 10) return null;
    var y = parseInt(iso.slice(0, 4), 10);
    var m = parseInt(iso.slice(5, 7), 10);
    var d = parseInt(iso.slice(8, 10), 10);
    if (isNaN(y) || isNaN(m) || isNaN(d)) return null;
    return { y: y, m: m, d: d };
  }

  function isoFromParts(y, m, d) {
    return (
      y +
      '-' +
      (m < 10 ? '0' + m : String(m)) +
      '-' +
      (d < 10 ? '0' + d : String(d))
    );
  }

  function addDaysIso(iso, delta) {
    var p = parseIso(iso);
    if (!p) return iso;
    var dt = new Date(p.y, p.m - 1, p.d);
    dt.setDate(dt.getDate() + delta);
    return isoFromParts(dt.getFullYear(), dt.getMonth() + 1, dt.getDate());
  }

  function compareIso(a, b) {
    if (a === b) return 0;
    return a < b ? -1 : 1;
  }

  function todayIsoLocal() {
    var t = new Date();
    return isoFromParts(t.getFullYear(), t.getMonth() + 1, t.getDate());
  }

  function formatWhen(ev) {
    if (ev.allDay) return '';
    var s = ev.startTime || '';
    var e = ev.endTime || '';
    if (s && e) return s + '–' + e;
    return s || '';
  }

  function buildEventsByDate(events) {
    var map = Object.create(null);
    function add(iso, item) {
      if (!map[iso]) map[iso] = [];
      map[iso].push(item);
    }
    (events || []).forEach(function (ev) {
      var start = ev.startDate;
      var end = ev.endDate || start;
      if (!start) return;
      var item = {
        title: ev.title || '',
        when: formatWhen(ev),
        allDay: !!ev.allDay,
        source: ev.source || 'ics'
      };
      if (ev.allDay && end && compareIso(end, start) >= 0) {
        var cur = start;
        var guard = 0;
        while (compareIso(cur, end) <= 0 && guard < 400) {
          add(cur, item);
          cur = addDaysIso(cur, 1);
          guard++;
        }
      } else {
        add(start, item);
      }
    });
    var k;
    for (k in map) {
      if (Object.prototype.hasOwnProperty.call(map, k)) {
        map[k].sort(function (a, b) {
          if (a.when && !b.when) return -1;
          if (!a.when && b.when) return 1;
          if (a.when && b.when && a.when !== b.when) {
            return a.when < b.when ? -1 : 1;
          }
          return String(a.title).localeCompare(String(b.title));
        });
      }
    }
    return map;
  }

  function academicYearMonths(rangeStart, rangeEnd) {
    var start = parseIso(rangeStart);
    var end = parseIso(rangeEnd);
    if (!start || !end) return [];
    var list = [];
    var y = start.y;
    var m = start.m;
    var guard = 0;
    while (guard < 14) {
      list.push({ y: y, m: m });
      if (y === end.y && m === end.m) break;
      m++;
      if (m > 12) {
        m = 1;
        y++;
      }
      guard++;
    }
    return list;
  }

  function daysInMonth(year, month) {
    return new Date(year, month, 0).getDate();
  }

  function dayOfWeek(year, month, dayNum) {
    return new Date(year, month - 1, dayNum).getDay();
  }

  function weekdayLabel(year, month, dayNum) {
    var labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    return labels[dayOfWeek(year, month, dayNum)];
  }

  function renderEventItems(events) {
    var html = '';
    (events || []).forEach(function (ev) {
      var evCls = 'syc-day-event';
      if (ev.source === 'sheet') {
        evCls += ' syc-day-event--sheet';
      } else if (ev.source === 'staff') {
        evCls += ' syc-day-event--staff';
      }
      html += '<li class="' + evCls + '">';
      if (ev.when) {
        html += '<span class="syc-ev-time">' + esc(ev.when) + '</span>';
      } else if (ev.allDay) {
        html += '<span class="syc-ev-time">All day</span>';
      }
      html += '<span class="syc-ev-title">' + esc(ev.title) + '</span></li>';
    });
    return html;
  }

  function renderMonthVertical(y, m, rangeStart, rangeEnd, eventsByDate, todayIso) {
    var dim = daysInMonth(y, m);
    var html =
      '<section class="syc-month-block">' +
      '<h2 class="syc-month-title">' +
      esc(MONTHS[m - 1] + ' ' + y) +
      '</h2>' +
      '<div class="syc-days-vertical">';

    var dayNum;
    for (dayNum = 1; dayNum <= dim; dayNum++) {
      var iso = isoFromParts(y, m, dayNum);
      var cls = 'syc-day-row';
      if (compareIso(iso, rangeStart) < 0 || compareIso(iso, rangeEnd) > 0) {
        cls += ' syc-day-row--out-of-year';
      }
      if (iso === todayIso) {
        cls += ' syc-day-row--today';
      }
      var dow = dayOfWeek(y, m, dayNum);
      if (dow === 0 || dow === 6) {
        cls += ' syc-day-row--weekend';
      }

      html += '<article class="' + cls + '" data-date="' + esc(iso) + '">';

      html +=
        '<header class="syc-day-row-head">' +
        '<span class="syc-day-row-num">' +
        dayNum +
        '</span>' +
        '<span class="syc-day-row-wd">' +
        esc(weekdayLabel(y, m, dayNum)) +
        '</span>' +
        '</header>';

      var dayEvents = eventsByDate[iso] || [];
      var schoolEvents = dayEvents.filter(function (ev) {
        return ev.source === 'ics' || !ev.source;
      });
      var staffEvents = dayEvents.filter(function (ev) {
        return ev.source === 'staff' || ev.source === 'sheet';
      });
      var inYear =
        compareIso(iso, rangeStart) >= 0 && compareIso(iso, rangeEnd) <= 0;

      if (inYear && (schoolEvents.length || staffEvents.length)) {
        html +=
          '<div class="syc-day-row-body">' +
          '<ul class="syc-day-events syc-day-events-col syc-day-events-col--school" aria-label="School-wide events">' +
          renderEventItems(schoolEvents) +
          '</ul>' +
          '<ul class="syc-day-events syc-day-events-col syc-day-events-col--staff" aria-label="Staff absence events">' +
          renderEventItems(staffEvents) +
          '</ul>' +
          '</div>';
      }

      html += '</article>';
    }

    html += '</div></section>';
    return html;
  }

  function formatDisplayDate(iso) {
    var p = parseIso(iso);
    if (!p) return iso || '';
    return (
      p.d +
      ' ' +
      MONTHS[p.m - 1].slice(0, 3) +
      ' ' +
      p.y
    );
  }

  function formatEventDateCell(start, end) {
    if (!start) return '—';
    if (!end || end === start) return formatDisplayDate(start);
    return formatDisplayDate(start) + ' – ' + formatDisplayDate(end);
  }

  function formatCompactDate(iso) {
    var p = parseIso(iso);
    if (!p) return iso || '';
    return p.d + ' ' + MONTHS[p.m - 1].slice(0, 3);
  }

  function formatEventWhenCompact(ev) {
    var start = ev.startDate;
    var end = ev.endDate || start;
    if (!start) return '—';
    var datePart;
    if (!end || end === start) {
      datePart = formatCompactDate(start);
    } else {
      var ps = parseIso(start);
      var pe = parseIso(end);
      if (ps && pe && ps.y === pe.y && ps.m === pe.m) {
        datePart =
          ps.d +
          '–' +
          pe.d +
          ' ' +
          MONTHS[ps.m - 1].slice(0, 3);
      } else {
        datePart = formatCompactDate(start) + '–' + formatCompactDate(end);
      }
    }
    if (ev.allDay) return datePart + ' · All day';
    var when = formatWhen(ev);
    if (when) return datePart + ' · ' + when;
    return datePart;
  }

  function sourceLabel(source) {
    if (source === 'sheet') return 'Sheet';
    if (source === 'staff') return 'Staff absence';
    return 'School calendar';
  }

  function sourceClass(source) {
    if (source === 'sheet') return 'syc-src-sheet';
    if (source === 'staff') return 'syc-src-staff';
    return 'syc-src-school';
  }

  function mondayFirstIndex(year, month, dayNum) {
    var d = dayOfWeek(year, month, dayNum);
    return d === 0 ? 6 : d - 1;
  }

  function eventsInCalendarMonth(y, m, events) {
    var monthStart = isoFromParts(y, m, 1);
    var monthEnd = isoFromParts(y, m, daysInMonth(y, m));
    var list = [];
    (events || []).forEach(function (ev) {
      var start = ev.startDate;
      var end = ev.endDate || start;
      if (!start) return;
      if (compareIso(end, monthStart) < 0 || compareIso(start, monthEnd) > 0) {
        return;
      }
      list.push(ev);
    });
    list.sort(function (a, b) {
      var c = compareIso(a.startDate || '', b.startDate || '');
      if (c !== 0) return c;
      return String(a.title || '').localeCompare(String(b.title || ''));
    });
    return list;
  }

  function dayEventTone(eventsOnDay) {
    if (!eventsOnDay || !eventsOnDay.length) return '';
    var hasSchool = false;
    var hasSheet = false;
    var hasStaff = false;
    eventsOnDay.forEach(function (ev) {
      var s = ev.source || 'ics';
      if (s === 'sheet') hasSheet = true;
      else if (s === 'staff') hasStaff = true;
      else hasSchool = true;
    });
    if (hasSchool) return 'school';
    if (hasSheet) return 'sheet';
    if (hasStaff) return 'staff';
    return 'school';
  }

  function eventRangeLabel(ev) {
    var start = ev.startDate;
    var end = ev.endDate || start;
    if (!start) return ev.title || '';
    var range =
      !end || end === start
        ? formatCompactDate(start)
        : formatCompactDate(start) + ' – ' + formatCompactDate(end);
    var title = ev.title || 'Event';
    if (!ev.allDay && formatWhen(ev)) {
      return title + ' (' + range + ', ' + formatWhen(ev) + ')';
    }
    return title + ' (' + range + ')';
  }

  function dayTooltipFromEvents(iso, events) {
    var lines = [];
    (events || []).forEach(function (ev) {
      var start = ev.startDate;
      var end = ev.endDate || start;
      if (!start || compareIso(iso, start) < 0 || compareIso(iso, end) > 0) {
        return;
      }
      lines.push(eventRangeLabel(ev));
    });
    return lines.join('\n');
  }

  function renderMonthTile(y, m, events, eventsByDate, rangeStart, rangeEnd, todayIso) {
    var dim = daysInMonth(y, m);
    var monthEvents = eventsInCalendarMonth(y, m, events);
    var offset = mondayFirstIndex(y, m, 1);
    var html =
      '<article class="syc-year-tile">' +
      '<h3 class="syc-year-tile-title">' +
      esc(MONTHS[m - 1] + ' ' + y) +
      '</h3>' +
      '<div class="syc-tile-cal" aria-hidden="true">' +
      '<div class="syc-tile-cal-head">' +
      ['M', 'T', 'W', 'T', 'F', 'S', 'S']
        .map(function (lbl) {
          return '<span>' + lbl + '</span>';
        })
        .join('') +
      '</div>' +
      '<div class="syc-tile-cal-grid">';

    var pad;
    for (pad = 0; pad < offset; pad++) {
      html += '<span class="syc-tile-day syc-tile-day--pad"></span>';
    }

    var dayNum;
    for (dayNum = 1; dayNum <= dim; dayNum++) {
      var iso = isoFromParts(y, m, dayNum);
      var cls = 'syc-tile-day';
      var inYear =
        compareIso(iso, rangeStart) >= 0 && compareIso(iso, rangeEnd) <= 0;
      if (!inYear) cls += ' syc-tile-day--out';
      if (iso === todayIso) cls += ' syc-tile-day--today';
      var dow = dayOfWeek(y, m, dayNum);
      if (dow === 0 || dow === 6) cls += ' syc-tile-day--weekend';
      var dayEv = eventsByDate[iso];
      if (dayEv && dayEv.length) {
        cls += ' syc-tile-day--has syc-tile-day--' + dayEventTone(dayEv);
      }
      html +=
        '<span class="' +
        cls +
        '" title="' +
        esc(dayEv && dayEv.length ? dayTooltipFromEvents(iso, events) : '') +
        '">' +
        dayNum +
        '</span>';
    }

    html += '</div></div><ul class="syc-tile-events">';
    monthEvents.forEach(function (ev) {
      var src = ev.source || 'ics';
      html +=
        '<li class="syc-tile-ev ' +
        sourceClass(src) +
        '">' +
        '<span class="syc-tile-ev-title">' +
        esc(ev.title || '') +
        '</span>' +
        '<span class="syc-tile-ev-sep" aria-hidden="true">–</span>' +
        '<span class="syc-tile-ev-when">' +
        esc(formatEventWhenCompact(ev)) +
        '</span></li>';
    });
    if (!monthEvents.length) {
      html += '<li class="syc-tile-ev-empty">No events</li>';
    }
    html += '</ul></article>';
    return html;
  }

  function renderAllEventsList(data) {
    var el = $('calendarBody');
    var events = data.events || [];
    var rangeStart = data.rangeStart;
    var rangeEnd = data.rangeEnd;
    if (!rangeStart || !rangeEnd) {
      el.innerHTML = '<p class="today-empty">Calendar range unavailable.</p>';
      el.hidden = false;
      return;
    }

    var eventsByDate = buildEventsByDate(events);
    var months = academicYearMonths(rangeStart, rangeEnd);
    var todayIso = todayIsoLocal();
    var html = '<div class="syc-year-tiles">';
    months.forEach(function (mo) {
      html += renderMonthTile(
        mo.y,
        mo.m,
        events,
        eventsByDate,
        rangeStart,
        rangeEnd,
        todayIso
      );
    });
    html += '</div>';
    el.innerHTML = html;
    el.hidden = false;
  }

  function renderCalendar(data) {
    var el = $('calendarBody');
    var events = data.events || [];
    var rangeStart = data.rangeStart;
    var rangeEnd = data.rangeEnd;
    if (!rangeStart || !rangeEnd) {
      el.innerHTML = '<p class="today-empty">Calendar range unavailable.</p>';
      el.hidden = false;
      return;
    }

    var eventsByDate = buildEventsByDate(events);
    var months = academicYearMonths(rangeStart, rangeEnd);
    var todayIso = todayIsoLocal();
    var html =
      '<div class="syc-split-legend" aria-hidden="true">' +
      '<span class="syc-split-legend-school">School-wide (Google Calendar)</span>' +
      '<span class="syc-split-legend-staff">Sheet &amp; staff absence</span>' +
      '</div>' +
      '<div class="syc-year-grids">';
    months.forEach(function (mo) {
      html += renderMonthVertical(
        mo.y,
        mo.m,
        rangeStart,
        rangeEnd,
        eventsByDate,
        todayIso
      );
    });
    html += '</div>';
    el.innerHTML = html;
    el.hidden = false;
    scrollToTodayRow(todayIso, rangeStart, rangeEnd);
  }

  function scrollToTodayRow(todayIso, rangeStart, rangeEnd) {
    if (
      compareIso(todayIso, rangeStart) < 0 ||
      compareIso(todayIso, rangeEnd) > 0
    ) {
      return;
    }
    window.requestAnimationFrame(function () {
      var row = document.querySelector(
        '.syc-day-row--today[data-date="' + todayIso + '"]'
      );
      if (!row) return;
      row.setAttribute('tabindex', '-1');
      var scrollRoot = document.querySelector('.syc-page-scroll');
      if (scrollRoot) {
        var rowRect = row.getBoundingClientRect();
        var rootRect = scrollRoot.getBoundingClientRect();
        var target =
          scrollRoot.scrollTop +
          (rowRect.top - rootRect.top) -
          rootRect.height / 2 +
          rowRect.height / 2;
        scrollRoot.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
      } else {
        row.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
    });
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

  function fillYearSelect(currentStartYear) {
    var sel = $('yearSelect');
    if (!sel) return;
    var now = new Date();
    var thisStart = now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1;
    sel.innerHTML = '';
    var y;
    for (y = thisStart + 1; y >= thisStart - 4; y--) {
      var opt = document.createElement('option');
      opt.value = String(y);
      opt.textContent = y + '–' + String(y + 1).slice(-2);
      if (y === currentStartYear) opt.selected = true;
      sel.appendChild(opt);
    }
  }

  function load() {
    var sel = $('yearSelect');
    var year = sel && sel.value ? sel.value : '';
    var url =
      '/api/school-calendar/academic-year' +
      (year ? '?year=' + encodeURIComponent(year) : '');
    setErr('');
    $('loading').hidden = false;
    $('calendarBody').hidden = true;
    $('btnRefresh').disabled = true;
    fetch(url)
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.error || r.statusText);
          return j;
        });
      })
      .then(function (data) {
        if (data.academicYearStart) {
          fillYearSelect(data.academicYearStart);
          if (sel) sel.value = String(data.academicYearStart);
        }
        var metaLabel = data.label ? 'Academic year ' + data.label : '';
        var staff = data.staffAbsenceCalendar;
        if (staff) {
          if (staff.enabled && staff.ok && staff.count) {
            metaLabel += ' · ' + staff.count + ' staff absence (amber)';
          } else if (staff.enabled && !staff.ok && staff.error) {
            metaLabel += ' · Staff absence not loaded';
            setErr(staff.error);
          } else if (!staff.enabled) {
            metaLabel +=
              ' · Staff absence: set TODAY_STAFF_ABSENCE_CALENDAR_ICS_URL on server (secret iCal link)';
          }
        }
        var sc = data.sheetCalendar;
        if (sc) {
          if (sc.ok && sc.count) {
            metaLabel += ' · ' + sc.count + ' sheet days (purple)';
          } else if (!sc.ok && sc.error) {
            if (!$('err').textContent) {
              setErr(
                'Purple sheet events could not be loaded: ' +
                  sc.error +
                  ' (Google Calendar events still shown in blue.)'
              );
            }
          } else if (sc.ok && !sc.count) {
            metaLabel += ' · No sheet days in this range';
          }
        }
        $('generatedMeta').textContent = formatGenerated(
          data.generatedAt,
          data.timezone,
          metaLabel
        );
        if (!data.ok) {
          throw new Error(data.error || 'Calendar unavailable');
        }
        if (PAGE_MODE === 'list') {
          renderAllEventsList(data);
        } else {
          renderCalendar(data);
        }
      })
      .catch(function (e) {
        setErr(e.message || String(e));
      })
      .finally(function () {
        $('loading').hidden = true;
        $('btnRefresh').disabled = false;
      });
  }

  $('btnRefresh').addEventListener('click', load);
  $('yearSelect').addEventListener('change', load);
  load();
})();
