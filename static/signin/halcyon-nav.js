/**
 * Shared top bar + drawer.
 * Sign-in pages: set window.HALCYON_NAV_PAGE = 'home'|'whos'|'registration'|…
 * Timetables & Tools: set window.HALCYON_NAV_MODE = 'tools' and window.HALCYON_SECTION_PAGE.
 * Admin: set window.HALCYON_NAV_MODE = 'admin' and window.HALCYON_SECTION_PAGE
 *   (Room Bookings Admin + Registration Attendance Access only — not in staff menus).
 */
(function () {
  /** Shown at bottom of menu — bump when deploying so iPad/PWA cache can be verified. */
  var HALCYON_APP_VERSION = '0.46';

  var MAIN_NAV = [
    { id: 'home', label: 'Home', href: '/', action: 'home' },
    { id: 'whos', label: "Who's In/Out", href: '/whos-in-out' },
    { id: 'visitorexpected', label: 'Expected visitors', href: '/visitor-expected' },
    { id: 'registration', label: 'Registration', href: '/registration-py' },
    { id: 'firedrill', label: 'Fire Drill', href: '/firedrill' },
    { id: 'settings', label: 'Settings', href: '', action: 'settings' },
    { id: 'about', label: 'About', href: '', action: 'about' }
  ];

  var TOOLS_NAV = [
    { id: 'timetabledrooms', label: 'Timetabled Rooms', href: '/timetabled-rooms' },
    { id: 'personaltimetables', label: 'Personal Timetables', href: '/personal-timetables' },
    { id: 'teachingstaff', label: 'Teaching Staff', href: '/teaching-staff-timetable' },
    { id: 'lessonout', label: 'Out of lesson', href: '/lesson-out' },
    { id: 'registrationattendance', label: 'Registration Attendance', href: '/registration-wholeyear' },
    { id: 'schoolyearcalendar', label: 'School year calendar', href: '/school-calendar-year' },
    { id: 'calendar', label: 'Calendar', href: '/calendar' },
    { id: 'studentemails', label: 'Student Emails', href: '/student-emails' }
  ];

  var ADMIN_NAV = [
    { id: 'roombookingsadmin', label: 'Room Bookings Admin', href: '/room-bookings-admin' },
    {
      id: 'registrationattendanceadmin',
      label: 'Registration Attendance Access',
      href: '/registration-attendance-admin'
    }
  ];

  function isAdminMode() {
    return window.HALCYON_NAV_MODE === 'admin';
  }

  function isToolsMode() {
    return window.HALCYON_NAV_MODE === 'tools';
  }

  function navItems() {
    if (isAdminMode()) return ADMIN_NAV;
    if (isToolsMode()) return TOOLS_NAV;
    return MAIN_NAV;
  }

  function activeNavId() {
    if (isAdminMode() || isToolsMode()) {
      return typeof window.HALCYON_SECTION_PAGE === 'string' ? window.HALCYON_SECTION_PAGE : '';
    }
    return typeof window.HALCYON_NAV_PAGE === 'string' ? window.HALCYON_NAV_PAGE : '';
  }

  function navItemHtml(item, activeId) {
    var cls = 'drawer-item';
    if (item.id === activeId) cls += ' is-active';
    if (item.action) {
      return (
        '<button type="button" class="' +
        cls +
        '" data-action="' +
        item.action +
        '" role="listitem">' +
        item.label +
        '</button>'
      );
    }
    return (
      '<a class="' + cls + '" href="' + item.href + '" role="listitem">' + item.label + '</a>'
    );
  }

  function wireTopBarClock() {
    var clock = document.getElementById('clock');
    if (!clock) return;

    if (typeof window.HalcyonOnClockTap === 'function') {
      if (clock.getAttribute('data-halcyon-clock-wired') === 'app') return;
      clock.setAttribute('data-halcyon-clock-wired', 'app');
      clock.addEventListener('click', function () {
        window.HalcyonOnClockTap();
      });
      clock.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          window.HalcyonOnClockTap();
        }
      });
    }
  }

  window.HalcyonWireTopBarClock = wireTopBarClock;

  function mountShell() {
    var mount = document.getElementById('halcyonNavMount');
    if (!mount) return;

    var activeId = activeNavId();
    var itemsHtml = navItems()
      .map(function (item) {
        return navItemHtml(item, activeId);
      })
      .join('');

    var drawerTitle = isAdminMode()
      ? 'Admin'
      : isToolsMode()
        ? 'Timetables & Tools'
        : 'Menu';
    var hideMenu = window.HALCYON_HIDE_NAV_MENU === true;
    var topBarClass = 'top-bar top-bar-glass' + (hideMenu ? ' top-bar--no-menu' : '');

    mount.innerHTML =
      '<header class="' +
      topBarClass +
      '" aria-label="Top bar">' +
      (hideMenu
        ? ''
        : '<button type="button" class="hamburger" aria-label="Open menu" id="btnMenu">' +
          '<span class="hamburger-lines" aria-hidden="true"></span></button>') +
      '<div class="top-bar-date" id="dateLine">—</div>' +
      '<div class="top-bar-time" id="clock" role="button" tabindex="0" ' +
      'aria-label="Preload staff directory from Supabase">—</div>' +
      '<div class="top-bar-weather-line" id="weatherLine" aria-label="London weather">' +
      '<span class="weather-icon" id="weatherIcon" aria-hidden="true"></span>' +
      '<span id="tempLine">…</span>' +
      '<span class="weather-inline-sep" id="weatherSepAfterTemp" aria-hidden="true" hidden>·</span>' +
      '<span id="weatherFeels" class="weather-feels"></span>' +
      '<span class="weather-inline-sep" id="weatherSepBeforeWind" aria-hidden="true" hidden>·</span>' +
      '<span id="weatherWind" class="weather-wind"></span>' +
      '</div>' +
      '</header>' +
      (hideMenu
        ? ''
        : '<div class="drawer-overlay" id="drawerOverlay" aria-hidden="true">' +
          '<nav class="drawer" aria-label="Menu">' +
          '<div class="drawer-head">' +
          '<div class="drawer-title">' +
          drawerTitle +
          '</div>' +
          '<button type="button" class="drawer-close" id="btnDrawerClose" aria-label="Close menu">×</button>' +
          '</div>' +
          '<div class="drawer-items" role="list">' +
          itemsHtml +
          '</div>' +
          '<p class="drawer-version" aria-label="App version">version ' +
          HALCYON_APP_VERSION +
          '</p>' +
          '</nav></div>');
    wireTopBarClock();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountShell);
  } else {
    mountShell();
  }
})();
