// Search and filters for the error code index (docs/index.html).
//
// Every code is already rendered in the HTML, so the page works without
// JavaScript and crawlers see the full list. This script only hides,
// reorders and highlights rows. Fuse.js is served locally (assets/fuse.min.js).
(function () {
  'use strict';

  if (typeof ERRORS === 'undefined' || typeof Fuse === 'undefined') return;
  document.documentElement.classList.add('js');

  var DEFAULT_TITLE = document.title;
  var listEl   = document.getElementById('code-list');
  var emptyEl  = document.getElementById('empty-state');
  var searchEl = document.getElementById('search');
  var countEl  = document.getElementById('result-count');
  var chips    = Array.prototype.slice.call(document.querySelectorAll('.chip'));
  var total    = ERRORS.length;

  // Rows keyed by code, with their original text kept for highlighting.
  var rows = {};
  Array.prototype.forEach.call(listEl.querySelectorAll('.code-row'), function (row) {
    var t = row.querySelector('.t');
    var s = row.querySelector('.s');
    rows[row.dataset.code] = { el: row, t: t, s: s, tText: t.textContent, sText: s.textContent };
  });
  var defaultOrder = ERRORS.map(function (e) { return e.code; });

  var fuse = new Fuse(ERRORS, {
    threshold: 0.35,
    ignoreLocation: true,
    minMatchCharLength: 2,
    keys: [
      { name: 'code',        weight: 3.0 },
      { name: 'title',       weight: 2.0 },
      { name: 'description', weight: 1.5 },
      { name: 'causes',      weight: 1.0 },
      { name: 'fix',         weight: 1.0 },
      { name: 'gotcha',      weight: 0.8 }
    ]
  });

  var query = '';
  var filter = 'All';

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Wrap each search word in <mark>. Works on escaped text only.
  function highlight(text, q) {
    var safe = escapeHtml(text);
    if (!q) return safe;
    var words = q.split(/\s+/).filter(function (w) { return w.length >= 2; });
    if (!words.length) return safe;
    var pattern = words.map(function (w) {
      return escapeHtml(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }).join('|');
    return safe.replace(new RegExp('(' + pattern + ')', 'gi'), '<mark>$1</mark>');
  }

  function matchesFilter(e) {
    if (filter === 'All') return true;
    if (filter === 'Notes') return !!e.gotcha;
    if (filter === 'Official') return e.category === 'Official' || e.category === 'Observed';
    return e.category === filter;
  }

  // A query that looks like a code ("902", "E0") matches codes by prefix, so
  // typing an exact code doesn't also return fuzzy look-alikes. Anything
  // else, or a code-like query with no prefix match, uses fuzzy search.
  function search(q) {
    if (!q) return ERRORS;
    if (/^[A-Za-z]?\d{1,4}$/.test(q)) {
      var up = q.toUpperCase();
      var byCode = ERRORS.filter(function (e) { return e.code.toUpperCase().indexOf(up) === 0; });
      if (byCode.length) {
        return byCode.sort(function (a, b) { return a.code.length - b.code.length; });
      }
    }
    return fuse.search(q).map(function (r) { return r.item; });
  }

  function render() {
    var entries = search(query);
    entries = entries.filter(matchesFilter);

    var shown = {};
    entries.forEach(function (e) { shown[e.code] = true; });

    // Matching rows first, in relevance order; the rest keep their place, hidden.
    var order = entries.map(function (e) { return e.code; })
      .concat(defaultOrder.filter(function (c) { return !shown[c]; }));
    order.forEach(function (code) {
      var r = rows[code];
      if (!r) return;
      r.el.hidden = !shown[code];
      r.t.innerHTML = highlight(r.tText, query);
      r.s.innerHTML = highlight(r.sText, query);
      listEl.appendChild(r.el);
    });

    countEl.textContent = (!query && filter === 'All')
      ? total + ' codes'
      : entries.length + ' of ' + total + ' codes';
    emptyEl.classList.toggle('show', entries.length === 0);
  }

  function setQuery(value, updateUrl) {
    query = value.trim();
    if (updateUrl) {
      history.replaceState(null, '', query
        ? location.pathname + '?q=' + encodeURIComponent(query)
        : location.pathname);
    }
    document.title = query ? 'resultCd ' + query + ' — KRA eTIMS Error Reference' : DEFAULT_TITLE;
    render();
  }

  searchEl.addEventListener('input', function () { setQuery(searchEl.value, true); });

  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      chips.forEach(function (c) { c.setAttribute('aria-pressed', 'false'); });
      chip.setAttribute('aria-pressed', 'true');
      filter = chip.dataset.filter;
      render();
    });
  });

  // "/" focuses search from anywhere; Escape clears it.
  document.addEventListener('keydown', function (e) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (e.key === '/' && !typing) {
      e.preventDefault();
      searchEl.focus();
      searchEl.select();
    }
    if (e.key === 'Escape' && document.activeElement === searchEl) {
      searchEl.value = '';
      setQuery('', true);
      searchEl.blur();
    }
  });

  // Restore a shared search (?q=902).
  var params = new URLSearchParams(location.search);
  if (params.has('q')) {
    searchEl.value = params.get('q');
    setQuery(searchEl.value, false);
  } else {
    render();
  }
})();
