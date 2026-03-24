# KRA eTIMS Error Code Reference — Changelog

## 2026-03-24

- Added fuzzy search (Fuse.js 7) with match highlighting, URL sync (?q=), keyboard shortcuts
- Added per-error HTML pages for direct URL indexing (901.html, 921.html, etc.)
- Added FAQ page with verbatim developer Q&A
- Added errors.json for direct machine-readable access without JS execution
- Added robots.txt with explicit AI crawler permissions (GPTBot, ClaudeBot, PerplexityBot)
- Added sitemap.xml covering all error pages and FAQ
- Expanded llms.txt to complete machine-readable reference
- Fixed Gotcha filter (was filtering on category="Gotcha"; now filters on gotcha field presence)
- Fixed "Claude-Web" user agent string to "ClaudeBot" (Anthropic's documented UA)

## 2026-03-20

- Added production-observed codes E04, E11, 0000, 905
- Confirmed all §4.18 codes against KRA OSCU Specification v2.0 (April 2023)
- Added VSCU 24-hour ceiling warning to resultCd 894
- Added cmcKey leak warning to resultCd 902
- Added idempotent success guidance to resultCd 994

## 2026-03-01

- Initial release
- Covers all official KRA OSCU Spec v2.0 §4.18 result codes
- Covers production-observed codes absent from official documentation
- Category filters: Official Spec, Production Only, Client-Side, Has Gotcha
