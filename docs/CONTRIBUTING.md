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

## Publication boundaries

The Pages workflow regenerates the reference and fails if committed outputs drift.
It copies only the explicit public inventory in `scripts/check_distribution.py`
into `build/pages-site`, checks that directory, then uploads it to Pages. Source
Markdown guides remain available in GitHub; local notes, tests and SDK source are
not Pages inputs. The staged site includes the notices and licenses for its
vendored fonts and Fuse.js; see `docs/assets/licenses/NOTICE.txt` for provenance.
Generated reference HTML/JSON, public fonts and reviewed synthetic test fixtures
are intentional source assets. They are not blanket exemptions for new outputs.

Python dependencies are declared only in `pyproject.toml`. Install development
requirements with `python -m pip install -e ".[dev,cli]"`. Build packages with
`python -m build`, then run `python scripts/check_distribution.py --dist-dir dist`
and `TAXID_DIST_DIR=dist pytest tests/test_distribution.py -q`. The wheel includes
only the SDK and its metadata. The source archive also includes complete tests,
synthetic JSON fixtures and the release-inventory checker. Transcript exports,
local credentials, private agent instructions and audit handoffs are never release
inputs. Extending a publication inventory requires reviewing the owning build
script and its rejection tests together.
