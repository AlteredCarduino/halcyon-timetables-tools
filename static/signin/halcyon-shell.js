/**
 * Shared date/time/weather bar + drawer behaviour (index.html top bar).
 * Load after halcyon-nav.js. Sets window.HALCYON_SHELL_ACTIVE = true.
 */
(function () {
  /** Set before app.js so home page does not bind a missing #btnMenu (nav injects on DOMContentLoaded). */
  window.HALCYON_SHELL_ACTIVE = true;

  var LONDON_LAT = 51.5074;
  var LONDON_LON = -0.1278;
  var WEATHER_REFRESH_MS = 15 * 60 * 1000;
  var WEATHER_ANIM_LS_KEY = 'signInWeatherAnim:v1';
  var WEATHER_ANIM_DEFAULT = 'bold';
  var STAFF_DIR_LS_KEY = 'signInStaffDirectory:v4';
  var STAFF_PRELOAD_COOLDOWN_MS = 45 * 1000;
  var preloadInFlight = false;
  var lastPreloadMs = 0;

  var days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  var months = [
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

  function ordinal(n) {
    if (n >= 11 && n <= 13) return n + 'th';
    switch (n % 10) {
      case 1:
        return n + 'st';
      case 2:
        return n + 'nd';
      case 3:
        return n + 'rd';
      default:
        return n + 'th';
    }
  }

  function formatDateLine(d) {
    return (
      days[d.getDay()] +
      ' ' +
      ordinal(d.getDate()) +
      ' ' +
      months[d.getMonth()] +
      ' ' +
      d.getFullYear()
    );
  }

  function pad(n) {
    return (n < 10 ? '0' : '') + n;
  }

  function formatTimeForDisplay(d) {
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  function tickDate() {
    var el = document.getElementById('dateLine');
    if (el) el.textContent = formatDateLine(new Date());
  }

  function tickClock() {
    var el = document.getElementById('clock');
    if (el) el.textContent = formatTimeForDisplay(new Date());
  }

  function wmoWeatherKind(code) {
    var c = typeof code === 'number' ? code : Number(code);
    if (!isFinite(c)) return 'unknown';
    if (c === 0) return 'clear';
    if (c >= 1 && c <= 3) return 'cloud';
    if (c === 45 || c === 48) return 'fog';
    if ((c >= 51 && c <= 67) || (c >= 80 && c <= 82)) return 'rain';
    if ((c >= 71 && c <= 77) || (c >= 85 && c <= 86)) return 'snow';
    if (c >= 95) return 'storm';
    return 'cloud';
  }

  function wmoWeatherLabel(code) {
    switch (wmoWeatherKind(code)) {
      case 'clear':
        return 'Clear';
      case 'cloud':
        return 'Cloudy';
      case 'fog':
        return 'Fog';
      case 'rain':
        return 'Rain';
      case 'snow':
        return 'Snow';
      case 'storm':
        return 'Thunderstorm';
      default:
        return 'Weather';
    }
  }

  function weatherIconSvg(kind) {
    var common =
      ' class="weather-ico-svg" viewBox="0 0 24 24" fill="none" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"';
    switch (kind) {
      case 'clear':
        return (
          '<svg' +
          common +
          '><g class="weather-ico-sun-rays"><circle class="weather-ico-sun-core" cx="12" cy="12" r="4" fill="currentColor" stroke="none"/><path class="weather-ico-sun-ray" stroke="currentColor" d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></g></svg>'
        );
      case 'rain':
        return (
          '<svg' +
          common +
          '><path class="weather-ico-cloud" stroke="currentColor" d="M8 14a4 4 0 0 1 8 0"/><path class="weather-ico-rain-drop" stroke="currentColor" d="M8 18v2"/><path class="weather-ico-rain-drop" stroke="currentColor" d="M12 18v3"/><path class="weather-ico-rain-drop" stroke="currentColor" d="M16 18v2"/></svg>'
        );
      case 'snow':
        return (
          '<svg' +
          common +
          '><g class="weather-ico-snowflake"><path stroke="currentColor" d="M12 4v16M8 8l8 8M16 8l-8 8M4 12h16"/></g></svg>'
        );
      case 'fog':
        return (
          '<svg' +
          common +
          '><path class="weather-ico-fog-line" stroke="currentColor" d="M4 10h16"/><path class="weather-ico-fog-line" stroke="currentColor" d="M6 14h12"/><path class="weather-ico-fog-line" stroke="currentColor" d="M5 18h14"/></svg>'
        );
      case 'storm':
        return (
          '<svg' +
          common +
          '><path class="weather-ico-cloud" stroke="currentColor" d="M8 14a4 4 0 0 1 7.5-1.5 3.5 3.5 0 0 1 .5 6.5H8"/><path class="weather-ico-lightning" stroke="currentColor" fill="currentColor" d="M13 16l-2 4h3l-2 4"/></svg>'
        );
      case 'cloud':
      default:
        return (
          '<svg' +
          common +
          '><path class="weather-ico-cloud" stroke="currentColor" d="M7 18h11a3 3 0 0 0 .5-5.97A4.5 4.5 0 0 0 8.5 8.5 4 4 0 0 0 7 18z"/></svg>'
        );
    }
  }

  function getWeatherAnimId() {
    try {
      var id = String(localStorage.getItem(WEATHER_ANIM_LS_KEY) || '').replace(/^\s+|\s+$/g, '');
      if (
        id === 'expressive' ||
        id === 'subtle' ||
        id === 'bold' ||
        id === 'bounce' ||
        id === 'static'
      ) {
        return id;
      }
    } catch (e) {
      // ignore
    }
    return WEATHER_ANIM_DEFAULT;
  }

  function applyWeatherAnimToIcon(el) {
    if (!el) return;
    el.setAttribute('data-anim', getWeatherAnimId());
  }

  function setWeatherIcon(code) {
    var el = document.getElementById('weatherIcon');
    if (!el) return;
    var kind = wmoWeatherKind(code);
    el.setAttribute('data-kind', kind);
    applyWeatherAnimToIcon(el);
    el.innerHTML = weatherIconSvg(kind);
  }

  function setTempDisplay(text) {
    var el = document.getElementById('tempLine');
    if (el) el.textContent = text;
  }

  function windCompass(deg) {
    if (typeof deg !== 'number' || !isFinite(deg)) return '';
    var dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    return dirs[Math.round(deg / 45) % 8];
  }

  function applyCurrentWeather(payload) {
    var t = null;
    var feels = null;
    var code = null;
    var windSpeed = null;
    var windDir = null;

    if (payload && typeof payload.temperature_2m === 'number') {
      t = payload.temperature_2m;
      feels =
        typeof payload.apparent_temperature === 'number' ? payload.apparent_temperature : null;
      code = payload.weather_code;
      windSpeed = typeof payload.wind_speed_10m === 'number' ? payload.wind_speed_10m : null;
      windDir = typeof payload.wind_direction_10m === 'number' ? payload.wind_direction_10m : null;
    } else if (payload && typeof payload.temperature === 'number') {
      t = payload.temperature;
      code = payload.weathercode;
      windSpeed = typeof payload.windspeed === 'number' ? payload.windspeed : null;
      windDir = typeof payload.winddirection === 'number' ? payload.winddirection : null;
    }

    var tempText = t != null ? Math.round(t) + '°C' : '—°C';
    setTempDisplay(tempText);
    if (typeof code === 'number') {
      setWeatherIcon(code);
    } else {
      setWeatherIcon(null);
    }

    var feelsEl = document.getElementById('weatherFeels');
    var windEl = document.getElementById('weatherWind');
    var sepAfterTemp = document.getElementById('weatherSepAfterTemp');
    var sepBeforeWind = document.getElementById('weatherSepBeforeWind');
    var feelsText = '';
    var windText = '';

    if (feels != null && t != null && Math.abs(Math.round(feels) - Math.round(t)) >= 1) {
      feelsText = 'Feels ' + Math.round(feels) + '°';
    } else if (feels != null && t == null) {
      feelsText = 'Feels ' + Math.round(feels) + '°';
    }

    if (windSpeed != null) {
      windText = Math.round(windSpeed) + ' mph';
      var compass = windCompass(windDir);
      if (compass) windText += ' ' + compass;
    }

    if (feelsEl) feelsEl.textContent = feelsText;
    if (windEl) windEl.textContent = windText;
    if (sepAfterTemp) sepAfterTemp.hidden = !(feelsText || windText);
    if (sepBeforeWind) sepBeforeWind.hidden = !(feelsText && windText);

    var ariaParts = [wmoWeatherLabel(code), tempText];
    if (feelsText) ariaParts.push(feelsText);
    if (windText) ariaParts.push('Wind ' + windText);
    ariaParts.push('London');
    var row = document.getElementById('weatherLine');
    if (row) row.setAttribute('aria-label', ariaParts.join(', '));
  }

  function flashTempStatus(msg, restoreMs) {
    var el = document.getElementById('tempLine');
    if (!el) return;
    el.textContent = msg;
    setTimeout(function () {
      fetchLondonTemperature();
    }, restoreMs == null ? 2500 : restoreMs);
  }

  function fetchLondonTemperature() {
    var url =
      'https://api.open-meteo.com/v1/forecast?latitude=' +
      LONDON_LAT +
      '&longitude=' +
      LONDON_LON +
      '&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m' +
      '&wind_speed_unit=mph&timezone=Europe%2FLondon';
    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error('weather');
        return r.json();
      })
      .then(function (data) {
        applyCurrentWeather(data && data.current ? data.current : null);
      })
      .catch(function () {
        applyCurrentWeather(null);
      });
  }

  function openDrawer() {
    var overlay = document.getElementById('drawerOverlay');
    if (!overlay) return;
    overlay.classList.add('open');
    overlay.setAttribute('aria-hidden', 'false');
    var closeBtn = document.getElementById('btnDrawerClose');
    if (closeBtn) closeBtn.focus();
    document.addEventListener('keydown', drawerOnKeydown);
  }

  function closeDrawer() {
    var overlay = document.getElementById('drawerOverlay');
    if (!overlay || !overlay.classList.contains('open')) return;
    overlay.classList.remove('open');
    overlay.setAttribute('aria-hidden', 'true');
    document.removeEventListener('keydown', drawerOnKeydown);
    var btn = document.getElementById('btnMenu');
    if (btn) btn.focus();
  }

  function drawerOnKeydown(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeDrawer();
    }
  }

  function defaultDrawerAction(action) {
    closeDrawer();
    if (action === 'home') {
      if (window.location.pathname === '/' || window.location.pathname === '/index.html') {
        window.scrollTo(0, 0);
        return;
      }
      window.location.href = '/';
      return;
    }
    if (action === 'whos') {
      window.location.href = '/whos-in-out';
      return;
    }
    if (action === 'settings') {
      window.alert('Settings is not wired yet.');
      return;
    }
    if (action === 'about') {
      window.alert('About is not wired yet.');
    }
  }

  function handleDrawerAction(action) {
    if (typeof window.HalcyonHandleDrawerAction === 'function') {
      window.HalcyonHandleDrawerAction(action);
      return;
    }
    defaultDrawerAction(action);
  }

  function preloadStaffDirectory() {
    if (preloadInFlight) return;
    if (Date.now() - lastPreloadMs < STAFF_PRELOAD_COOLDOWN_MS) {
      flashTempStatus('Wait…', 2000);
      return;
    }
    preloadInFlight = true;
    lastPreloadMs = Date.now();
    flashTempStatus('Loading…', 60000);
    fetch('/api/staff/directory', { credentials: 'same-origin' })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error((j && j.error) || 'Failed');
          return j;
        });
      })
      .then(function (p) {
        if (p && p.ok && p.map) {
          try {
            localStorage.setItem(
              STAFF_DIR_LS_KEY,
              JSON.stringify({ at: Date.now(), map: p.map })
            );
          } catch (e) {
            // ignore
          }
          var n = Object.keys(p.map).length;
          flashTempStatus('Refreshed (' + n + ')', 2500);
        } else {
          flashTempStatus('No data', 2500);
        }
      })
      .catch(function () {
        flashTempStatus('Refresh failed', 3000);
      })
      .finally(function () {
        preloadInFlight = false;
      });
  }

  function wireDrawer() {
    var menu = document.getElementById('btnMenu');
    var closeBtn = document.getElementById('btnDrawerClose');
    var overlay = document.getElementById('drawerOverlay');
    if (menu) menu.addEventListener('click', openDrawer);
    if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
    if (overlay) {
      overlay.addEventListener('click', function (e) {
        if (e.target && e.target.id === 'drawerOverlay') closeDrawer();
      });
    }
    document.querySelectorAll('.drawer-item[data-action]').forEach(function (el) {
      el.addEventListener('click', function () {
        var action = String(el.getAttribute('data-action') || '');
        if (!action) return;
        if (action === 'registration') return;
        handleDrawerAction(action);
      });
    });
  }

  function wireClock() {
    if (window.HALCYON_SKIP_SHELL_CLOCK) return;
    var clock = document.getElementById('clock');
    if (!clock || clock.getAttribute('data-halcyon-clock-wired')) return;
    clock.setAttribute('data-halcyon-clock-wired', 'shell');
    clock.addEventListener('click', preloadStaffDirectory);
    clock.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        preloadStaffDirectory();
      }
    });
  }

  function init() {
    applyWeatherAnimToIcon(document.getElementById('weatherIcon'));
    tickDate();
    tickClock();
    fetchLondonTemperature();
    setInterval(tickClock, 1000);
    setInterval(tickDate, 60 * 1000);
    setInterval(fetchLondonTemperature, WEATHER_REFRESH_MS);
    wireDrawer();
    wireClock();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
