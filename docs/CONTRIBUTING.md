# Contributing to the response-code reference

Report a missing or incorrect code with the endpoint, runtime version,
environment, exact resultCd and redacted resultMsg. Distinguish a reproducible
observation from an interpretation. Exclude credentials, device keys and buyer data.

Edit `docs/errors.js`, then run `node scripts/generate-pages.js`. It regenerates
`docs/index.html`, every `docs/<code>.html` page, `docs/faq.html`, `docs/errors.json`
and `docs/sitemap.xml`. Don't hand-edit those files except for the FAQ questions.

FAQ questions live in `docs/faq.html` between the `FAQ:START` and `FAQ:END`
markers, with matching entries in its FAQPage JSON-LD. Keep the two consistent.

Each code page answers "What does KRA eTIMS error <code> mean?" using the entry's
`description`, and "How do I resolve it?" using `fix`, in both the page and its
FAQPage structured data. Add `dateModified` (YYYY-MM-DD) when you change an entry.

Styles are in `docs/assets/taxid-docs.css` and follow taxid.co.ke. Search runs
from `docs/assets/search.js` with a local copy of Fuse.js, and the full code list
is in the HTML so the page works without JavaScript.

[Open an issue](https://github.com/Linkd-TaxID/kra-etims-sdk/issues/new).
