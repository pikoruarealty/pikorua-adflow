"""Quick autopilot output simulation — shows what the UI will render with live campaigns."""
import sys, os
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")
os.environ["DRY_RUN"] = "true"

from pikorua_adflow.api.services import autopilot as ap

CAMPAIGNS = [
    dict(name="Bungalow ahmd general", spend=10500, leads=20, cpl=525, cpl30=323, freq=3.88, rising=True, auto_fixes=[
        "Turned on Meta audience expansion (Advantage+) — buyers seen ad 3.9x, expanding to lookalike pool",
    ], decisions=[
        ("NRI layer (UAE/UK/US/SG)", "~200k fresh Gujarati NRI audience — untouched by current targeting", "APPROVE"),
    ]),
    dict(name="Nehrunagar (NN)", spend=15750, leads=22, cpl=716, cpl30=536, freq=3.99, rising=True, auto_fixes=[
        "Turned on Meta audience expansion (Advantage+) — same buyers seen ad 4x",
    ], decisions=[
        ("Fresh creative needed", "Ad is 21+ days old at 4x frequency. Cost rising: Rs.536 -> Rs.716. New visual resets attention.", "fresh_creative"),
    ]),
    dict(name="LAARGE Apartments", spend=6300, leads=6, cpl=1050, cpl30=400, freq=3.22, rising=True, auto_fixes=[
        "Added CRM lookalike audience — Meta will target people who look like your actual buyers",
    ], decisions=[]),
    dict(name="GODREJ VASTRAPUR", spend=10500, leads=7, cpl=1500, cpl30=1073, freq=2.37, rising=True, auto_fixes=[
        "Stopped spending on Mumbai and Gurgaon buyers — wrong city for an Ahmedabad property",
        "Excluded 9 disqualified CRM leads (not-interested / wrong profile)",
    ], decisions=[]),
]

total_spend = sum(c["spend"] for c in CAMPAIGNS)
total_leads = sum(c["leads"] for c in CAMPAIGNS)
blended_cpl = round(total_spend / total_leads)
all_auto = [(c["name"], f) for c in CAMPAIGNS for f in c["auto_fixes"]]
all_decisions = [(c["name"], d) for c in CAMPAIGNS for d in c["decisions"]][:2]

SEP = "=" * 65
SEP2 = "-" * 65

print(SEP)
print("AUTOPILOT  —  What opens when you click the tab")
print(SEP)
print()
print("ZONE 1  THE ONE NUMBER")
print(SEP2)
print(f"  Cost per lead:  Rs.{blended_cpl:,}")
print(f"  4 active campaigns  |  Rs.{total_spend:,} spent last 7 days  |  {total_leads} leads")
print(f"  Quality scoring builds as you review leads in the CRM tab")
print()

print("ZONE 2  WHAT I DID FOR YOU")
print(SEP2)
if all_auto:
    for name, fix in all_auto:
        print(f"  [checkmark] {name}")
        print(f"    {fix}")
        print(f"    [Undo]")
        print()
else:
    print("  Nothing needed fixing automatically.")
    print()

print("ZONE 3  NEEDS YOUR CALL  (max 2)")
print(SEP2)
if all_decisions:
    for name, (title, detail, action) in all_decisions:
        print(f"  Campaign: {name}")
        print(f"  {title}")
        print(f"  {detail}")
        if action == "fresh_creative":
            print(f"  [Open Ad Flow]   [Not now]")
        else:
            print(f"  [Do it]   [Not now]")
        print()
else:
    print("  No decisions waiting. Autopilot has it handled.")
    print()

print("COLLAPSED  See full numbers")
print(SEP2)
print(f"  {'Campaign':<28} {'Spend':>10} {'Leads':>6} {'CPL':>10} {'Freq':>6}  Status")
for c in CAMPAIGNS:
    status = "COST RISING" if c["rising"] else "stable"
    print(f"  {c['name']:<28} Rs.{c['spend']:>7,} {c['leads']:>6}  Rs.{c['cpl']:>6,} {c['freq']:>5.2f}x  {status}")

print()
print(SEP)
print("GAPS / AREAS TO IMPROVE (for discussion)")
print(SEP2)
print("  1. GODREJ: both auto-fixes applied (geo + exclusion). Next: rung 3")
print("     (CRM lookalike) should also auto-fire if registry has lookalike ID")
print()
print("  2. NN 'fresh creative' deep-link goes to /portal (blank form).")
print("     Could pre-populate the brief so user doesn't re-enter property details")
print()
print("  3. All 4 profile-match scores are very low (8-42%) because few CRM leads")
print("     are quality-tagged. Quality metric upgrades automatically as leads are")
print("     reviewed in the CRM / Lead Insights tab")
print()
print("  4. Rung 9 (budget reduce) not firing yet. Rs.1,050 CPL on LAARGE is 12x")
print("     benchmark but the ladder tries rung 3 first. After CRM lookalike runs")
print("     5 days with no improvement, rung 9 will surface")
print()
print("  5. 2 pre-existing test failures in test_image_pipeline.py")
print("     (test expects 5 variants, config has 7 — needs test update)")
