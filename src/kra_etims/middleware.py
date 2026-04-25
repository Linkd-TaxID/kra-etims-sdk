"""
KRA eTIMS SDK — Middleware layer (intentionally minimal)

The KRA GavaConnect trailing-space URL bug (silent failures on URLs with trailing
whitespace) is handled at the TIaaS middleware tier via TrailingSpaceInterceptor
(AppConfig.java). The SDK communicates with the TIaaS middleware, not with KRA
GavaConnect directly — there is no trailing-space concern at the SDK tier.

The previous ``sanitize_kra_url`` decorator that stripped whitespace from all
string arguments was removed because:
  1. Wrong tier — TIaaS already handles the KRA quirk server-side.
  2. Over-broad — stripping all string arguments mutated business data fields
     (``buyer_name``, ``item_description``) as a side-effect of a URL fix.
  3. Dead code — ``gateway.py`` never imported it.

If a future SDK method needs to call KRA GavaConnect directly (bypassing TIaaS),
re-introduce a narrowly scoped URL-segment sanitiser at that specific call site,
not as a blanket decorator over business data parameters.
"""
