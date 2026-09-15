# -*- coding: utf-8 -*-
"""Build 2 hero header+service-line claims (one Professional, one Institutional),
each with MULTIPLE lines pended for different edits. Each pended line carries the
full edit metadata so the REAL resolver (resolve_claim) can adjudicate it line-by-line."""
import json

ec = json.load(open("data/edit_codes.json", encoding="utf-8"))
members = list(json.load(open("data/eligibility.json", encoding="utf-8")).values())

def pend(edit_code):
    m = ec[edit_code]
    return {"edit_code": edit_code, "edit_category": m["category"], "edit_description": m["desc"],
            "carc_code": m["carc"], "rarc_code": m["rarc"], "resolution_path": m["resolution"]}

def line(no, desc, units, charge, allowed, icd, cpt=None, rev=None, edit=None, hr=False):
    ln = {"line_no": no, "rev_code": rev, "cpt_code": cpt, "description": desc,
          "modifier": None, "units": units, "charge": float(charge),
          "allowed": (None if allowed is None else float(allowed)),
          "icd10_principal": icd, "human_review_flag": hr,
          "pended": edit is not None}
    if edit:
        ln.update(pend(edit))
    return ln

mP = members[3]; mI = members[7]

professional = {
    "icn": "ICN-2026-LINE-P01", "claim_type": "Professional", "form": "CMS-1500 / 837P",
    "label": "Professional — 3 lines pended (Auth · Units · LCD)",
    "scenario_note": "Orthopedic surgical day: 5 service lines, 3 pend for different edits. Agent resolves each line, then rolls up to a single header disposition.",
    "member_id": mP["member_id"], "member_name": mP["name"], "member_dob": mP["dob"], "plan": mP["plan"],
    "provider_name": "Dr. Alan Ross", "provider_specialty": "Orthopedic Surgery",
    "npi_billing": "1131647525", "npi_rendering": "1401640052", "group_name": "Advanced Specialty Care",
    "dos": "2026-03-14", "received_date": "2026-03-18", "pend_date": "2026-04-01",
    "days_in_queue": 12, "priority": "high", "place_of_service": "22",
    "lines": [
        line(1, "Total knee arthroplasty", 1, 3800, 1580, "M17.11", cpt="27447", edit="E-AUTH-001"),
        line(2, "Anesthesia — knee arthroplasty", 6, 900, 105, "M17.11", cpt="01402", edit="E-PRICE-002"),
        line(3, "Office visit, established (pre-op)", 1, 180, 118, "M17.11", cpt="99213"),
        line(4, "Arthrocentesis, major joint", 1, 250, 95, "M25.561", cpt="20610", edit="E-CODE-003"),
        line(5, "MRI lower extremity w/o contrast", 1, 600, 410, "M17.11", cpt="73721"),
    ],
}

institutional = {
    "icn": "ICN-2026-LINE-I01", "claim_type": "Institutional", "form": "UB-04 / 837I",
    "label": "Institutional — 3 lines pended (Med-Nec · Auth · Duplicate)",
    "scenario_note": "Inpatient stay billed by revenue code: 6 lines, 3 pend for different edits. Agent adjudicates each revenue line, then rolls up to the claim.",
    "member_id": mI["member_id"], "member_name": mI["name"], "member_dob": mI["dob"], "plan": mI["plan"],
    "provider_name": "Metro General Hospital", "provider_specialty": "Acute Care Hospital",
    "npi_billing": "1902847733", "npi_rendering": "1902847733", "group_name": "Metro Health System",
    "dos": "2026-02-27", "received_date": "2026-03-05", "pend_date": "2026-03-16",
    "days_in_queue": 21, "priority": "urgent", "type_of_bill": "0111", "place_of_service": "21",
    "lines": [
        line(1, "Room & Board — Semi-private (3 days)", 3, 6000, 1650, "J18.9", rev="0120", cpt="99231", edit="E-MN-002", hr=True),
        line(2, "Pharmacy", 1, 1200, 840, "J18.9", rev="0250"),
        line(3, "Laboratory — Clinical diagnostic", 1, 800, 520, "J18.9", rev="0300"),
        line(4, "Operating Room services", 1, 4500, 2100, "J18.9", rev="0360", cpt="32551", edit="E-AUTH-003"),
        line(5, "Emergency Room", 1, 1500, 0, "J18.9", rev="0450", cpt="99284", edit="E-DUP-002"),
        line(6, "Radiology — Diagnostic", 1, 700, 430, "J18.9", rev="0320", cpt="71046"),
    ],
}

for c in (professional, institutional):
    c["billed_amount"] = round(sum(l["charge"] for l in c["lines"]), 2)
    c["pended_lines"] = sum(1 for l in c["lines"] if l["pended"])
    c["edit_codes"] = [l["edit_code"] for l in c["lines"] if l["pended"]]

json.dump([professional, institutional], open("data/line_item_claims.json", "w", encoding="utf-8"), indent=2)
print("wrote data/line_item_claims.json")
for c in (professional, institutional):
    print(f"  {c['icn']} {c['claim_type']:14} lines={len(c['lines'])} pended={c['pended_lines']} billed=${c['billed_amount']:.2f} edits={c['edit_codes']}")
