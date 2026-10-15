#!/usr/bin/env node
// Generate the docs.taxid.co.ke result-code reference from docs/errors.js.
//
// Writes:
//   docs/index.html      code list, pre-rendered so it works without JavaScript
//   docs/<code>.html     one page per resultCd
//   docs/errors.json     machine-readable copy of docs/errors.js
//   docs/sitemap.xml     index, FAQ, errors.json and every code page
//
// docs/faq.html is hand-maintained but uses the same shell (nav, footer, styles).
// Styles live in docs/assets/taxid-docs.css and match taxid.co.ke.

const fs   = require('fs');
const path = require('path');

const { ERRORS } = require('../docs/errors.js');

const BASE_URL     = 'https://docs.taxid.co.ke';
const SITE_URL     = 'https://taxid.co.ke';
const REPO_URL     = 'https://github.com/Linkd-TaxID/kra-etims-sdk';
const ISSUE_URL    = `${REPO_URL}/issues/new`;
const DOCS_DIR     = path.join(__dirname, '..', 'docs');
const DEFAULT_DATE = '2026-10-07';

// Index and FAQ dates for the sitemap. Bump when that page's content changes.
const INDEX_UPDATED = '2026-10-14';
const FAQ_UPDATED   = '2026-10-14';

const ERROR_MAP = Object.fromEntries(ERRORS.map(e => [e.code, e]));

const BADGES = {
  Official: { label: 'Official spec', cls: 'b-official' },
  Observed: { label: 'Observed',      cls: 'b-official' },
  Reported: { label: 'Reported',      cls: 'b-reported' },
  Client:   { label: 'Client-side',   cls: 'b-client' },
};

function escapeHtml(text) {
  if (text === undefined || text === null) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function badge(category) {
  const b = BADGES[category] || BADGES.Official;
  return `<span class="badge ${b.cls}">${b.label}</span>`;
}

function formatDate(iso) {
  const d = new Date(iso + 'T00:00:00Z');
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

// First sentence of a description, for the one-line summary in the list.
function summary(text) {
  const m = String(text).match(/^.*?[.!?](\s|$)/);
  return (m ? m[0] : String(text)).trim();
}

// JSON for <script type="application/ld+json">, safe against "</script>".
function jsonLd(obj) {
  return JSON.stringify(obj, null, 2).replace(/</g, '\\u003c');
}

// ─────────────────────────────────────────
// Shared shell (also used for faq.html, see FAQ section below)
// ─────────────────────────────────────────

function head({ title, description, canonical, ogTitle, ogDescription, keywords, extra = '' }) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(title)}</title>
  <meta name="description" content="${escapeHtml(description)}">
${keywords ? `  <meta name="keywords" content="${escapeHtml(keywords)}">\n` : ''}  <meta name="robots" content="index, follow, max-snippet:-1">
  <link rel="canonical" href="${canonical}">
  <link rel="icon" type="image/x-icon" href="${SITE_URL}/favicon.ico">
  <link rel="icon" type="image/png" sizes="32x32" href="${SITE_URL}/favicon-32x32.png">
  <link rel="apple-touch-icon" sizes="180x180" href="${SITE_URL}/apple-touch-icon.png">
  <meta property="og:title" content="${escapeHtml(ogTitle || title)}">
  <meta property="og:description" content="${escapeHtml(ogDescription || description)}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="${canonical}">
  <meta property="og:site_name" content="TaxID">
  <meta property="og:image" content="${SITE_URL}/og-image.png">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="${escapeHtml(ogTitle || title)}">
  <meta name="twitter:description" content="${escapeHtml(ogDescription || description)}">
  <meta name="twitter:image" content="${SITE_URL}/og-image.png">
  <link rel="preload" href="assets/fonts/inter-var.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="preload" href="assets/fonts/jetbrains-mono-var.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="assets/taxid-docs.css">
${extra}</head>`;
}

function nav(active) {
  const cls = name => `nav-link${active === name ? ' is-active' : ''}`;
  const cur = name => (active === name ? ' aria-current="page"' : '');
  return `<nav id="nav">
  <div class="nav-row">
    <a href="${SITE_URL}/" class="nav-logo" aria-label="TaxID home">Tax<span>ID</span><span class="docs-tag">docs</span></a>
    <button class="nav-burger" id="nav-burger" aria-label="Toggle navigation menu" aria-expanded="false" aria-controls="nav-links">
      <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
    </button>
    <div class="nav-links" id="nav-links">
      <a href="${SITE_URL}/#how-it-works" class="nav-link">How it works</a>
      <a href="${SITE_URL}/blog/" class="nav-link">Blog</a>
      <a href="./" class="${cls('codes')}"${cur('codes')}>Error codes</a>
      <a href="faq.html" class="${cls('faq')}"${cur('faq')}>FAQ</a>
      <a href="${SITE_URL}/#waitlist" class="btn btn-primary" style="margin-left: 8px; font-size: var(--fs-sm); padding: 8px 18px;">Request developer access</a>
    </div>
  </div>
</nav>`;
}

function footer() {
  return `<footer>
  <div class="wrap">
    <div class="footer-row">
      <a href="${SITE_URL}/" class="nav-logo">Tax<span>ID</span></a>
      <div class="footer-links">
        <a href="${SITE_URL}/blog/">Blog</a>
        <a href="./">Error codes</a>
        <a href="faq.html">FAQ</a>
        <a href="${REPO_URL}">Python SDK</a>
        <a href="${ISSUE_URL}">Add a missing code</a>
        <a href="mailto:support@taxid.co.ke">Contact</a>
      </div>
      <p class="footer-copy">© ${new Date().getUTCFullYear()} TaxID. Built for Kenya.</p>
    </div>
  </div>
</footer>
<script src="assets/nav.js" defer></script>`;
}

function cta() {
  return `<aside class="d-cta">
      <div>
        <h3>Integrating eTIMS?</h3>
        <p>TaxID handles device setup, signing and recovery behind one API.</p>
      </div>
      <a href="${SITE_URL}/#waitlist" class="btn btn-primary">Request developer access</a>
    </aside>`;
}

const SOURCE_NOTE = 'Data sourced from KRA OSCU Specification v2.0 §4.18 and retained sandbox observations and separately labelled reports.';

// ─────────────────────────────────────────
// Index
// ─────────────────────────────────────────

function buildIndex() {
  const title = 'KRA eTIMS Error Codes — OSCU/VSCU resultCd Reference';

  const articleLd = jsonLd({
    '@context': 'https://schema.org',
    '@type': 'TechArticle',
    'name': 'KRA eTIMS OSCU/VSCU API Error Code Reference',
    'description': 'Reference for KRA eTIMS result codes including official OSCU/VSCU spec codes and reported codes outside the cited specification.',
    'url': `${BASE_URL}/`,
    'dateModified': INDEX_UPDATED,
    'author': { '@type': 'Organization', 'name': 'TaxID', 'url': `${SITE_URL}/` },
    'about': [
      { '@type': 'Thing', 'name': 'KRA eTIMS' },
      { '@type': 'Thing', 'name': 'OSCU API' },
      { '@type': 'Thing', 'name': 'VSCU API' },
      { '@type': 'Thing', 'name': 'Kenya Revenue Authority' },
      { '@type': 'Thing', 'name': 'GavaConnect' },
    ],
    'keywords': 'KRA eTIMS, OSCU, VSCU, resultCd, error codes, Kenya tax API',
  });

  const listLd = jsonLd({
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    'name': 'KRA eTIMS result codes',
    'numberOfItems': ERRORS.length,
    'itemListElement': ERRORS.map((e, i) => ({
      '@type': 'ListItem',
      'position': i + 1,
      'name': `${e.code} — ${e.title}`,
      'url': `${BASE_URL}/${e.code}.html`,
    })),
  });

  const rows = ERRORS.map(e => `      <a class="code-row" href="${escapeHtml(e.code)}.html" data-code="${escapeHtml(e.code)}">
        <span class="c">${escapeHtml(e.code)}</span>
        <span class="t">${escapeHtml(e.title)}</span>
        ${badge(e.category)}
        <span class="s">${escapeHtml(summary(e.description))}</span>
      </a>`).join('\n');

  return `${head({
    title,
    description: 'Reference for KRA eTIMS OSCU and VSCU API result codes. Covers resultCd 000, 001, 901, 902, 903, 910, 921, 922, 990, 994, E04, E11 with causes and fixes. Includes undocumented reported codes outside the cited KRA specification.',
    canonical: `${BASE_URL}/`,
    ogDescription: 'Reference for KRA eTIMS OSCU/VSCU result codes, including reported codes outside the cited KRA specification.',
    keywords: 'KRA eTIMS error codes, OSCU resultCd, VSCU error codes, eTIMS 901, eTIMS 910, eTIMS 921, KRA API errors, GavaConnect errors, kenya revenue authority API, eTIMS integration Kenya',
    extra: `  <meta name="google-site-verification" content="kvoqWPq6RXNOcyORJr4pyubDvsm7YdA_T15Y6yJ86VY">
  <script type="application/ld+json">${articleLd}</script>
  <script type="application/ld+json">${listLd}</script>
`,
  })}
<body>
${nav('codes')}

<main id="main">
  <section class="d-head wrap">
    <span class="eyebrow">eTIMS reference</span>
    <h1 class="d-title">KRA eTIMS error codes</h1>
    <p class="lead">TaxID embeds fiscal operations in existing invoicing, POS and ERP software. This reference covers raw control-unit codes, not TaxID transaction statuses. A signed receipt does not establish central KRA acceptance.</p>
    <p class="d-sub">OSCU/VSCU <code>resultCd</code> reference, including reported codes outside the cited KRA specification.</p>

    <div class="warn" role="note">
      <strong>Multiple success-code variants exist</strong>
      KRA returns <code>"000"</code> (OSCU spec §4.18, also confirmed live from the VSDC 2.0.6 JAR), <code>"0000"</code> (observed live from GavaConnect), and <code>"00"</code> has been reported for the VSCU JAR. Never hardcode a single string. Always check membership in <code>{"0", "00", "000", "0000"}</code>.
    </div>

    <div class="controls">
      <div class="search-wrap">
        <label for="search" class="sr-only">Search error codes</label>
        <input type="search" id="search" class="search-input" placeholder="Search codes, messages and fixes…" autocomplete="off" spellcheck="false">
        <kbd class="search-key" aria-hidden="true">/</kbd>
      </div>
      <div class="filters" role="group" aria-label="Filter by source">
        <button class="chip" data-filter="All" aria-pressed="true">All</button>
        <button class="chip" data-filter="Official" aria-pressed="false">Official spec</button>
        <button class="chip" data-filter="Reported" aria-pressed="false">Reported</button>
        <button class="chip" data-filter="Client" aria-pressed="false">Client-side</button>
        <button class="chip" data-filter="Notes" aria-pressed="false">Has notes</button>
      </div>
    </div>
  </section>

  <section class="wrap d-list" aria-label="Result codes">
    <div class="list-meta">
      <span id="result-count" aria-live="polite">${ERRORS.length} codes</span>
      <span class="hint">Press <kbd>/</kbd> to search · <kbd>Esc</kbd> to clear</span>
    </div>
    <div class="code-list" id="code-list">
${rows}
    </div>
    <p class="empty" id="empty-state">No matching codes found. <a href="${ISSUE_URL}">Open an issue</a> to add this code.</p>
    <p class="d-sub source-note">${SOURCE_NOTE}</p>
  </section>
</main>

${footer()}
<script src="assets/fuse.min.js" defer></script>
<script src="errors.js" defer></script>
<script src="assets/search.js" defer></script>
</body>
</html>
`;
}

// ─────────────────────────────────────────
// Code pages
// ─────────────────────────────────────────

function buildPage(e) {
  const updated = e.dateModified || DEFAULT_DATE;
  const url = `${BASE_URL}/${e.code}.html`;

  // Q&A text reuses the reviewed title, description and fix, so no new claims are made.
  const meaning = `${e.title}. ${e.description}`;

  const causesHtml = e.causes && e.causes.length
    ? `\n    <p class="lbl">Possible causes</p>
    <ul class="causes">${e.causes.map(c => `<li>${escapeHtml(c)}</li>`).join('')}</ul>`
    : '';

  const fixHtml = e.fix
    ? `\n    <p class="lbl">Resolution</p>
    <div class="box box-fix">${escapeHtml(e.fix)}</div>`
    : '';

  const gotchaHtml = e.gotcha
    ? `\n    <p class="lbl">Notes</p>
    <div class="box box-note">${escapeHtml(e.gotcha)}</div>`
    : '';

  const related = (e.related || []).filter(code => ERROR_MAP[code]);
  const relatedHtml = related.length
    ? `\n    <p class="lbl">Related codes</p>
    <div class="rel">${related.map(code =>
      `<a href="./${escapeHtml(code)}.html"><b>${escapeHtml(code)}</b>${escapeHtml(ERROR_MAP[code].title)}</a>`).join('')}</div>`
    : '';

  const articleLd = jsonLd({
    '@context': 'https://schema.org',
    '@type': 'TechArticle',
    'name': `KRA eTIMS resultCd ${e.code} — ${e.title}`,
    'description': e.description,
    'url': url,
    'dateModified': updated,
    'author': { '@type': 'Organization', 'name': 'TaxID', 'url': `${SITE_URL}/` },
    'about': [
      { '@type': 'Thing', 'name': 'KRA eTIMS' },
      { '@type': 'Thing', 'name': `resultCd ${e.code}` },
    ],
  });

  const breadcrumbLd = jsonLd({
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    'itemListElement': [
      { '@type': 'ListItem', 'position': 1, 'name': 'KRA eTIMS Error Codes', 'item': `${BASE_URL}/` },
      { '@type': 'ListItem', 'position': 2, 'name': `Error ${e.code}: ${e.title}`, 'item': url },
    ],
  });

  const faqEntities = [{
    '@type': 'Question',
    'name': `What does KRA eTIMS error ${e.code} mean?`,
    'acceptedAnswer': { '@type': 'Answer', 'text': meaning },
  }];
  if (e.fix) {
    faqEntities.push({
      '@type': 'Question',
      'name': `How do I resolve eTIMS error ${e.code}?`,
      'acceptedAnswer': { '@type': 'Answer', 'text': e.fix },
    });
  }
  const faqLd = jsonLd({ '@context': 'https://schema.org', '@type': 'FAQPage', 'mainEntity': faqEntities });

  return `${head({
    title: `Error ${e.code}: ${e.title} | KRA eTIMS Error Reference`,
    description: e.description,
    canonical: url,
    extra: `  <script type="application/ld+json">${articleLd}</script>
  <script type="application/ld+json">${breadcrumbLd}</script>
  <script type="application/ld+json">${faqLd}</script>
`,
  })}
<body>
${nav('codes')}

<main id="main">
  <article class="d-head wrap d-page">
    <a class="back-link" href="./">&larr; All error codes</a>
    <div class="code-head">
      <span class="code-hero">${escapeHtml(e.code)}</span>
      ${badge(e.category)}
    </div>
    <h1 class="d-title">${escapeHtml(e.title)}</h1>

    <section class="qa" aria-label="Summary">
      <h2>What does KRA eTIMS error ${escapeHtml(e.code)} mean?</h2>
      <p>${escapeHtml(e.description)}</p>
      <p>Raw control-unit codes differ from TaxID HTTP errors and transaction statuses. A code alone neither authorizes fiscal replay nor proves central KRA acceptance.</p>
    </section>
${causesHtml}${fixHtml}${gotchaHtml}${relatedHtml}

    <p class="meta-line">Updated <time datetime="${updated}">${formatDate(updated)}</time> · ${escapeHtml((BADGES[e.category] || BADGES.Official).label)} · <a href="${ISSUE_URL}">Report a correction</a></p>

    ${cta()}
  </article>
</main>

${footer()}
</body>
</html>
`;
}

// ─────────────────────────────────────────
// FAQ: keep the hand-written questions, wrap them in the shared shell.
// Edit the questions in docs/faq.html between the FAQ markers.
// ─────────────────────────────────────────

function buildFaq() {
  const file = path.join(DOCS_DIR, 'faq.html');
  const src = fs.readFileSync(file, 'utf8');
  const ld = src.match(/<script type="application\/ld\+json">[\s\S]*?<\/script>/);
  const body = src.match(/<!-- FAQ:START -->([\s\S]*?)<!-- FAQ:END -->/);
  if (!ld || !body) throw new Error('faq.html: missing JSON-LD or FAQ:START/FAQ:END markers');

  return `${head({
    title: 'KRA eTIMS FAQ — Common Integration Questions Answered',
    description: 'TaxID API outcomes, authentication, original-reference lookup and KRA control-unit response semantics.',
    canonical: `${BASE_URL}/faq.html`,
    keywords: 'KRA eTIMS FAQ, eTIMS integration questions, OSCU VSCU setup, KRA resultCd 901 fix, eTIMS 921 error, Kenya eTIMS developer guide',
    ogDescription: 'Developer FAQ for KRA eTIMS: resultCd errors, VSCU vs OSCU differences, cmcKey, tax bands, offline queueing, and more.',
    extra: `  ${ld[0]}\n`,
  })}
<body>
${nav('faq')}

<main id="main">
  <section class="d-head wrap d-page">
    <a class="back-link" href="./">&larr; All error codes</a>
    <span class="eyebrow">Developer FAQ</span>
    <h1 class="d-title">KRA eTIMS integration FAQ</h1>
    <p class="lead">TaxID integration contracts and control-unit evidence.</p>

    <div class="faq-list">
<!-- FAQ:START -->${body[1]}<!-- FAQ:END -->
    </div>

    <p class="d-sub source-note">${SOURCE_NOTE} <a href="${ISSUE_URL}">Add a missing question</a>.</p>

    ${cta()}
  </section>
</main>

${footer()}
</body>
</html>
`;
}

// ─────────────────────────────────────────
// Sitemap
// ─────────────────────────────────────────

function buildSitemap() {
  const latest = ERRORS.map(e => e.dateModified || DEFAULT_DATE).sort().pop();
  const indexDate = INDEX_UPDATED > latest ? INDEX_UPDATED : latest;
  const url = (loc, lastmod, priority) => `  <url>
    <loc>${loc}</loc>
    <lastmod>${lastmod}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>${priority}</priority>
  </url>`;
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${[
    url(`${BASE_URL}/`, indexDate, '1.0'),
    url(`${BASE_URL}/faq.html`, FAQ_UPDATED, '0.9'),
    url(`${BASE_URL}/errors.json`, latest, '0.8'),
    ...ERRORS.map(e => url(`${BASE_URL}/${e.code}.html`, e.dateModified || DEFAULT_DATE, '0.7')),
  ].join('\n')}
</urlset>
`;
}

// ─────────────────────────────────────────
// Write
// ─────────────────────────────────────────

function write(name, content) {
  const out = content.split('\n').map(line => line.trimEnd()).join('\n');
  fs.writeFileSync(path.join(DOCS_DIR, name), out, 'utf8');
  console.log(`  wrote ${name}`);
}

fs.writeFileSync(path.join(DOCS_DIR, 'errors.json'), JSON.stringify(ERRORS, null, 2) + '\n', 'utf8');
console.log('  wrote errors.json');

write('index.html', buildIndex());
for (const e of ERRORS) write(`${e.code}.html`, buildPage(e));
write('faq.html', buildFaq());
write('sitemap.xml', buildSitemap());

console.log(`\nDone — index, ${ERRORS.length} code pages, FAQ and sitemap written to docs/`);
