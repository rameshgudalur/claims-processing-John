# -*- coding: utf-8 -*-
"""Append the client-taxonomy gap categories (Manual Pricing/Burgess, OON, Enrollment, PCP,
Workers Comp, Medigap, Adjustment) as new edit codes + pend claims. Idempotent: re-running
removes the previously-appended extras first (matched by the ICN-2026-2xxx range)."""
import json

EDITS = {
    "E-PRICE-006": {"category": "Manual Pricing", "desc": "Manual pricing required (Burgess/Multiplan/Zelis)",
                    "carc": "CO-45", "rarc": "N574", "resolution": "manual_price_via_engine"},
    "E-OON-001":   {"category": "OON", "desc": "Out-of-network — repricing required",
                    "carc": "CO-242", "rarc": "N130", "resolution": "oon_reprice"},
    "E-ENR-001":   {"category": "Enrollment", "desc": "Enrollment (old-born) — patient detail missing/mismatch",
                    "carc": "CO-140", "rarc": "N382", "resolution": "enrollment_correct_details"},
    "E-ENR-002":   {"category": "Enrollment", "desc": "Enrollment (newborn) — add / father on policy",
                    "carc": "CO-140", "rarc": "N382", "resolution": "enrollment_newborn"},
    "E-PCP-001":   {"category": "PCP", "desc": "PCP not mapped / line to delete",
                    "carc": "CO-16", "rarc": "N286", "resolution": "pcp_remap"},
    "E-WC-001":    {"category": "Workers Comp", "desc": "Work-related injury — redirect to WC carrier",
                    "carc": "CO-19", "rarc": "N418", "resolution": "wc_redirect"},
    "E-MG-001":    {"category": "Medigap", "desc": "Medigap secondary crossover",
                    "carc": "CO-22", "rarc": "N598", "resolution": "medigap_crossover"},
    "E-ADJ-001":   {"category": "Adjustment", "desc": "Adjustment / POS-DA reprocess (HPI, claimstop, flush)",
                    "carc": "CO-16", "rarc": "N286", "resolution": "adjustment_reprocess"},
}

# (edit_code, count, cpt, cpt_desc, billed, allowed(None=engine prices), icd, icd_desc, hr_flag)
SPECS = [
    ("E-PRICE-006", 3, "27447", "Total knee arthroplasty", 42800.0, None, "M17.11", "Osteoarthritis, right knee", False),
    ("E-OON-001",   2, "70553", "MRI brain w/ & w/o contrast", 3200.0, None, "R51.9", "Headache", False),
    ("E-ENR-001",   2, "99213", "Office visit, established", 180.0, 120.0, "I10", "Essential hypertension", False),
    ("E-ENR-002",   2, "99460", "Newborn care, initial", 340.0, 300.0, "Z38.00", "Single liveborn, vaginal", False),
    ("E-PCP-001",   2, "99214", "Office visit, moderate", 260.0, 175.0, "E11.9", "Type 2 diabetes", False),
    ("E-WC-001",    2, "99283", "ED visit, expanded", 520.0, 0.0, "S61.401A", "Open wound, hand", False),
    ("E-MG-001",    2, "99213", "Office visit, established", 180.0, 42.0, "N18.3", "CKD stage 3", False),
    ("E-ADJ-001",   2, "99214", "Office visit, moderate", 260.0, 175.0, "J45.909", "Asthma, uncomplicated", True),
]

members = list(json.load(open("data/eligibility.json", encoding="utf-8")).values())
PROVIDERS = [("Dr. Alan Ross", "Orthopedics", "1401640052", "Advanced Specialty Care"),
             ("Dr. Nina Patel", "Radiology", "1558493021", "Metro Imaging"),
             ("Dr. Omar Reyes", "Family Medicine", "1730285566", "Riverside Primary Care"),
             ("Dr. Lucy Kim", "Emergency Medicine", "1902847733", "Harbor ED Group")]

# edit_codes.json
ec = json.load(open("data/edit_codes.json", encoding="utf-8"))
ec.update(EDITS)
json.dump(ec, open("data/edit_codes.json", "w", encoding="utf-8"), indent=2)

# claims_pend.json — drop prior extras, append fresh
claims = json.load(open("data/claims_pend.json", encoding="utf-8"))
claims = [c for c in claims if not str(c.get("icn", "")).startswith("ICN-2026-2")]
seq = 2001
mi = 0
for edit_code, count, cpt, cpt_desc, billed, allowed, icd, icd_desc, hr in SPECS:
    meta = EDITS[edit_code]
    for _ in range(count):
        m = members[mi % len(members)]; mi += 1
        prov = PROVIDERS[seq % len(PROVIDERS)]
        claims.append({
            "icn": f"ICN-2026-{seq}", "claim_type": "Professional", "member_id": m["member_id"],
            "member_name": m["name"], "member_dob": m["dob"], "plan": m["plan"],
            "npi_billing": "1131647525", "npi_rendering": prov[2], "provider_name": prov[0],
            "provider_specialty": prov[1], "group_name": prov[3],
            "dos": "2026-03-14", "received_date": "2026-03-18", "pend_date": "2026-04-01",
            "days_in_queue": 10 + (seq % 20), "priority": "high" if hr else "routine",
            "cpt_code": cpt, "cpt_description": cpt_desc, "modifier": None,
            "icd10_principal": icd, "icd10_secondary": None, "icd10_desc": icd_desc,
            "place_of_service": "22", "units_billed": 1, "billed_amount": billed, "allowed_amount": allowed,
            "auth_number": None, "edit_code": edit_code, "edit_category": meta["category"],
            "edit_description": meta["desc"], "carc_code": meta["carc"], "rarc_code": meta["rarc"],
            "resolution_path": meta["resolution"], "status": "pending", "resolution": None,
            "payment_amount": None, "denial_code": None, "resolution_step": None, "resolved_by": None,
            "processing_ms": None, "human_review_flag": hr,
            "human_review_reason": "Adjustment requires examiner posting" if hr else None,
            "is_featured": False,
        })
        seq += 1

json.dump(claims, open("data/claims_pend.json", "w", encoding="utf-8"), indent=2)
print(f"edit_codes now {len(ec)}; claims_pend now {len(claims)} (+{seq-2001} extras across {len(SPECS)} categories)")
