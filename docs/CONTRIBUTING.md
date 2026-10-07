# Contributing to the response-code reference

Report a missing or incorrect code with the endpoint, runtime version,
environment, exact resultCd and redacted resultMsg. Distinguish a reproducible
observation from an interpretation. Exclude credentials, device keys and buyer data.

Edit `docs/errors.js`, then run `node scripts/generate-pages.js` to regenerate
the code pages and `docs/errors.json`. Keep the FAQ body and structured data consistent.

[Open an issue](https://github.com/Linkd-TaxID/kra-etims-sdk/issues/new).
