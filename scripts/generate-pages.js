#!/usr/bin/env node
/**
 * Generates a standalone HTML page for each error code in errors.js.
 *
 * Each page at /docs/{code}.html is independently indexable — no meta-refresh,
 * no canonical redirect. The URL itself signals exact intent to AI crawlers and
 * search engines. Pages are self-contained with full content and link back to
 * the main reference.
 *
 * Usage: node scripts/generate-pages.js
 */

const fs   = require('fs');
const path = require('path');

const { ERRORS } = require('../docs/errors.js');

const BASE_URL   = 'https://linkd-taxid.github.io/kra-etims-sdk';
const DOCS_DIR   = path.join(__dirname, '..', 'docs');
const FAVICON    = `data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text x='16' y='24' font-family='system-ui,sans-serif' font-size='22' font-weight='700' fill='%23f85149' text-anchor='middle'>!</text></svg>`;

function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function badgeStyle(category) {
  const styles = {
    Official:   'background:#d1ecf1;color:#0c5460',
    Production: 'background:#f8d7da;color:#721c24',
    Client:     'background:#fff3cd;color:#856404',
  };
  return styles[category] || styles.Official;
}

function buildPage(e) {
  const causesHtml = e.causes && e.causes.length
    ? `<div class="section-label">Likely Causes</div>
       <ul>${e.causes.map(c => `<li>${escapeHtml(c)}</li>`).join('\n')}</ul>`
    : '';

  const fixHtml = e.fix
    ? `<div class="section-label">Fix</div>
       <div class="fix-box">${escapeHtml(e.fix)}</div>`
    : '';

  const gotchaHtml = e.gotcha
    ? `<div class="section-label">Gotcha</div>
       <div class="gotcha-box">${escapeHtml(e.gotcha)}</div>`
    : '';

  // JSON-LD: TechArticle for each error page
  const jsonLd = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'TechArticle',
    'name': `KRA eTIMS resultCd ${e.code} — ${e.title}`,
    'description': e.description,
    'url': `${BASE_URL}/${e.code}.html`,
    'author': { '@type': 'Organization', 'name': 'Linkd TaxID', 'url': 'https://github.com/Linkd-TaxID' },
    'about': [
      { '@type': 'Thing', 'name': 'KRA eTIMS' },
      { '@type': 'Thing', 'name': `resultCd ${e.code}` },
    ],
  }, null, 2);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>resultCd ${escapeHtml(e.code)} — ${escapeHtml(e.title)} | KRA eTIMS Error Reference</title>
  <meta name="description" content="${escapeHtml(e.description)}">
  <link rel="icon" type="image/svg+xml" href="${FAVICON}">
  <script type="application/ld+json">${jsonLd}</script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #222; line-height: 1.6; }
    .container { max-width: 760px; margin: 0 auto; padding: 32px 20px; }
    .back { font-size: 14px; color: #0050c8; text-decoration: none; display: inline-block; margin-bottom: 24px; }
    .back:hover { text-decoration: underline; }
    .card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 28px; }
    .card-top { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
    .code { font-family: monospace; font-size: 28px; font-weight: 700; color: #0050c8; }
    .badge { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 12px; }
    h1 { font-size: 20px; font-weight: 600; margin-bottom: 12px; }
    p { font-size: 15px; color: #444; margin-bottom: 12px; }
    .section-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #666; margin-bottom: 6px; margin-top: 16px; }
    .fix-box { background: #f0fff4; border-left: 3px solid #28a745; padding: 10px 14px; border-radius: 0 6px 6px 0; font-size: 14px; }
    .gotcha-box { background: #fff8e1; border-left: 3px solid #f59e0b; padding: 10px 14px; border-radius: 0 6px 6px 0; font-size: 13px; color: #555; }
    ul { padding-left: 20px; font-size: 14px; }
    li { margin-bottom: 4px; }
    footer { margin-top: 32px; font-size: 13px; color: #666; }
    footer a { color: #0050c8; text-decoration: none; }
    footer a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="container">
    <a class="back" href="./">← All KRA eTIMS Error Codes</a>
    <div class="card">
      <div class="card-top">
        <span class="code">${escapeHtml(e.code)}</span>
        <span class="badge" style="${badgeStyle(e.category)}">${escapeHtml(e.category)}</span>
      </div>
      <h1>${escapeHtml(e.title)}</h1>
      <p>${escapeHtml(e.description)}</p>
      ${causesHtml}
      ${fixHtml}
      ${gotchaHtml}
    </div>
    <footer>
      <p>Part of the <a href="${BASE_URL}/">KRA eTIMS Error Code Reference</a> · Maintained by <a href="https://github.com/Linkd-TaxID">Linkd TaxID</a></p>
    </footer>
  </div>
</body>
</html>`;
}

let generated = 0;
for (const e of ERRORS) {
  const filename = `${e.code}.html`;
  const filepath = path.join(DOCS_DIR, filename);
  fs.writeFileSync(filepath, buildPage(e), 'utf8');
  generated++;
  console.log(`  wrote ${filename}`);
}

console.log(`\nDone — ${generated} pages written to docs/`);
