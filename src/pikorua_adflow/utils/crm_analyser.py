"""
CRM lead data analyser.

Reads project_context/crm_export.csv and produces a structured insight summary
for the audience crew to use as context when building targeting briefs and copy angles.

Output: outputs/crm_insights.md
Graceful: if the file is missing or malformed, returns a no-data string and does not crash.

Handles both:
- Legacy format with funnel-stage data (lead_stage, source, budget_bracket columns)
- Pikorua CRM export format (Name, Phone, Email, City, Campaign, Source, Status,
  Assigned To, Budget, Profession, Company, Received)
"""
import csv
import pathlib
import re
from collections import Counter, defaultdict

_CRM_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "project_context" / "crm_export.csv"
_OUT_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "outputs" / "crm_insights.md"

# Stage ordering — higher = further along the funnel
_STAGE_ORDER = {
    "contacted": 1,
    "follow_up": 2,
    "site_visit": 3,
    "negotiating": 4,
    "converted": 5,
    "dead": 0,
    "lost": 0,
}

# Column name aliases — map common CRM variants to canonical names
_ALIASES = {
    "lead_source": ["lead_source", "source", "ad_source", "campaign", "ad_angle"],
    "lead_stage": ["lead_stage", "stage", "status", "funnel_stage", "crm_stage"],
    "budget_bracket": ["budget_bracket", "budget", "price_range", "budget_range"],
    "property_interest": ["property_interest", "property_type", "interest", "enquiry_type"],
    "city": ["city", "location", "buyer_city", "current city"],
    "buyer_type": ["buyer_type", "type", "segment", "nri_hni"],
    "phone": ["phone", "mobile", "contact", "phone_number"],
    "email": ["email", "email_address"],
    "profession": ["profession", "job", "occupation", "designation"],
    "company": ["company", "organisation", "employer", "company name"],
    "received": ["received", "date", "created", "lead_date"],
    "name": ["name", "full_name", "lead_name"],
    "assigned_to": ["assigned to", "assigned_to", "owner", "sales_rep"],
}

# Budget strings from real Pikorua CRM export → numeric lower bound (Cr)
_BUDGET_TO_LOWER_CR = {
    "2 cr – 3 cr": 2.0,
    "3 cr – 4 cr": 3.0,
    "4 cr – 5 cr": 4.0,
    "5 cr – inr 6 cr+": 5.0,
    "5 cr – 6 cr": 5.0,
    "6 cr – 8 cr": 6.0,
    "7 cr – inr 8 cr+": 7.0,
    "7 cr – 8 cr": 7.0,
    "8 cr – inr 9 cr+": 8.0,
    "9 cr – inr 10 cr+": 9.0,
    "9 cr – 10 cr": 9.0,
    "10 cr – inr 11 cr+": 10.0,
    "11 cr – inr 12 cr+": 11.0,
    "12 cr & above": 12.0,
    "12 cr+": 12.0,
    "15 cr+": 15.0,
    "15 cr & above": 15.0,
    "inr 4 cr to 6 cr": 4.0,
    "inr 7 cr to 10 cr": 7.0,
}

# Junk detection — leads with these values in profession/company are noise
_JUNK_PROFESSIONS = {"a", "b", "h", "o", "q", "yruery", "qwerty", "buf", "yggd", "yuranus"}
_JUNK_EMAILS = {"noreply@mailers.zomato.com", "abcd@gmail.com"}

# NRI signals — phone prefixes or cities that indicate international leads
_NRI_PREFIXES = {"971", "44", "1", "61", "65", "60"}  # UAE, UK, US, AU, SG, MY


def _resolve_columns(header: list[str]) -> dict[str, str]:
    """Map canonical names to actual CSV column names."""
    header_lower = {h.strip().lower(): h for h in header}
    resolved = {}
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in header_lower:
                resolved[canonical] = header_lower[alias]
                break
    return resolved


def _budget_lower_cr(budget_str: str) -> float | None:
    """Return numeric lower bound in Cr for a budget string, or None if unrecognised."""
    key = budget_str.strip().lower()
    if key in _BUDGET_TO_LOWER_CR:
        return _BUDGET_TO_LOWER_CR[key]
    # fallback: extract first number
    nums = re.findall(r"\d+(?:\.\d+)?", key)
    if nums:
        return float(nums[0])
    return None


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


def _stage_rank(stage_val: str) -> int:
    return _STAGE_ORDER.get(stage_val.strip().lower().replace(" ", "_"), 0)


def _has_funnel_data(rows: list[dict], col: dict) -> bool:
    """Return True if the CRM data contains meaningful funnel stage values."""
    if "lead_stage" not in col:
        return False
    stage_col = col["lead_stage"]
    meaningful = {s for s in _STAGE_ORDER if s not in ("dead", "lost")}
    for row in rows[:20]:
        val = row.get(stage_col, "").strip().lower().replace(" ", "_")
        if val in meaningful:
            return True
    return False


def _build_insights(rows: list[dict], col: dict) -> dict:
    source_col = col.get("lead_source")
    budget_col = col.get("budget_bracket")
    city_col = col.get("city")
    buyer_col = col.get("buyer_type")
    prof_col = col.get("profession")
    stage_col = col.get("lead_stage")

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

    # Stage data (if available)
    stage_breakdown = []
    best_sources = []
    best_budget = []
    if has_funnel:
        stage_counts: Counter = Counter()
        source_to_stages: dict[str, list[int]] = defaultdict(list)
        budget_to_stages: dict[str, list[int]] = defaultdict(list)

        for row in clean_rows:
            stage = row.get(stage_col, "").strip()
            rank = _stage_rank(stage)
            stage_counts[stage] += 1

            if source_col:
                src = row.get(source_col, "").strip() or "Unknown"
                source_to_stages[src].append(rank)
            if budget_col:
                bkt = row.get(budget_col, "").strip() or "Unknown"
                budget_to_stages[bkt].append(rank)

        stage_breakdown = sorted(stage_counts.items(), key=lambda x: _stage_rank(x[0]), reverse=True)
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
                lines.append(f"- **{src}** — avg funnel depth {avg:.1f}/5")

        if insights["best_budget"]:
            lines += ["", "## Budget Brackets That Progress Furthest", ""]
            for bkt, avg in insights["best_budget"]:
                lines.append(f"- **{bkt}** — avg funnel depth {avg:.1f}/5")
    else:
        lines += [
            "",
            "## Funnel Status",
            "",
            "No funnel-stage data in this export — all leads show as Unassigned/Migrated.",
            "Follow-up tracking not yet active in the CRM.",
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
