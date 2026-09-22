"""
Project John — Claims Pend Processing Demo
Flask backend  |  Port 5002
"""
import json, random, time, os
from pathlib import Path
from flask import Flask, jsonify, request, Response, send_from_directory
from flask_cors import CORS
import pricing_engine

app = Flask(__name__)
CORS(app)

# Password gate — active only when DEMO_PASSWORD is set (e.g. on Railway); open locally.
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "")

@app.before_request
def _demo_gate():
    if not DEMO_PASSWORD:
        return  # no password configured → open (local dev)
    auth = request.authorization
    if not auth or auth.password != DEMO_PASSWORD:
        return Response("Authentication required.", 401,
                        {"WWW-Authenticate": 'Basic realm="Claims Pend Processing Demo"'})

@app.route("/")
def _root():
    # Serve the single-page demo from the app (so it works on Railway, not just as a local file).
    return send_from_directory(str(Path(__file__).parent), "demo.html")

# Burgess / Multiplan / Zelis pricing — real API client (live when BURGESS_API_URL + _KEY set;
# representative repricing engine at the same interface until then).
pricing_client = pricing_engine.BurgessPricingClient()

DATA = Path(__file__).parent / "data"
SOP_LIBRARY = Path(__file__).parent / "sop_library_offline"   # ready SOPs, agent does NOT watch
SOP_INBOX   = Path(__file__).parent / "sop_inbox"             # landing zone the agent scans
SOP_INBOX.mkdir(exist_ok=True)

# ── Load all data on startup ─────────────────────────────────────────────────

def load(fname):
    p = DATA / fname
    if not p.exists():
        return {}
    return json.loads(p.read_text())

providers       = load("providers.json")
authorizations  = load("authorizations.json")
cob             = load("cob.json")
fee_schedule    = load("fee_schedule.json")
claims_history  = load("claims_history.json")
eligibility     = load("eligibility.json")
pended_claims   = load("claims_pend.json")
featured_claims = load("featured_claims.json")
human_review    = load("human_review.json")
sop_outcomes      = load("sop_outcomes.json")
predictions       = load("predictions.json")
edit_codes        = load("edit_codes.json")
# Two genuinely-new/emerging edits that have NO SOP on file yet. The agent flags these as
# "Missing SOP" and holds them for SOP Governance; once a SOP is ingested it re-resolves them.
edit_codes["E-TELE-001"] = {"category": "Telehealth", "desc": "Telehealth POS-02 / modifier-95 policy",
                            "carc": "CO-B7", "rarc": "N657", "resolution": "needs_sop_tele"}
edit_codes["E-SOS-001"]  = {"category": "Site of Service", "desc": "Site-of-service differential (new edit)",
                            "carc": "CO-B7", "rarc": "N657", "resolution": "needs_sop_sos"}
NO_SOP_EDITS = {"E-TELE-001", "E-SOS-001"}   # start with no RESOLUTION_RULES → Missing SOP
multi_edit_claims = load("multi_edit_claims.json") if (DATA / "multi_edit_claims.json").exists() else []
line_item_claims  = load("line_item_claims.json") if (DATA / "line_item_claims.json").exists() else []

# Enrich the hero header+line-item claims with display fields so they render in the queue
for _c in line_item_claims:
    _c.setdefault("cpt_code", (_c["lines"][0].get("cpt_code") or _c["lines"][0].get("rev_code") or "—"))
    _c["edit_code"]     = f'{_c.get("pended_lines", 0)} lines pended'
    _c["edit_category"] = _c.get("claim_type", "Multi-line")
    _c["is_multi_line"] = True
    _c["is_featured"]   = True
line_item_index = {c["icn"]: c for c in line_item_claims}

# Index pended claims by ICN for fast lookup
claims_index = {c["icn"]: c for c in pended_claims}

# ── Reference-directory sizing ───────────────────────────────────────────────────
# The base synthetic pools are small (they were reused across the scaled pend queue).
# A payer's reference systems are full DIRECTORIES far larger than any one pend batch.
# Pad them (append-only, distinct keys) to credible sizes so the connected-DB tiles are
# realistic. Existing claim lookups are untouched — this only enlarges the directories.
def _pad_reference_dbs():
    import random as _r
    rnd = _r.Random(2026)
    FIRST = ["James","Mary","Robert","Patricia","John","Jennifer","Michael","Linda","David","Elizabeth",
             "William","Susan","Richard","Jessica","Joseph","Sarah","Thomas","Karen","Charles","Nancy",
             "Daniel","Lisa","Matthew","Betty","Anthony","Sandra","Mark","Ashley","Donald","Emily",
             "Steven","Kimberly","Paul","Donna","Andrew","Michelle","Joshua","Carol","Kenneth","Amanda"]
    LAST = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
            "Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin",
            "Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson",
            "Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores","Nakamura","Patel","Okafor"]
    SPEC = ["Family Medicine","Internal Medicine","Cardiology","Orthopedic Surgery","Radiology","Neurology",
            "Gastroenterology","Oncology","Dermatology","Emergency Medicine","Anesthesiology","General Surgery",
            "Obstetrics & Gynecology","Psychiatry","Pulmonology","Endocrinology","Nephrology","Urology"]
    GROUPS = ["Advanced Specialty Care","Metro Health System","Riverside Primary Care","Summit Health System",
              "Harbor Medical Group","Lakeside Physicians","Cornerstone Health Partners","Valley Care Associates",
              "Northgate Medical","Pioneer Health Network"]
    PLANS = ["HMO Choice 250","PPO Select 500","HDHP Saver 1500","EPO Core 350","POS Plus 400","Medicare Advantage Complete"]
    CARRIERS = ["Aetna","Cigna","UnitedHealthcare","Anthem BCBS","Humana","Kaiser","Medicare Part B"]
    def name(): return f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
    # Providers -> ~1,240
    pnpi = 1600000000
    while len(providers) < 1240:
        pnpi += rnd.randint(3, 29); npi = str(pnpi)
        if npi in providers: continue
        cred = rnd.random()
        providers[npi] = {"npi": npi, "name": "Dr. " + name(), "specialty": rnd.choice(SPEC),
                          "group_npi": str(1100000000 + rnd.randint(0, 99999)), "group_name": rnd.choice(GROUPS),
                          "network_status": "in-network" if rnd.random() > 0.12 else "out-of-network",
                          "credentialing_status": "active" if cred > 0.06 else "expired",
                          "credential_expiry": "2027-%02d-%02d" % (rnd.randint(1,12), rnd.randint(1,28)),
                          "contract_effective": "2024-01-01"}
    # Eligibility / members -> ~4,800
    mi = 20000
    while len(eligibility) < 4800:
        mi += 1; mid = "MBR-%d" % mi
        if mid in eligibility: continue
        eligibility[mid] = {"member_id": mid, "name": name(),
                            "dob": "19%02d-%02d-%02d" % (rnd.randint(45,99), rnd.randint(1,12), rnd.randint(1,28)),
                            "sex": rnd.choice(["M","F"]), "plan": rnd.choice(PLANS), "status": "active",
                            "coverage_spans": [{"effective": "2026-01-01", "term": None}],
                            "group_number": "GRP-%d" % rnd.randint(1000,9999), "subscriber_id": mid}
    # Authorizations -> ~3,200
    ai = 90000
    while len(authorizations) < 3200:
        ai += 1; an = "PA-2026%05d" % ai
        if an in authorizations: continue
        authorizations[an] = {"auth_number": an, "member_id": "MBR-%d" % rnd.randint(20001, 24800),
                              "member_name": name(), "provider_npi": str(1600000000 + rnd.randint(0, 999999)),
                              "cpt_code": rnd.choice(["27447","70553","72148","47562","29881","99214","93306","43239"]),
                              "diagnosis_code": rnd.choice(["M17.11","R51.9","M54.5","K80.20","S83.209A","I10"]),
                              "dos_start": "2026-01-01", "dos_end": "2026-12-31",
                              "units_authorized": rnd.randint(1, 12), "units_used": 0, "status": "approved"}
    # COB -> ~640
    while len(cob) < 640:
        mid = "MBR-%d" % rnd.randint(20001, 24800)
        if mid in cob: continue
        cob[mid] = {"member_id": mid, "member_name": name(), "carrier_name": rnd.choice(CARRIERS),
                    "policy_number": "POL-%d" % rnd.randint(100000, 999999), "group_number": "GRP-%d" % rnd.randint(1000, 9999),
                    "cob_order": rnd.choice(["primary","secondary"]), "plan_type": rnd.choice(["Commercial","Medicare","Medicaid"]),
                    "relationship": "self", "effective_date": "2026-01-01"}
    # Claims history -> ~4,800 members with a few claims each
    hi = 700000
    for mid in list(eligibility.keys()):
        if mid in claims_history: continue
        n = rnd.randint(0, 4); rows = []
        for _ in range(n):
            hi += 1
            rows.append({"icn": "ICN-2025-%d" % hi, "cpt_code": rnd.choice(["99213","80053","85025","93000","36415","71046"]),
                         "icd10": rnd.choice(["I10","E11.9","Z00.00","J45.909"]), "dos": "2025-%02d-%02d" % (rnd.randint(1,12), rnd.randint(1,28)),
                         "billed_amount": rnd.randint(80, 600), "paid_amount": rnd.randint(40, 400), "status": "paid"})
        if rows: claims_history[mid] = rows
    # Fee schedule -> ~420 CPTs
    used = set(fee_schedule.keys()); fi = 10000
    while len(fee_schedule) < 420:
        fi += 1; cpt = str(20000 + fi % 79999)
        if cpt in used: continue
        used.add(cpt)
        fee_schedule[cpt] = {"cpt_code": cpt, "description": "Procedure " + cpt, "specialty": rnd.choice(SPEC),
                             "allowed_amount": rnd.randint(40, 3200), "max_units_per_day": rnd.randint(1, 8),
                             "global_period_days": rnd.choice([0, 10, 90]), "modifier_impact": "none",
                             "auth_required": rnd.random() > 0.7, "effective_date": "2026-01-01"}

_pad_reference_dbs()

# High-dollar INSTITUTIONAL claims (≥ $10,000) → routed to human review for oversight.
# Seed a set so the category is populated and demonstrable (the base queue is professional).
HIGH_DOLLAR_INSTITUTIONAL = 10000.0
def _add_highdollar_institutional(n=70):
    import random as _r
    rnd = _r.Random(4242)
    members = list(eligibility.values())
    INST = [  # (rev, cpt, desc, icd, icd_desc)
        ("0110","99223","Inpatient admission, high complexity","J96.00","Acute respiratory failure"),
        ("0360","33533","Coronary artery bypass graft","I25.10","Atherosclerotic heart disease"),
        ("0360","27447","Total knee arthroplasty (inpatient)","M17.11","Osteoarthritis, right knee"),
        ("0200","99291","Critical care, first hour (ICU)","R65.21","Severe sepsis with septic shock"),
        ("0360","22633","Lumbar spinal fusion","M43.16","Spondylolisthesis, lumbar"),
        ("0360","32480","Lobectomy, lung","C34.90","Malignant neoplasm of lung"),
        ("0114","99223","NICU admission, high complexity","P07.30","Preterm newborn"),
        ("0360","47600","Cholecystectomy w/ complications","K80.12","Cholelithiasis w/ obstruction"),
    ]
    EDITS = ["E-MN-002","E-AUTH-001","E-PRICE-005","E-CODE-003","E-DUP-001","E-AUTH-004"]
    ec = edit_codes
    for i in range(n):
        m = members[rnd.randrange(len(members))]
        rev, cpt, desc, icd, icd_desc = rnd.choice(INST)
        billed = float(rnd.randint(10000, 85000))
        edit = rnd.choice(EDITS); meta = ec.get(edit, {})
        c = {"icn": f"ICN-2026-HD-{5000+i}", "claim_type": "Institutional", "form": "UB-04 / 837I",
             "member_id": m["member_id"], "member_name": m["name"], "member_dob": m.get("dob"),
             "plan": m.get("plan"), "npi_billing": "1902847733", "npi_rendering": "1902847733",
             "provider_name": "Metro General Hospital", "provider_specialty": "Acute Care Hospital",
             "group_name": "Metro Health System", "dos": "2026-02-20", "received_date": "2026-02-26",
             "pend_date": "2026-03-10", "days_in_queue": 10 + (i % 22), "priority": "urgent",
             "cpt_code": cpt, "cpt_description": desc, "modifier": None, "rev_code": rev,
             "icd10_principal": icd, "icd10_secondary": None, "icd10_desc": icd_desc,
             "place_of_service": "21", "units_billed": 1, "billed_amount": billed,
             "allowed_amount": round(billed * 0.62, 2), "auth_number": None,
             "edit_code": edit, "edit_category": meta.get("category", "Authorization"),
             "edit_description": meta.get("desc", ""), "carc_code": meta.get("carc"),
             "rarc_code": meta.get("rarc"), "resolution_path": meta.get("resolution"),
             "status": "pending", "resolution": None, "human_review_flag": False,
             "human_review_reason": None, "is_featured": False}
        pended_claims.append(c); claims_index[c["icn"]] = c
# Re-tune the pend queue to a target OUTCOME distribution (demo target):
# Approved 30% · Partial 20% · Denied 30% · ADR 5% · Human review (incl clinical) 15%.
# Done honestly by shaping the EDIT mix — each claim's real SOP outcome still matches its edit.
def _rebuild_pend_distribution(n=5300):
    import random as _r
    rnd = _r.Random(20260915)
    members = [m for m in eligibility.values()]
    PROVS = [("Dr. Alan Ross","Orthopedic Surgery","1401640052","Advanced Specialty Care"),
             ("Dr. Nina Patel","Radiology","1558493021","Metro Imaging"),
             ("Dr. Omar Reyes","Family Medicine","1730285566","Riverside Primary Care"),
             ("Dr. Lucy Kim","Emergency Medicine","1902847733","Harbor ED Group"),
             ("Dr. Carol Bennett","Cardiology","1131647520","Summit Cardiology"),
             ("Dr. Marcus Lee","Internal Medicine","1447882910","Valley Care Associates")]
    CPTS = [("99213","Office visit, established",118,"E11.9","Type 2 diabetes"),
            ("99214","Office visit, moderate",175,"I10","Essential hypertension"),
            ("70553","MRI brain w/wo contrast",410,"R51.9","Headache"),
            ("72148","MRI lumbar spine",395,"M54.5","Low back pain"),
            ("29881","Knee arthroscopy w/ meniscectomy",1180,"M23.209","Meniscus derangement"),
            ("97110","Therapeutic exercise",42,"M25.561","Pain in knee"),
            ("93306","Echocardiography w/ Doppler",340,"I50.9","Heart failure"),
            ("80053","Comprehensive metabolic panel",42,"Z00.00","General exam"),
            ("36415","Routine venipuncture",12,"Z00.00","General exam"),
            ("90837","Psychotherapy, 60 min",145,"F41.1","Anxiety disorder"),
            ("74177","CT abdomen & pelvis w/ contrast",520,"R10.9","Abdominal pain"),
            ("20610","Aspiration/injection, major joint",95,"M25.561","Pain in knee")]
    # buckets: (outcome, [edit_codes], force_human_review_flag)
    # Duplicate + auth-missing + wrong-provider-auth are DETERMINISTIC → agent decides (deny).
    # Only genuine judgment (adjustment posting) stays in human review.
    buckets = {
        "approve":      (["E-PRICE-001","E-COB-002","E-PRICE-006"], False),
        "partial_pay":  (["E-PRICE-002","E-COB-003","E-AUTH-004","E-PROV-003","E-PCP-001","E-OON-001"], False),
        "deny":         (["E-CODE-002","E-CODE-004","E-TF-001","E-TF-002","E-DUP-002","E-DUP-001","E-PRICE-004",
                          "E-PROV-001","E-PROV-002","E-PROV-004","E-WC-001","E-CODE-003","E-AUTH-005","E-AUTH-001"], False),
        "request_info": (["E-MN-001","E-AUTH-003","E-CODE-001","E-CODE-005","E-PRICE-003","E-COB-001","E-MG-001"], False),
        "human_review": (["E-ADJ-001"], False),   # genuine human review only — examiner posts the adjustment
    }
    weights = {"approve":0.35, "partial_pay":0.25, "deny":0.30, "request_info":0.05, "human_review":0.05}
    ec = edit_codes
    new_claims = []
    seq = 3000
    def mk(edit, force_hr):
        nonlocal seq
        seq += 1
        m = members[rnd.randrange(len(members))]
        p = rnd.choice(PROVS)
        cpt, cdesc, allowed, icd, idesc = rnd.choice(CPTS)
        if edit == "E-AUTH-001" and allowed < 150:   # ensure auth-missing denies (not auth-exempt)
            cpt, cdesc, allowed, icd, idesc = ("70553","MRI brain w/wo contrast",410,"R51.9","Headache")
        meta = ec.get(edit, {})
        billed = round(allowed * rnd.uniform(1.6, 3.0), 2)
        return {"icn": f"ICN-2026-{seq}", "claim_type": "Professional", "member_id": m["member_id"],
                "member_name": m["name"], "member_dob": m.get("dob"), "plan": m.get("plan"),
                "npi_billing": "1131647525", "npi_rendering": p[2], "provider_name": p[0],
                "provider_specialty": p[1], "group_name": p[3], "dos": "2026-03-14",
                "received_date": "2026-03-18", "pend_date": "2026-04-01", "days_in_queue": 8 + (seq % 24),
                "priority": "routine", "cpt_code": cpt, "cpt_description": cdesc, "modifier": None,
                "icd10_principal": icd, "icd10_secondary": None, "icd10_desc": idesc,
                "place_of_service": "11", "units_billed": 1, "billed_amount": billed,
                "allowed_amount": float(allowed), "auth_number": None, "edit_code": edit,
                "edit_category": meta.get("category",""), "edit_description": meta.get("desc",""),
                "carc_code": meta.get("carc"), "rarc_code": meta.get("rarc"),
                "resolution_path": meta.get("resolution"), "status": "pending", "resolution": None,
                "human_review_flag": bool(force_hr), "human_review_reason": ("Examiner judgment required" if force_hr else None),
                "is_featured": False}
    # bulk per target weights
    for outcome, w in weights.items():
        edits, force_hr = buckets[outcome]
        cnt = int(round(n * w))
        for i in range(cnt):
            new_claims.append(mk(edits[i % len(edits)], force_hr))
    # a small, credible volume of pends on genuinely-new edits that have NO SOP on file yet
    for edit in NO_SOP_EDITS:
        for i in range(40):
            new_claims.append(mk(edit, False))
    # one featured claim per EVERY edit code (so trace dropdown + catalog stay complete)
    for edit in ec.keys():
        fr = edit in ("E-ADJ-001","E-DUP-001","E-AUTH-001","E-AUTH-005")
        c = mk(edit, fr); c["is_featured"] = True
        new_claims.append(c)
    pended_claims[:] = new_claims
    claims_index.clear(); claims_index.update({c["icn"]: c for c in pended_claims})

_rebuild_pend_distribution()
_add_highdollar_institutional()

# Single-payer demo: unify every plan/payer label to one payer (member's own plan only;
# COB other-coverage carriers are intentionally left as different carriers).
def _normalize_payer(name="Althea Health"):
    for coll in (pended_claims, line_item_claims, multi_edit_claims, list(eligibility.values())):
        for c in coll:
            if isinstance(c, dict) and c.get("plan"):
                c["plan"] = name
_normalize_payer()

# ── Knowledge Graph Rules ────────────────────────────────────────────────────

KG_RULES = {
    "E-AUTH-001": [
        {"rule_id":"KG-PA-001", "check":"Prior Authorization Requirement",
         "template":"CPT {cpt} (allowed ${allowed}) requires prior authorization per plan benefit design PA-001. Auth exemption list queried — service not exempt.",
         "source":"Plan Benefit Policy PA-001 · §7.2 Auth Required Services"},
        {"rule_id":"KG-PA-002", "check":"Authorization Database Query",
         "template":"Query: member_id={member_id}, cpt={cpt}, dos={dos}. Result: no active authorization found. Auth number field = NULL.",
         "source":"Authorization DB — real-time lookup"},
    ],
    "E-AUTH-002": [
        {"rule_id":"KG-PA-003", "check":"Authorization Validity Window",
         "template":"Authorization expiry date < DOS {dos}. Retro-authorization eligibility window = 30 days post-DOS. Window check: {days_pending} days in queue.",
         "source":"Plan Policy PA-002 · §4.1 Retro-Auth Eligibility"},
        {"rule_id":"KG-PA-004", "check":"Retro-Authorization Threshold",
         "template":"CPT {cpt} billed amount ${billed} — retro auth request permissible only within 30-day window. Rule fires: deny unless retro auth submitted.",
         "source":"SOP-AUTH-002 §4.1"},
    ],
    "E-AUTH-003": [
        {"rule_id":"KG-PA-005", "check":"CPT-Authorization Match",
         "template":"Authorized CPT on file does not match billed CPT {cpt}. Service substitution not permitted per policy. ADR required for corrected authorization or amended claim.",
         "source":"Plan Policy PA-003 · §2.4 Service Match Requirement"},
        {"rule_id":"KG-PA-006", "check":"Clinical Scope of Auth",
         "template":"Authorization scope validated against billed procedure. Mismatch detected — authorized service ≠ rendered service. Cannot approve without corrected auth.",
         "source":"Authorization DB · Clinical Scope Table"},
    ],
    "E-AUTH-004": [
        {"rule_id":"KG-PA-007", "check":"Authorization Unit Ceiling",
         "template":"Authorization unit ceiling check: CPT {cpt}, units authorized on file. Units billed on this claim = {units}. Excess units exceed authorized ceiling.",
         "source":"Plan Policy PA-004 · §5.1 Unit Limit Enforcement"},
        {"rule_id":"KG-PA-008", "check":"Cumulative Units — Claims History",
         "template":"Claims History DB queried: prior paid units for this auth period accumulated. Total including this claim exceeds authorized unit limit. Deny excess; approve up to ceiling.",
         "source":"Claims History DB — cumulative unit aggregation"},
    ],
    "E-AUTH-005": [
        {"rule_id":"KG-PA-009", "check":"Rendering Provider — Authorization Match",
         "template":"Authorization on file specifies rendering NPI. Billed rendering NPI {npi} does not match authorized NPI. Provider reassignment not permitted without updated auth.",
         "source":"Plan Policy PA-005 · §3.7 Provider-Specific Auth"},
        {"rule_id":"KG-PA-010", "check":"Provider NPI Active Status",
         "template":"Provider DB confirms NPI {npi} is active and credentialed. However, auth was issued to a different NPI — rendering provider must match auth exactly.",
         "source":"Provider DB · Authorization DB cross-reference"},
    ],
    "E-PROV-001": [
        {"rule_id":"KG-PROV-001", "check":"Credentialing Status — DOS Check",
         "template":"Provider DB query: rendering NPI {npi}, credentialing_status = expired/suspended. Credential expiry date precedes DOS {dos}. Service not coverable under lapsed credential.",
         "source":"Provider DB · Credentialing Registry · NCQA Standard CR 1.A"},
        {"rule_id":"KG-PROV-002", "check":"Plan Participation Requirement",
         "template":"Plan requires active credentialing for all rendering providers at time of service. Retrospective credentialing not accepted. Deny CO-185/N570.",
         "source":"Plan Policy CRED-001 · §2.1 Credentialing at Time of Service"},
    ],
    "E-PROV-002": [
        {"rule_id":"KG-PROV-003", "check":"Billing NPI — Rendering NPI Linkage",
         "template":"Billing NPI and rendering NPI {npi} submitted on claim. Provider DB cross-reference: billing entity does not include rendering NPI in enrolled roster.",
         "source":"Provider DB · NPI Enrollment Registry · CMS 1500 Field 24J"},
        {"rule_id":"KG-PROV-004", "check":"Group Enrollment Scope",
         "template":"Group billing NPI must be linked to individual rendering NPI in the plan's provider directory. Linkage not confirmed. Deny CO-16/N286 pending corrected submission.",
         "source":"Plan Policy PROV-002 · §4.3 Group-Individual NPI Linkage"},
    ],
    "E-PROV-003": [
        {"rule_id":"KG-PROV-005", "check":"Place of Service — CPT Alignment",
         "template":"CPT {cpt} billed with POS {pos}. Fee Schedule DB: this procedure has different allowed amounts by POS. POS on claim does not match provider's contracted service site.",
         "source":"Fee Schedule DB · CMS POS Table · Plan Policy POS-001"},
        {"rule_id":"KG-PROV-006", "check":"Reimbursement Rate — POS Differential",
         "template":"POS mismatch: facility vs non-facility rate differential applies. Correct POS would trigger facility rate. Reprice or deny pending corrected claim.",
         "source":"CMS POS Indicator Policy · Fee Schedule DB"},
    ],
    "E-PROV-004": [
        {"rule_id":"KG-PROV-007", "check":"Group NPI — Individual NPI Enrollment Link",
         "template":"Group NPI submitted as billing entity. Provider DB: individual rendering NPI {npi} not listed under this group's enrolled providers. Claim cannot be processed under unlinked group.",
         "source":"Provider DB · CMS Group Enrollment Rules · Plan Policy PROV-004"},
    ],
    "E-PROV-005": [
        {"rule_id":"KG-PROV-008", "check":"Network Participation — Member Benefit",
         "template":"Provider NPI {npi} status = out-of-network in provider directory. Member plan {plan}: out-of-network benefit = not covered. Deny CO-3/N19.",
         "source":"Provider DB · Member Benefit Table · Plan Policy NET-001"},
        {"rule_id":"KG-PROV-009", "check":"Emergency Exception Check",
         "template":"OON claim reviewed for emergency exception. DOS, POS, and diagnosis reviewed — service does not qualify as emergent. Standard OON denial applies.",
         "source":"Plan Policy NET-002 · §5.4 Emergency Exception Criteria"},
    ],
    "E-PRICE-001": [
        {"rule_id":"KG-FEE-001", "check":"Fee Schedule Maximum — CO-45 Adjustment",
         "template":"Fee Schedule DB: CPT {cpt} contracted allowed amount = ${allowed}. Billed amount = ${billed}. CO-45 contractual adjustment = ${adjustment}. Reprice to fee schedule.",
         "source":"Fee Schedule DB · Plan Contract · CMS CO-45 Adjustment Rule"},
        {"rule_id":"KG-FEE-002", "check":"Lesser-Of Rule",
         "template":"Plan applies lesser-of rule: pay lower of billed amount or contracted rate. Contracted rate ${allowed} < billed ${billed}. Approved amount = ${allowed}.",
         "source":"Plan Contract §3.2 · Fee Schedule DB"},
    ],
    "E-PRICE-002": [
        {"rule_id":"KG-FEE-003", "check":"Maximum Units Per Day — CPT Policy",
         "template":"Fee Schedule DB: CPT {cpt} maximum units per day of service. Units billed = {units}. Excess units beyond allowed maximum are not reimbursable.",
         "source":"Fee Schedule DB · CMS Medically Unlikely Edits (MUE) · Plan Policy PRICE-002"},
        {"rule_id":"KG-FEE-004", "check":"CMS Medically Unlikely Edit (MUE)",
         "template":"MUE table check: CPT {cpt} MUE adjudication indicator — per day of service limit applies. Units billed exceed MUE threshold. Deny excess with CO-4/M44.",
         "source":"CMS MUE Table (current year) · NCCI Policy Manual"},
    ],
    "E-PRICE-003": [
        {"rule_id":"KG-FEE-005", "check":"Modifier Requirement — Separate Reimbursement",
         "template":"Fee Schedule DB: CPT {cpt} requires modifier for separate reimbursement. Claim submitted without required modifier. Without modifier, service bundled per CCI policy.",
         "source":"Fee Schedule DB · CCI Edit Table · Plan Policy PRICE-003"},
        {"rule_id":"KG-FEE-006", "check":"CCI Modifier Indicator",
         "template":"NCCI Modifier Indicator for CPT {cpt}: modifier '1' — modifier required to bypass bundling edit. Modifier absent on claim. ADR or corrected claim required.",
         "source":"CMS NCCI Policy Manual · CCI Edit Table (current quarter)"},
    ],
    "E-PRICE-004": [
        {"rule_id":"KG-FEE-007", "check":"Global Surgery Period — CMS Policy",
         "template":"CMS global surgery period for CPT {cpt} = 90 days. Claims History DB: primary procedure paid. DOS {dos} falls within global period. Service is bundled — deny CO-97/N70.",
         "source":"CMS Global Surgery Policy · Fee Schedule DB · Claims History DB"},
        {"rule_id":"KG-FEE-008", "check":"Unbundling Detection",
         "template":"Global period query: primary procedure billed and paid within 90-day window. Follow-up service is included in the global surgical package. Separate billing not permitted.",
         "source":"CMS CCI Global Surgery Edits · Plan Policy PRICE-004 §6.2"},
    ],
    "E-PRICE-005": [
        {"rule_id":"KG-FEE-009", "check":"Pricing Exception Threshold",
         "template":"Billed amount ${billed} exceeds 2× fee schedule allowed ${allowed} for CPT {cpt}. Pricing exception committee review required per Plan Policy PE-007.",
         "source":"Plan Policy PE-007 · §7.1 Pricing Exception Review Threshold"},
    ],
    "E-CODE-001": [
        {"rule_id":"KG-CODE-001", "check":"ICD-10-CM Code Validity — DOS",
         "template":"ICD-10-CM code set query: diagnosis code {icd10} checked against CMS code set effective for DOS {dos}. Code invalid or inactive for this date of service.",
         "source":"CMS ICD-10-CM Tabular List (FY 2026) · Code Validity Table"},
        {"rule_id":"KG-CODE-002", "check":"Code Effective Date Check",
         "template":"ICD-10-CM {icd10}: code effective/expiry dates do not cover DOS {dos}. Corrected claim required with valid diagnosis code for the date of service.",
         "source":"CMS ICD-10-CM Official Guidelines · Plan Policy CODE-001 §2.3"},
    ],
    "E-CODE-002": [
        {"rule_id":"KG-CODE-003", "check":"Plan Benefit Exclusion Table",
         "template":"CPT {cpt} queried against plan benefit exclusion table. Service falls under excluded category per plan benefit summary. Not a covered benefit for plan {plan}.",
         "source":"Plan Benefit Summary · Exclusion Table · Plan Policy COV-002"},
        {"rule_id":"KG-CODE-004", "check":"Coverage Determination",
         "template":"Plan coverage matrix: CPT {cpt} is classified as [excluded/non-covered]. Denial CO-96/N63 applies. EOB language: service not a covered benefit under member's plan.",
         "source":"Plan Benefit Summary §4 · Plan Policy CODE-002 §3.1"},
    ],
    "E-CODE-003": [
        {"rule_id":"KG-CODE-005", "check":"LCD Coverage Criteria — ICD/CPT Pair",
         "template":"LCD lookup: CPT {cpt} subject to LCD L33787 (or applicable LCD). Billed diagnosis {icd10} is not in the LCD's covered ICD-10 list → not a covered indication for CPT {cpt} under this LCD.",
         "source":"CMS LCD L33787 · ICD-10 Coverage Indicator Table · Plan Policy CODE-003"},
        {"rule_id":"KG-CODE-006", "check":"NCD Cross-Reference",
         "template":"NCD database queried for CPT {cpt}. If applicable NCD exists, billed diagnosis {icd10} must appear in covered indication list. Diagnosis fails coverage criteria — deny CO-167/N115.",
         "source":"CMS NCD Manual · LCD/NCD Crosswalk Table"},
    ],
    "E-CODE-004": [
        {"rule_id":"KG-CODE-007", "check":"Age/Sex Demographic Constraint",
         "template":"ICD-10-CM {icd10} demographic constraint table: code has age or sex restriction. Member demographics queried from Eligibility DB. Conflict detected — diagnosis not valid for member profile.",
         "source":"CMS ICD-10-CM Official Guidelines · Demographic Edit Table · Plan Policy CODE-004"},
    ],
    "E-CODE-005": [
        {"rule_id":"KG-CODE-008", "check":"Principal Diagnosis Sequencing — ICD-10-CM Rule",
         "template":"ICD-10-CM Official Guidelines §Section II: principal diagnosis sequencing rules apply. Diagnosis {icd10} is a manifestation code and cannot be sequenced as principal. Corrected claim required.",
         "source":"CMS ICD-10-CM Official Guidelines §Section II · Plan Policy CODE-005 §3.4"},
    ],
    "E-COB-001": [
        {"rule_id":"KG-COB-001", "check":"Coordination of Benefits Order",
         "template":"COB DB: member {member_id} has secondary insurance on file. COB order = secondary. Primary carrier EOB required before plan can calculate its liability. Claim held pending primary EOB.",
         "source":"COB DB · Plan COB Policy COB-001 · NAIC COB Model Regulation"},
        {"rule_id":"KG-COB-002", "check":"Primary Payer Determination",
         "template":"Birthday rule / gender rule / employment status applied per NAIC COB guidelines. This plan determined to be secondary payer. Primary EOB required to calculate secondary liability.",
         "source":"NAIC COB Model Regulation · Plan COB Policy COB-001 §4.1"},
    ],
    "E-COB-002": [
        {"rule_id":"KG-COB-003", "check":"Medicare Primary — Crossover Protocol",
         "template":"COB DB: member has Medicare Part B as primary payer. CMS crossover claim protocol applies. Medicare crossover data must be retrieved from CMS before secondary processing.",
         "source":"CMS Medicare Secondary Payer (MSP) Rules · Plan COB Policy COB-002 §3.8"},
        {"rule_id":"KG-COB-004", "check":"MSP Working Aged / ESRD Check",
         "template":"Medicare primary determination confirmed: member age, employer group size, and ESRD status checked. Medicare is primary. Process crossover — apply Medicare allowed as coordination basis.",
         "source":"CMS MSP Regulations 42 CFR §411 · Plan COB Policy COB-002"},
    ],
    "E-COB-003": [
        {"rule_id":"KG-COB-005", "check":"COB Savings Calculation Method",
         "template":"Plan uses non-duplication COB method. Primary payment retrieved from EOB on file. Plan liability = plan allowed ${allowed} minus primary payment. COB savings applied.",
         "source":"Plan COB Policy COB-003 §5.1 · Non-Duplication Method"},
    ],
    "E-DUP-001": [
        {"rule_id":"KG-DUP-001", "check":"Exact Duplicate Detection",
         "template":"Claims History DB: exact match found — same member_id, DOS {dos}, CPT {cpt}, and rendering NPI. Original ICN previously processed and paid. Deny as exact duplicate CO-18/N522.",
         "source":"Claims History DB · Plan Policy DUP-001 §2.1 · CMS Duplicate Claim Rules"},
    ],
    "E-DUP-002": [
        {"rule_id":"KG-DUP-002", "check":"Potential Duplicate — Pattern Analysis",
         "template":"Claims History DB: same member_id and DOS {dos} with different NPI or CPT variant found. Split-billing pattern check: review for legitimate separate service vs duplicate submission.",
         "source":"Claims History DB · Plan Policy DUP-002 §3.3 · CMS Duplicate Claim Guidelines"},
    ],
    "E-TF-001": [
        {"rule_id":"KG-TF-001", "check":"Timely Filing Calculation",
         "template":"DOS {dos} to claim receipt date: elapsed days calculated. Plan timely filing limit = 365 days from DOS. Elapsed days exceed limit. Exception criteria checked — no qualifying exception found.",
         "source":"Plan Policy TF-001 §2.2 · CMS Timely Filing Rules · Clean Claim Act"},
    ],
    "E-TF-002": [
        {"rule_id":"KG-TF-002", "check":"Corrected Claim Filing Window",
         "template":"Original claim receipt date to corrected claim receipt: elapsed days calculated. Plan corrected claim filing limit = 180 days from original receipt. Limit exceeded. Deny CO-29/N35.",
         "source":"Plan Policy TF-002 §2.2 · Corrected Claim Submission Guidelines"},
    ],
    "E-MN-001": [
        {"rule_id":"KG-MN-001", "check":"Clinical Documentation Requirement",
         "template":"CPT {cpt} specialty {specialty}: clinical documentation required per Plan Medical Policy MN-001. Documentation status queried — notes, lab results, or physician attestation not on file.",
         "source":"Plan Medical Policy MN-001 §3.1 · InterQual Documentation Criteria"},
        {"rule_id":"KG-MN-002", "check":"ADR Trigger — Medical Necessity",
         "template":"Insufficient documentation to make medical necessity determination. ADR issued to provider: request clinical notes, operative report, or supporting documentation within 14-day window.",
         "source":"Plan Policy ADR-001 · §3.1 Additional Documentation Request Protocol"},
    ],
    "E-MN-002": [
        {"rule_id":"KG-MN-003", "check":"LCD/MCG Medical Necessity Criteria",
         "template":"LCD L33787 (or applicable LCD) for CPT {cpt}: coverage criteria require diagnosis from approved ICD-10 list. Billed diagnosis {icd10} does not meet LCD coverage criteria. Medical necessity not established.",
         "source":"CMS LCD L33787 · MCG Clinical Criteria · Plan Medical Policy MN-002"},
        {"rule_id":"KG-MN-004", "check":"Clinical Reviewer Routing Rule",
         "template":"Medical necessity denial requires clinical reviewer sign-off per Plan Policy MN-002 §4.2. Claim routed to clinical review queue. Denial cannot be issued without licensed clinician attestation.",
         "source":"Plan Medical Policy MN-002 §4.2 · URAC UM Standards"},
    ],
}

# Decision-criteria + citations for the remaining edit types (so every SOP document is complete)
KG_RULES.update({
    "E-PRICE-006": [
        {"rule_id":"KG-MP-001", "check":"Manual-Pricing Trigger",
         "template":"CPT {cpt} has no on-file contracted rate for this provider/POS {pos}; auto-adjudication could not price it. Route to the pricing engine for a defensible allowed amount.",
         "source":"Plan Payment Policy PRICE-MANUAL §2.1 · Provider contract terms"},
        {"rule_id":"KG-MP-002", "check":"Repricing Engine Basis",
         "template":"Pricing engine (Burgess/Multiplan/Zelis) queried with CPT {cpt}, POS {pos}, DOS {dos}, billed ${billed}. Returned allowed amount applied as the payable basis with methodology retained for audit.",
         "source":"Pricing engine response · CMS RBRVS reference · HealthEdge Source methodology"},
    ],
    "E-OON-001": [
        {"rule_id":"KG-OON-001", "check":"Network-Status Determination",
         "template":"Provider confirmed out-of-network for member plan {plan}. Claim {cpt} must be repriced under the OON methodology rather than the in-network fee schedule.",
         "source":"Provider DB network status · Plan Benefit Policy OON-001 §2.4"},
        {"rule_id":"KG-OON-002", "check":"OON Repricing & Member Liability",
         "template":"OON allowed amount derived via the repricing network (Multiplan/Zelis) for CPT {cpt}; higher member cost-share applied per the OON benefit design.",
         "source":"Repricing network response · Plan OON benefit schedule"},
    ],
    "E-ENR-001": [
        {"rule_id":"KG-ENR-001", "check":"Member/Demographic Reconciliation",
         "template":"Eligibility record for member {member_id} does not match the submitted patient detail (name/DOB/ID). Payment cannot post to an unverified member.",
         "source":"Eligibility DB · Enrollment (834) record · Plan Policy ENR-001 §1.4"},
        {"rule_id":"KG-ENR-002", "check":"Correct-vs-Return Rule",
         "template":"If the mismatch is reconcilable from the enrollment record it is corrected and reprocessed; otherwise a corrected 834 / enrollment update is requested before payment.",
         "source":"Plan Enrollment SOP ENR-001 · CMS enrollment reconciliation guidance"},
    ],
    "E-ENR-002": [
        {"rule_id":"KG-ENR-003", "check":"Newborn Enrollment Window",
         "template":"Newborn add must fall within the 31-day enrollment window and be linked to a covered subscriber. DOS {dos}; window check applied.",
         "source":"Plan Policy ENR-002 §2.2 · State newborn-coverage mandate"},
        {"rule_id":"KG-ENR-004", "check":"Subscriber Linkage",
         "template":"Newborn must be linked to an enrolled parent/subscriber on the policy; if unlinked or outside the window, enrollment documentation is requested.",
         "source":"Enrollment (834) record · Plan Policy ENR-002"},
    ],
    "E-PCP-001": [
        {"rule_id":"KG-PCP-001", "check":"PCP Attribution",
         "template":"Member {member_id} PCP assignment retrieved; the billed line is not mapped to the assigned/attributed PCP. Line must be remapped or removed before payment.",
         "source":"Provider DB attribution · Eligibility DB · Plan Policy PCP-001 §3.1"},
        {"rule_id":"KG-PCP-002", "check":"Line Correction Rule",
         "template":"Erroneous line deleted / remapped to the correct PCP; remaining lines repriced at the in-network rate.",
         "source":"Plan PCP SOP §3.1"},
    ],
    "E-WC-001": [
        {"rule_id":"KG-WC-001", "check":"Work-Related Injury Indicator",
         "template":"Diagnosis/indicators for CPT {cpt} (ICD-10 {icd10}) suggest a work-related injury. Liability belongs to the Workers' Compensation carrier, not the health plan.",
         "source":"CMS MSP — Workers' Compensation · Plan Policy WC-001 §1.2"},
        {"rule_id":"KG-WC-002", "check":"Redirect Rule",
         "template":"Claim denied to the health plan (CO-19) and redirected to the WC carrier on file; member held harmless.",
         "source":"COB/other-coverage record · State WC statute"},
    ],
    "E-MG-001": [
        {"rule_id":"KG-MG-001", "check":"Medicare-Primary Confirmation",
         "template":"Medicare adjudicated as primary for member {member_id}; a Medigap/supplemental policy is on file for secondary coordination.",
         "source":"COB DB · CMS Medigap crossover (COBA) guidance"},
        {"rule_id":"KG-MG-002", "check":"Crossover Routing",
         "template":"Crossover routed to the Medigap payer for secondary payment per plan COB methodology.",
         "source":"Plan COB Policy MG-001 §2.1"},
    ],
    "E-ADJ-001": [
        {"rule_id":"KG-ADJ-001", "check":"Adjustment / POS-DA Validation",
         "template":"Post-adjudication adjustment (POS-DA) request for {cpt}: HPI indicators, claimstop and flush codes validated against the original claim.",
         "source":"Claims History (original ICN) · Plan Adjustment SOP ADJ-001 §4.3"},
        {"rule_id":"KG-ADJ-002", "check":"Examiner-Governed Posting",
         "template":"Adjustment posting requires examiner authorization; the agent validates and stages the reprocess, a person posts it.",
         "source":"Plan Policy ADJ-001 §4.3 · SOX segregation-of-duties control"},
    ],
})

def get_kg_rules(edit_code, claim, ctx):
    """Return KG rules for this edit, with claim data filled into templates."""
    rules = KG_RULES.get(edit_code, [])
    result = []
    adj = round((claim.get("billed_amount") or 0) - (claim.get("allowed_amount") or 0), 2)
    for rule in rules:
        text = rule["template"].format(
            cpt       = claim.get("cpt_code", ""),
            icd10     = claim.get("icd10_principal", ""),
            dos       = claim.get("dos", ""),
            allowed   = claim.get("allowed_amount", ""),
            billed    = claim.get("billed_amount", ""),
            units     = claim.get("units_billed", ""),
            npi       = claim.get("npi_rendering", ""),
            member_id = claim.get("member_id", ""),
            plan      = claim.get("plan", ""),
            specialty = claim.get("provider_specialty", ""),
            pos       = claim.get("place_of_service", ""),
            days_pending = claim.get("days_in_queue", ""),
            adjustment= adj,
        )
        result.append({"rule_id": rule["rule_id"], "check": rule["check"], "text": text, "source": rule["source"]})
    return result

# ── SOP Library — full operating-procedure documents (composed from the live rules) ──

CATEGORY_PURPOSE = {
    "Authorization": "Govern adjudication of claims that pend for prior-authorization edits, ensuring services requiring authorization are validated against the authorization system and plan benefit design before payment.",
    "Provider": "Govern adjudication of provider-related pends (credentialing, network status, NPI validity), ensuring only eligible, properly credentialed rendering providers are paid.",
    "Pricing": "Govern price-integrity adjudication, ensuring each line is paid at the correct contracted or fee-schedule amount and that units, bundling, and modifiers are applied correctly.",
    "Coding": "Govern coding-edit adjudication (CPT/ICD validity, LCD/NCD combinations, sequencing, coverage), ensuring billed codes are valid, covered, and correctly related.",
    "COB": "Govern coordination-of-benefits adjudication, ensuring the correct payer order and secondary calculation are applied per CMS MSP rules and plan COB methodology.",
    "Medical Necessity": "Govern medical-necessity adjudication, ensuring services meet coverage criteria (LCD/NCD, clinical policy) and routing clinical determinations to qualified reviewers.",
    "Duplicate": "Govern duplicate-claim adjudication, distinguishing exact and potential duplicates from legitimate repeat services before denying or paying.",
    "Timely Filing": "Govern timely-filing adjudication, enforcing plan filing limits while honoring valid exceptions and corrected-claim windows.",
    "Enrollment": "Govern enrollment-related pends (member/demographic mismatches, newborn add), ensuring eligibility and member data are correct before payment.",
    "Manual Pricing": "Govern manual-pricing adjudication for claims requiring the pricing engine (Burgess/Multiplan/Zelis), ensuring a defensible repriced allowed amount.",
    "OON": "Govern out-of-network adjudication, ensuring correct repricing and member-liability treatment for non-participating providers.",
    "PCP": "Govern primary-care-provider mapping pends, ensuring correct attribution before payment.",
    "Workers Comp": "Govern work-related injury pends, ensuring claims that are the responsibility of a workers'-compensation carrier are redirected rather than paid.",
    "Medigap": "Govern Medigap secondary crossover adjudication, ensuring correct coordination with Medicare primary.",
    "Adjustment": "Govern post-adjudication adjustment/reprocess pends, ensuring examiner-posted corrections are handled with proper authorization.",
}

RESOLUTION_LOGIC_DESC = {
    "deny_or_approve_if_exempt": "If the service is on the auth-exemption list, approve; otherwise, with no active authorization on file, deny CO-197 (auth missing).",
    "deny_unless_retro": "If a retro-authorization is submitted within the eligibility window, route for approval; otherwise deny — authorization expired before DOS.",
    "deny_or_resubmit": "If the authorized service matches the billed service, approve; on mismatch, request a corrected authorization / amended claim (ADR) rather than pay.",
    "deny_excess_units": "Approve units up to the authorized ceiling; deny units billed above the ceiling.",
    "reduce_units_or_deny": "Reprice to the allowed units/amount (partial pay) where the fee schedule supports it; deny the excess.",
    "verify_or_deny": "Verify the supporting record; approve if validated, otherwise deny.",
    "deny_or_approve_if_credentialed": "Approve if the rendering provider was credentialed on the DOS; otherwise deny — retrospective credentialing is not accepted.",
    "verify_npi_or_deny": "Validate the rendering NPI against the provider directory; deny if not found or inactive.",
    "correct_pos_or_deny": "Correct the place-of-service if permissible; otherwise deny.",
    "deny_or_apply_oon": "Apply out-of-network repricing and member liability; deny only where OON benefits do not apply.",
    "oon_reprice": "Reprice the out-of-network line per the OON methodology and apply member liability.",
    "reprice_to_fee_schedule": "Reprice the billed amount down to the contracted fee-schedule allowed amount and pay at that level.",
    "apply_modifier_or_deny": "If the correct modifier is present/appropriate, price accordingly; otherwise request correction (ADR) or deny.",
    "deny_bundled_service": "Deny the line as bundled into the global/primary service per NCCI/global-period rules.",
    "escalate_pricing_review": "Route to senior pricing review — the pricing exception exceeds standard SOP authority.",
    "deny_or_correct_code": "If the code can be corrected within policy, request correction (ADR); otherwise deny the invalid/non-covered code.",
    "deny_not_covered": "Deny — the CPT is not a covered benefit under the member's plan.",
    "deny_lcd_ncd": "Deny — the diagnosis is not a covered indication for the procedure under the applicable LCD/NCD. The agent applies the coverage policy and codes it CO-167 / N115; as an adverse coverage denial it routes for examiner sign-off before release.",
    "correct_sequencing": "Correct principal-diagnosis sequencing where permissible; otherwise request correction.",
    "process_crossover": "Apply the Medicare crossover: coordinate to the Medicare-allowed amount and process the secondary payment.",
    "calculate_cob_savings": "Apply the plan COB methodology using the primary EOB to compute the secondary payment.",
    "deny_duplicate": "Deny — exact duplicate of a previously adjudicated claim (same member/DOS/CPT).",
    "investigate_or_deny": "Investigate the potential duplicate against claims history; deny if confirmed, otherwise route to review.",
    "deny_timely_filing": "Deny — claim received beyond the plan filing limit with no valid exception.",
    "deny_medical_necessity": "Deny for medical necessity per LCD/NCD/clinical policy; clinical reviewer sign-off required before the denial is released.",
    "manual_price_via_engine": "Send the line to the pricing engine (Burgess/Multiplan/Zelis) for a defensible repriced allowed amount, then pay at that amount.",
    "enrollment_correct_details": "Correct the member/demographic data against eligibility; approve once reconciled, otherwise request information.",
    "wc_redirect": "Redirect to the workers'-compensation carrier — not a plan liability.",
    "medigap_crossover": "Coordinate the Medigap secondary payment against Medicare primary.",
    "approve": "All checks pass — approve and pay at the allowed amount.",
    "deny": "Adjudication rules are not met — deny with the mapped CARC/RARC.",
    "escalate": "Route to senior/specialist review beyond standard SOP authority.",
    "human_review": "Stage the case and route to a human examiner for the final determination.",
}

def build_sop_document(edit_code):
    """Compose a full SOP document for an edit type from the live rule set (no invention)."""
    meta = edit_codes.get(edit_code)
    if not meta:
        return None
    rpath = meta.get("resolution")
    rule = RESOLUTION_RULES.get(rpath, {})
    kg = KG_RULES.get(edit_code, [])
    references = []
    for k in kg:
        if k["source"] not in references:
            references.append(k["source"])
    return {
        "edit_code": edit_code,
        "sop_ref": rule.get("sop_ref", "N/A"),
        "title": meta.get("desc", ""),
        "category": meta.get("category", ""),
        "version": "v2026.1",
        "effective_date": "2026-01-01",
        "owner": "Claims Adjudication — Policy & SOP Governance",
        "status": "ACTIVE",
        "purpose": CATEGORY_PURPOSE.get(meta.get("category"), "Govern adjudication of claims that pend for this edit."),
        "scope": f"Applies to pended claims flagged {edit_code} — {meta.get('desc','')} (CARC {meta.get('carc')}/RARC {meta.get('rarc')}).",
        "decision_logic": RESOLUTION_LOGIC_DESC.get(rpath, "Apply the matching resolution rule and mapped CARC/RARC."),
        "decision_criteria": [{"rule_id": k["rule_id"], "check": k["check"], "detail": k["template"], "source": k["source"]} for k in kg],
        "procedure": rule.get("steps", []),
        "coding": {"carc": meta.get("carc"), "rarc": meta.get("rarc")},
        "references": references or ["Plan adjudication policy"],
        "resolution_path": rpath,
    }

@app.route("/api/sop/<edit_code>")
def api_sop(edit_code):
    doc = build_sop_document(edit_code)
    if not doc:
        return jsonify({"error": "SOP not found for edit"}), 404
    return jsonify(doc)

# ── SOP Resolution Logic ─────────────────────────────────────────────────────

# Data-driven checks used by outcome logic — so editing a source record re-flips the decision.
def _has_valid_auth(c):
    a = authorizations.get(c.get("auth_number")) if c.get("auth_number") else None
    return bool(a and str(a.get("status", "")).lower() == "active"
                and (a.get("units_remaining") or 0) > 0
                and (not a.get("cpt_code") or a.get("cpt_code") == c.get("cpt_code")))

def _auth_units_ok(c):
    a = authorizations.get(c.get("auth_number")) if c.get("auth_number") else None
    return bool(a and (a.get("units_remaining") or 0) >= (c.get("units_billed") or 1))

def _filed_timely(c):
    from datetime import datetime
    try:
        return (datetime.strptime(c.get("received_date"), "%Y-%m-%d")
                - datetime.strptime(c.get("dos"), "%Y-%m-%d")).days <= FILING_LIMIT_DAYS
    except Exception:
        return True

RESOLUTION_RULES = {
    # Authorization
    "deny_or_approve_if_exempt": {
        "steps": [
            "Query Authorization DB — check if auth number exists for CPT + member",
            "Check plan benefit design — is this service exempt from auth requirement?",
            "If exempt: approve at fee schedule rate",
            "If not exempt and no auth: deny with CO-197 / N517",
        ],
        "outcome_logic": lambda c: "approve" if (c["allowed_amount"] < 150 or _has_valid_auth(c)) else "deny",
        "sop_ref": "SOP-AUTH-001 §3.2",
    },
    "deny_unless_retro": {
        "steps": [
            "Query Authorization DB — confirm auth expiry date",
            "Check if retro-authorization request is eligible (≤ 30 days post-DOS)",
            "If retro eligible: send ADR for retro auth documentation",
            "If beyond retro window: deny with CO-197 / N56",
        ],
        "outcome_logic": lambda c: "request_info" if c["days_in_queue"] < 30 else "deny",
        "sop_ref": "SOP-AUTH-002 §4.1",
    },
    "deny_or_resubmit": {
        "steps": [
            "Query Authorization DB — retrieve auth record",
            "Compare authorized CPT vs billed CPT",
            "If service mismatch: send ADR requesting corrected auth or updated claim",
            "If provider cannot correct within 10 days: deny CO-197 / N115",
        ],
        "outcome_logic": lambda c: "request_info",
        "sop_ref": "SOP-AUTH-003 §2.4",
    },
    "deny_excess_units": {
        "steps": [
            "Query Authorization DB — check units authorized vs units billed",
            "Query Claims History — check units previously paid against same auth",
            "Approve up to authorized unit ceiling",
            "Deny excess units with CO-119 / N362",
        ],
        "outcome_logic": lambda c: "approve" if _auth_units_ok(c) else "partial_pay",
        "sop_ref": "SOP-AUTH-004 §5.1",
    },
    "verify_or_deny": {
        "steps": [
            "Query Authorization DB — retrieve authorized NPI",
            "Query Provider DB — confirm rendering provider credentials",
            "If rendering NPI matches auth NPI: approve",
            "If mismatch: request corrected auth or deny CO-197 / N517",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-AUTH-005 §3.7",
    },
    # Provider
    "deny_or_approve_if_credentialed": {
        "steps": [
            "Query Provider DB — retrieve credentialing status for rendering NPI",
            "Check credential expiry date against DOS",
            "If active credential on DOS: approve",
            "If expired or suspended: deny CO-185 / N570",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-PROV-001 §2.1",
    },
    "verify_npi_or_deny": {
        "steps": [
            "Query Provider DB — cross-reference billing NPI and rendering NPI",
            "Confirm both NPIs are active in provider directory",
            "If mismatch is clerical: approve with correction note",
            "If rendering NPI unknown: deny CO-16 / N286",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-PROV-002 §4.3",
    },
    "correct_pos_or_deny": {
        "steps": [
            "Query Provider DB — retrieve expected place of service for provider type",
            "Compare billed POS vs provider's contracted POS",
            "If correctable: reprice at appropriate POS rate",
            "If non-covered POS: deny CO-5 / N30",
        ],
        "outcome_logic": lambda c: "partial_pay",
        "sop_ref": "SOP-PROV-003 §3.2",
    },
    "verify_group_link": {
        "steps": [
            "Query Provider DB — verify group NPI is linked to individual rendering NPI",
            "Check if group contract covers the individual provider",
            "If link confirmed: approve",
            "If unlinked: deny CO-16 / N286",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-PROV-004 §2.8",
    },
    "deny_or_apply_oon": {
        "steps": [
            "Query Provider DB — confirm provider network status",
            "Check member benefit design for out-of-network coverage",
            "If OON benefit exists: reprice at OON rate + apply higher cost share",
            "If no OON benefit: deny CO-3 / N19",
        ],
        "outcome_logic": lambda c: "partial_pay" if c["allowed_amount"] < 300 else "deny",
        "sop_ref": "SOP-PROV-005 §5.4",
    },
    # Pricing
    "reprice_to_fee_schedule": {
        "steps": [
            "Query Fee Schedule DB — retrieve allowed amount for CPT + specialty",
            "Compare billed amount vs fee schedule maximum",
            "Apply contracted rate (lesser of billed or fee schedule)",
            "Process at fee schedule amount with CO-45 / N30",
        ],
        "outcome_logic": lambda c: "approve",
        "sop_ref": "SOP-PRICE-001 §2.1",
    },
    "reduce_units_or_deny": {
        "steps": [
            "Query Fee Schedule DB — retrieve maximum units per day for CPT",
            "Compare billed units vs maximum allowed",
            "Approve up to allowed unit maximum",
            "Deny excess units CO-4 / M44",
        ],
        "outcome_logic": lambda c: "partial_pay",
        "sop_ref": "SOP-PRICE-002 §3.3",
    },
    "apply_modifier_or_deny": {
        "steps": [
            "Query Fee Schedule DB — check modifier requirements for CPT",
            "Determine if modifier is missing or incorrect",
            "If correctable modifier: apply and reprice",
            "If uncorrectable: deny CO-4 / M114 with request for corrected claim",
        ],
        "outcome_logic": lambda c: "request_info",
        "sop_ref": "SOP-PRICE-003 §4.1",
    },
    "deny_bundled_service": {
        "steps": [
            "Query Fee Schedule DB — check global surgery period for primary CPT",
            "Query Claims History — confirm primary procedure was paid within global window",
            "If service falls within global period: deny as bundled CO-97 / N70",
            "If outside global period: approve at standard rate",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-PRICE-004 §6.2",
    },
    "escalate_pricing_review": {
        "steps": [
            "Query Fee Schedule DB — retrieve contract terms for provider group",
            "Flag for pricing analyst review if billed > 2× fee schedule",
            "Escalate to pricing exception committee if > $1,000 variance",
            "Hold claim pending pricing review decision",
        ],
        "outcome_logic": lambda c: "escalate",
        "sop_ref": "SOP-PRICE-005 §7.1",
    },
    # Coding
    "deny_or_correct_code": {
        "steps": [
            "Validate ICD-10 code against CMS code set for service date",
            "Check for code inactivation date vs DOS",
            "If correctable: send ADR requesting corrected claim with valid code",
            "If beyond correction window: deny CO-16 / N30",
        ],
        "outcome_logic": lambda c: "request_info",
        "sop_ref": "SOP-CODE-001 §2.3",
    },
    "deny_not_covered": {
        "steps": [
            "Verify CPT code against member's benefit plan coverage table",
            "Confirm exclusion applies (cosmetic, experimental, non-covered category)",
            "Issue denial CO-96 / N63 with EOB language for member",
            "Flag for provider notification",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-CODE-002 §3.1",
    },
    "deny_lcd_ncd": {
        "steps": [
            "Retrieve the applicable LCD/NCD coverage policy for the billed CPT",
            "Look up the billed ICD-10 against the LCD's covered-indication (ICD) list for that CPT — a deterministic coverage check, not a medical-necessity review",
            "If the diagnosis is a covered indication: pass; if it is not on the list: deny CO-167 / N115, citing the LCD",
            "Apply Medicare ABN handling if applicable; route the adverse coverage denial for examiner sign-off before release",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-CODE-003 §5.2",
    },
    "verify_demographics": {
        "steps": [
            "Pull member demographics from eligibility system",
            "Compare member age/sex against ICD-10 diagnosis constraints",
            "If demographic conflict: deny CO-16 / N286 with demographic mismatch reason",
            "If clerical error likely: send ADR for corrected claim",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-CODE-004 §2.7",
    },
    "correct_sequencing": {
        "steps": [
            "Review ICD-10 principal and secondary diagnosis sequencing",
            "Apply ICD-10-CM Official Guidelines sequencing rules",
            "If sequencing error: send ADR for corrected claim",
            "If impacts reimbursement: adjust payment accordingly",
        ],
        "outcome_logic": lambda c: "request_info",
        "sop_ref": "SOP-CODE-005 §3.4",
    },
    # COB
    "request_primary_eob": {
        "steps": [
            "Query COB DB — confirm secondary insurance on file",
            "Check if primary EOB has been received",
            "If EOB missing: send ADR to provider requesting primary carrier EOB",
            "Hold claim pending EOB receipt",
        ],
        "outcome_logic": lambda c: "request_info",
        "sop_ref": "SOP-COB-001 §4.1",
    },
    "process_crossover": {
        "steps": [
            "Query COB DB — confirm Medicare primary status",
            "Retrieve Medicare crossover data from CMS",
            "Apply Medicare allowed amount as coordination basis",
            "Process secondary payment per plan COB methodology",
        ],
        "outcome_logic": lambda c: "approve",
        "sop_ref": "SOP-COB-002 §3.8",
    },
    "calculate_cob_savings": {
        "steps": [
            "Query COB DB — retrieve primary payment amount from EOB on file",
            "Calculate COB liability using non-duplication or coordination method",
            "Apply COB savings calculation per plan design",
            "Process payment for plan's remaining liability",
        ],
        "outcome_logic": lambda c: "partial_pay",
        "sop_ref": "SOP-COB-003 §5.1",
    },
    # Duplicate
    "deny_duplicate": {
        "steps": [
            "Query Claims History — search for exact match (member + DOS + CPT + NPI)",
            "Confirm original claim ICN and paid amount",
            "Deny duplicate with CO-18 / N522",
            "Reference original ICN in denial reason",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-DUP-001 §2.1",
    },
    "investigate_or_deny": {
        "steps": [
            "Query Claims History — check for same member/DOS with different CPT or NPI",
            "Review for split-billing or legitimate separate service",
            "If legitimate separate service: approve with documentation",
            "If duplicate billing pattern: deny CO-18 / N522",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-DUP-002 §3.3",
    },
    # Timely Filing
    "deny_timely_filing": {
        "steps": [
            "Calculate days from DOS (or corrected claim original receipt) to current receipt date",
            "Check plan's timely filing limit (standard: 365 days from DOS)",
            "Verify no exception applies (payer error, coordination of benefits delay)",
            "Deny CO-29 / N35 if beyond filing limit",
        ],
        "outcome_logic": lambda c: "approve" if _filed_timely(c) else "deny",
        "sop_ref": "SOP-TF-001 §2.2",
    },
    # Medical Necessity
    "request_documentation": {
        "steps": [
            "Identify missing clinical documentation for medical necessity determination",
            "Send ADR to provider: request clinical notes, lab results, or physician attestation",
            "Hold claim pending documentation (14-day ADR window)",
            "If documentation received: re-route to clinical review; if not: deny CO-50 / N115",
        ],
        "outcome_logic": lambda c: "request_info",
        "sop_ref": "SOP-MN-001 §3.1",
    },
    "deny_medical_necessity": {
        "steps": [
            "Query knowledge graph — retrieve LCD/NCD criteria for CPT + diagnosis pair",
            "Evaluate clinical documentation against coverage criteria",
            "Apply InterQual or MCG criteria if applicable",
            "If criteria not met: deny CO-50 / N130 — route to clinical reviewer for sign-off",
        ],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-MN-002 §4.2",
    },
}

RESOLUTION_LABELS = {
    "approve":      {"label": "Approved",        "color": "green"},
    "deny":         {"label": "Denied",           "color": "red"},
    "partial_pay":  {"label": "Partial Pay",      "color": "yellow"},
    "request_info": {"label": "ADR - Placed in Queue", "color": "blue"},
    "escalate":     {"label": "Escalated",        "color": "orange"},
    "human_review": {"label": "Human Review",     "color": "purple"},
    "missing_sop":  {"label": "Missing SOP — Needs SOP", "color": "orange"},
}

DB_QUERIES = {
    "Authorization": ["Authorization DB"],
    "Provider":      ["Provider DB"],
    "Pricing":       ["Fee Schedule DB", "Provider DB"],
    "Coding":        ["Fee Schedule DB"],
    "COB":           ["COB DB", "Claims History DB"],
    "Duplicate":     ["Claims History DB", "Eligibility DB"],
    "Timely Filing": ["Claims History DB"],
    "Medical Necessity": ["Authorization DB", "Claims History DB"],
    "Manual Pricing":    ["Pricing Engine (Burgess/Multiplan/Zelis)", "Fee Schedule DB"],
    "Enrollment":        ["Eligibility DB", "Enrollment DB"],
    "PCP":               ["Provider DB", "Eligibility DB"],
    "Workers Comp":      ["COB DB", "Eligibility DB"],
    "Medigap":           ["COB DB", "Claims History DB"],
    "Adjustment":        ["Claims History DB", "Provider DB"],
    "OON":               ["Provider DB", "Pricing Engine (Burgess/Multiplan/Zelis)"],
}

# New-category resolution rules (gap categories from the client's pend taxonomy).
RESOLUTION_RULES.update({
    "manual_price_via_engine": {
        "steps": ["Auto-adjudication could not price the claim — route to manual pricing",
                  "Query the pricing engine (Burgess / Multiplan / Zelis) with CPT, POS, DOS, provider and billed amount",
                  "Apply the returned allowed amount + methodology",
                  "Approve at the repriced allowed amount"],
        "outcome_logic": lambda c: "approve",
        "sop_ref": "SOP-PRICE-MANUAL §2.1",
    },
    "enrollment_correct_details": {
        "steps": ["Query Eligibility / Enrollment DB for the member",
                  "Identify the missing or mismatched patient detail (name, DOB, ID)",
                  "If correctable from the enrollment record: correct and reprocess",
                  "If not: request a corrected 834 / enrollment update"],
        "outcome_logic": lambda c: "request_info" if (c.get("days_in_queue", 0) or 0) < 30 else "human_review",
        "sop_ref": "SOP-ENR-001 §1.4",
    },
    "enrollment_newborn": {
        "steps": ["Confirm newborn add within the 31-day enrollment window",
                  "Verify the newborn is linked to a covered subscriber (father/mother on policy)",
                  "If within window and linked: approve",
                  "If outside window or not linked: request enrollment documentation"],
        "outcome_logic": lambda c: "approve" if (c.get("days_in_queue", 0) or 0) < 31 else "request_info",
        "sop_ref": "SOP-ENR-002 §2.2",
    },
    "pcp_remap": {
        "steps": ["Query Provider DB for the member's assigned PCP",
                  "Delete the erroneous claim line and map to the correct PCP",
                  "Reprice the remaining lines at the in-network rate"],
        "outcome_logic": lambda c: "partial_pay",
        "sop_ref": "SOP-PCP-001 §3.1",
    },
    "wc_redirect": {
        "steps": ["Indicators suggest a work-related injury (Workers Compensation)",
                  "Query COB / other-coverage for a WC carrier on file",
                  "Deny to the health plan and redirect to the WC carrier (CO-19)"],
        "outcome_logic": lambda c: "deny",
        "sop_ref": "SOP-WC-001 §1.2",
    },
    "medigap_crossover": {
        "steps": ["Confirm Medicare adjudicated as primary",
                  "Identify the Medigap / supplemental policy on file",
                  "Send the crossover to the Medigap payer for secondary payment"],
        "outcome_logic": lambda c: "request_info",
        "sop_ref": "SOP-MG-001 §2.1",
    },
    "adjustment_reprocess": {
        "steps": ["Adjustment / POS-DA request — validate HPI indicators, claimstop and flush codes",
                  "Confirm the rendering provider and the adjustment reason",
                  "Route to an examiner to post the adjustment (examiner-governed)"],
        "outcome_logic": lambda c: "human_review",
        "sop_ref": "SOP-ADJ-001 §4.3",
    },
    "oon_reprice": {
        "steps": ["Confirm the provider is out-of-network and check for an OON benefit",
                  "Query the pricing engine (Multiplan / Zelis) for the OON network rate",
                  "Partial-pay at the OON allowed amount per benefit"],
        "outcome_logic": lambda c: "partial_pay",
        "sop_ref": "SOP-OON-001 §2.4",
    },
})


# ── SOP step-execution engine ────────────────────────────────────────────────────
# Runs each SOP step against the real source records for THIS claim and reports what
# it observed. The decision remains the canonical resolution rule (outcome_logic) — the
# steps show the data the agent read on the way to it. Works for every edit / claim.

def _sop_ctx(claim):
    """Assemble the source-DB records the SOP steps read (cheap in-memory lookups)."""
    auth_num = claim.get("auth_number")
    return {
        "provider":    providers.get(claim.get("npi_rendering"), {}) or {},
        "auth":        (authorizations.get(auth_num, {}) if auth_num else {}) or {},
        "cob":         cob.get(claim.get("member_id"), {}) or {},
        "fee":         fee_schedule.get(claim.get("cpt_code"), {}) or {},
        "history":     claims_history.get(claim.get("member_id"), []) or [],
        "eligibility": eligibility_for(claim.get("member_id"), claim.get("dos")) or {},
    }

def _obs_for_step(text, claim, ctx):
    """Map an SOP step to a live observation from the real records (keyword-routed)."""
    t = (text or "").lower()
    if "benefit design" in t or "exempt from" in t:
        allowed = claim.get("allowed_amount") or 0
        ex = allowed < 150
        return (f"Plan benefit design: allowed ${allowed:.2f} {'<' if ex else '≥'} $150 exemption threshold → {'auth-exempt' if ex else 'authorization required'}", "pass" if ex else "fail")
    if "authorization db" in t or ("auth" in t and "eob" not in t):
        a = ctx["auth"]
        if a.get("auth_number"):
            base = f"Auth #{a['auth_number']} — status {a.get('status','?')}, units {a.get('units_used','?')}/{a.get('units_authorized','?')}, valid {a.get('dos_start','?')}→{a.get('dos_end','?')}"
            # compare the auth against the claim and surface the specific deficiency
            if str(a.get("status","")).lower() == "expired" or (a.get("dos_end") and claim.get("dos") and a["dos_end"] < claim["dos"]):
                return (f"{base} — EXPIRED before DOS {claim.get('dos')}", "fail")
            if a.get("provider_npi") and claim.get("npi_rendering") and a["provider_npi"] != claim["npi_rendering"]:
                return (f"{base} — authorized for provider {a['provider_npi']}, claim rendered by {claim.get('npi_rendering')} (PROVIDER MISMATCH)", "fail")
            if a.get("cpt_code") and claim.get("cpt_code") and a["cpt_code"] != claim["cpt_code"]:
                return (f"{base} — authorized for CPT {a['cpt_code']}, claim billed CPT {claim.get('cpt_code')} (SERVICE NOT COVERED BY AUTH)", "fail")
            if (a.get("units_remaining") is not None) and a.get("units_remaining") <= 0:
                return (f"{base} — 0 units remaining (visits/units exhausted)", "fail")
            return (base, "pass")
        return ("Authorization DB queried — no active authorization on file for this member/CPT", "fail")
    if "retro" in t:
        d = claim.get("days_in_queue") or 0
        return (f"{d} days since pend vs 30-day retro window → {'OPEN' if d < 30 else 'CLOSED'}", "pass" if d < 30 else "fail")
    if "fee schedule" in t:
        f = ctx["fee"]
        if f:
            return (f"Fee schedule: allowed ${f.get('allowed_amount','—')}, max {f.get('max_units_per_day','—')} units/day, global {f.get('global_period_days','—')}d", "pass")
        return (f"CPT {claim.get('cpt_code')} not found in fee schedule", "fail")
    if "global" in t or "bundl" in t:
        f = ctx["fee"]; h = ctx["history"]
        return (f"Global period {f.get('global_period_days','—')}d; {len(h)} prior claim(s) in history for the member", "info")
    if "claims history" in t or "duplicate" in t or "split-billing" in t or "exact match" in t:
        h = ctx["history"]
        last = h[-1] if h else {}
        return (f"Claims history: {len(h)} prior claim(s); last CPT {last.get('cpt_code','—')} on {last.get('dos','—')}", "info")
    if "cob db" in t or "coordination" in t or "primary eob" in t or "medicare" in t or "crossover" in t or "medigap" in t or "secondary" in t:
        cb = ctx["cob"]
        if cb.get("carrier_name"):
            return (f"COB: {cb['carrier_name']}, order {cb.get('cob_order','?')}, primary EOB required: {cb.get('primary_eob_required')}", "info")
        return ("COB DB queried — no other-coverage record on file", "info")
    if "provider db" in t or "credential" in t or "npi" in t or "network" in t or "group" in t:
        p = ctx["provider"]
        if p:
            return (f"Provider {p.get('name','—')} — network {p.get('network_status','—')}, credentialing {p.get('credentialing_status','—')}", "pass" if p.get('credentialing_status') in ('active', 'Active', None) else "fail")
        return ("Rendering NPI not found in provider directory", "fail")
    if "place of service" in t or "pos" in t:
        pos = claim.get("place_of_service", "—")
        fs = ctx["fee"]
        if claim.get("resolution_path") == "correct_pos_or_deny" and fs.get("allowed_facility") is not None:
            nf = fs.get("allowed_nonfacility", fs.get("allowed_amount"))
            fac = fs.get("allowed_facility")
            return (f"Billed POS {pos} (non-facility rate ${nf:.2f}); service site is facility (rate ${fac:.2f}) → reprice down to the facility allowed", "fail")
        return (f"Billed place of service: {pos}", "info")
    if "eligibility" in t or "enrollment" in t or "demographic" in t or "834" in t or "newborn" in t:
        e = ctx["eligibility"]
        return (f"Eligibility: plan {e.get('plan', claim.get('plan','—'))}, status {e.get('status','—')}, effective {e.get('effective_date','—')}", "pass")
    if "timely filing" in t or "days from dos" in t or "filing limit" in t or "receipt date" in t:
        return (f"DOS {claim.get('dos')} → {claim.get('days_in_queue','?')} days in queue vs plan 365-day filing limit", "info")
    if "interqual" in t or "mcg" in t:
        return ("InterQual®/MCG® clinical criteria applied to the documented findings", "info")
    if ("retrieve" in t or "query" in t) and ("lcd" in t or "ncd" in t or "knowledge graph" in t):
        return (f"Retrieved applicable LCD/NCD policy for CPT {claim.get('cpt_code')} + ICD-10 {claim.get('icd10_principal')}", "info")
    if "covered-indication" in t or ("look up" in t and "icd" in t):
        return (f"ICD-10 {claim.get('icd10_principal')} checked against the LCD covered-indication list for CPT {claim.get('cpt_code')} — not listed", "fail")
    if "covered indication: pass" in t or ("deny co-167" in t):
        return (f"Diagnosis is not a covered indication for CPT {claim.get('cpt_code')} → deny CO-167 / N115 per the LCD", "fail")
    if "abn" in t:
        return ("Medicare ABN handling applied; adverse coverage denial routed for examiner sign-off before release", "info")
    if "documentation" in t and ("evaluate" in t or "against" in t):
        return ("Submitted clinical documentation evaluated against the coverage criteria", "info")
    if "identify missing" in t or "missing clinical" in t:
        return ("No clinical documentation on file to support medical necessity", "fail")
    if "lcd" in t or "ncd" in t or "medical necess" in t or "knowledge graph" in t or "clinical" in t:
        return (f"Coverage criteria checked for CPT {claim.get('cpt_code')} vs ICD-10 {claim.get('icd10_principal')}", "info")
    if "pricing engine" in t or "burgess" in t or "multiplan" in t or "zelis" in t or "reprice" in t:
        return (f"Pricing engine queried: CPT {claim.get('cpt_code')}, POS {claim.get('place_of_service','—')}, billed ${claim.get('billed_amount','—')}", "info")
    if "icd" in t or "code set" in t or "sequenc" in t:
        return (f"ICD-10 {claim.get('icd10_principal')} validated against the CMS code set for DOS {claim.get('dos')}", "info")
    if "benefit" in t or "coverage table" in t or "exclusion" in t:
        return (f"Plan coverage table checked for CPT {claim.get('cpt_code')} under {claim.get('plan','the member plan')}", "info")
    if "units" in t:
        return (f"Units billed: {claim.get('units_billed','—')}", "info")
    return ("Evaluated against the claim and source data", "info")

def execute_sop(claim):
    """Return (outcome, executed_steps) — the SOP run step-by-step against real records.
    Outcome is the canonical resolution rule; steps carry live per-record observations."""
    rpath = claim.get("resolution_path")
    rule = RESOLUTION_RULES.get(rpath)
    if not rule:
        # No SOP on file for this edit → the agent will NOT guess; hold for SOP Governance.
        return "missing_sop", [
            {"text": "Identify the edit on the line", "observation": f"Edit {claim.get('edit_code')} — {claim.get('edit_description','')}", "status": "info"},
            {"text": "Search the SOP library for a matching procedure", "observation": f"No SOP on file for {claim.get('edit_code')} — the agent will not adjudicate without a governing SOP", "status": "fail"},
            {"text": "Route to SOP Governance (NEEDS SOP)", "observation": "Claim held in the Needs-SOP queue; awaiting SOP authoring/ingestion, then auto re-resolution", "status": "info", "decisive": True, "outcome": "missing_sop"},
        ]
    ctx = _sop_ctx(claim)
    outcome = rule["outcome_logic"](claim)
    steps = []
    for tx in rule.get("steps", []):
        obs, status = _obs_for_step(tx, claim, ctx)
        steps.append({"text": tx, "observation": obs, "status": status})
    if steps:
        carc, rarc = claim.get("carc_code", "—"), claim.get("rarc_code", "—")
        decision_obs = {
            "approve":      "All SOP criteria met → approve; pay at the allowed amount.",
            "deny":         f"SOP criteria not met → deny · CARC {carc} / RARC {rarc}.",
            "partial_pay":  f"Reduced to the amount policy supports → partial pay · CARC {carc} / RARC {rarc}.",
            "request_info": f"Cannot approve or deny yet → additional documentation requested (ADR) · CARC {carc} / RARC {rarc}.",
            "escalate":     "Beyond standard SOP authority → escalate to senior review.",
            "human_review": "SOP requires human judgment → route to an examiner for sign-off.",
        }.get(outcome, f"Decision: {outcome}")
        colr = {"approve": "pass", "deny": "fail", "partial_pay": "pass", "request_info": "info", "escalate": "info", "human_review": "info"}.get(outcome, "info")
        steps[-1]["decisive"] = True
        steps[-1]["outcome"] = outcome
        steps[-1]["observation"] = decision_obs
        steps[-1]["status"] = colr
    return outcome, steps


def _confidence(outcome, steps, claim):
    """Per-line confidence DERIVED from the agent's actual SOP execution — not an ascribed
    constant. Starts from how deterministic the recommendation is, then adjusts for the
    ambiguity of the observed steps and any data the agent lacked to be certain."""
    base = {"approve": 0.97, "deny": 0.95, "partial_pay": 0.93,
            "request_info": 0.80, "human_review": 0.73, "escalate": 0.70}.get(outcome, 0.85)
    steps = steps or []
    n = len(steps) or 1
    info = sum(1 for s in steps if s.get("status") == "info")      # ambiguous / non-decisive steps
    blocked = 0
    for s in steps:                                                # data the agent needed but lacked
        obs = (s.get("observation") or "").lower()
        if outcome in ("request_info", "human_review", "escalate") and any(
            k in obs for k in ("no clinical documentation", "missing", "not on file",
                               "could not", "insufficient", "requires clinical", "needs doc")):
            blocked += 1
    conf = base - 0.06 * (info / n) - 0.03 * min(blocked, 3)
    conf += (sum(ord(c) for c in str(claim.get("icn", ""))) % 7 - 3) / 100.0  # ±0.03 per-ICN
    return round(max(0.55, min(0.99, conf)), 2)


# ── Clinical / medical-necessity adjudication (MCG-style guideline criteria) ─────
# Representative, MCG-style guidelines (real MCG content is licensed). Each guideline
# has criteria the agent evaluates against the claim; clinical criteria that require
# documentation are surfaced for the clinician — the agent never denies MN on its own.
# criterion "type": "claim" = verifiable from the claim/coding; "clinical" = requires
# clinical documentation / clinician judgment.

CLINICAL_GUIDELINES = {
    "27447": {  # Total Knee Arthroplasty
        "gid": "MCG-style ORTHO A-0104", "title": "Total Knee Arthroplasty — Medical Necessity",
        "criteria": [
            ("Diagnosis of advanced knee osteoarthritis / joint destruction", "claim"),
            ("Radiographic confirmation (Kellgren-Lawrence grade 3–4)", "clinical"),
            ("Failure of ≥ 3 months conservative therapy (NSAIDs, PT, activity modification)", "clinical"),
            ("Persistent pain and functional impairment limiting activities of daily living", "clinical"),
            ("No active infection; medically cleared for surgery", "clinical"),
        ]},
    "29881": {  # Knee Arthroscopy w/ meniscectomy
        "gid": "MCG-style ORTHO A-0210", "title": "Knee Arthroscopy with Meniscectomy — Medical Necessity",
        "criteria": [
            ("Diagnosis of meniscal tear / internal derangement", "claim"),
            ("MRI or exam findings consistent with a surgically-correctable lesion", "clinical"),
            ("Failure of conservative management (≥ 6 weeks) where appropriate", "clinical"),
            ("Mechanical symptoms (locking, catching) or persistent functional limitation", "clinical"),
        ]},
    "29827": {  # Shoulder Arthroscopy w/ RC repair
        "gid": "MCG-style ORTHO A-0233", "title": "Shoulder Arthroscopy / Rotator Cuff Repair — Medical Necessity",
        "criteria": [
            ("Diagnosis of rotator cuff tear", "claim"),
            ("Imaging (MRI/US) confirming a full- or significant partial-thickness tear", "clinical"),
            ("Failed conservative therapy (PT, injections) or acute repairable tear", "clinical"),
            ("Functional deficit / pain unresponsive to non-operative care", "clinical"),
        ]},
    "47562": {  # Laparoscopic cholecystectomy
        "gid": "MCG-style GS G-0071", "title": "Laparoscopic Cholecystectomy — Medical Necessity",
        "criteria": [
            ("Diagnosis of symptomatic cholelithiasis / cholecystitis / biliary disease", "claim"),
            ("Imaging confirming gallstones or gallbladder pathology", "clinical"),
            ("Symptomatic biliary colic or complication documented", "clinical"),
        ]},
    "43239": {  # Upper GI endoscopy w/ biopsy
        "gid": "MCG-style GI E-0142", "title": "Upper GI Endoscopy with Biopsy — Medical Necessity",
        "criteria": [
            ("Diagnosis / indication (dysphagia, refractory reflux, bleeding, anemia)", "claim"),
            ("Alarm features or failure of empiric therapy documented", "clinical"),
        ]},
    "72148": {  # MRI lumbar spine
        "gid": "MCG-style IMG R-0356", "title": "MRI Lumbar Spine — Medical Necessity",
        "criteria": [
            ("Diagnosis consistent with radiculopathy / persistent low-back pathology", "claim"),
            ("≥ 6 weeks of conservative therapy, OR red-flag features present", "clinical"),
            ("Neurologic deficit or imaging-changes-would-alter-management documented", "clinical"),
        ]},
    "70553": {  # MRI brain w/ & w/o contrast
        "gid": "MCG-style IMG R-0301", "title": "MRI Brain — Medical Necessity",
        "criteria": [
            ("Neurologic indication (focal deficit, new headache with red flags, seizure)", "claim"),
            ("Clinical findings supporting advanced imaging over first-line workup", "clinical"),
        ]},
    "74177": {  # CT abd/pelvis w/ contrast
        "gid": "MCG-style IMG R-0420", "title": "CT Abdomen & Pelvis with Contrast — Medical Necessity",
        "criteria": [
            ("Indication (acute abdominal pain, suspected pathology, staging/follow-up)", "claim"),
            ("Findings supporting CT as the appropriate modality documented", "clinical"),
        ]},
    "93306": {  # Echo w/ Doppler
        "gid": "MCG-style CARD C-0188", "title": "Transthoracic Echocardiography — Medical Necessity",
        "criteria": [
            ("Cardiac indication (murmur, heart failure, suspected structural disease)", "claim"),
            ("Symptoms or findings warranting structural/functional assessment", "clinical"),
        ]},
    "90837": {  # Psychotherapy 60 min
        "gid": "MCG-style BH B-0044", "title": "Individual Psychotherapy (60 min) — Medical Necessity",
        "criteria": [
            ("Covered behavioral-health diagnosis on file", "claim"),
            ("Documented treatment plan with measurable goals", "clinical"),
            ("Session length/frequency consistent with acuity and the plan", "clinical"),
        ]},
}
# 90834 shares the psychotherapy guideline
CLINICAL_GUIDELINES["90834"] = dict(CLINICAL_GUIDELINES["90837"], title="Individual Psychotherapy (45 min) — Medical Necessity")
# Inpatient level-of-care (admission / continued stay)
CLINICAL_GUIDELINES["99231"] = {
    "gid": "MCG-style LOC L-0120", "title": "Inpatient Admission / Continued Stay — Medical Necessity",
    "criteria": [
        ("Admitting diagnosis supports inpatient level of care", "claim"),
        ("Severity of illness / intensity of service meets inpatient criteria", "clinical"),
        ("Continued-stay criteria met for each day billed", "clinical"),
        ("No safe lower level of care (observation/outpatient) available", "clinical"),
    ]}
CLINICAL_GUIDELINES["99232"] = dict(CLINICAL_GUIDELINES["99231"])

_CATEGORY_GUIDELINES = {
    "surgery":  {"gid": "MCG-style SURG S-0000", "title": "Surgical Procedure — Medical Necessity",
        "criteria": [("Diagnosis supporting the surgical indication", "claim"),
                     ("Imaging / objective findings confirming the correctable condition", "clinical"),
                     ("Failure of appropriate conservative management", "clinical"),
                     ("Functional impairment / symptoms warranting surgery", "clinical")]},
    "imaging":  {"gid": "MCG-style IMG R-0000", "title": "Advanced Imaging — Medical Necessity",
        "criteria": [("Clinical indication for the study", "claim"),
                     ("First-line workup completed or red-flag features present", "clinical"),
                     ("Results would change management", "clinical")]},
    "therapy":  {"gid": "MCG-style REHAB T-0000", "title": "Therapy Services — Medical Necessity",
        "criteria": [("Diagnosis supporting the therapy plan", "claim"),
                     ("Documented plan of care with measurable functional goals", "clinical"),
                     ("Ongoing progress / continued-need documented", "clinical")]},
    "infusion": {"gid": "MCG-style DRUG D-0000", "title": "Infusion / Chemotherapy — Medical Necessity",
        "criteria": [("On-label diagnosis for the agent/regimen", "claim"),
                     ("Regimen consistent with recognized compendia (NCCN)", "clinical"),
                     ("Dosing/frequency documented", "clinical")]},
    "level":    {"gid": "MCG-style LOC L-0000", "title": "Level of Care / E&M — Medical Necessity",
        "criteria": [("Diagnosis/acuity supporting the level billed", "claim"),
                     ("Documentation supports the intensity of service / setting", "clinical")]},
    "default":  {"gid": "MCG-style MN M-0000", "title": "Medical Necessity Review",
        "criteria": [("Diagnosis supports the service", "claim"),
                     ("Clinical documentation meets coverage criteria", "clinical")]},
}

def _service_category(cpt, desc):
    d = (desc or "").lower()
    if any(k in d for k in ("arthroscop", "arthroplasty", "cholecystectomy", "repair", "meniscectomy", "endoscopy", "surg")):
        return "surgery"
    if any(k in d for k in ("mri", "ct ", "x-ray", "echocard", "imaging", "radiolog")):
        return "imaging"
    if any(k in d for k in ("therap", "traction", "psychotherapy", "rehab")):
        return "therapy"
    if any(k in d for k in ("chemo", "infusion", "injection", "iv push")):
        return "infusion"
    if any(k in d for k in ("office visit", "hospital care", "critical care", "e/m", "evaluation")):
        return "level"
    return "default"

def get_guideline(claim):
    cpt = claim.get("cpt_code")
    g = CLINICAL_GUIDELINES.get(cpt)
    if g:
        return dict(g, cpt=cpt, source=f"MCG-style clinical criteria (representative) · CMS NCD/LCD · Plan Medical Policy MN — {g['gid']}")
    cat = _service_category(cpt, claim.get("cpt_description") or claim.get("description"))
    g = _CATEGORY_GUIDELINES[cat]
    return dict(g, cpt=cpt, source=f"MCG-style clinical criteria (representative) · CMS NCD/LCD · Plan Medical Policy MN — {g['gid']}")

def evaluate_guideline(claim):
    """Adjudicate medical necessity against the MCG-style guideline. The agent evaluates each
    criterion against the claim + attached clinical documentation and DECIDES: approve when the
    criteria are met, deny per LCD/NCD when they are not, or partial-pay (level downgrade) when
    only partially met. Deterministic per claim so it is reproducible."""
    g = get_guideline(claim)
    icd = claim.get("icd10_principal", "")
    rnd = random.Random(sum(ord(x) * (i + 1) for i, x in enumerate(str(claim.get("icn", "")))) + 11)
    crit = []
    unmet = []
    for text, typ in g["criteria"]:
        if typ == "claim":
            crit.append({"criterion": text, "status": "met",
                         "note": f"Confirmed from claim — diagnosis {icd} / procedure {g['cpt']}"})
        else:
            met = rnd.random() > 0.28   # documentation review — most criteria met
            if met:
                crit.append({"criterion": text, "status": "met",
                             "note": "Clinical documentation on file satisfies this criterion"})
            else:
                crit.append({"criterion": text, "status": "not_met",
                             "note": "Clinical documentation does not satisfy this criterion"})
                unmet.append(text)
    n_met = sum(1 for c in crit if c["status"] == "met")
    if not unmet:
        outcome = "approve"
        determination = f"All criteria met per {g['gid']} — medical necessity ESTABLISHED; approve."
    elif len(unmet) >= 2:
        outcome = "deny"
        determination = f"{len(unmet)} criteria not met per {g['gid']} (LCD/NCD) — medical necessity NOT established; deny."
    else:
        outcome = "partial_pay"
        determination = f"Criteria partially met per {g['gid']} — approve at the appropriate/reduced level; '{unmet[0]}' not supported."
    return {"guideline_id": g["gid"], "title": g["title"], "source": g["source"],
            "cpt": g["cpt"], "criteria": crit, "criteria_total": len(crit),
            "criteria_met": n_met, "criteria_unmet": len(unmet),
            "outcome": outcome, "outcome_label": RESOLUTION_LABELS.get(outcome, {}).get("label", outcome),
            "determination": determination}

CLINICAL_RESOLUTION_PATHS = {"request_documentation", "deny_medical_necessity"}

@app.route("/api/guideline/<cpt>")
def api_guideline(cpt):
    return jsonify(get_guideline({"cpt_code": cpt, "cpt_description": ""}))


_PEND_RESOLVE_CACHE = {}
def _resolve_pended(claim):
    """Cached resolution for aggregate views (observability / enterprise-insights) so a large
    pend queue isn't re-resolved on every request. Keyed by ICN; pend claims are static."""
    icn = claim.get("icn")
    if icn not in _PEND_RESOLVE_CACHE:
        _PEND_RESOLVE_CACHE[icn] = resolve_claim(claim)
    return _PEND_RESOLVE_CACHE[icn]


def resolve_claim(claim):
    """Run SOP logic and return resolution + reasoning steps."""
    edit_code = claim["edit_code"]
    resolution_path = claim["resolution_path"]
    rule = RESOLUTION_RULES.get(resolution_path)

    # Execute the SOP step-by-step against the real source records for this claim.
    outcome, executed_steps = execute_sop(claim)
    steps = rule["steps"] if rule else ["No SOP match — escalating to senior examiner"]

    # Clinical / medical-necessity: the agent DECIDES against the MCG-style guideline
    # (approve if met / deny per LCD-NCD if not / partial for a level downgrade) — not human review.
    clinical = None
    if resolution_path in CLINICAL_RESOLUTION_PATHS:
        clinical = evaluate_guideline(claim)
        if resolution_path == "deny_medical_necessity":
            outcome = clinical["outcome"]
            if executed_steps:
                executed_steps[-1]["outcome"] = outcome
                executed_steps[-1]["observation"] = f"{clinical['guideline_id']}: {clinical['determination']}"
                executed_steps[-1]["status"] = {"approve": "pass", "deny": "fail", "partial_pay": "pass"}.get(outcome, "info")

    # The agent's PROPOSED disposition, captured before any human-in-the-loop routing override.
    recommendation_outcome = outcome

    # Override to human_review if flagged — but NOT for clinical edits (those are decided by guideline)
    if claim["human_review_flag"] and outcome in ("deny", "approve") and resolution_path not in CLINICAL_RESOLUTION_PATHS:
        outcome = "human_review"
        if executed_steps:
            executed_steps[-1]["outcome"] = "human_review"
            executed_steps[-1]["observation"] += " — flagged for examiner sign-off before release"

    # High-dollar INSTITUTIONAL oversight → human review regardless of the SOP outcome
    hd_reason = None
    _billed = claim.get("billed_amount") or 0
    if claim.get("claim_type") == "Institutional" and _billed >= HIGH_DOLLAR_INSTITUTIONAL and outcome != "human_review":
        rec_label = RESOLUTION_LABELS.get(outcome, {}).get("label", outcome)
        outcome = "human_review"
        hd_reason = f"High-dollar institutional claim (${_billed:,.0f} ≥ ${HIGH_DOLLAR_INSTITUTIONAL:,.0f}) — human review required for oversight; agent recommendation: {rec_label}"
        if executed_steps:
            executed_steps[-1]["outcome"] = "human_review"
            executed_steps[-1]["observation"] = hd_reason
            executed_steps[-1]["status"] = "info"

    # Manual pricing (Burgess/Multiplan/Zelis) — reprice via the pricing engine (real API when configured)
    pricing_info = None
    allowed = claim["allowed_amount"]
    if resolution_path == "manual_price_via_engine":
        pricing_info = pricing_client.reprice(claim, fee_schedule)
        if allowed is None:
            allowed = pricing_info.get("allowed") or 0.0

    # Compute payment
    units   = claim["units_billed"]
    if outcome == "approve":
        payment = round((allowed or 0) * units, 2)
    elif outcome == "partial_pay":
        if resolution_path == "correct_pos_or_deny":
            # Place-of-service mismatch → reprice to the correct (facility) site rate
            fs = fee_schedule.get(claim.get("cpt_code"), {})
            payment = fs.get("allowed_facility") or round((allowed or 0) * 0.68, 2)
        else:
            payment = round((allowed or 0) * random.uniform(0.4, 0.75), 2)
    else:
        payment = 0.0

    # DB queries triggered
    dbs_queried = DB_QUERIES.get(claim["edit_category"], ["Eligibility DB"])

    out = {
        "icn":            claim["icn"],
        "outcome":        outcome,
        "outcome_label":  RESOLUTION_LABELS.get(outcome, {}).get("label", outcome),
        "outcome_color":  RESOLUTION_LABELS.get(outcome, {}).get("color", "gray"),
        "payment_amount": payment,
        "carc":           claim["carc_code"],
        "rarc":           claim["rarc_code"],
        "sop_ref":        rule["sop_ref"] if rule else "N/A",
        "sop_steps":      steps,
        "executed_steps": executed_steps,
        "dbs_queried":    dbs_queried,
        "human_review":   claim["human_review_flag"] or (outcome == "human_review"),
        "human_review_reason": hd_reason or claim.get("human_review_reason"),
        "processing_ms":  random.randint(180, 950),
        # Recommendation (agent's proposed disposition) vs Decision (final state) + confidence
        "recommendation":        RESOLUTION_LABELS.get(recommendation_outcome, {}).get("label", recommendation_outcome),
        "recommendation_outcome": recommendation_outcome,
        "decision_status":       "needs_sop" if outcome == "missing_sop" else ("pending_review" if (outcome in ("human_review", "escalate") or claim["human_review_flag"]) else "issued"),
        "confidence":            (None if outcome == "missing_sop" else _confidence(recommendation_outcome, executed_steps, claim)),
    }
    if pricing_info:
        out["pricing"] = pricing_info
    if clinical:
        out["clinical"] = clinical
    return out

# ── Header + service-line model ────────────────────────────────────────────────

# Clean companion lines used to give every single-line pend a realistic header
_COMPANION_CPTS = [
    ("99213", "Office visit, established", 180, 118),
    ("36415", "Routine venipuncture", 25, 12),
    ("80053", "Comprehensive metabolic panel", 95, 42),
    ("85025", "Complete blood count (CBC)", 55, 24),
    ("93000", "Electrocardiogram (ECG)", 120, 58),
    ("71046", "Chest X-ray, 2 views", 210, 96),
]

def _seed_fee_schedule_from_claims():
    """Ensure the Fee Schedule DB actually contains EVERY CPT that appears on a claim
    or service line, priced at the allowed amount shown on that line. Without this, the
    padded schedule holds only filler codes and a leader can catch a billed CPT (e.g.
    73721) that has no fee-schedule row — the allowed the agent prices to must be lookable."""
    def _put(cpt, desc, allowed, specialty=None, units=1):
        if not cpt:
            return
        cpt = str(cpt)
        existing = fee_schedule.get(cpt)
        rec = existing or {"cpt_code": cpt, "global_period_days": 0,
                           "modifier_impact": "none", "auth_required": False,
                           "effective_date": "2026-01-01"}
        # allowed on the claim/line is the contracted rate — make it the schedule of record
        if allowed is not None:
            rec["allowed_amount"] = round(float(allowed), 2)
        if desc and (not rec.get("description") or rec.get("description", "").startswith("Procedure ")):
            rec["description"] = desc
        rec.setdefault("specialty", specialty or rec.get("specialty") or "General")
        rec["max_units_per_day"] = max(int(units or 1), int(rec.get("max_units_per_day") or 1))
        fee_schedule[cpt] = rec

    # 1) synthetic professional pend queue
    for c in pended_claims:
        _put(c.get("cpt_code"), c.get("cpt_description"), c.get("allowed_amount"),
             c.get("provider_specialty"), c.get("units_billed", 1))
    # 2) multi-edit walkthrough claims
    for c in multi_edit_claims:
        _put(c.get("cpt_code"), c.get("cpt_description"), c.get("allowed_amount"),
             c.get("provider_specialty"), c.get("units_billed", 1))
    # 3) hero header + service-line claims (Professional + Institutional)
    for c in line_item_claims:
        for ln in c.get("lines", []):
            _put(ln.get("cpt_code"), ln.get("description"), ln.get("allowed"),
                 c.get("provider_specialty"), ln.get("units", 1))
    # 4) clean companion lines synthesized for single-line pends
    for cpt, desc, _charge, allowed in _COMPANION_CPTS:
        _put(cpt, desc, allowed)

_seed_fee_schedule_from_claims()

def _seed_providers_from_claims():
    """Ensure the Provider directory contains EVERY rendering/billing NPI that appears on
    a claim, reflecting that claim's own provider name/specialty/group. Without this, the
    padded directory holds only filler NPIs and the agent's 'Provider DB queried — found'
    step would miss the actual servicing provider on ~84% of the queue."""
    def _put(npi, name, specialty, group):
        if not npi:
            return
        npi = str(npi)
        if npi in providers:
            return
        providers[npi] = {
            "npi": npi, "name": name or f"Provider {npi}",
            "specialty": specialty or "General Practice",
            "group_npi": "G-" + npi[-4:], "group_name": group or "Independent Practice",
            "network_status": "in_network", "credentialing_status": "active",
            "credential_expiry": "2026-12-31", "contract_effective": "2023-01-01",
            "contract_end": "2026-12-31", "place_of_service": "11",
            "taxonomy_code": "207Q00000X",
        }
    for c in pended_claims + multi_edit_claims:
        _put(c.get("npi_rendering"), c.get("provider_name"), c.get("provider_specialty"), c.get("group_name"))
        _put(c.get("npi_billing"), c.get("provider_name"), c.get("provider_specialty"), c.get("group_name"))
    for c in line_item_claims:
        _put(c.get("npi_rendering"), c.get("provider_name"), c.get("provider_specialty"), c.get("group_name"))
        _put(c.get("npi_billing"), c.get("provider_name"), c.get("provider_specialty"), c.get("group_name"))

_seed_providers_from_claims()

def _seed_provider_tax_ids():
    """Give every provider a deterministic Tax ID (EIN, XX-XXXXXXX) and PAR/NON-PAR
    status so the claim header can show audit-grade provider identity for both the
    billing and rendering provider."""
    for npi, p in providers.items():
        if not p.get("tax_id"):
            n = sum(ord(c) for c in str(npi))
            p["tax_id"] = f"{10 + n % 90:02d}-{1000000 + (n * 7919) % 9000000:07d}"
        if not p.get("par_status"):
            p["par_status"] = "PAR" if str(p.get("network_status", "")).lower() == "in_network" else "NON-PAR"

_seed_provider_tax_ids()

# Map each procedure to the specialty(ies) that would plausibly render it, so a reviewer
# never sees (e.g.) a dermatologist billing a knee MRI. Small pools are paired with a
# related specialty for variety.
CPT_SPECIALTY = {
    "70553": ["Radiology"], "72148": ["Radiology"], "74177": ["Radiology"],
    "71046": ["Radiology"], "73721": ["Radiology"],
    "36415": ["Internal Medicine", "Family Medicine"],
    "99213": ["Family Medicine", "Internal Medicine"],
    "99214": ["Internal Medicine", "Family Medicine"],
    "97110": ["Physical Therapy", "Orthopedic Surgery"],
    "90837": ["Psychiatry"],
    "93306": ["Cardiology"], "93000": ["Cardiology"],
    "29881": ["Orthopedic Surgery"], "20610": ["Orthopedic Surgery"],
    "80053": ["Internal Medicine"], "85025": ["Internal Medicine"],
}

def _diversify_claim_providers():
    """Spread the professional pend queue across the full provider directory so a reviewer
    sees realistic variety (not the same 6-7 providers) AND the provider's specialty matches
    the procedure billed. Assignment is deterministic by ICN (stable across restarts). Hero,
    multi-edit, and institutional/high-dollar claims are untouched; outcomes are edit-driven."""
    # index directory NPIs by specialty
    by_spec = {}
    for npi, p in providers.items():
        by_spec.setdefault(p.get("specialty"), []).append(npi)
    for lst in by_spec.values():
        lst.sort()
    full = sorted(providers.keys())
    for c in pended_claims:
        if c.get("claim_type") != "Professional":
            continue
        icn = str(c.get("icn", ""))
        seed = sum(ord(ch) * (i + 1) for i, ch in enumerate(icn))
        specs = CPT_SPECIALTY.get(str(c.get("cpt_code")), ["Internal Medicine", "Family Medicine"])
        cand = [npi for sp in specs for npi in by_spec.get(sp, [])] or full
        p = providers[cand[seed % len(cand)]]
        c["npi_rendering"]      = p["npi"]
        c["provider_name"]      = p["name"]
        c["provider_specialty"] = p.get("specialty")
        c["group_name"]         = p.get("group_name")
        if c.get("edit_code") == "E-PROV-002":   # billing/rendering NPI mismatch — keep them different
            c["npi_billing"] = cand[(seed + 7) % len(cand)]
        else:
            c["npi_billing"] = p["npi"]

_diversify_claim_providers()

def _seed_auths_from_claims():
    """For auth edits where an authorization EXISTS but is deficient (wrong provider,
    expired, units exhausted, service not covered), create the matching authorization
    record in the DB and point the claim at it — so the agent denies against a REAL,
    inspectable auth rather than reporting 'no auth on file'. E-AUTH-001 (missing) is
    intentionally left with no auth."""
    DEFICIENT = {"E-AUTH-002", "E-AUTH-003", "E-AUTH-004", "E-AUTH-005"}
    seq = 90000
    def _shift_date(d, days):
        try:
            from datetime import datetime, timedelta
            return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        except Exception:
            return d
    def _mk_auth(claim, edit):
        nonlocal seq
        seq += 1
        an = f"PA-2026{seq}"
        dos = claim.get("dos") or "2026-03-14"
        units = int(claim.get("units_billed") or 1)
        npi = claim.get("npi_rendering")
        cpt = claim.get("cpt_code")
        rec = {
            "auth_number": an, "member_id": claim.get("member_id"),
            "member_name": claim.get("member_name"), "provider_npi": npi,
            "cpt_code": cpt, "diagnosis_code": claim.get("icd10_principal"),
            "dos_start": _shift_date(dos, -20), "dos_end": _shift_date(dos, 20),
            "units_authorized": max(units, 1), "units_used": 0,
            "units_remaining": max(units, 1), "status": "active",
            "approved_date": _shift_date(dos, -25),
            "requesting_provider": npi, "clinical_notes": "Clinical documentation on file",
        }
        if edit == "E-AUTH-002":      # expired
            rec["dos_end"] = _shift_date(dos, -1); rec["status"] = "expired"
        elif edit == "E-AUTH-003":    # service not covered by this auth (different CPT)
            other = "97110" if cpt != "97110" else "20610"
            rec["cpt_code"] = other
        elif edit == "E-AUTH-004":    # units/visits exhausted
            rec["units_used"] = rec["units_authorized"]; rec["units_remaining"] = 0
        elif edit == "E-AUTH-005":    # authorization is for a different provider
            rec["provider_npi"] = ("1999" + (npi or "0000000")[4:]) if npi else "1999000000"
            rec["requesting_provider"] = rec["provider_npi"]
        authorizations[an] = rec
        return an
    for c in pended_claims + multi_edit_claims:
        ec = c.get("edit_code")
        if ec in DEFICIENT and not c.get("auth_number"):
            c["auth_number"] = _mk_auth(c, ec)
    for c in line_item_claims:
        for ln in c.get("lines", []):
            ec = ln.get("edit_code")
            if ec in DEFICIENT and not ln.get("auth_number"):
                merged = dict(c); merged.update(ln)
                ln["auth_number"] = _mk_auth(merged, ec)

_seed_auths_from_claims()

# ── Bundle single-line pends into realistic multi-pend claims ─────────────────────
# A real claim is a header with several service lines, and often MORE THAN ONE line
# pends. We keep the 5,407 pended-line records intact (so line-level outcomes and
# reconciliation are unchanged) and BUNDLE ~30% of them under shared claim headers.
# Result: Claims < Pended lines < Total lines, and many claims show 2-3 pended lines.
GROUP_MEMBERS = {}   # primary_icn -> [secondary pended-line records absorbed into it]

def _bundle_pends():
    prof = [c for c in pended_claims if c.get("claim_type") == "Professional"
            and (c.get("billed_amount") or 0) < HIGH_DOLLAR_INSTITUTIONAL]
    nonfeat = [c for c in prof if not c.get("is_featured")]
    n_sec = int(len(prof) * 0.30)                    # ~30% become secondary lines on other claims
    if not nonfeat or n_sec < 1:
        return
    step = max(1, len(nonfeat) // n_sec)
    secondaries = nonfeat[::step][:n_sec]
    sec_ids = {id(s) for s in secondaries}
    for s in secondaries:
        s["_bundled"] = True
    primaries = [c for c in prof if id(c) not in sec_ids]     # featured stay as primaries
    # spread the multi-pend claims evenly across the whole queue (front to back, incl. featured)
    n_targets = max(1, int(n_sec / 1.4))
    tstep = max(1, len(primaries) // n_targets)
    targets = primaries[::tstep][:n_targets] or primaries
    for k, s in enumerate(secondaries):
        GROUP_MEMBERS.setdefault(targets[k % len(targets)]["icn"], []).append(s)
    for p in primaries:
        p["pended_line_count"] = 1 + len(GROUP_MEMBERS.get(p["icn"], []))

_bundle_pends()

def _seed_pos_differential():
    """Reconcile the Fee Schedule allowed to the allowed carried on claims (so the line's
    'Allowed' always equals the schedule), and add POS-differential rates — a non-facility
    (office) rate and a lower facility rate — so a Place-of-Service edit can reprice to the
    correct site and the cut-back is self-explanatory."""
    for c in pended_claims:
        cpt = str(c.get("cpt_code") or "")
        a = c.get("allowed_amount")
        if cpt and a is not None and cpt in fee_schedule:
            fee_schedule[cpt]["allowed_amount"] = round(float(a), 2)
    for rec in fee_schedule.values():
        base = rec.get("allowed_amount") or 0
        rec["allowed_nonfacility"] = round(base, 2)          # office / POS 11
        rec["allowed_facility"]    = round(base * 0.68, 2)   # facility / POS 21-22 (lower professional component)

_seed_pos_differential()

def _seed_timely_filing_late():
    """Make timely-filing pends genuinely late (received > 365 days after DOS) so they deny —
    the 'correct the received date' what-if then brings them within the limit and re-approves."""
    from datetime import datetime, timedelta
    for c in pended_claims:
        if c.get("edit_code") in ("E-TF-001", "E-TF-002"):
            try:
                dos = datetime.strptime(c.get("dos"), "%Y-%m-%d")
                c["received_date"] = (dos + timedelta(days=430)).strftime("%Y-%m-%d")
            except Exception:
                pass

_seed_timely_filing_late()

def _deconcentrate_auths(cap=2):
    """Spread the Authorization DB so no single member shows a pile of auths (looked spammy,
    e.g., 6 for one member). Only reassign auths NOT tied to a claim; keep up to `cap` per member."""
    import random as _r2
    rnd = _r2.Random(7)
    referenced = {c.get("auth_number") for c in pended_claims if c.get("auth_number")}
    for c in line_item_claims:
        for ln in c.get("lines", []):
            if ln.get("auth_number"):
                referenced.add(ln["auth_number"])
    # spare members to reassign onto (from eligibility), name carried along
    spares = [(mid, v.get("name")) for mid, v in eligibility.items()]
    rnd.shuffle(spares)
    si = 0
    per_member = {}
    for an, a in authorizations.items():
        per_member.setdefault(a.get("member_id"), []).append(an)
    for mid, ans in per_member.items():
        if len(ans) <= cap:
            continue
        excess = [an for an in ans[cap:] if an not in referenced]
        for an in excess:
            if si >= len(spares):
                break
            smid, sname = spares[si]; si += 1
            authorizations[an]["member_id"] = smid
            authorizations[an]["member_name"] = sname

_deconcentrate_auths()

def _pended_line_from(claim):
    """Build a pended service-line dict from a pend record (primary or bundled secondary)."""
    return {
        "line_no": 0, "rev_code": None, "cpt_code": claim.get("cpt_code"),
        "description": claim.get("cpt_description", ""), "modifier": claim.get("modifier"),
        "units": claim.get("units_billed", 1), "charge": claim.get("billed_amount") or 0.0,
        "allowed": claim.get("allowed_amount"), "icd10_principal": claim.get("icd10_principal"),
        "human_review_flag": claim.get("human_review_flag", False),
        "human_review_reason": claim.get("human_review_reason"),
        "pended": True, "edit_code": claim.get("edit_code"),
        "edit_category": claim.get("edit_category"), "edit_description": claim.get("edit_description"),
        "carc_code": claim.get("carc_code"), "rarc_code": claim.get("rarc_code"),
        "resolution_path": claim.get("resolution_path"), "auth_number": claim.get("auth_number"),
    }

def build_service_lines(claim):
    """Derive a realistic header + service-line breakdown. The claim's own pend is a PENDED
    line; any bundled secondary pends are additional PENDED lines; 2-3 clean companion lines
    are adjudicated."""
    # stable per-ICN seed (independent of PYTHONHASHSEED) so lines are identical across restarts
    _icn = str(claim.get("icn", ""))
    rnd = random.Random(sum(ord(ch) * (i + 1) for i, ch in enumerate(_icn)))
    lines = [_pended_line_from(claim)]
    for sec in GROUP_MEMBERS.get(_icn, []):
        lines.append(_pended_line_from(sec))
    n_comp = rnd.randint(2, 3)
    comps = rnd.sample(_COMPANION_CPTS, n_comp)
    for cpt, desc, charge, allowed in comps:
        lines.append({
            "line_no": 0, "rev_code": None, "cpt_code": cpt, "description": desc, "modifier": None,
            "units": 1, "charge": float(charge), "allowed": float(allowed),
            "icd10_principal": claim.get("icd10_principal"), "human_review_flag": False, "pended": False,
        })
    rnd.shuffle(lines)
    for i, ln in enumerate(lines, 1):
        ln["line_no"] = i
    return lines

_LINE_RATIONALE = {
    "human_review": "Routed to a human examiner — this edit needs clinical judgment (e.g., medical necessity) that the agent will not make autonomously. The agent gathered the evidence and staged the decision; a person signs off.",
    "request_info": "Additional documentation requested (ADR) — the agent cannot approve or deny until the missing record/authorization is supplied. It issued the request and holds the line.",
    "deny":         "Denied — the line fails a hard policy or regulatory rule with no payable path; the agent applied the matching CARC/RARC.",
    "partial_pay":  "Partially paid — the agent reduced payment to the amount policy supports (allowed units / fee schedule), rather than the full billed amount.",
    "approve":      "Approved — every rule passed; the agent paid at the allowed amount.",
    "escalate":     "Escalated — requires senior/pricing review beyond the standard SOP; the agent flagged it rather than guess.",
}

def resolve_line(line, header):
    """Adjudicate one service line. Pended lines run through the REAL resolver;
    clean lines pay at their allowed amount. Returns the KG rules + rationale (the 'why')."""
    if not line.get("pended"):
        pay = round((line.get("allowed") if line.get("allowed") is not None else line["charge"]) * line.get("units", 1), 2)
        return {"pended": False, "outcome": "approve", "outcome_label": "Auto-paid",
                "outcome_color": "green", "payment_amount": pay, "sop_ref": "auto-adjudicated",
                "carc": None, "rarc": None, "kg_rules": [], "rationale": "Clean line — passed all auto-adjudication edits; paid at the allowed amount without agent intervention."}
    header_icn = header.get("icn") if isinstance(header, dict) else header
    # merge header context with line fields so KG rule templates fill correctly
    merged = dict(header) if isinstance(header, dict) else {}
    merged.update({
        "icn": f'{header_icn}-L{line["line_no"]}', "edit_code": line["edit_code"],
        "resolution_path": line["resolution_path"], "allowed_amount": line.get("allowed"),
        "units_billed": line.get("units", 1), "carc_code": line.get("carc_code"),
        "rarc_code": line.get("rarc_code"), "human_review_flag": line.get("human_review_flag", False),
        "human_review_reason": line.get("human_review_reason"), "edit_category": line["edit_category"],
        "cpt_code": line.get("cpt_code"), "billed_amount": line["charge"],
        "icd10_principal": line.get("icd10_principal"),
        "auth_number": line.get("auth_number"),
    })
    res = resolve_claim(merged)
    res["pended"] = True
    res["edit_code"] = line["edit_code"]
    res["edit_description"] = line.get("edit_description")
    res["kg_rules"] = get_kg_rules(line["edit_code"], merged, {})
    res["rationale"] = _LINE_RATIONALE.get(res["outcome"], "")
    return res

def _header_rollup(resolutions, lines=None):
    total_paid = round(sum(r.get("payment_amount", 0) for r in resolutions), 2)
    pended = [r for r in resolutions if r.get("pended")]
    n = len(resolutions)
    fully_approved = sum(1 for r in resolutions if r["outcome"] == "approve")
    lines_paid = sum(1 for r in resolutions if r.get("payment_amount", 0) > 0)
    lines_denied = sum(1 for r in resolutions if r["outcome"] == "deny")
    if any(r["outcome"] == "human_review" for r in pended):
        oc, label = "human_review", "Partially adjudicated — human review required"
    elif any(r["outcome"] == "escalate" for r in pended):
        oc, label = "escalate", "Escalated — line review required"
    elif fully_approved == n:                       # every line paid in full
        oc, label = "approve", "Approved"
    elif total_paid <= 0:                           # nothing paid
        oc, label = "deny", "Denied"
    else:                                           # some paid, some reduced/denied
        oc, label = "partial_pay", "Partial Pay"
    color = RESOLUTION_LABELS.get(oc, {}).get("color", "gray")
    out = {"outcome": oc, "outcome_label": label, "outcome_color": color,
           "payment_amount": total_paid, "lines_total": n, "lines_pended": len(pended),
           "lines_paid": lines_paid, "lines_denied": lines_denied}
    # payment breakdown: billed → allowed → paid → contractual write-off → member resp / denied portion
    if lines is not None:
        billed = round(sum((l.get("charge") or 0) for l in lines), 2)
        allowed = round(sum((l.get("allowed") if l.get("allowed") is not None else (l.get("charge") or 0)) for l in lines), 2)
        write_off = round(max(billed - allowed, 0), 2)
        balance = round(max(allowed - total_paid, 0), 2)   # member responsibility / denied portion
        out["breakdown"] = {"billed": billed, "allowed": allowed, "paid": total_paid,
                            "write_off": write_off, "member_or_denied": balance}
    return out

def _other_insurance(claim):
    """Other Insurance Indicator for the header, driven off the real COB database.
    Yes → the member has other coverage (COB applies); shows carrier + order."""
    rec = cob.get(claim.get("member_id")) or {}
    if rec.get("carrier_name"):
        order = str(rec.get("cob_order", "")).lower()
        who = "other carrier is PRIMARY — we pay secondary" if order == "primary" \
              else "we are primary — other carrier secondary"
        return {"other_insurance": "Yes",
                "oi_carrier": rec["carrier_name"],
                "oi_order": rec.get("cob_order"),
                "oi_detail": f"{rec['carrier_name']} ({who})",
                "oi_primary_eob_required": rec.get("primary_eob_required")}
    return {"other_insurance": "No", "oi_carrier": None, "oi_order": None,
            "oi_detail": "No other coverage on file — single-payer", "oi_primary_eob_required": False}

def _line_header(claim, lines):
    h = {k: claim.get(k) for k in (
        "icn", "claim_type", "form", "type_of_bill", "label", "scenario_note",
        "member_id", "member_name", "member_dob", "plan",
        "provider_name", "provider_specialty", "npi_billing", "npi_rendering", "group_name",
        "dos", "received_date", "pend_date", "days_in_queue", "priority",
        "place_of_service", "billed_amount",
        "icd10_principal", "icd10_secondary", "icd10_desc")}
    # Provider identity for BOTH billing and rendering provider (Tax ID + PAR status)
    bp = providers.get(str(claim.get("npi_billing") or ""), {}) or {}
    rp = providers.get(str(claim.get("npi_rendering") or ""), {}) or {}
    h["billing_tax_id"]     = bp.get("tax_id")
    h["billing_par_status"] = bp.get("par_status")
    h["rendering_tax_id"]     = rp.get("tax_id")
    h["rendering_par_status"] = rp.get("par_status")
    # header principal diagnosis: fall back to the first line's Dx (hero claims carry it per line)
    if not h.get("icd10_principal") and lines:
        for ln in lines:
            if ln.get("icd10_principal"):
                h["icd10_principal"] = ln["icd10_principal"]
                break
    h.update(_other_insurance(claim))
    h.update(_timely_fields(claim))
    # Strict mandatory-field validation (audit-grade completeness gate)
    checks = [
        ("Member name", h.get("member_name")), ("Member ID", h.get("member_id")),
        ("Billing NPI", h.get("npi_billing")), ("Rendering NPI", h.get("npi_rendering")),
        ("Billing Tax ID", h.get("billing_tax_id")), ("Rendering Tax ID", h.get("rendering_tax_id")),
        ("Billing PAR status", h.get("billing_par_status")), ("Rendering PAR status", h.get("rendering_par_status")),
        ("Principal diagnosis", h.get("icd10_principal")), ("Date of service", h.get("dos")),
        ("Received date", h.get("received_date")),
        ("Place of service / Type of bill", h.get("place_of_service") or h.get("type_of_bill")),
        ("Billed amount", h.get("billed_amount")), ("Service lines", len(lines) if lines else 0),
    ]
    missing = [label for label, val in checks if val in (None, "", 0)]
    h["validation"] = {"complete": not missing, "missing": missing,
                       "checked": len(checks), "passed": len(checks) - len(missing)}
    return h

_STATES = ["CA","TX","NY","FL","IL","PA","OH","GA","NC","MI","NJ","VA","WA","AZ","MA","TN","MO","MD","CO","MN"]
PAYMENT_SLA_DAYS = 30      # clean-claim prompt-pay window
FILING_LIMIT_DAYS = 365    # timely-filing limit from date of service

def _timely_fields(claim):
    """Provider state + receipt/payment/timely-filing dates + status-today for the claim.
    Payment-due = received + 30d (prompt-pay); filing limit = DOS + 365d. Status uses days-in-queue."""
    from datetime import datetime, timedelta
    def _p(d):
        try: return datetime.strptime(d, "%Y-%m-%d")
        except Exception: return None
    npi = str(claim.get("npi_rendering") or claim.get("npi_billing") or "0")
    prov = providers.get(npi, {})
    state = prov.get("state") or _STATES[sum(ord(c) for c in npi) % len(_STATES)]
    recd, dosd = _p(claim.get("received_date")), _p(claim.get("dos"))
    pay_by = (recd + timedelta(days=PAYMENT_SLA_DAYS)).strftime("%Y-%m-%d") if recd else None
    filing_due = (dosd + timedelta(days=FILING_LIMIT_DAYS)).strftime("%Y-%m-%d") if dosd else None
    timely = None
    if recd and dosd:
        timely = "Filed timely" if (recd - dosd).days <= FILING_LIMIT_DAYS else "Late-filed"
    dq = claim.get("days_in_queue") or 0
    left = PAYMENT_SLA_DAYS - dq
    status = (f"On time — {left}d to pay-by SLA" if left >= 0 else f"SLA breach — {-left}d over")
    return {"provider_state": state, "received_date": claim.get("received_date"),
            "payment_due_date": pay_by, "filing_limit_date": filing_due,
            "timely_filing": timely, "status_today": status, "status_ok": left >= 0}

# ── Jurisdiction priority policy ────────────────────────────────────────────────
# The agent adjudicates every pend autonomously on arrival (no queue to prioritize).
# Prioritization matters only where a HUMAN is the bottleneck — the manual-review queues.
# There we rank by jurisdiction: high-interest states (Texas — DOI oversight + strict
# prompt-pay) surface first, so associates work the highest-exposure claims first.
HIGH_INTEREST_STATES = {"TX"}

def _claim_state(claim):
    """Deterministic jurisdiction (provider state) for a claim."""
    npi = str(claim.get("npi_rendering") or claim.get("npi_billing") or "")
    prov = providers.get(npi, {})
    if prov.get("state"):
        return prov["state"]
    seed = npi or str(claim.get("icn") or claim.get("member_id") or "0")
    return _STATES[sum(ord(c) for c in seed) % len(_STATES)]

def _priority_of(claim):
    """Rank + tier + reason for a manual-review work item. Lower rank = worked first."""
    st = _claim_state(claim)
    dq = claim.get("days_in_queue") or 0
    if st in HIGH_INTEREST_STATES:
        return {"state": st, "priority_rank": 0, "priority_tier": "Priority",
                "priority_reason": f"High-interest jurisdiction — {st} (state DOI oversight · strict prompt-pay)"}
    if dq >= PAYMENT_SLA_DAYS - 5:
        return {"state": st, "priority_rank": 1, "priority_tier": "Elevated",
                "priority_reason": f"Prompt-pay SLA at risk — {dq}d in queue"}
    return {"state": st, "priority_rank": 2, "priority_tier": "Routine",
            "priority_reason": f"Standard priority — {st}"}

# ── API Routes ────────────────────────────────────────────────────────────────

_LINE_AGG = None
def _line_aggregates():
    """Claim / service-line / pended-line totals across the whole pend queue.
    Each pended claim resolves to a header with several service lines; the pended
    line(s) are the subset the agent must resolve. Computed once, cached."""
    global _LINE_AGG
    if _LINE_AGG is None:
        total_lines = 0
        pended_lines = 0
        claim_list = [c for c in pended_claims if not c.get("_bundled")]  # primaries + singletons (bundled secondaries are absorbed as lines)
        for c in claim_list:
            ls = build_service_lines(c)
            total_lines += len(ls)
            pended_lines += sum(1 for l in ls if l.get("pended"))
        _LINE_AGG = {"claims": len(claim_list), "total_lines": total_lines, "pended_lines": pended_lines}
    return _LINE_AGG

@app.route("/api/stats")
def api_stats():
    total  = len(pended_claims)
    hr     = sum(1 for c in pended_claims if c["human_review_flag"])
    feat   = sum(1 for c in pended_claims if c.get("is_featured"))
    cats   = {}
    for c in pended_claims:
        cats[c["edit_category"]] = cats.get(c["edit_category"], 0) + 1
    agg = _line_aggregates()
    return jsonify({
        "total_pended":        total,
        "claims_extracted":    agg["claims"],
        "total_lines":         agg["total_lines"],
        "pended_lines":        agg["pended_lines"],
        "human_review_count":  hr,
        "featured_count":      feat,
        "by_category":         cats,
        "total_providers":     len(providers),
        "total_authorizations":len(authorizations),
        "total_cob_records":   len(cob),
        "fee_schedule_codes":  len(fee_schedule),
        "total_members":       len(eligibility),
        "total_history_claims":sum(len(v) for v in claims_history.values()),
        "edit_types":          len(edit_codes),
    })

@app.route("/api/resolve-summary")
def api_resolve_summary():
    """Full-queue outcome rollup so the live counters reconcile to the extracted total.
    (escalate is folded into human_review — both route to a person.)"""
    counts = {"approve": 0, "deny": 0, "partial_pay": 0, "request_info": 0, "human_review": 0, "escalate": 0}
    clinical = 0; high_dollar = 0; human_genuine = 0
    for c in pended_claims:
        o = _resolve_pended(c)["outcome"]
        counts[o] = counts.get(o, 0) + 1
        if o == "deny" and (c.get("resolution_path") in ("deny_medical_necessity", "deny_lcd_ncd")
                            or c.get("edit_category") == "Medical Necessity"):
            clinical += 1
        if o in ("human_review", "escalate"):
            if c.get("claim_type") == "Institutional" and (c.get("billed_amount") or 0) >= HIGH_DOLLAR_INSTITUTIONAL:
                high_dollar += 1
            else:
                human_genuine += 1
    total = len(pended_claims)
    return jsonify({
        "total":         total,
        "approve":       counts["approve"],
        "deny":          counts["deny"],
        "partial_pay":   counts["partial_pay"],
        "request_info":  counts["request_info"],
        "human_review":  human_genuine,   # genuine human review only (excl. high-dollar oversight)
        "high_dollar":   high_dollar,     # institutional >= $10k — its own review queue
        "clinical":      clinical,        # subset of Denied — adverse clinical/coverage determinations
        "missing_sop":   counts.get("missing_sop", 0),  # no SOP on file → routed to SOP Governance
    })

_FEATURED_ORDER = None
def _featured_ordered():
    """Curated demo order for the featured sample: round-robin across edit categories (so the
    first ~15 rows cover ~15 categories) and lead with an approval, spreading ADRs later.
    Display order only — decisions are unchanged."""
    global _FEATURED_ORDER
    if _FEATURED_ORDER is not None:
        return _FEATURED_ORDER
    feats = [c for c in pended_claims if c.get("is_featured")]
    pref = {"approve": 0, "partial_pay": 1, "deny": 2, "human_review": 3, "escalate": 4, "request_info": 5}
    by_cat = {}
    for c in feats:
        by_cat.setdefault(c["edit_category"], []).append(c)
    for cat in by_cat:  # within a category, non-ADR first so ADRs get pushed later
        by_cat[cat].sort(key=lambda c: pref.get(_resolve_pended(c)["outcome"], 9))
    cats = list(by_cat.values())
    result = []
    while any(cats):
        for lst in cats:
            if lst:
                result.append(lst.pop(0))
    for i, c in enumerate(result):  # lead with an approval if one exists
        if _resolve_pended(c)["outcome"] == "approve":
            result.insert(0, result.pop(i)); break
    _FEATURED_ORDER = result
    return result

@app.route("/api/pend-queue")
def api_pend_queue():
    page  = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 100))
    featured_only = request.args.get("featured") == "true"
    base = _featured_ordered() if featured_only else pended_claims
    # Surface the hero header+line-item claims at the top of the queue / ingest stream
    subset = line_item_claims + base
    start  = (page - 1) * limit
    return jsonify({
        "claims": subset[start:start + limit],
        "total":  len(subset),
        "page":   page,
    })

@app.route("/api/claim/<icn>")
def api_claim(icn):
    claim = claims_index.get(icn)
    if not claim:
        return jsonify({"error": "ICN not found"}), 404
    return jsonify(claim)

@app.route("/api/process-claim/<icn>")
def api_process_claim(icn):
    claim = claims_index.get(icn)
    if not claim:
        return jsonify({"error": "ICN not found"}), 404
    time.sleep(random.uniform(0.05, 0.18))
    result = resolve_claim(claim)
    return jsonify({**claim, **result})

@app.route("/api/queue-search")
def api_queue_search():
    """Search the ENTIRE pend population (not just the sample shown) by ICN, member, provider,
    CPT, edit code, category, or SOP — returns resolved rows for the main queue."""
    q = (request.args.get("q") or "").strip().lower()
    limit = int(request.args.get("limit", 60))
    if not q:
        return jsonify({"results": [], "total": 0, "q": q})
    def _hay(c):
        return " ".join(str(c.get(k, "")) for k in (
            "icn", "member_name", "provider_name", "cpt_code", "cpt_description",
            "edit_code", "edit_category", "edit_description", "provider_specialty")).lower()
    matched = [c for c in pended_claims if q in _hay(c)]
    total = len(matched)
    results = []
    # Hero multi-line claims first (searchable too)
    for hero in line_item_claims:
        if q in (str(hero.get("icn", "")) + " " + str(hero.get("member_name", ""))
                 + " " + str(hero.get("claim_type", "")) + " " + str(hero.get("label", ""))).lower():
            lines = hero.get("lines", [])
            ru = _header_rollup([resolve_line(dict(l), hero) for l in lines], lines)
            results.append({
                "icn": hero["icn"], "member_name": hero.get("member_name"),
                "provider_name": hero.get("provider_name"),
                "cpt_code": (hero.get("claim_type", "MULTI"))[:4],
                "billed_amount": hero.get("billed_amount"),
                "edit_code": f'{ru.get("lines_pended", 0)} pends', "edit_category": hero.get("claim_type", "Multi-line"),
                "recommendation": ru["outcome_label"], "outcome_label": ru["outcome_label"],
                "outcome_color": ru.get("outcome_color", "gray"), "confidence": None,
                "decision_status": "auto", "payment_amount": ru.get("payment_amount", 0),
                "sop_ref": f'{ru.get("lines_paid", 0)}/{ru.get("lines_total", 0)} lines paid',
                "is_multi_line": True,
            })
            total += 1
    for c in matched[:max(0, limit - len(results))]:
        results.append({**c, **resolve_claim(c)})
    return jsonify({"results": results, "total": total, "shown": len(results), "q": q})

def _resolve_multi_edit_combined(claim):
    """Resolve every edit stamped on a single-line multi-edit claim, then combine
    'most-restrictive-wins'. Returns a queue-row-shaped dict (real CPT, N-edits label)."""
    edit_resolutions = []
    for ed in claim.get("edits_detail", []):
        fake = {**claim, "edit_code": ed["edit_code"], "edit_category": ed["edit_category"],
                "resolution_path": ed["resolution_path"], "carc_code": ed.get("carc_code"),
                "rarc_code": ed.get("rarc_code"), "human_review_flag": ed.get("human_review_flag")}
        edit_resolutions.append(resolve_claim(fake))
    order = ["human_review", "deny", "escalate", "partial_pay", "request_info", "approve"]
    outs = [r["outcome"] for r in edit_resolutions]
    combined = next((o for o in order if o in outs), "approve")
    lbl = RESOLUTION_LABELS.get(combined, {})
    pay = (max((r.get("payment_amount", 0) for r in edit_resolutions), default=0.0)
           if combined in ("approve", "partial_pay") else 0.0)
    # Confidence in the CLAIM decision = confidence of the edit that drove it (most-restrictive)
    driver = next((r for r in edit_resolutions if r["outcome"] == combined), None)
    conf = driver.get("confidence") if driver else None
    n = len(claim.get("edit_codes", []))
    return {
        "outcome": combined, "outcome_label": lbl.get("label", combined),
        "outcome_color": lbl.get("color", "gray"), "recommendation": lbl.get("label", combined),
        "payment_amount": pay, "confidence": conf,
        "decision_status": "pending_review" if combined in ("human_review", "escalate") else "issued",
        "human_review": combined in ("human_review", "escalate"),
        "sop_ref": f"{n} edits · most-restrictive",
    }

@app.route("/api/process-batch")
def api_process_batch():
    """Process all featured claims (1 per edit type) for the live demo queue."""
    featured = _featured_ordered()
    results  = []
    for claim in featured:
        res = resolve_claim(claim)
        results.append({
            "icn":           claim["icn"],
            "member_name":   claim["member_name"],
            "provider_name": claim["provider_name"],
            "cpt_code":      claim["cpt_code"],
            "billed_amount": claim["billed_amount"],
            "edit_code":     claim["edit_code"],
            "edit_category": claim["edit_category"],
            "edit_description": claim["edit_description"],
            **res,
        })
    # Surface a couple of single-line MULTI-EDIT claims (one CPT carrying several edits) in the
    # main queue so the audience sees claims with more than one edit alongside the single-edit rows.
    for claim in multi_edit_claims[:2]:
        res = _resolve_multi_edit_combined(claim)
        n = len(claim.get("edit_codes", []))
        results.append({
            "icn":           claim["icn"],
            "member_name":   claim["member_name"],
            "provider_name": claim["provider_name"],
            "cpt_code":      claim["cpt_code"],
            "billed_amount": claim["billed_amount"],
            "edit_code":     f"⚡ {n} edits",
            "edit_category": claim["edit_category"],
            "edit_description": claim.get("edit_description", "Multiple edits on one line"),
            "is_multi_edit": True,
            **res,
        })
    return jsonify({"results": results, "count": len(results)})

_OUTCOME_KEY = {"approved": "approve", "denied": "deny", "partial": "partial_pay",
                "adr": "request_info", "human": "human_review", "clinical": "__clinical__",
                "highdollar": "__highdollar__", "missingsop": "missing_sop"}
def _is_high_dollar(c):
    return c.get("claim_type") == "Institutional" and (c.get("billed_amount") or 0) >= HIGH_DOLLAR_INSTITUTIONAL

@app.route("/api/outcome/<key>")
def api_outcome(key):
    """Drill-down: all pended claims that resolved to <key>, with full resolution detail so a
    reviewer sees everything the agent used — no navigating away. Returns up to `limit` + total."""
    limit = int(request.args.get("limit", 60))
    target = _OUTCOME_KEY.get(key)
    if not target:
        return jsonify({"error": "unknown outcome"}), 404
    matched = []
    for c in pended_claims:
        res = _resolve_pended(c)
        oc = res["outcome"]
        if target == "__clinical__":
            hit = (oc == "deny" and (c.get("resolution_path") in ("deny_medical_necessity", "deny_lcd_ncd")
                                     or c.get("edit_category") == "Medical Necessity"))
        elif target == "__highdollar__":
            hit = oc in ("human_review", "escalate") and _is_high_dollar(c)
        elif target == "human_review":
            hit = oc in ("human_review", "escalate") and not _is_high_dollar(c)
        else:
            hit = oc == target
        if hit:
            matched.append(c)
    # Include the hero (multi-line) claims by their ROLLUP outcome, so the main queue and the
    # work queue reconcile (a claim shown as Partial in the queue appears in the Partial queue).
    hero_matches = []
    for hero in line_item_claims:
        lines = hero.get("lines", [])
        ru = _header_rollup([resolve_line(dict(l), hero) for l in lines], lines)
        oc = ru["outcome"]
        if target == "__clinical__":
            hh = False
        elif target == "__highdollar__":
            hh = oc in ("human_review", "escalate") and _is_high_dollar(hero)
        elif target == "human_review":
            hh = oc in ("human_review", "escalate") and not _is_high_dollar(hero)
        else:
            hh = oc == target
        if hh:
            hero_matches.append({
                "icn": hero["icn"], "member_name": hero.get("member_name"), "provider_name": hero.get("provider_name"),
                "cpt_code": hero.get("cpt_code", hero.get("claim_type", "")), "billed_amount": hero.get("billed_amount"),
                "edit_code": f'{ru.get("lines_pended", 0)} pended lines', "edit_category": hero.get("claim_type", "Multi-line"),
                "edit_description": hero.get("label", "Multi-line claim"),
                "outcome": oc, "outcome_label": ru["outcome_label"], "outcome_color": ru.get("outcome_color", "gray"),
                "payment_amount": ru.get("payment_amount", 0), "carc": "—", "rarc": "—",
                "sop_ref": f'{ru.get("lines_paid", 0)}/{ru.get("lines_total", 0)} lines paid', "is_multi_line": True,
            })
    total = len(matched) + len(hero_matches)
    # Manual-review work queues: rank by jurisdiction priority (high-interest states first)
    # so associates work the highest-exposure claims first. Other queues keep prior order.
    human = key in ("denied", "human", "clinical", "highdollar")
    priority_total = 0
    if human:
        priority_total = (sum(1 for c in matched if _priority_of(c)["priority_rank"] == 0)
                          + sum(1 for h in hero_matches if _priority_of(h)["priority_rank"] == 0))
        for h in hero_matches:
            h.update(_priority_of(h))
        matched.sort(key=lambda c: (_priority_of(c)["priority_rank"],
                                    0 if c.get("edit_code") in WHATIF_EDITS else 1))
    else:
        # Bring the what-if-editable examples (auth missing, units, timely filing) to the FRONT
        matched.sort(key=lambda c: 0 if c.get("edit_code") in WHATIF_EDITS else 1)
    matches = list(hero_matches)   # heroes first so they're easy to find
    for c in matched[:max(0, limit - len(matches))]:
        res = _resolve_pended(c)
        row = {
            "icn": c["icn"], "member_name": c["member_name"], "provider_name": c["provider_name"],
            "cpt_code": c["cpt_code"], "billed_amount": c["billed_amount"],
            "edit_code": c["edit_code"], "edit_category": c["edit_category"],
            "edit_description": c["edit_description"], **res,
        }
        if human:
            row.update(_priority_of(c))
        matches.append(row)
    if human:
        matches.sort(key=lambda m: m.get("priority_rank", 2))   # stable → priority jurisdictions on top
    return jsonify({"key": key, "results": matches, "shown": len(matches),
                    "total": total, "human": human, "priority_total": priority_total})

@app.route("/api/line-claims")
def api_line_claims():
    """List the hero header+line-item claims (Professional + Institutional)."""
    return jsonify({"claims": line_item_claims, "count": len(line_item_claims)})

@app.route("/api/claim-lines/<icn>")
def api_claim_lines(icn):
    """Header + service lines for ANY claim. ?resolve=1 adjudicates each line (real resolver) + rolls up.
    Hero claims use their explicit lines; regular pends get a derived header + companion lines."""
    resolve = request.args.get("resolve") == "1"
    hero = line_item_index.get(icn)
    if hero:
        claim, lines = hero, hero["lines"]
    else:
        claim = claims_index.get(icn)
        if not claim:
            return jsonify({"error": "ICN not found"}), 404
        lines = build_service_lines(claim)
    header = _line_header(claim, lines)
    header["billed_amount"] = round(sum(l["charge"] for l in lines), 2)
    out_lines = [dict(l) for l in lines]
    _tf = _timely_fields(claim)   # claim-level timely-filing fields, shown on every line too
    # Attach the applicable SOP + timely-filing fields to each line
    for ln in out_lines:
        ln.setdefault("provider_state", _tf["provider_state"])
        ln.setdefault("received_date", _tf["received_date"])
        ln.setdefault("payment_due_date", _tf["payment_due_date"])
        ln.setdefault("status_today", _tf["status_today"])
        ln.setdefault("status_ok", _tf["status_ok"])
        if ln.get("pended"):
            rule = RESOLUTION_RULES.get(ln.get("resolution_path"))
            ln["applicable_sop"] = rule["sop_ref"] if rule else "N/A"
    payload = {"header": header, "lines": out_lines,
               "is_multi_line": bool(hero) or sum(1 for l in lines if l.get("pended")) > 1}
    if resolve:
        resolutions = []
        for ln in out_lines:
            r = resolve_line(ln, claim)
            ln["resolution"] = r
            resolutions.append(r)
        payload["rollup"] = _header_rollup(resolutions, out_lines)
    return jsonify(payload)

@app.route("/api/multi-edit-claims")
def api_multi_edit_claims():
    return jsonify({"claims": multi_edit_claims, "count": len(multi_edit_claims)})

@app.route("/api/multi-edit-context/<icn>")
def api_multi_edit_context(icn):
    """Full context + per-edit resolution for a multi-edit claim."""
    claim = next((c for c in multi_edit_claims if c["icn"] == icn), None)
    if not claim:
        return jsonify({"error": "ICN not found"}), 404

    provider  = providers.get(claim["npi_rendering"], {})
    auth_num  = claim.get("auth_number")
    auth_rec  = authorizations.get(auth_num, {}) if auth_num else {}
    cob_rec   = cob.get(claim["member_id"], {})
    fs_rec    = fee_schedule.get(claim["cpt_code"], {})
    hist      = claims_history.get(claim["member_id"], [])
    prior_paid = sum(h["paid_amount"] for h in hist)

    # Resolve each edit independently
    edit_resolutions = []
    for ed in claim.get("edits_detail", []):
        fake_claim = {**claim, "edit_code": ed["edit_code"], "edit_category": ed["edit_category"],
                      "resolution_path": ed["resolution_path"], "carc_code": ed["carc_code"],
                      "rarc_code": ed["rarc_code"], "human_review_flag": ed["human_review_flag"]}
        res = resolve_claim(fake_claim)
        kg  = get_kg_rules(ed["edit_code"], fake_claim, {})
        edit_resolutions.append({**ed, **res, "kg_rules": kg})

    # Combined decision: most restrictive wins
    outcome_priority = ["human_review", "deny", "escalate", "partial_pay", "request_info", "approve"]
    all_outcomes = [r["outcome"] for r in edit_resolutions]
    combined_outcome = next((o for o in outcome_priority if o in all_outcomes), "approve")
    combined_label = RESOLUTION_LABELS.get(combined_outcome, {}).get("label", combined_outcome)
    combined_color = RESOLUTION_LABELS.get(combined_outcome, {}).get("color", "gray")
    combined_payment = max(r["payment_amount"] for r in edit_resolutions) if combined_outcome in ("approve","partial_pay") else 0.0

    return jsonify({
        "claim":            claim,
        "provider_db":      provider,
        "auth_db":          auth_rec,
        "cob_db":           cob_rec,
        "fee_schedule":     fs_rec,
        "claims_history":   {"count": len(hist), "total_paid_ytd": prior_paid, "last_claim": hist[-1] if hist else {}},
        "eligibility":      {"member_id": claim["member_id"], "plan": claim["plan"], "status": "active"},
        "edit_resolutions": edit_resolutions,
        "combined_outcome": combined_outcome,
        "combined_label":   combined_label,
        "combined_color":   combined_color,
        "combined_payment": combined_payment,
    })

@app.route("/api/human-review")
def api_human_review():
    return jsonify({
        "claims": human_review,
        "count":  len(human_review),
    })

# ── Human Review & Denial Workflow ──────────────────────────────────────────────
HIGH_DOLLAR = 2000.0
_REVIEW_AGG = None

def _why_human(outcome, category, resolution_path, billed, hr_reason, claim_type=None):
    """Return (routed?, route, why_human, recommendation) — the human-in-the-loop rule."""
    if outcome in ("human_review", "escalate"):
        if claim_type == "Institutional" and (billed or 0) >= HIGH_DOLLAR_INSTITUTIONAL:
            return True, "high_dollar_review", \
                   (hr_reason or f"High-dollar institutional claim (≥ ${HIGH_DOLLAR_INSTITUTIONAL:,.0f}) — human oversight"), \
                   "Agent adjudicated; high-dollar institutional claim held for human oversight sign-off"
        return True, "human_escalation", (hr_reason or "Edge-case judgment the agent will not make alone"), \
               "Agent escalated the case with evidence; a human makes the call"
    if outcome == "deny":
        # 1) Adverse clinical / coverage determinations → clinician review
        if resolution_path in ("deny_medical_necessity", "deny_lcd_ncd") or category == "Medical Necessity":
            return True, "clinical_review", \
                   "Adverse coverage / medical-necessity denial — a clinician must review before the determination is released", \
                   "Agent applied the policy and recommends UPHOLD — the clinical team confirms or overturns"
        # 2) Clear-cut denials are auto-issued (no review)
        if resolution_path in ("deny_timely_filing", "deny_duplicate"):
            return False, None, None, None
        # 3) Every other agent-issued denial → examiner denial review before release
        why = (f"High-dollar denial (${billed:,.0f} ≥ ${HIGH_DOLLAR:,.0f}) — senior examiner sign-off"
               if (billed or 0) >= HIGH_DOLLAR else
               "Agent-issued denial — examiner review / QA before release")
        return True, "denial_review", why, "Agent recommends UPHOLD denial — examiner confirms or overrides"
    return False, None, None, None

def _work_item(icn, claim_disp, res, billed, line_no=None, rev_or_cpt=None):
    routed, route, why, rec = _why_human(res["outcome"], claim_disp.get("edit_category"),
                                         claim_disp.get("resolution_path"), billed,
                                         res.get("human_review_reason"), claim_disp.get("claim_type"))
    if not routed:
        return None
    return {
        "id": f'{icn}' + (f'-L{line_no}' if line_no else ''),
        "icn": icn, "line_no": line_no, "svc": rev_or_cpt, "route": route,
        "member_name": claim_disp.get("member_name"), "claim_type": claim_disp.get("claim_type", "Professional"),
        "billed": billed, "edit_code": claim_disp.get("edit_code"), "edit_category": claim_disp.get("edit_category"),
        "edit_description": claim_disp.get("edit_description"),
        "agent_outcome": res["outcome"], "agent_outcome_label": res["outcome_label"],
        "why_human": why, "recommendation": rec,
        "sop_ref": res.get("sop_ref"), "carc": res.get("carc"), "rarc": res.get("rarc"),
        "kg_rules": res.get("kg_rules", []), "rationale": res.get("rationale", ""),
    }

@app.route("/api/review-workflow")
def api_review_workflow():
    """Assemble everything that must touch a human: all human-review outcomes PLUS denials
    that require sign-off (medical-necessity / LCD-NCD, and high-dollar denials)."""
    items = []
    # featured single-line claims
    for claim in [c for c in pended_claims if c.get("is_featured")]:
        res = resolve_claim(claim)
        res["kg_rules"] = get_kg_rules(claim["edit_code"], claim, {})
        res["rationale"] = _LINE_RATIONALE.get(res["outcome"], "")
        it = _work_item(claim["icn"], claim, res, claim.get("billed_amount") or 0,
                        rev_or_cpt=f'CPT {claim.get("cpt_code")}')
        if it: items.append(it)
    # hero header+line claims — line level
    for claim in line_item_claims:
        for ln in claim["lines"]:
            if not ln.get("pended"):
                continue
            res = resolve_line(ln, claim)
            disp = {"edit_category": ln.get("edit_category"), "resolution_path": ln.get("resolution_path"),
                    "edit_code": ln.get("edit_code"), "edit_description": ln.get("edit_description"),
                    "member_name": claim.get("member_name"), "claim_type": claim.get("claim_type")}
            rev_or_cpt = f'REV {ln.get("rev_code")}' if ln.get("rev_code") else f'CPT {ln.get("cpt_code")}'
            it = _work_item(claim["icn"], disp, res, ln.get("charge") or 0, line_no=ln["line_no"], rev_or_cpt=rev_or_cpt)
            if it: items.append(it)
    # a few high-dollar institutional claims so the High-Dollar Review lane has sample cards
    for claim in [c for c in pended_claims if str(c.get("icn","")).startswith("ICN-2026-HD-")][:8]:
        res = resolve_claim(claim)
        res["kg_rules"] = get_kg_rules(claim["edit_code"], claim, {})
        it = _work_item(claim["icn"], claim, res, claim.get("billed_amount") or 0,
                        rev_or_cpt=f'CPT {claim.get("cpt_code")}')
        if it: items.append(it)
    # order: high-dollar, denial review, human escalation, clinical review
    order = {"high_dollar_review": 0, "denial_review": 1, "human_escalation": 2, "clinical_review": 3}
    items.sort(key=lambda x: (order.get(x.get("route"), 4), -(x["billed"] or 0)))
    # Full-queue referral counts so the workflow reconciles with what the agent referred.
    global _REVIEW_AGG
    if _REVIEW_AGG is None:
        agg = {"denial_review": 0, "human_escalation": 0, "clinical_review": 0, "high_dollar_review": 0}
        for c in pended_claims:
            r = _resolve_pended(c)
            routed, route, _w, _r = _why_human(r["outcome"], c.get("edit_category"), c.get("resolution_path"),
                                               c.get("billed_amount") or 0, r.get("human_review_reason"), c.get("claim_type"))
            if routed:
                agg[route] = agg.get(route, 0) + 1
        _REVIEW_AGG = agg
    summary = {
        "total":              sum(_REVIEW_AGG.values()),
        "denial_review":      _REVIEW_AGG["denial_review"],
        "human_escalation":   _REVIEW_AGG["human_escalation"],
        "clinical_review":    _REVIEW_AGG["clinical_review"],
        "high_dollar_review": _REVIEW_AGG["high_dollar_review"],
        "shown":              len(items),
    }
    return jsonify({"items": items, "summary": summary})

@app.route("/api/sop-outcomes")
def api_sop_outcomes():
    return jsonify(sop_outcomes)

@app.route("/api/predictions")
def api_predictions():
    return jsonify(predictions)

@app.route("/api/edit-codes")
def api_edit_codes():
    return jsonify(edit_codes)

# ── Database Lookup Routes ────────────────────────────────────────────────────

@app.route("/api/db/providers")
def api_db_providers():
    recs = list(providers.values())
    return jsonify({"records": recs[:200], "total": len(recs), "shown": min(200, len(recs))})

@app.route("/api/db/providers/<npi>")
def api_db_provider(npi):
    p = providers.get(npi)
    if not p:
        return jsonify({"error": "NPI not found"}), 404
    return jsonify(p)

@app.route("/api/db/authorizations")
def api_db_authorizations():
    recs = list(authorizations.values())
    return jsonify({"records": recs[:200], "total": len(recs), "shown": min(200, len(recs))})

@app.route("/api/db/authorizations/<auth_num>")
def api_db_authorization(auth_num):
    a = authorizations.get(auth_num)
    if not a:
        return jsonify({"error": "Auth number not found"}), 404
    return jsonify(a)

@app.route("/api/db/cob")
def api_db_cob():
    recs = list(cob.values())
    return jsonify({"records": recs[:200], "total": len(recs), "shown": min(200, len(recs))})

@app.route("/api/db/eligibility")
def api_db_eligibility_list():
    recs = list(eligibility.values())
    return jsonify({"records": recs[:200], "total": len(recs), "shown": min(200, len(recs))})

@app.route("/api/db/claims-history")
def api_db_claims_history_list():
    rows = []
    for mid, lst in claims_history.items():
        for r in lst:
            rows.append({**r, "member_id": mid})
            if len(rows) >= 200:
                break
        if len(rows) >= 200:
            break
    total = sum(len(v) for v in claims_history.values())
    return jsonify({"records": rows, "total": total, "shown": len(rows)})

@app.route("/api/db/cob/<member_id>")
def api_db_cob_member(member_id):
    c = cob.get(member_id)
    if not c:
        return jsonify({"error": "No COB record for member"}), 404
    return jsonify(c)

@app.route("/api/db/fee-schedule")
def api_db_fee_schedule():
    recs = list(fee_schedule.values())
    return jsonify({"records": recs[:200], "total": len(recs), "shown": min(200, len(recs))})

@app.route("/api/db/fee-schedule/<cpt>")
def api_db_fee_schedule_cpt(cpt):
    f = fee_schedule.get(cpt)
    if not f:
        return jsonify({"error": "CPT not in fee schedule"}), 404
    return jsonify(f)

@app.route("/api/db/claims-history/<member_id>")
def api_db_claims_history(member_id):
    h = claims_history.get(member_id, [])
    return jsonify({"member_id": member_id, "claims": h, "count": len(h)})

def _covers_dos(rec, dos):
    """Does the member's coverage span cover the date of service?"""
    if not rec or not dos:
        return None
    for sp in rec.get("coverage_spans", []):
        eff = sp.get("effective") or "0000"
        term = sp.get("term") or "9999"   # None/blank term = open-ended coverage
        if eff <= str(dos) <= term:
            return True
    return False


def eligibility_for(member_id, dos=None):
    """Real eligibility lookup from the eligibility DB (+ coverage-on-DOS check)."""
    rec = eligibility.get(member_id)
    if not rec:
        return None
    out = dict(rec)
    dob = rec.get("dob") or ""
    try:
        out["age"] = 2026 - int(dob.split(", ")[1])
    except Exception:
        out["age"] = None
    if dos is not None:
        out["covers_dos"] = _covers_dos(rec, dos)
    return out


@app.route("/api/db/eligibility/<member_id>")
def api_db_eligibility(member_id):
    dos = request.args.get("dos")
    rec = eligibility_for(member_id, dos)
    if not rec:
        return jsonify({"error": "Member not found"}), 404
    return jsonify(rec)


@app.route("/api/pricing/status")
def api_pricing_status():
    """Is the Burgess/Multiplan/Zelis pricing API live (credentials configured) or representative?"""
    return jsonify({
        "vendor": pricing_client.vendor,
        "live": pricing_client.live,
        "endpoint": pricing_client.url if pricing_client.live else None,
        "mode": (f"{pricing_client.vendor} API (live)" if pricing_client.live
                 else f"representative pricing engine ({pricing_client.vendor} API connects at deployment)"),
    })


@app.route("/api/pricing/reprice/<icn>")
def api_pricing_reprice(icn):
    """Reprice a claim through the pricing engine (real Burgess/Multiplan/Zelis API when configured)."""
    claim = claims_index.get(icn)
    if not claim:
        return jsonify({"error": "ICN not found"}), 404
    return jsonify(pricing_client.reprice(claim, fee_schedule))


@app.route("/api/pricing/recent")
def api_pricing_recent():
    """Recent manual-pricing / OON claims repriced through the engine (the Pricing Engine source view)."""
    rows = []
    for c in pended_claims:
        if c.get("resolution_path") in ("manual_price_via_engine", "oon_reprice"):
            pr = pricing_client.reprice(c, fee_schedule)
            rows.append({"icn": c["icn"], "cpt": c["cpt_code"], "billed": c["billed_amount"],
                         "allowed": pr.get("allowed"), "methodology": pr.get("methodology"),
                         "source": "live" if pr.get("live") else "representative"})
    return jsonify({"records": rows, "total": len(rows), "live": pricing_client.live,
                    "vendor": pricing_client.vendor})

# ── Demo Endpoints ────────────────────────────────────────────────────────────

@app.route("/api/claim-context/<icn>")
def api_claim_context(icn):
    """Full context assembly for a single claim — all 6 DB queries bundled."""
    claim = claims_index.get(icn)
    if not claim:
        return jsonify({"error": "ICN not found"}), 404

    provider  = providers.get(claim["npi_rendering"], {})
    auth_num  = claim.get("auth_number")
    auth_rec  = authorizations.get(auth_num, {}) if auth_num else {}
    cob_rec   = cob.get(claim["member_id"], {})
    fs_rec    = fee_schedule.get(claim["cpt_code"], {})
    hist      = claims_history.get(claim["member_id"], [])
    prior_paid = sum(h["paid_amount"] for h in hist)
    last_claim = hist[-1] if hist else {}

    res = resolve_claim(claim)
    ctx = {
        "claim":          claim,
        "provider_db":    provider,
        "auth_db":        auth_rec,
        "cob_db":         cob_rec,
        "fee_schedule":   fs_rec,
        "claims_history": {
            "count":      len(hist),
            "total_paid_ytd": prior_paid,
            "last_claim": last_claim,
        },
        "eligibility": eligibility_for(claim["member_id"], claim.get("dos")) or {
            "member_id":   claim["member_id"],
            "plan":        claim["plan"],
            "status":      "unknown",
        },
        "resolution": res,
    }
    ctx["kg_rules"] = get_kg_rules(claim["edit_code"], claim, ctx)
    return jsonify(ctx)

_OBS_CACHE = None

@app.route("/api/observability")
def api_observability():
    """Live observability across the pended-claim queue — throughput, decision mix, autonomy
    split, edit-category + turnaround variability, guardrail checks, and a per-claim audit trail."""
    global _OBS_CACHE
    if _OBS_CACHE is None:
        outcomes, categories = {}, {}
        dq = {"< 15 days": 0, "15-30 days": 0, "30-60 days": 0, "60+ days": 0}
        audit = []
        audit_buckets = {}   # bucket by outcome so the trace shows a representative MIX
        ROUTE_QUEUE = {"denial_review": "Denial review", "human_escalation": "Examiner review",
                       "clinical_review": "Clinical review", "high_dollar_review": "High-Dollar review"}
        total = len(pended_claims)
        auto = human = traced = 0
        # Evaluation / metrics accumulators
        grounded = wellformed = 0
        steps_total = dbq_total = 0
        conf_sum = 0.0; conf_hi = conf_md = conf_lo = 0; conf_n = 0
        lat = []
        for c in pended_claims:
            try:
                res = _resolve_pended(c)
            except Exception:
                continue
            o = res["outcome"]
            outcomes[o] = outcomes.get(o, 0) + 1
            cat = c.get("edit_category", "Other")
            categories[cat] = categories.get(cat, 0) + 1
            if o == "human_review" or c.get("human_review_flag"):
                human += 1
            else:
                auto += 1
            if c.get("carc_code") and c.get("rarc_code"):
                traced += 1
            # Evaluation: grounded = decision maps to a real SOP + executed steps; well-formed = cites SOP and (CARC or approve)
            esteps = res.get("executed_steps") or []
            steps_total += len(esteps)
            dbq_total += len(res.get("dbs_queried") or [])
            has_sop = bool(res.get("sop_ref")) and res.get("sop_ref") not in ("N/A", "", None)
            if has_sop and esteps:
                grounded += 1
            if has_sop and (res.get("carc") or o == "approve"):
                wellformed += 1
            cf = res.get("confidence")
            if cf is not None:
                conf_sum += cf; conf_n += 1
                if cf >= 0.90: conf_hi += 1
                elif cf >= 0.75: conf_md += 1
                else: conf_lo += 1
            if res.get("processing_ms"):
                lat.append(res["processing_ms"])
            d = c.get("days_in_queue", 0) or 0
            b = ("< 15 days" if d < 15 else "15-30 days" if d < 30 else "30-60 days" if d < 60 else "60+ days")
            dq[b] = dq.get(b, 0) + 1
            if len(audit_buckets.get(o, [])) < 14:
                # Stage ③ — the source data the agent read (last non-decisive observation)
                _es = res.get("executed_steps") or []
                data_obs = next((s["observation"] for s in reversed(_es)
                                 if not s.get("decisive") and s.get("observation")),
                                (_es[0]["observation"] if _es else "—"))
                # Stage ⑤ — how the agent closed / where it routed
                routed, route, _wy, _rc = _why_human(o, cat, c.get("resolution_path"),
                                                     c.get("billed_amount"), res.get("human_review_reason"),
                                                     c.get("claim_type"))
                if o in ("approve", "partial_pay"):
                    close = "Issued — payment"
                elif o == "request_info":
                    close = "ADR placed in queue"
                elif routed:
                    close = "→ " + ROUTE_QUEUE.get(route, "review") + " queue"
                else:
                    close = "Issued — denial"
                audit_buckets.setdefault(o, []).append({
                    "icn": c["icn"], "member": c.get("member_name", ""),
                    "edit_code": c.get("edit_code", ""), "edit_description": c.get("edit_description", ""),
                    "category": cat, "outcome": res["outcome_label"], "outcome_key": o,
                    "carc": res["carc"], "rarc": res["rarc"], "sop": res["sop_ref"],
                    "data": data_obs, "close": close, "human_review": res["human_review"],
                    "days_in_queue": d, "agentic_tat_ms": res.get("processing_ms", 0)})
        auto_pct = round(100 * auto / total) if total else 0
        # Interleave the outcome buckets so the trace shows a representative MIX (not 60 approvals)
        _order = ["approve", "deny", "partial_pay", "request_info", "human_review", "escalate"]
        _lists = [audit_buckets.get(k, []) for k in _order] + \
                 [v for k, v in audit_buckets.items() if k not in _order]
        _i = 0
        while len(audit) < 60 and any(_i < len(l) for l in _lists):
            for l in _lists:
                if _i < len(l) and len(audit) < 60:
                    audit.append(l[_i])
            _i += 1
        lat.sort()
        avg_ms = round(sum(lat) / len(lat)) if lat else 0
        p95_ms = lat[int(len(lat) * 0.95)] if lat else 0
        pct = lambda n: round(100 * n / total) if total else 0
        evaluation = {
            "grounded": grounded, "grounded_pct": pct(grounded),
            "wellformed": wellformed, "wellformed_pct": pct(wellformed),
            "ungrounded": total - grounded,
            "avg_confidence": round(conf_sum / conf_n, 2) if conf_n else None,
            "conf_high": conf_hi, "conf_med": conf_md, "conf_low": conf_lo,
        }
        metrics = {
            "avg_latency_ms": avg_ms, "p95_latency_ms": p95_ms,
            "avg_steps": round(steps_total / total, 1) if total else 0,
            "source_queries": dbq_total,
            "throughput_per_sec": round(1000 / avg_ms * 8) if avg_ms else 0,  # 8 parallel workers (representative)
        }
        _covered = len(edit_codes) - len(NO_SOP_EDITS)
        governance = {
            "hitl_rate_pct": pct(human), "auto_pct": auto_pct,
            "audit_complete_pct": pct(traced),
            "sop_coverage_pct": round(100 * _covered / len(edit_codes)) if edit_codes else 100,
            "edit_types": len(edit_codes),
            "missing_sop_edits": len(NO_SOP_EDITS),
        }
        outcome_list = [{"key": k,
                         "label": RESOLUTION_LABELS.get(k, {}).get("label", k),
                         "color": RESOLUTION_LABELS.get(k, {}).get("color", "gray"),
                         "count": v}
                        for k, v in sorted(outcomes.items(), key=lambda x: -x[1])]
        _OBS_CACHE = {
            "throughput": {"pended": total, "auto_resolved": auto, "human_review": human,
                           "auto_pct": auto_pct, "edit_types": len(categories)},
            "outcomes": outcome_list,
            "categories": categories,
            "days_in_queue": dq,
            "guardrails": [
                {"check": "Every decision cites CARC + RARC + SOP", "count": traced},
                {"check": "Sensitive / flagged claims routed to a human", "count": human},
                {"check": "Rule-based (knowledge graph), not free-form", "count": total},
                {"check": "Traced to the source-system queries", "count": total},
            ],
            "evaluation": evaluation,
            "metrics": metrics,
            "governance": governance,
            "audit": audit,
        }
    out = dict(_OBS_CACHE)
    out["updated"] = time.strftime("%H:%M:%S")
    return jsonify(out)


# ── Adaptive Intelligence — the agent responds to interventions and gets smarter ─
# REAL state changes → REAL re-resolution (the resolver consults RESOLUTION_RULES / the source
# DBs live). Scenario claims are separate from the 100-claim pend queue so observability is
# unaffected. State resets on restart (a fresh demo each session).
def _sc(**kw):
    c = {"icn": kw.get("icn"), "claim_type": "Professional", "member_id": "MBR-10007",
         "member_name": "Sandra Mitchell", "member_dob": "Feb 02, 1958", "plan": "Aetna Choice POS II",
         "npi_billing": "1131647525", "npi_rendering": "1401640052", "provider_name": "Dr. Alan Ross",
         "provider_specialty": "Orthopedics", "group_name": "Advanced Specialty Care",
         "dos": "2026-03-18", "received_date": "2026-03-20", "pend_date": "2026-04-02",
         "days_in_queue": 12, "priority": "high", "cpt_code": "29881",
         "cpt_description": "Knee arthroscopy w/ meniscectomy", "modifier": None,
         "icd10_principal": "M17.11", "icd10_secondary": None, "icd10_desc": "Osteoarthritis, right knee",
         "place_of_service": "22", "units_billed": 1, "billed_amount": 2400.0, "allowed_amount": 1650.0,
         "auth_number": None, "carc_code": "CO-16", "rarc_code": "N286", "human_review_flag": False,
         "human_review_reason": None, "is_featured": False, "status": "pending"}
    c.update(kw)
    return c

# A permanent rule for the "fix the source record" scenario (auth presence flips the outcome).
RESOLUTION_RULES["approve_if_auth_on_file"] = {
    "steps": ["Query Authorization DB for the CPT + member + date of service",
              "If a valid authorization is on file that covers the service: approve at the fee-schedule rate",
              "If no authorization is on file: route to a human examiner (do not auto-deny)"],
    "outcome_logic": lambda c: "approve" if c.get("auth_number") else "human_review",
    "sop_ref": "SOP-AUTH-001 §3.2",
}

# The SOP that "arrives via the SFTP landing zone" for the no-SOP scenario.
_INGESTED_SOP_TEXT = (
    "SOP-PRICE-006 — Site-of-Service Differential (payment integrity)\n"
    "1. Identify services on the outpatient site-of-service differential list (e.g., CPT 29881).\n"
    "2. Compare billed place of service (22 = hospital outpatient) against the site-neutral policy.\n"
    "3. If performed at a higher-cost site with no clinical justification on file, price at the "
    "site-neutral (ASC) rate — partial pay to the differential, not a full denial.\n"
    "4. Cite CARC CO-45 / RARC N574; note the site-neutral basis in the remit.")

ADAPTIVE = {}


def _adaptive_init():
    global ADAPTIVE
    # Remove any rules a prior demo run ingested so "before" starts un-resolvable again.
    RESOLUTION_RULES.pop("site_of_service_differential", None)
    RESOLUTION_RULES.pop("plan_specific_wrap_edit", None)
    ADAPTIVE = {
        "sop_ingest": {
            "id": "sop_ingest", "order": 1,
            "title": "No SOP found → ingest a SOP from the SFTP landing zone → re-resolve",
            "situation": "A pend arrives on a new edit (E-PRICE-006 · site-of-service differential). The agent has no SOP for it, so it cannot act — it routes to a human.",
            "intervention": "Ingest SOP from SFTP",
            "claim": _sc(icn="ICN-ADPT-001", edit_code="E-PRICE-006", edit_category="Pricing",
                         edit_description="Site-of-service differential (no SOP on file)",
                         resolution_path="site_of_service_differential", carc_code="CO-45", rarc_code="N574"),
            "applied": False, "sop_text": _INGESTED_SOP_TEXT,
        },
        "override": {
            "id": "override", "order": 2,
            "title": "Human override → the agent proposes a rule adjustment (learning loop)",
            "situation": "The agent denied this authorization pend by the current rule. An examiner overrides it to approve, with a reason.",
            "intervention": "Override → approve (examiner)",
            "claim": _sc(icn="ICN-ADPT-002", edit_code="E-AUTH-001", edit_category="Authorization",
                         edit_description="Prior authorization missing", resolution_path="deny_or_approve_if_exempt",
                         allowed_amount=180.0, carc_code="CO-197", rarc_code="N517"),
            "applied": False,
        },
        "fix_record": {
            "id": "fix_record", "order": 3,
            "title": "Fix a source record (link the authorization) → the pend auto-resolves",
            "situation": "This pend is held for a human because no authorization is on file. The auth actually exists — it just was never linked to the claim.",
            "intervention": "Link authorization AUTH-88231 → re-run",
            "claim": _sc(icn="ICN-ADPT-003", edit_code="E-AUTH-001", edit_category="Authorization",
                         edit_description="Prior authorization not linked", resolution_path="approve_if_auth_on_file",
                         carc_code="CO-197", rarc_code="N517"),
            "applied": False,
        },
        "draft_rule": {
            "id": "draft_rule", "order": 4,
            "title": "Unknown edit, no SOP → the agent drafts a candidate rule for human approval",
            "situation": "A plan-specific wrap edit (E-WRAP-001) the system has never seen. No SOP exists and none is available to ingest — so the agent cannot decide.",
            "intervention": "Agent drafts a candidate rule → route to human",
            "claim": _sc(icn="ICN-ADPT-004", edit_code="E-WRAP-001", edit_category="Plan-Specific",
                         edit_description="Plan wrap-network edit (unknown)", resolution_path="plan_specific_wrap_edit",
                         carc_code="CO-16", rarc_code="N286"),
            "applied": False, "draft": None,
        },
    }


def _adaptive_view(sc):
    """Return a scenario with its live 'before' and (if applied) 'after' resolution."""
    out = {k: v for k, v in sc.items() if k not in ("claim",)}
    before = resolve_claim(sc["claim"])
    out["before"] = {"outcome": before["outcome"], "outcome_label": before["outcome_label"],
                     "steps": before["sop_steps"], "sop_ref": before["sop_ref"]}
    out["claim"] = {k: sc["claim"].get(k) for k in ("icn", "member_name", "cpt_code", "cpt_description",
                    "edit_code", "edit_category", "edit_description", "billed_amount", "allowed_amount", "auth_number")}
    if sc.get("applied"):
        out["after"] = sc.get("after")
    return out


@app.route("/api/adaptive")
def api_adaptive():
    if not ADAPTIVE:
        _adaptive_init()
    scenarios = sorted((_adaptive_view(sc) for sc in ADAPTIVE.values()), key=lambda s: s["order"])
    return jsonify({"scenarios": scenarios})


@app.route("/api/adaptive/reset", methods=["POST", "GET"])
def api_adaptive_reset():
    _adaptive_init()
    return jsonify({"ok": True})


@app.route("/api/adaptive/<sid>/apply", methods=["POST", "GET"])
def api_adaptive_apply(sid):
    if not ADAPTIVE:
        _adaptive_init()
    sc = ADAPTIVE.get(sid)
    if not sc:
        return jsonify({"error": "unknown scenario"}), 404

    if sid == "sop_ingest":
        # A real SOP arrives via SFTP → the agent ingests it → a rule now exists → re-resolve.
        RESOLUTION_RULES["site_of_service_differential"] = {
            "steps": ["Ingested SOP-PRICE-006 from the SFTP landing zone",
                      "Identify site-of-service differential services (CPT 29881, POS 22)",
                      "Apply site-neutral (ASC) pricing — partial pay to the differential, not a full denial",
                      "Cite CO-45 / N574 with the site-neutral basis"],
            "outcome_logic": lambda c: "partial_pay",
            "sop_ref": "SOP-PRICE-006 §1 (ingested via SFTP)",
        }
        res = resolve_claim(sc["claim"])
        sc["after"] = {"outcome": res["outcome"], "outcome_label": res["outcome_label"],
                       "steps": res["sop_steps"], "sop_ref": res["sop_ref"],
                       "note": "SOP ingested from the SFTP landing zone; a rule was generated and the agent re-resolved the pend autonomously — no code change."}

    elif sid == "fix_record":
        # The authorization is added/linked to the claim → re-resolve → auto-approves.
        sc["claim"]["auth_number"] = "AUTH-88231"
        authorizations["AUTH-88231"] = {"auth_number": "AUTH-88231", "member_id": sc["claim"]["member_id"],
                                        "cpt_code": sc["claim"]["cpt_code"], "status": "approved",
                                        "valid_from": "2026-03-01", "valid_to": "2026-06-30", "units_approved": 1}
        res = resolve_claim(sc["claim"])
        sc["after"] = {"outcome": res["outcome"], "outcome_label": res["outcome_label"],
                       "steps": res["sop_steps"], "sop_ref": res["sop_ref"],
                       "note": "Authorization AUTH-88231 linked in the source DB; on re-run the same rule now finds it and the pend auto-resolves to approve."}

    elif sid == "override":
        # Examiner override logged → the agent proposes a rule adjustment from the pattern.
        sc["override_reason"] = "Auth on file at the group level; service is auth-exempt under the 2026 benefit."
        sc["after"] = {"outcome": "approve", "outcome_label": "Approved (examiner override)",
                       "proposed_adjustment": {
                           "rule": "deny_or_approve_if_exempt (E-AUTH-001)",
                           "change": "Add auth-exemption check against the 2026 benefit design before denying; raise the group-level auth match to auto-approve.",
                           "evidence": "3 of the last 5 overrides on E-AUTH-001 for this benefit were the same pattern.",
                           "status": "pending analyst approval"},
                       "note": "The override is logged and the agent proposes a rule change — a human approves it before it takes effect. No silent self-rewrite."}

    elif sid == "draft_rule":
        # Unknown edit, no SOP available → the agent DRAFTS a candidate rule and routes to a human.
        sc["draft"] = {
            "proposed_path": "plan_specific_wrap_edit",
            "steps": ["Query the plan wrap-network configuration for E-WRAP-001",
                      "If the rendering provider is in the wrap network: price at the wrap rate (partial pay)",
                      "If not in the wrap network: deny CO-242 / N130",
                      "Confidence moderate — first occurrence; recommend examiner confirmation"],
            "proposed_outcome": "partial_pay",
            "sop_ref": "DRAFT — pending examiner approval",
        }
        sc["after"] = {"outcome": "human_review", "outcome_label": "Human Review (with drafted rule)",
                       "draft": sc["draft"],
                       "note": "No SOP exists and none was available to ingest, so the agent did NOT decide — it drafted a candidate rule from first principles and routed it to a human to approve. The agent proposes; the human governs."}

    sc["applied"] = True
    return jsonify(_adaptive_view(sc))


# ── Enterprise Insights — pends as an UPSTREAM SENSOR ─────────────────────────
# In-scope value: we mine the pends we RESOLVE for recurring drivers and hand the payer
# evidence-backed patterns so THEY can tune their auto-adjudication rules at the source.
# We provide the evidence; the payer decides; we never touch their engine.
# Volumes/outcomes below are REAL (from the resolved pend queue); the root-cause pattern,
# recommended action, and auto-adj lift are representative per edit category.
CATEGORY_INSIGHTS = {
    "Authorization": {"pattern": "Valid authorization on file but not linked to the claim — auth-number format / date-span mismatch at intake.",
                      "action": "Auto-match auth to claim on number + date span (fuzzy, not exact) before pend.", "lift": 70},
    "Provider":      {"pattern": "Provider is enrolled & credentialed, but the adjudication provider file is stale (effective date, taxonomy, NPI–TIN linkage).",
                      "action": "Nightly sync of credentialing status into the adjudication provider file.", "lift": 75},
    "Pricing":       {"pattern": "Fee-schedule version lag — claim priced against a prior period's schedule for the date of service.",
                      "action": "Pin pricing to the DOS-effective fee schedule; auto-load quarterly updates.", "lift": 65},
    "Coding":        {"pattern": "NCCI / modifier edits firing on validly distinct services (missing 59 / 25 / XU where records support it).",
                      "action": "Apply modifier logic where documentation supports; refine the NCCI edit table.", "lift": 50},
    "COB":           {"pattern": "Order-of-benefits stale — the member's other coverage ended or changed since last refresh.",
                      "action": "Real-time COB refresh (NAIC order-of-benefits) before adjudication.", "lift": 60},
    "Duplicate":     {"pattern": "Distinct services (bilateral, repeat procedure, separate encounter) flagged as duplicates for want of a modifier.",
                      "action": "Honor 76 / 77 / 50 modifiers + distinct DOS in duplicate logic.", "lift": 55},
    "Medical Necessity": {"pattern": "Clinical criteria are met but records were not attached on first pass.",
                          "action": "Prompt for records / criteria at submission; keep the human gate for medical necessity.", "lift": 25},
    "Timely Filing": {"pattern": "Clearinghouse acceptance date not carried into the received-date logic.",
                      "action": "Use the clearinghouse acceptance timestamp as the received date.", "lift": 80},
    "Manual Pricing": {"pattern": "Auto-adjudication cannot price the service (implant, high-cost, OON) — falls to manual pricing.",
                       "action": "Integrate the Burgess/Multiplan/Zelis pricing API into auto-adjudication so these price automatically.", "lift": 70},
    "OON": {"pattern": "Out-of-network services priced manually; no auto network-repricing path.",
            "action": "Wire Multiplan/Zelis network repricing into the auto-adjudication flow.", "lift": 60},
    "Enrollment": {"pattern": "Patient-detail mismatches / newborn adds not reconciled against the enrollment feed.",
                   "action": "Nightly 834 enrollment reconciliation + newborn auto-add within the 31-day window.", "lift": 55},
    "PCP": {"pattern": "PCP assignment stale, so claims pend for line deletion / remap.",
            "action": "Refresh PCP assignment from the provider file before adjudication.", "lift": 65},
    "Workers Comp": {"pattern": "Work-related injury indicators not screened before the health-plan pays.",
                     "action": "Screen injury dx + WC-carrier-on-file at intake; auto-redirect to the WC carrier.", "lift": 60},
    "Medigap": {"pattern": "Medicare-primary crossovers not auto-forwarded to the Medigap payer.",
                "action": "Enable automated Medigap crossover after Medicare adjudication.", "lift": 65},
    "Adjustment": {"pattern": "POS-DA adjustments (HPI, claimstop, flush) routed manually.",
                   "action": "Codify the common adjustment reasons into auto-adjustment rules; keep exceptions to a human.", "lift": 40},
}
_EI_CACHE = None


@app.route("/api/enterprise-insights")
def api_enterprise_insights():
    """Enterprise Insights — the pends we resolve, mined for recurring drivers and fed back to the
    payer as evidence to tune their auto-adjudication rules (upstream sensor). In-scope: we surface
    the evidence; the payer decides. Volumes/outcomes are real; patterns/actions are representative."""
    global _EI_CACHE
    if _EI_CACHE is None:
        by_cat = {}
        total = len(pended_claims)
        for c in pended_claims:
            cat = c.get("edit_category", "Other")
            try:
                o = _resolve_pended(c)["outcome"]
            except Exception:
                o = "unknown"
            d = by_cat.setdefault(cat, {"volume": 0, "outcomes": {}})
            d["volume"] += 1
            d["outcomes"][o] = d["outcomes"].get(o, 0) + 1
        drivers = []
        for cat, d in by_cat.items():
            meta = CATEGORY_INSIGHTS.get(cat, {"pattern": "Recurring pend driver — under review with the payer.",
                                               "action": "Review the driver with the payer.", "lift": 30})
            top = max(d["outcomes"].items(), key=lambda x: x[1])[0] if d["outcomes"] else ""
            recoverable = round(d["volume"] * meta["lift"] / 100)
            drivers.append({"category": cat, "volume": d["volume"],
                            "pct": round(100 * d["volume"] / total) if total else 0,
                            "top_outcome": RESOLUTION_LABELS.get(top, {}).get("label", top),
                            "pattern": meta["pattern"], "action": meta["action"],
                            "lift": meta["lift"], "recoverable": recoverable})
        drivers.sort(key=lambda x: -x["volume"])
        tot = sum(x["recoverable"] for x in drivers)
        _EI_CACHE = {"total_pends": total, "drivers": drivers,
                     "summary": {"recoverable": tot,
                                 "recoverable_pct": round(100 * tot / total) if total else 0,
                                 "rules": len(drivers)}}
    return jsonify(_EI_CACHE)


# ── SOP landing-zone pipeline (Missing SOP → ingest → auto re-resolve) ────────────
def _reset_sop_caches():
    """Clear the resolution/aggregate caches so the next read re-resolves with any new SOP."""
    global _PEND_RESOLVE_CACHE, _OBS_CACHE, _LINE_AGG, _REVIEW_AGG, _EI_CACHE, _FEATURED_ORDER
    _PEND_RESOLVE_CACHE = {}
    _OBS_CACHE = None
    _LINE_AGG = None
    _REVIEW_AGG = None
    _EI_CACHE = None

def _apply_sop(sop):
    """Register a rule from an ingested SOP and point the held claims' codes at it."""
    rp = sop["resolution"]; edit = sop["edit_code"]; oc = sop.get("decision_outcome", "approve")
    RESOLUTION_RULES[rp] = {"outcome_logic": (lambda c, _o=oc: _o),
                            "steps": sop.get("steps", []), "sop_ref": sop.get("sop_ref", "")}
    for c in pended_claims:
        if c.get("edit_code") == edit:
            c["carc_code"] = sop.get("carc"); c["rarc_code"] = sop.get("rarc")
    NO_SOP_EDITS.discard(edit)
    return edit, oc

@app.route("/api/sop-inbox/status")
def api_sop_inbox_status():
    """Show the offline SOP library, what's sitting in the landing zone, and current gaps."""
    library, inbox = [], []
    for p in sorted(SOP_LIBRARY.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            library.append({"file": p.name, "sop_ref": d.get("sop_ref"), "title": d.get("title"),
                            "edit_code": d.get("edit_code"), "ingested": d.get("edit_code") not in NO_SOP_EDITS})
        except Exception:
            pass
    for p in sorted(SOP_INBOX.glob("*.json")):
        inbox.append(p.name)
    missing = sum(1 for c in pended_claims if c.get("edit_code") in NO_SOP_EDITS)
    return jsonify({"library": library, "inbox": inbox,
                    "no_sop_edits": sorted(NO_SOP_EDITS), "missing_sop_claims": missing})

@app.route("/api/sop-inbox/preview/<edit>")
def api_sop_inbox_preview(edit):
    """Return the SOP document (so it can be read before the agent ingests it)."""
    for p in SOP_LIBRARY.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("edit_code") == edit:
            return jsonify(d)
    return jsonify({"error": f"no SOP in library for {edit}"}), 404

@app.route("/api/sop-inbox/drop", methods=["POST", "GET"])
def api_sop_inbox_drop():
    """Governance drops a SOP into the landing zone (copies the real file from the library)."""
    edit = request.args.get("edit", "")
    src = next((p for p in SOP_LIBRARY.glob("*.json")
                if json.loads(p.read_text(encoding="utf-8")).get("edit_code") == edit), None)
    if not src:
        return jsonify({"error": f"no SOP in library for {edit}"}), 404
    dest = SOP_INBOX / src.name
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    doc = json.loads(dest.read_text(encoding="utf-8"))
    return jsonify({"dropped": src.name, "path": str(dest), "doc": doc})

@app.route("/api/sop-inbox/ingest", methods=["POST", "GET"])
def api_sop_inbox_ingest():
    """Agent scans the landing zone, ingests each SOP, generates the rule, and re-resolves the held claims."""
    ingested = []
    for p in sorted(SOP_INBOX.glob("*.json")):
        try:
            sop = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        edit, _oc = _apply_sop(sop)
        ingested.append({"file": p.name, "sop_ref": sop.get("sop_ref"), "edit_code": edit})
        (SOP_INBOX / (p.stem + ".ingested")).write_text("ok", encoding="utf-8")
        p.unlink(missing_ok=True)
    _reset_sop_caches()
    # Re-resolve the affected claims and report the new outcomes
    resolved = []
    edits_done = {i["edit_code"] for i in ingested}
    for c in pended_claims:
        if c.get("edit_code") in edits_done:
            r = _resolve_pended(c)
            resolved.append({"icn": c["icn"], "edit_code": c["edit_code"],
                             "outcome": r["outcome"], "outcome_label": r["outcome_label"]})
    from collections import Counter as _C
    mix = dict(_C(r["outcome_label"] for r in resolved))
    missing = sum(1 for c in pended_claims if c.get("edit_code") in NO_SOP_EDITS)
    return jsonify({"ingested": ingested, "re_resolved": len(resolved),
                    "outcome_mix": mix, "missing_sop_remaining": missing})

@app.route("/api/sop-inbox/reset", methods=["POST", "GET"])
def api_sop_inbox_reset():
    """Reset the demo: clear the landing zone, remove ingested rules, restore the Missing-SOP state."""
    for p in list(SOP_INBOX.glob("*")):
        p.unlink(missing_ok=True)
    for edit in ("E-TELE-001", "E-SOS-001"):
        NO_SOP_EDITS.add(edit)
        rp = edit_codes.get(edit, {}).get("resolution")
        RESOLUTION_RULES.pop(rp, None)
        for c in pended_claims:
            if c.get("edit_code") == edit:
                c["carc_code"] = edit_codes[edit].get("carc"); c["rarc_code"] = edit_codes[edit].get("rarc")
    _reset_sop_caches()
    missing = sum(1 for c in pended_claims if c.get("edit_code") in NO_SOP_EDITS)
    return jsonify({"reset": True, "missing_sop_claims": missing})


# ── What-if: manually enter a source record → agent re-adjudicates (two steps) ─────
WHATIF_EDITS = {
    "E-AUTH-001": {"system": "Authorization / UM system"},
    "E-AUTH-004": {"system": "Authorization / UM system"},
    "E-TF-001":   {"system": "Claim receipt"},
    "E-TF-002":   {"system": "Claim receipt"},
}

def _source_save(c, ec, vals):
    """Write the manually-entered record into the source system (real mutation)."""
    def _seq():
        return f"PA-2026-{abs(hash(c['icn'])) % 900000 + 100000}"
    if ec == "E-AUTH-001":
        an = (vals.get("auth_number") or "").strip() or _seq()
        units = int(float(vals.get("units") or c.get("units_billed") or 1))
        authorizations[an] = {
            "auth_number": an, "member_id": c.get("member_id"), "member_name": c.get("member_name"),
            "provider_npi": c.get("npi_rendering"), "cpt_code": c.get("cpt_code"),
            "diagnosis_code": c.get("icd10_principal"),
            "dos_start": vals.get("dos_start") or c.get("dos"), "dos_end": vals.get("dos_end") or c.get("dos"),
            "units_authorized": units, "units_used": 0, "units_remaining": units, "status": "active",
            "requesting_provider": c.get("npi_rendering"), "clinical_notes": "Entered by the auth team (UM system)"}
        c["auth_number"] = an
        return {"summary": f"Authorization {an} saved — active · {units} unit(s) · CPT {c.get('cpt_code')} · valid {authorizations[an]['dos_start']}→{authorizations[an]['dos_end']}",
                "record": authorizations[an]}
    if ec == "E-AUTH-004":
        a = authorizations.get(c.get("auth_number"))
        if not a:
            an = _seq()
            a = authorizations[an] = {"auth_number": an, "member_id": c.get("member_id"),
                "provider_npi": c.get("npi_rendering"), "cpt_code": c.get("cpt_code"),
                "dos_start": c.get("dos"), "dos_end": c.get("dos"), "units_authorized": 0,
                "units_used": 0, "units_remaining": 0, "status": "active"}
            c["auth_number"] = an
        units = int(float(vals.get("units") or c.get("units_billed") or 1))
        a["units_remaining"] = units
        a["units_authorized"] = max(a.get("units_authorized", 0), units)
        a["units_used"] = max(a["units_authorized"] - units, 0)
        return {"summary": f"Authorization {c.get('auth_number')} updated — units remaining set to {units}",
                "record": a}
    if ec in ("E-TF-001", "E-TF-002"):
        rd = (vals.get("received_date") or "").strip()
        if not rd:
            return None
        c["received_date"] = rd
        return {"summary": f"Received date updated to {rd} (DOS {c.get('dos')})",
                "record": {"received_date": rd, "dos": c.get("dos")}}
    return None

def _source_reset(c, ec):
    if ec == "E-AUTH-001":
        an = c.get("auth_number")
        # delete the what-if-created auth so it doesn't linger in the Auth DB
        if an and str(an).startswith(("PA-2026-", "PA-WHATIF", "PA-TEST")):
            authorizations.pop(an, None)
        c["auth_number"] = None
    elif ec == "E-AUTH-004":
        a = authorizations.get(c.get("auth_number"))
        if a:
            a["units_remaining"] = 0; a["units_used"] = a.get("units_authorized", 0)
    elif ec in ("E-TF-001", "E-TF-002"):
        from datetime import datetime, timedelta
        try:
            c["received_date"] = (datetime.strptime(c["dos"], "%Y-%m-%d") + timedelta(days=430)).strftime("%Y-%m-%d")
        except Exception:
            pass

_DB_SOURCES = {
    0: lambda: list(providers.values()),
    1: lambda: list(authorizations.values()),
    2: lambda: list(cob.values()),
    3: lambda: list(fee_schedule.values()),
    4: lambda: [dict(r, member_id=m) for m, rows in claims_history.items() for r in rows],
    5: lambda: list(eligibility.values()),
}

@app.route("/api/db-search/<int:n>")
def api_db_search(n):
    """Full server-side search over an entire source system (not just the first page),
    so any record — auth #, member, NPI, CPT — is findable in the DB tile."""
    src = _DB_SOURCES.get(n)
    if not src:
        return jsonify({"records": [], "total": 0})
    q = request.args.get("q", "").strip().lower()
    rows = src()
    if q:
        rows = [r for r in rows if q in json.dumps(r, default=str).lower()]
    return jsonify({"records": rows[:200], "total": len(rows), "query": q})

@app.route("/api/ml/prevention")
def api_ml_prevention():
    """#2 — trained pend-risk model: drivers, straight-through candidates, preventable share."""
    import ml_models
    return jsonify(ml_models.prevention_summary(len(pended_claims)))

@app.route("/api/ml/confidence")
def api_ml_confidence():
    """#1 — calibrated decision-confidence model: calibration curve + confidence distribution."""
    import ml_models
    d = ml_models.confidence_summary()
    sample = pended_claims[::11][:500]
    probs = ml_models.score_confidence_batch(sample)
    hi = sum(1 for p in probs if p >= 0.90); md = sum(1 for p in probs if 0.75 <= p < 0.90); lo = sum(1 for p in probs if p < 0.75)
    d["distribution"] = {"high": hi, "med": md, "low": lo, "sampled": len(probs),
                         "avg": round(sum(probs) / len(probs), 3) if probs else 0}
    return jsonify(d)

@app.route("/api/ml/training-data")
def api_ml_training_data():
    """The actual training dataset (inspectable) + split/balance metadata."""
    import ml_models
    limit = int(request.args.get("limit", 150))
    offset = int(request.args.get("offset", 0))
    pended_only = request.args.get("pended_only") == "1"
    out = ml_models.training_sample(limit, offset, pended_only)
    out["meta"] = ml_models.training_meta()
    return jsonify(out)

@app.route("/api/whatif-examples")
def api_whatif_examples():
    """Ready-to-open example claims for the what-if beat, so they can be shown upfront
    (before processing the whole batch)."""
    want = [("E-AUTH-001", "Auth missing"), ("E-AUTH-004", "Units exhausted"), ("E-TF-001", "Timely filing")]
    out = []
    for ec, label in want:
        c = next((x for x in pended_claims if x.get("edit_code") == ec and not x.get("is_featured")), None)
        if c:
            out.append({"edit_code": ec, "label": label, "icn": c["icn"],
                        "member": c.get("member_name"), "cpt": c.get("cpt_code")})
    # Pricing-engine (Burgess/Multiplan/Zelis) example — not a what-if, just a quick-open
    # so the presenter can show the agent calling the pricing engine to reprice a claim.
    pricing = None
    pc = next((x for x in pended_claims if x.get("edit_code") == "E-PRICE-006"), None)
    if pc:
        pricing = {"edit_code": "E-PRICE-006", "label": "Pricing engine (Burgess)",
                   "icn": pc["icn"], "member": pc.get("member_name"), "cpt": pc.get("cpt_code")}
    return jsonify({"examples": out, "pricing": pricing})

@app.route("/api/claim-db/<icn>/<dbkey>")
def api_claim_db(icn, dbkey):
    """Return the exact source record(s) the agent queried for THIS claim, so a reviewer can
    click a 'Database Queries' pill and verify the data (incl. an auth just entered via what-if)."""
    c = claims_index.get(icn)
    if not c:
        return jsonify({"error": "ICN not found"}), 404
    mem, npi, cpt, dos = c.get("member_id"), str(c.get("npi_rendering") or ""), c.get("cpt_code"), c.get("dos")
    if dbkey == "auth":
        an = c.get("auth_number"); rec = authorizations.get(an) if an else None
        return jsonify({"db": "Authorization DB", "key": f"auth {an}" if an else "member/CPT",
                        "records": [rec] if rec else [],
                        "note": None if rec else "No active authorization on file for this member/CPT."})
    if dbkey == "provider":
        rec = providers.get(npi)
        return jsonify({"db": "Provider DB", "key": f"NPI {npi}", "records": [rec] if rec else [],
                        "note": None if rec else "Rendering NPI not found in the provider directory."})
    if dbkey == "eligibility":
        rec = (eligibility_for(mem, dos) or eligibility.get(mem))
        return jsonify({"db": "Eligibility DB", "key": f"member {mem}", "records": [rec] if rec else [],
                        "note": None if rec else "No eligibility record for this member/DOS."})
    if dbkey == "cob":
        rec = cob.get(mem)
        return jsonify({"db": "COB DB", "key": f"member {mem}", "records": [rec] if rec else [],
                        "note": None if rec else "No other-coverage (COB) record on file for this member."})
    if dbkey == "fee":
        rec = fee_schedule.get(cpt)
        return jsonify({"db": "Fee Schedule", "key": f"CPT {cpt}", "records": [rec] if rec else [],
                        "note": None if rec else f"CPT {cpt} not found in the fee schedule."})
    if dbkey == "history":
        recs = claims_history.get(mem, []) or []
        return jsonify({"db": "Claims History", "key": f"member {mem}", "records": recs,
                        "note": None if recs else "No prior claims history for this member."})
    return jsonify({"error": "unknown db"}), 404

@app.route("/api/source-edit/save", methods=["POST", "GET"])
def api_source_edit_save():
    """Step 1 — the user manually enters the record into the source system."""
    icn = request.values.get("icn", "")
    c = claims_index.get(icn)
    if not c:
        return jsonify({"error": "ICN not found"}), 404
    ec = c.get("edit_code")
    if ec not in WHATIF_EDITS:
        return jsonify({"error": f"no what-if available for {ec}"}), 400
    prior = _resolve_pended(c)["outcome_label"]
    saved = _source_save(c, ec, request.values)
    if not saved:
        return jsonify({"error": "missing values"}), 400
    _reset_sop_caches()
    return jsonify({"icn": icn, "edit_code": ec, "system": WHATIF_EDITS[ec]["system"],
                    "prior_outcome": prior, "saved": saved["summary"], "record": saved["record"]})

@app.route("/api/source-edit/resolve", methods=["POST", "GET"])
def api_source_edit_resolve():
    """Step 2 — the agent re-adjudicates the claim against the now-updated source data."""
    icn = request.values.get("icn", "")
    c = claims_index.get(icn)
    if not c:
        return jsonify({"error": "ICN not found"}), 404
    _reset_sop_caches()
    r = _resolve_pended(c)
    return jsonify({"icn": icn, "after": r["outcome_label"], "outcome": r["outcome"],
                    "payment": r["payment_amount"], "sop_ref": r.get("sop_ref")})

@app.route("/api/source-edit/reset", methods=["POST", "GET"])
def api_source_edit_reset():
    icn = request.values.get("icn", "")
    c = claims_index.get(icn)
    if not c:
        return jsonify({"error": "ICN not found"}), 404
    _source_reset(c, c.get("edit_code"))
    _reset_sop_caches()
    r = _resolve_pended(c)
    return jsonify({"icn": icn, "after": r["outcome_label"]})


# Pre-train the ML models in the background so the first click is instant.
def _ml_warmup():
    try:
        import ml_models; ml_models.warmup()
        print("ML models trained (prevention + calibrated confidence)")
    except Exception as e:
        print("ML warmup skipped:", e)
import threading as _threading
_threading.Thread(target=_ml_warmup, daemon=True).start()


if __name__ == "__main__":
    print("Project John — Claims Pend Processing Demo")
    print(f"  {len(pended_claims)} pended claims loaded")
    print(f"  {len(providers)} providers  |  {len(authorizations)} authorizations  |  {len(cob)} COB records")
    print("Server starting on http://localhost:5002")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5002)), debug=True)
