"""
CRM lead data analyser.

Reads CRM leads (Supabase live, or project_context/crm_export.csv fallback, both
via crm_source.py) and produces a structured insight summary for the audience
crew to use as context when building targeting briefs and copy angles.

Output: outputs/crm_insights.md
Graceful: if the file is missing or malformed, returns a no-data string and does not crash.

Column resolution is alias-based, matched via a stripped-to-alphanumeric key (see
_key()) so it's tolerant to header variants across the two sources — e.g. the CSV's
"Call Status" and Supabase's "CallStatus" both resolve to the same canonical column.
This mirrors analytics/crm_analytics.py's approach, since both modules read the
same underlying CRM rows and previously drifted out of sync with each other.

Funnel stages are derived from four real disposition columns — Call Status, Buying
Status, Site Visit Status, Client Status — not a single generic "status" field.
Client Status is the CRM's explicit human disposition (hot/warm/cold/broker/lost/
...); Status (assignment: Assigned/Unassigned/Cold Pool) is sales-routing metadata
and carries no funnel-progress signal, so it is never used for stage derivation.
"""
import csv
import pathlib
import re
from collections import Counter, defaultdict

_CRM_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "project_context" / "crm_export.csv"
_OUT_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "outputs" / "crm_insights.md"

# Column name aliases — map common CRM variants to canonical names. Matched via
# _key() (lowercase, non-alphanumerics stripped) so "Call Status", "CallStatus",
# and "call_status" all resolve the same way regardless of source formatting.
# "lead_source" prefers "campaign" over generic "source": in the real export,
# Source is ~95% the single meaningless value "Migrated" (a data-migration stamp,
# not a channel), while Campaign carries the actual per-campaign breakdown.
_ALIASES = {
    "lead_source": ["campaign", "leadsource", "adsource", "source", "adangle"],
    "call_status": ["callstatus"],
    "buying_status": ["buyingstatus"],
    "site_visit_status": ["sitevisitstatus"],
    "client_status": ["clientstatus"],
    "budget_bracket": ["budgetbracket", "budget", "pricerange", "budgetrange"],
    "property_interest": ["propertyinterest", "propertytype", "interest", "enquirytype"],
    "city": ["city", "location", "buyercity", "currentcity"],
    "buyer_type": ["buyertype", "type", "segment", "nrihni"],
    "phone": ["phone", "mobile", "contact", "phonenumber"],
    "email": ["email", "emailaddress"],
    "profession": ["profession", "job", "occupation", "designation"],
    "company": ["company", "organisation", "employer", "companyname"],
    "received": ["received", "date", "created", "leaddate"],
    "name": ["name", "fullname", "leadname"],
    "assigned_to": ["assignedto", "owner", "salesrep"],
}


def _key(s: str) -> str:
    """Lowercase + strip all non-alphanumerics, for tolerant header matching."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# Real disposition values (Client Status) that end a lead's funnel as dead/lost.
# "Construction Biz Owner" is a profession tag that ended up in this same enum,
# not a disposition judgement — excluded here for the same reason it's excluded
# from analytics/crm_analytics.py's _QUALITY_CLIENT_STATUSES.
_DEAD_CLIENT_STATUSES = {"broker", "not interested", "cold", "lost", "low budget"}
_QUALIFIED_CLIENT_STATUSES = {"hot", "warm", "interested", "active"}
_SITE_VISIT_SCHEDULED = {"yet to visit", "visit date confirmed"}
_ENGAGED_BUYING_STATUSES = {"interested", "still searching", "postponed", "exploring"}
_ENGAGED_CALL_STATUSES = {"spoken", "call back later"}

# Real, observed funnel stages, ranked by how far the lead has progressed.
# Priority on read: client_status (explicit human call) beats site-visit beats
# buying/call signal — a lead can be "Site Visited" per site_visit_status but
# already marked "Cold" in client_status, and the human judgement should win.
_STAGE_RANK = {
    "Dead / Lost": 0,
    "New / Uncalled": 1,
    "Contacted": 2,
    "Exploring": 3,
    "Site Visit Scheduled": 4,
    "Site Visited": 5,
    "Qualified (Warm/Hot)": 6,
}


def _derive_stage(row: dict, col: dict) -> str:
    """Return this lead's funnel stage label from its real disposition columns."""
    client_status = row.get(col.get("client_status", ""), "").strip().lower()
    if client_status in _DEAD_CLIENT_STATUSES:
        return "Dead / Lost"
    if client_status in _QUALIFIED_CLIENT_STATUSES:
        return "Qualified (Warm/Hot)"

    site_visit = row.get(col.get("site_visit_status", ""), "").strip().lower()
    if site_visit == "visited":
        return "Site Visited"
    if site_visit in _SITE_VISIT_SCHEDULED:
        return "Site Visit Scheduled"

    buying_status = row.get(col.get("buying_status", ""), "").strip().lower()
    if buying_status in _ENGAGED_BUYING_STATUSES:
        return "Exploring"

    call_status = row.get(col.get("call_status", ""), "").strip().lower()
    if call_status in _ENGAGED_CALL_STATUSES:
        return "Contacted"

    return "New / Uncalled"

# Junk detection — leads with these values in profession/company are noise
_JUNK_PROFESSIONS = {"a", "b", "h", "o", "q", "yruery", "qwerty", "buf", "yggd", "yuranus"}
_JUNK_EMAILS = {"noreply@mailers.zomato.com", "abcd@gmail.com"}

# NRI signals — phone prefixes or cities that indicate international leads
_NRI_PREFIXES = {"971", "44", "1", "61", "65", "60"}  # UAE, UK, US, AU, SG, MY


def _resolve_columns(header: list[str]) -> dict[str, str]:
    """Map canonical names to actual CSV/Supabase column names, tolerant of spacing."""
    header_keyed = {_key(h): h for h in header}
    resolved = {}
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in header_keyed:
                resolved[canonical] = header_keyed[alias]
                break
    return resolved


def _budget_lower_cr(budget_str: str) -> float | None:
    """
    Return the numeric lower bound in Cr for a budget string (e.g. "4 Cr – 6 Cr"
    -> 4.0, "12 Cr & Above" -> 12.0), or None if the string has no number at all
    (e.g. "Custom"). Real budget strings vary too much in punctuation/wording to
    maintain an exhaustive lookup table — taking the first number is both simpler
    and correct for every real bracket format observed (verified 2026-07-06
    against all distinct values in the live export).
    """
    nums = re.findall(r"\d+(?:\.\d+)?", budget_str.strip().lower())
    return float(nums[0]) if nums else None


def _is_junk(row: dict, col: dict) -> bool:
    """Return True if the row looks like a test/junk entry."""
    email_col = col.get("email")
    prof_col = col.get("profession")
    if email_col and row.get(email_col, "").strip().lower() in _JUNK_EMAILS:
        return True
    if prof_col and row.get(prof_col, "").strip().lower() in _JUNK_PROFESSIONS:
        return True
    return False


def _is_nri(row: dict, col: dict) -> bool:
    """Detect likely NRI lead from phone prefix or city."""
    phone_col = col.get("phone")
    city_col = col.get("city")
    if phone_col:
        phone = re.sub(r"\D", "", row.get(phone_col, ""))
        # International numbers typically have country code prefix (not starting with 91)
        if phone and not phone.startswith("91") and len(phone) > 10:
            prefix = phone[:2] if len(phone) >= 12 else phone[:1]
            if any(phone.startswith(p) for p in _NRI_PREFIXES):
                return True
    if city_col:
        city = row.get(city_col, "").strip().lower()
        nri_cities = {"abu dhabi", "dubai", "london", "singapore", "toronto", "new york",
                      "sydney", "melbourne", "houston", "new jersey", "doha", "riyadh"}
        if city in nri_cities:
            return True
    return False


def analyse(crm_path: pathlib.Path = _CRM_PATH) -> str:
    """
    Parse CRM leads (Supabase if configured, else CSV) and return a markdown
    insights summary string. Also writes to outputs/crm_insights.md.
    Returns a no-data string if no source is available.
    """
    from pikorua_adflow.utils import crm_source

    try:
        rows, source_label = crm_source.fetch_rows(crm_path)
    except Exception as exc:
        return f"CRM data could not be loaded ({exc}) — running without lead history."

    if not rows:
        return "No CRM data available — neither Supabase nor crm_export.csv returned leads."

    col = _resolve_columns(list(rows[0].keys()))
    insights = _build_insights(rows, col)
    md = _format_markdown(insights, total=len(rows), col=col, source_label=source_label)

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(md, encoding="utf-8")

    return md


def _load_csv(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def _has_funnel_data(rows: list[dict], col: dict) -> bool:
    """
    Return True if the CRM data has at least one of the four disposition columns
    (call/buying/site-visit/client status) with a non-blank value — i.e. there is
    real funnel signal to derive stages from, as opposed to an export where sales
    follow-up hasn't started yet.
    """
    signal_cols = [col.get(c) for c in
                   ("call_status", "buying_status", "site_visit_status", "client_status")]
    signal_cols = [c for c in signal_cols if c]
    if not signal_cols:
        return False
    for row in rows[:50]:
        if any(row.get(c, "").strip() for c in signal_cols):
            return True
    return False


def _build_insights(rows: list[dict], col: dict) -> dict:
    source_col = col.get("lead_source")
    budget_col = col.get("budget_bracket")
    city_col = col.get("city")
    prof_col = col.get("profession")

    has_funnel = _has_funnel_data(rows, col)

    # Separate clean vs junk leads
    clean_rows = [r for r in rows if not _is_junk(r, col)]
    junk_count = len(rows) - len(clean_rows)

    # NRI detection
    nri_rows = [r for r in clean_rows if _is_nri(r, col)]
    nri_count = len(nri_rows)

    # Budget distribution (clean leads only)
    budget_counts: Counter = Counter()
    budget_cr_buckets: dict[str, int] = defaultdict(int)

    for row in clean_rows:
        if budget_col:
            bkt = row.get(budget_col, "").strip() or "Unknown"
            budget_counts[bkt] += 1
            lower = _budget_lower_cr(bkt)
            if lower is not None:
                if lower >= 12:
                    bucket = "₹12 Cr+"
                elif lower >= 9:
                    bucket = "₹9–12 Cr"
                elif lower >= 7:
                    bucket = "₹7–9 Cr"
                elif lower >= 5:
                    bucket = "₹5–7 Cr"
                elif lower >= 3:
                    bucket = "₹3–5 Cr"
                else:
                    bucket = "₹2–3 Cr"
                budget_cr_buckets[bucket] += 1

    # Campaign / source split
    source_counts: Counter = Counter()
    for row in clean_rows:
        if source_col:
            src = row.get(source_col, "").strip() or "Unknown"
            source_counts[src] += 1

    # Geography
    city_counts: Counter = Counter()
    for row in clean_rows:
        if city_col:
            c = row.get(city_col, "").strip()
            if c:
                city_counts[c.title()] += 1

    # Profession clusters
    prof_counts: Counter = Counter()
    for row in clean_rows:
        if prof_col:
            p = row.get(prof_col, "").strip()
            if p and p.lower() not in _JUNK_PROFESSIONS:
                prof_counts[p.title()] += 1

    # Stage data (if available) — derived per-row from call/buying/site-visit/
    # client status, not a single generic column (see _derive_stage).
    stage_breakdown = []
    best_sources = []
    best_budget = []
    if has_funnel:
        stage_counts: Counter = Counter()
        source_to_stages: dict[str, list[int]] = defaultdict(list)
        budget_to_stages: dict[str, list[int]] = defaultdict(list)

        for row in clean_rows:
            stage = _derive_stage(row, col)
            rank = _STAGE_RANK[stage]
            stage_counts[stage] += 1

            if source_col:
                src = row.get(source_col, "").strip() or "Unknown"
                source_to_stages[src].append(rank)
            if budget_col:
                bkt = row.get(budget_col, "").strip() or "Unknown"
                budget_to_stages[bkt].append(rank)

        stage_breakdown = sorted(stage_counts.items(), key=lambda x: _STAGE_RANK[x[0]], reverse=True)
        source_avg = {s: sum(r) / len(r) for s, r in source_to_stages.items() if r}
        best_sources = sorted(source_avg.items(), key=lambda x: x[1], reverse=True)[:3]
        budget_avg = {b: sum(r) / len(r) for b, r in budget_to_stages.items() if r}
        best_budget = sorted(budget_avg.items(), key=lambda x: x[1], reverse=True)[:3]

    # High-value lead count (₹9 Cr+)
    high_value = sum(v for k, v in budget_cr_buckets.items() if k in ("₹9–12 Cr", "₹12 Cr+"))

    return {
        "total": len(rows),
        "clean": len(clean_rows),
        "junk_count": junk_count,
        "nri_count": nri_count,
        "has_funnel": has_funnel,
        "stage_breakdown": stage_breakdown,
        "best_sources": best_sources,
        "best_budget": best_budget,
        "budget_counts": budget_counts,
        "budget_cr_buckets": budget_cr_buckets,
        "source_counts": source_counts,
        "top_cities": city_counts.most_common(6),
        "top_professions": prof_counts.most_common(8),
        "buyer_type_split": Counter(),  # not in real export
        "high_value_leads": high_value,
    }


def _format_markdown(insights: dict, total: int, col: dict, source_label: str = "CRM export") -> str:
    clean = insights["clean"]
    junk = insights["junk_count"]
    nri = insights["nri_count"]
    high_value = insights["high_value_leads"]

    lines = [
        "# CRM Lead Intelligence",
        f"*Based on {total} total leads from {source_label}*",
        f"*Clean leads (excl. junk/test entries): {clean} | Junk filtered: {junk}*",
        "",
    ]

    # Budget distribution
    if insights["budget_cr_buckets"]:
        lines += ["## Budget Distribution (clean leads)", ""]
        ordered_buckets = ["₹2–3 Cr", "₹3–5 Cr", "₹5–7 Cr", "₹7–9 Cr", "₹9–12 Cr", "₹12 Cr+"]
        for bucket in ordered_buckets:
            count = insights["budget_cr_buckets"].get(bucket, 0)
            if count:
                bar = "█" * min(count, 30)
                pikorua_flag = " ← Pikorua target segment" if bucket in ("₹9–12 Cr", "₹12 Cr+") else ""
                lines.append(f"- **{bucket}**: {count} leads  {bar}{pikorua_flag}")
        lines.append(f"\n**High-value leads (₹9 Cr+): {high_value}** — these are the primary targeting segment.")

    # Campaign split
    if insights["source_counts"]:
        lines += ["", "## Campaign Source Split", ""]
        for src, count in insights["source_counts"].most_common():
            pct = round(count / clean * 100) if clean else 0
            lines.append(f"- **{src}**: {count} leads ({pct}%)")

    # Geography
    if insights["top_cities"]:
        lines += ["", "## Geographic Distribution (top cities)", ""]
        for city, count in insights["top_cities"]:
            pct = round(count / clean * 100) if clean else 0
            lines.append(f"- {city}: {count} leads ({pct}%)")
        lines.append(f"\n**NRI / international leads detected: {nri}** (Abu Dhabi, London, and other overseas cities/prefixes)")

    # Profession clusters
    if insights["top_professions"]:
        lines += ["", "## Buyer Profession Clusters (top 8)", ""]
        for prof, count in insights["top_professions"]:
            lines.append(f"- {prof}: {count}")

    # Funnel data (if available)
    if insights["has_funnel"] and insights["stage_breakdown"]:
        lines += ["", "## Funnel Breakdown", ""]
        for stage, count in insights["stage_breakdown"]:
            pct = round(count / clean * 100) if clean else 0
            lines.append(f"- **{stage}**: {count} leads ({pct}%)")

        if insights["best_sources"]:
            lines += ["", "## Best-Performing Lead Sources", ""]
            for src, avg in insights["best_sources"]:
                lines.append(f"- **{src}** — avg funnel depth {avg:.1f}/6")

        if insights["best_budget"]:
            lines += ["", "## Budget Brackets That Progress Furthest", ""]
            for bkt, avg in insights["best_budget"]:
                lines.append(f"- **{bkt}** — avg funnel depth {avg:.1f}/6")
    else:
        lines += [
            "",
            "## Funnel Status",
            "",
            "No call/buying/site-visit/client status data in this export — "
            "follow-up tracking has not started on these leads yet.",
        ]

    # Actionable summary
    lines += ["", "## Actionable Signals for Campaign Team", ""]

    if high_value:
        lines.append(
            f"1. **Primary target segment**: {high_value} leads at ₹9 Cr+ — these are Pikorua's ideal buyers. "
            "Build lookalike audiences from this segment first."
        )

    if nri:
        lines.append(
            f"2. **NRI pipeline is thin**: only {nri} confirmed international leads out of {clean}. "
            "NRI-targeted creatives are an underserved opportunity — consider dedicated NRI ad sets."
        )

    # Dominant profession signal
    top_profs = insights["top_professions"]
    if top_profs:
        top_prof_str = ", ".join(p for p, _ in top_profs[:3])
        lines.append(
            f"3. **Buyer profession signal**: top professions are {top_prof_str}. "
            "Use profession-resonant copy angles (legacy, discretion, investment credibility)."
        )

    # Budget split signal
    low_budget = insights["budget_cr_buckets"].get("₹2–3 Cr", 0)
    if low_budget and clean:
        low_pct = round(low_budget / clean * 100)
        if low_pct > 25:
            lines.append(
                f"4. **Budget mismatch warning**: {low_pct}% of leads are in the ₹2–3 Cr bracket — "
                "below Pikorua's typical property range. Consider tightening targeting or adding a budget qualifier to the lead form."
            )

    if junk:
        lines.append(
            f"5. **Data quality**: {junk} junk/test entries detected and excluded from analysis. "
            "Clean CRM regularly before uploading lookalike audiences to Meta."
        )

    return "\n".join(lines) + "\n"
