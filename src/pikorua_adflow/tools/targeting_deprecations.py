"""
Registry of Meta targeting parameters that are WRITE-DEPRECATED.

Meta periodically retires targeting fields: they still appear in GET responses and on
legacy ad sets, and may even pass /targetingvalidation, but ANY ad-set create/update
payload that contains them fails (e.g. `user_adclusters` → subcode 1487122 "invalid
broad categories", the incident that silently broke publishing in session 63).

Previously the strip for `user_adclusters` was hardcoded inside
`meta_tool._sanitize_targeting_for_write`. Keeping the list here as data means the next
deprecation Meta ships is a one-line edit, not a code change — and each entry carries a
human-readable reason so the portal can tell the user exactly what was removed and why.

Each entry:
  field          — the key to strip
  scope          — "flexible_spec" (strip from every flexible_spec group) for now
  reason         — plain-English explanation shown to the user
  substitute      — optional {id, name} to inject in its place (keeps the targeting intent)
  substitute_into — which group key the substitute goes into (e.g. "behaviors")
"""

from __future__ import annotations

from . import meta_targeting as _mt

WRITE_DEPRECATED: list[dict] = [
    {
        "field": "user_adclusters",
        "scope": "flexible_spec",
        "reason": (
            "Meta retired Broad Category Clusters (e.g. \"Household income (India): Top 10%\") "
            "for writing — any ad-set create/update that includes it fails with "
            "\"invalid broad categories\" (subcode 1487122)."
        ),
        "substitute": _mt.AFFLUENCE_PROXY_BEHAVIOUR,
        "substitute_into": "behaviors",
    },
]


def strip_from_flexible_group(group: dict, report: list[dict] | None = None) -> dict:
    """Remove every write-deprecated field from one flexible_spec group, injecting the
    configured substitute. Appends {field, reason, substitute} to `report` for anything
    actually removed. Returns the cleaned group (may be empty)."""
    g = dict(group)
    for entry in WRITE_DEPRECATED:
        if entry.get("scope") != "flexible_spec":
            continue
        field = entry["field"]
        if field in g:
            g.pop(field, None)
            sub = entry.get("substitute")
            into = entry.get("substitute_into")
            if sub and into:
                bucket = list(g.get(into) or [])
                if all(str(b.get("id")) != str(sub["id"]) for b in bucket):
                    bucket.append(dict(sub))
                g[into] = bucket
            if report is not None:
                report.append({
                    "field": field,
                    "reason": entry["reason"],
                    "substitute": (entry.get("substitute") or {}).get("name", ""),
                })
    return g
