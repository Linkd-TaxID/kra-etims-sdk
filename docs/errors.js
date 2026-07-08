const ERRORS = [
  {
    code: "000",
    title: "Success",
    category: "Official",
    description: "The request was processed successfully. This is the OSCU spec code per §4.18.",
    causes: [],
    fix: "No action needed.",
    gotcha: "Success-code shape varies by integration path: \"000\" per the OSCU spec §4.18 — and live signings against the real VSDC 2.0.6 sandbox JAR also return \"000\" (verified 2026-07-06); \"0000\" observed live from GavaConnect; \"00\" reported for the VSCU JAR but unconfirmed by direct observation. Never hardcode a single string. Always check membership in {\"0\", \"00\", \"000\", \"0000\"}.",
    related: ["00", "0000", "001"]
  },
  {
    code: "00",
    title: "Success (Reported VSCU JAR Variant)",
    category: "Production",
    description: "Two-digit success variant reported for the VSCU JAR. Unconfirmed by direct observation: live signings against the real VSDC 2.0.6 sandbox JAR (2026-07-06) returned \"000\" per the spec §3.3.6 sample. Keep \"00\" in your success set defensively.",
    causes: ["Reported for some VSCU/VSDC builds (unconfirmed — live VSDC 2.0.6 returns \"000\")"],
    fix: "Normalize all success checks to check membership in {\"0\", \"00\", \"000\", \"0000\"} rather than equality to \"000\".",
    gotcha: "Any hardcoded if resultCd == \"000\" check will silently swallow this as a failure, causing every VSCU invoice signature to appear unsuccessful with no exception raised.",
    related: ["000", "0000"]
  },
  {
    code: "0000",
    title: "Success (GavaConnect Variant)",
    category: "Production",
    description: "Four-digit success variant observed in live GavaConnect responses. Not in the official spec.",
    causes: ["Transient GavaConnect backend response variant"],
    fix: "Normalize all success checks to check membership in {\"0\", \"00\", \"000\", \"0000\"}.",
    gotcha: "Absent from all official documents. Only confirmed through production observation.",
    related: ["000", "00"]
  },
  {
    code: "001",
    title: "No Search Result",
    category: "Official",
    description: "The query returned an empty result set. This is not a failure — it means no records exist matching the request.",
    causes: [
      "Querying for purchases before any exist",
      "Querying stock before any movements have been recorded",
      "Valid query with no matching data"
    ],
    fix: "Treat this as an empty list [], not an exception. Only raise if your business logic requires at least one result.",
    gotcha: "Most public SDKs check if resultCd != \"000\" and raise an exception here. New integrations querying for purchases on day one hit this constantly and believe their integration is broken.",
    related: ["000", "990"]
  },
  {
    code: "891",
    title: "URL Construction Error",
    category: "Client",
    description: "The SCU JAR or library encountered a fault constructing the request URL.",
    causes: ["Malformed endpoint path in SCU configuration", "Trailing or leading whitespace in URL segments"],
    fix: "Strip all URL components of whitespace before use. Check your endpoint path configuration. GavaConnect returns HTTP 200 with an error body on whitespace URLs — no spec mention anywhere.",
    related: ["892", "893", "899"]
  },
  {
    code: "892",
    title: "Header Construction Error",
    category: "Client",
    description: "The SCU library failed to construct a required HTTP header.",
    causes: ["Missing required header value in SCU library call", "Null or empty cmcKey passed to OSCU request"],
    fix: "Verify all required headers are populated. For OSCU, cmcKey must be present in every transaction request body — it is not optional after initialization.",
    related: ["891", "900", "910"]
  },
  {
    code: "893",
    title: "Request Body Serialization Error",
    category: "Client",
    description: "The SCU library could not serialize the request payload.",
    causes: ["Non-serializable field type", "Invoice number sent as string instead of integer"],
    fix: "Invoice numbers must be sequential integers (1, 2, 3...) — not strings like INV001. Check all field types against the spec.",
    related: ["910", "891", "899"]
  },
  {
    code: "894",
    title: "Server Communication Error",
    category: "Client",
    description: "TCP or network failure between the SCU and the KRA backend.",
    causes: [
      "No internet connection",
      "VSCU 24-hour reconnection ceiling reached",
      "KRA API server unreachable"
    ],
    fix: "Check connectivity to etims-api.kra.go.ke. If on VSCU, check when the JAR last connected — it stops issuing receipt numbers exactly 24 hours after the last successful connection with no warning signal.",
    gotcha: "The VSCU 24-hour ceiling surfaces as a TCP connection refusal or HTTP 503 — indistinguishable from a JAR crash. Track connectivity timestamps at the application layer.",
    related: ["896", "990", "E11"]
  },
  {
    code: "895",
    title: "Unallowed Request Method",
    category: "Client",
    description: "The wrong HTTP verb was used for this endpoint.",
    causes: ["GET used instead of POST", "Incorrect method in SCU library call"],
    fix: "All write operations use POST. Check the endpoint method in your SCU configuration.",
    related: ["912", "911"]
  },
  {
    code: "896",
    title: "Request Status Error",
    category: "Client",
    description: "An unexpected HTTP status code was received from the server.",
    causes: ["Proxy or middleware returning unexpected status", "Transient KRA backend error"],
    fix: "Log the full response including HTTP status and body. Retry once with exponential backoff before escalating.",
    related: ["894", "999"]
  },
  {
    code: "899",
    title: "Client Catch-All",
    category: "Client",
    description: "Generic client-side error not covered by 891–896. Verified against the live VSDC 2.0.6 sandbox JAR (2026-07-06): the body is {\"resultCd\":\"899\",\"resultMsg\":\"An error regarding Client occurred.\",\"data\":null} — the resultMsg is the same generic sentence for every cause and carries no diagnostic detail.",
    causes: [
      "Resubmitting an invoice number KRA has already accepted — e.g. an offline-queue replay after a client timeout where the original submission actually succeeded (verified live)",
      "Missing, empty, or invalid device credential files (cmcKey, tin, bhfId, dvcSrlNo) on the VSCU path (verified live)",
      "Unclassified SCU library fault"
    ],
    fix: "Do not blindly retry. If this 899 follows a timeout retry or offline-queue replay, first check whether the receipt already exists (receipt-counter arithmetic or the corresponding select endpoint) — re-signing under a new invoice number creates a duplicate receipt at KRA. Then verify all device credential files are present and non-empty.",
    gotcha: "On the VSCU path, duplicate-invoice replays return 899 — not 994 as widely assumed. Because the resultMsg is identical for credential faults and duplicates, the two cases are indistinguishable from the response body alone.",
    related: ["994", "902", "891"]
  },
  {
    code: "900",
    title: "No Header Information",
    category: "Official",
    description: "A required HTTP header is missing from the request.",
    causes: [
      "Authorization header missing",
      "tin, bhfId, or cmcKey missing from OSCU request headers",
      "Content-Type not set to application/json",
      "A present-but-empty (0-byte) cmcKey device file on the VSCU path — file-exists bootstrap checks pass, but the JAR sends an empty credential (verified live 2026-07-04)"
    ],
    fix: "For OSCU, every request after initialization requires tin, bhfId, and cmcKey in the request headers or body depending on the endpoint. Verify all three are present.",
    related: ["910", "892"]
  },
  {
    code: "901",
    title: "Not a Valid Device",
    category: "Official",
    description: "The device serial number (dvcSrlNo) is not registered or approved in KRA's system.",
    causes: [
      "dvcSrlNo not yet approved by KRA",
      "Sandbox serial not pre-provisioned on the test server",
      "Production serial used in sandbox or vice versa"
    ],
    fix: "In sandbox, try device serial dvcv1130 — it may be pre-provisioned. In production, email timsupport@kra.go.ke with subject 'Request for OSCU Device Registration — [Company Name] — PIN: [Your PIN]'.",
    gotcha: "This error is unrecoverable without KRA intervention. You cannot self-register a device serial. Budget several business days for KRA response.",
    related: ["902", "903", "E04"]
  },
  {
    code: "902",
    title: "Device Already Installed",
    category: "Official",
    description: "The device serial has already been initialized and an active cmcKey exists.",
    causes: [
      "Re-running initialization on an already-active device",
      "Redeploying without persisting the original cmcKey"
    ],
    fix: "This is idempotent — not a hard failure, but the 902 response does NOT return the cmcKey: verified live against the KRA sandbox (2026-07-04), the body is {\"resultCd\":\"902\",\"resultMsg\":\"This device is installed\",\"data\":null}. Use the cmcKey you persisted at first initialization. If it is lost, contact timsupport@kra.go.ke to de-register the serial so the device can be re-initialized — there is no self-service recovery.",
    gotcha: "The cmcKey is issued exactly once, only in the original init response, with no rotation mechanism — 902 will not hand it back. Any log line recording the full init response permanently leaks this key; redact cmcKey before any logging. Also watch for a present-but-empty cmcKey file after a failed restore: a file-exists check passes, but every subsequent request fails with 899/900 — validate the file is non-empty.",
    related: ["901", "903"]
  },
  {
    code: "903",
    title: "Device Type Mismatch",
    category: "Official",
    description: "A VSCU serial was used on an OSCU endpoint or vice versa.",
    causes: [
      "VSCU device serial submitted to OSCU initialization endpoint",
      "Architecture migrated from VSCU to OSCU without re-provisioning"
    ],
    fix: "This is unrecoverable without a manual KRA re-provisioning process. Contact timsupport@kra.go.ke. There is no self-service path.",
    gotcha: "Developers who copy a device serial from VSCU documentation and test it against the OSCU endpoint are permanently blocked until KRA intervenes.",
    related: ["901", "905", "E04"]
  },
  {
    code: "905",
    title: "Only OSCU Device Can Be Verified",
    category: "Production",
    description: "VSCU variant of error 903. Returned when an OSCU-only serial is verified against a VSCU endpoint.",
    causes: ["OSCU serial used on VSCU endpoint"],
    fix: "Same resolution as 903 — contact KRA. This code is absent from the official spec and appears to be a GavaConnect version delta that was never communicated.",
    gotcha: "Not documented anywhere in official KRA spec documents. Confirmed only through community production reports.",
    related: ["903", "901"]
  },
  {
    code: "910",
    title: "Request Parameter Error",
    category: "Official",
    description: "A required field is missing or malformed in the request body.",
    causes: [
      "cmcKey missing from OSCU request body",
      "Invoice number sent as string instead of integer",
      "Date field in wrong format",
      "Tax fields missing — all 15 tax fields (taxblAmtA/B/C/D/E, taxRtA/B/C/D/E, taxAmtA/B/C/D/E) are required even for single-category businesses"
    ],
    fix: "Check all required fields. For OSCU, cmcKey must appear in every transaction request body. Invoice numbers must be sequential integers. Dates must be YYYYMMDD for salesDt/pchsDt and YYYYMMDDHHmmss for cfmDt/stockRlsDt. Set unused tax category fields to zero rather than omitting them.",
    gotcha: "The official §4.1 Tax Type table states B = 16.00%, but the spec's own JSON samples show the live API returning B-18.00%. Never hardcode tax rates. Always fetch from selectCodes at runtime.",
    related: ["911", "900", "893"]
  },
  {
    code: "911",
    title: "Empty Request Body",
    category: "Official",
    description: "The request body is null or empty.",
    causes: ["Serialization failure before send", "Accidental GET request to a POST endpoint"],
    fix: "Verify the request body is populated before sending. Log the serialized payload at debug level.",
    related: ["910", "912", "895"]
  },
  {
    code: "912",
    title: "Request Method Error",
    category: "Official",
    description: "Endpoint routing mismatch — the endpoint path does not match the expected route.",
    causes: ["Trailing slash on endpoint path", "Wrong endpoint path — VSCU and OSCU use different paths for equivalent operations"],
    fix: "OSCU uses /saveTrnsSalesOsdc for sales. VSCU uses two separate calls: /trnsSales/saveSales then /trnsSales/saveInvoice. These are not interchangeable.",
    related: ["911", "921", "895"]
  },
  {
    code: "913",
    title: "Code Value Error Among Request Parameters",
    category: "Production",
    description: "A field contains a value that is not in the KRA code table for that field. The resultMsg names the offending field path, e.g. 'Code value error among request parameters [<itemList><pkgUnitCd>]'.",
    causes: [
      "pkgUnitCd populated with a quantity-unit code — 'U' (Unit) belongs to the quantity-unit table (spec §4.6), not the packaging-unit table (§4.5)",
      "Any enumerated field (pkgUnitCd, qtyUnitCd, pmtTyCd, taxTyCd) populated with a value outside its spec §4 code table"
    ],
    fix: "Read the field path in resultMsg, then re-check that field against its code table in spec §4. For pkgUnitCd use packaging-unit codes (§4.5, e.g. NT = Net); for qtyUnitCd use quantity-unit codes (§4.6, e.g. U = Unit). Confirmed live: pkgUnitCd 'U' fails saveSales with 913; 'NT' signs.",
    gotcha: "saveItems does NOT validate these code fields — only saveSales does. An item can register successfully with a bad pkgUnitCd and then 913 every sale that references it. Stock movements can also pass while sales fail, hiding the bad registry row. Confirmed live against VSDC 2.0.6, 2026-07-07.",
    related: ["910", "912"]
  },
  {
    code: "921",
    title: "Sales Invoice Sequence Violation",
    category: "Official",
    description: "saveInvoice was called before saveSales. The VSCU requires a strict two-step sequence.",
    causes: [
      "Developer followed OSCU documentation which uses a single combined call",
      "Architecture migrated from OSCU without updating the sales flow",
      "saveInvoice called in retry logic without first re-sending saveSales"
    ],
    fix: "VSCU requires two sequential calls in this exact order: (1) POST /trnsSales/saveSales with transaction metadata, then (2) POST /trnsSales/saveInvoice with invoice detail and receipt signature. OSCU combines both into a single POST /saveTrnsSalesOsdc call. These paths cannot be mixed.",
    gotcha: "Every developer building from OSCU documentation or an OSCU-first architecture hits this. There is no mention of this difference in any introductory guide.",
    related: ["922", "912", "994"]
  },
  {
    code: "922",
    title: "Invoice Ordering Error",
    category: "Official",
    description: "The same ordering violation as 921 from the other direction — saveInvoice received but saveSales data is in an unexpected state.",
    causes: ["Same root causes as 921", "Race condition in concurrent sales processing"],
    fix: "Enforce strict sequential processing for VSCU sales. Do not parallelize saveSales and saveInvoice calls. Confirm a successful 000 from saveSales before calling saveInvoice.",
    related: ["921", "994"]
  },
  {
    code: "990",
    title: "Rate Limit Exceeded",
    category: "Official",
    description: "Too many requests to a fetch or search endpoint within the allowed window.",
    causes: ["Polling selectCodes or selectItems too frequently", "Bulk sync operations without throttling"],
    fix: "Back off and retry with exponential delay. Cache code list and item classification responses — fetch them once at initialization and refresh on a schedule, not on every transaction.",
    related: ["894", "999", "001"]
  },
  {
    code: "991",
    title: "Error During Registration",
    category: "Official",
    description: "Server-side database write failure on a create operation.",
    causes: ["KRA backend transient failure", "Constraint violation not caught by 994"],
    fix: "Retry once after a short delay. If the failure persists, log the full request and response and escalate to timsupport@kra.go.ke.",
    related: ["992", "994", "999"]
  },
  {
    code: "992",
    title: "Error During Modification",
    category: "Official",
    description: "Server-side database write failure on an update operation.",
    causes: ["KRA backend transient failure", "Updating a record that no longer exists"],
    fix: "Retry once. If persistent, verify the record exists via the corresponding select endpoint before retrying the update.",
    related: ["991", "993"]
  },
  {
    code: "993",
    title: "Error During Deletion",
    category: "Official",
    description: "Server-side database delete failure.",
    causes: ["KRA backend transient failure", "Attempting to delete a record with dependent data"],
    fix: "Retry once. If persistent, escalate to KRA — deletion failures often indicate a constraint on their side.",
    related: ["992", "999"]
  },
  {
    code: "994",
    title: "Duplicate Record",
    category: "Official",
    description: "A unique constraint violation — the record already exists. Documented for the OSCU path. On the VSCU path the same situation returns 899 instead — verified against the live VSDC 2.0.6 sandbox JAR (2026-07-06).",
    causes: [
      "Invoice number already used for this branch",
      "Duplicate transaction submitted after a timeout retry",
      "Offline queue replay submitting an already-processed invoice"
    ],
    fix: "Invoice numbers must be sequential integers and can never be reused even after cancellation. On OSCU retry and offline replay, treat 994 as an idempotent success — the invoice was already registered; do not raise an exception. On the VSCU JAR path, do not wait for a 994 that never comes: a duplicate-invoice replay returns 899 with a generic client-error message — confirm via the select endpoint or receipt-counter arithmetic before treating the replay as failed.",
    gotcha: "If a retry of a timed-out submission returns 994 (OSCU) or 899 (VSCU), the original submission likely succeeded — KRA consumes the receipt counter even when the HTTP response is lost to a client timeout. Check invoice status via the corresponding select endpoint before treating this as a hard failure.",
    related: ["899", "991", "921", "922"]
  },
  {
    code: "995",
    title: "No Downloaded File",
    category: "Official",
    description: "File retrieval failure in document or notice flows.",
    causes: ["Document no longer available on KRA servers", "Invalid document reference"],
    fix: "Verify the document reference is current. Re-fetch the notice list to confirm the document still exists before retrying.",
    related: ["999"]
  },
  {
    code: "999",
    title: "Unknown Error",
    category: "Official",
    description: "KRA catch-all error with no additional information.",
    causes: ["Unclassified KRA backend failure"],
    fix: "Log the full request and response including all headers. Retry once. If persistent, email timsupport@kra.go.ke with the full logged payload and the resultDt timestamp so KRA can trace the server-side event.",
    gotcha: "There is no actionable information in this code. The only path forward is KRA support.",
    related: ["991", "995", "896"]
  },
  {
    code: "E04",
    title: "Device / Branch Not Found",
    category: "Production",
    description: "The device or branch ID in the request does not exist in KRA's system.",
    causes: [
      "bhfId not registered for this TIN",
      "Device registered under a different branch than specified in the request"
    ],
    fix: "Verify your bhfId against what was assigned during OSCU/VSCU registration. The default sandbox bhfId is '00'. Check that tin + bhfId combination matches your registration.",
    gotcha: "This is a letter-prefixed code absent from all official spec documents. Only confirmed through live GavaConnect response observation.",
    related: ["901", "903"]
  },
  {
    code: "E11",
    title: "VSCU Memory Full",
    category: "Production",
    description: "The VSCU JAR has exhausted its local storage capacity.",
    causes: [
      "Large volume of offline transactions accumulated during a connectivity outage",
      "VSCU JAR running for extended period without sync"
    ],
    fix: "Restart the VSCU JAR. If invoices are queued offline, ensure connectivity is restored and sync completes before restarting to avoid data loss.",
    gotcha: "Letter-prefixed code absent from all official spec documents. Only confirmed through live GavaConnect response observation.",
    related: ["894", "990"]
  }
];

// Allow require() in Node (generate-pages.js) without breaking browser <script> usage
if (typeof module !== 'undefined') module.exports = { ERRORS };
