# Contributing

Report a missing or incorrect code with the endpoint, runtime version,
environment, exact resultCd and redacted resultMsg. Distinguish a reproducible
observation from an interpretation. Exclude credentials, device keys and buyer data.

## Layout

| Path | Contents |
|---|---|
| `src/`, `tests/`, `examples/` | The Python SDK |
| `docs/` | Guides, read on GitHub |
| `site/` | Source of docs.taxid.co.ke |
| `scripts/` | Repository and release checks |

## Result codes

Edit `site/errors.json`, then build the site and open it locally:

```bash
node site/build.js build/pages-site
python3 scripts/check_distribution.py --docs-dir build/pages-site
python3 -m http.server -d build/pages-site
```

The build needs an empty output directory. `build/` is ignored: never commit
generated pages. Record the change in `site/CHANGELOG.md`.

FAQ questions live in `site/faq.html` between the `FAQ:START` and `FAQ:END`
markers, with matching entries in its FAQPage JSON-LD. Keep the two consistent.

Each code page answers "What does KRA eTIMS error <code> mean?" using the entry's
`description`, and "How do I resolve it?" using `fix`, in both the page and its
FAQPage structured data. Add `dateModified` (YYYY-MM-DD) when you change an entry.

Styles are in `site/static/assets/taxid-docs.css` and follow taxid.co.ke. Search runs
from `site/static/assets/search.js` with a local copy of Fuse.js, and the full code list
is in the HTML so the page works without JavaScript.

[Open an issue](https://github.com/Linkd-TaxID/kra-etims-sdk/issues/new).

## Publication boundaries

The Pages workflow builds `site/` into `build/pages-site`, requires that directory
to match the public inventory in `scripts/check_distribution.py`, then uploads it. Source
Markdown guides remain available in GitHub; local notes, tests and SDK source are
not Pages inputs. The staged site includes the notices and licenses for its
vendored fonts and Fuse.js; see `site/static/assets/licenses/NOTICE.txt` for provenance.
Vendored fonts and reviewed synthetic test fixtures are intentional source assets.
They are not blanket exemptions for new files.

Python dependencies are declared only in `pyproject.toml`. Install development
requirements with `python -m pip install -e ".[dev,cli]"`. Build packages with
`python -m build`, then run `python scripts/check_distribution.py --dist-dir dist`
and `TAXID_DIST_DIR=dist pytest tests/test_distribution.py -q`. The wheel includes
only the SDK and its metadata. The source archive also includes complete tests,
synthetic JSON fixtures, the release checker, and its shared content scanner and
reviewed policy. Release checks scan actual archive member bytes, including package
metadata, after validating member paths, types and bounded sizes. Transcript exports,
local credentials, private agent instructions and audit handoffs are never release
inputs. Extending a publication inventory requires reviewing the owning build
script and its rejection tests together.

Both PyPI publication boundaries install checksum-pinned Gitleaks and require
`--gitleaks "$RUNNER_TEMP/gitleaks"` when checking distribution archives. The
checker first bounds and validates archive members, then scans their actual
unpacked bytes using `.github/gitleaks.toml`. Optional local runs without that
flag still enforce membership and the lightweight content policy; they do not
replace the required release secret scan. Findings fail publication without
printing their values.
