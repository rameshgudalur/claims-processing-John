"""
Project John — Claims Pend Processing
Generates all 6 databases + 100 pended claims
Run: python generate_data.py
"""
import json, random, uuid
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).parent / "data"
BASE.mkdir(exist_ok=True)

random.seed(42)

# ── REFERENCE DATA ─────────────────────────────────────────────────────────────

SPECIALTIES = [
    "Internal Medicine", "Family Medicine", "Orthopedic Surgery",
    "Cardiology", "Gastroenterology", "Neurology", "Psychiatry",
    "Physical Therapy", "Radiology", "Anesthesiology", "General Surgery",
    "Oncology", "Pulmonology", "Endocrinology", "Dermatology"
]

PROVIDER_NAMES = [
    ("Dr. James Harrington", "Internal Medicine"),
    ("Dr. Maria Santos", "Family Medicine"),
    ("Dr. Kevin Okafor", "Orthopedic Surgery"),
    ("Dr. Linda Chen", "Cardiology"),
    ("Dr. Robert Patel", "Gastroenterology"),
    ("Dr. Susan Whitmore", "Neurology"),
    ("Dr. David Kim", "Psychiatry"),
    ("Dr. Angela Torres", "Physical Therapy"),
    ("Dr. Michael Ross", "Radiology"),
    ("Dr. Patricia Nguyen", "Anesthesiology"),
    ("Dr. Thomas Caldwell", "General Surgery"),
    ("Dr. Rebecca Sharma", "Oncology"),
    ("Dr. William Foster", "Pulmonology"),
    ("Dr. Nancy Jackson", "Endocrinology"),
    ("Dr. Charles Moore", "Dermatology"),
    ("Dr. Helen Rivera", "Internal Medicine"),
    ("Dr. Frank Mitchell", "Orthopedic Surgery"),
    ("Dr. Carol Bennett", "Cardiology"),
    ("Dr. George Washington", "Family Medicine"),
    ("Dr. Dorothy Hayes", "Gastroenterology"),
    ("Dr. Raymond Clark", "Neurology"),
    ("Dr. Alice Cooper", "Psychiatry"),
    ("Dr. Bruce Lee", "Physical Therapy"),
    ("Dr. Diana Prince", "Radiology"),
    ("Dr. Clark Kent", "General Surgery"),
    ("Dr. Peter Parker", "Anesthesiology"),
    ("Dr. Mary Jane", "Internal Medicine"),
    ("Dr. Tony Stark", "Cardiology"),
    ("Dr. Steve Rogers", "Orthopedic Surgery"),
    ("Dr. Natasha Romanoff", "Oncology"),
    ("Dr. Bruce Banner", "Pulmonology"),
    ("Dr. Thor Odinson", "Endocrinology"),
    ("Dr. Wanda Maximoff", "Dermatology"),
    ("Dr. Scott Lang", "Family Medicine"),
    ("Dr. Hope Van Dyne", "Neurology"),
    ("Dr. Sam Wilson", "Psychiatry"),
    ("Dr. Bucky Barnes", "Physical Therapy"),
    ("Dr. Carol Danvers", "Radiology"),
    ("Dr. Nick Fury", "General Surgery"),
    ("Dr. Phil Coulson", "Internal Medicine"),
]

GROUPS = [
    ("Northeast Medical Group", "G-1001"),
    ("Premier Health Associates", "G-1002"),
    ("Coastal Physicians Network", "G-1003"),
    ("Midwest Surgical Group", "G-1004"),
    ("Advanced Specialty Care", "G-1005"),
    ("Valley Medical Partners", "G-1006"),
    ("Summit Health System", "G-1007"),
    ("Lakeside Medical Group", "G-1008"),
]

CPT_TABLE = {
    "99213": {"desc": "Office Visit — Established Patient, Low Complexity",        "specialty": "Internal Medicine", "allowed": 92.00,  "max_units": 1, "global_days": 0},
    "99214": {"desc": "Office Visit — Established Patient, Moderate Complexity",   "specialty": "Internal Medicine", "allowed": 133.00, "max_units": 1, "global_days": 0},
    "99215": {"desc": "Office Visit — Established Patient, High Complexity",        "specialty": "Internal Medicine", "allowed": 176.00, "max_units": 1, "global_days": 0},
    "99232": {"desc": "Subsequent Hospital Care, Moderate Complexity",              "specialty": "Internal Medicine", "allowed": 112.00, "max_units": 1, "global_days": 0},
    "27447": {"desc": "Total Knee Arthroplasty",                                   "specialty": "Orthopedic Surgery", "allowed": 1580.00,"max_units": 1, "global_days": 90},
    "29827": {"desc": "Shoulder Arthroscopy with Rotator Cuff Repair",             "specialty": "Orthopedic Surgery", "allowed": 890.00, "max_units": 1, "global_days": 90},
    "29881": {"desc": "Knee Arthroscopy with Meniscectomy",                        "specialty": "Orthopedic Surgery", "allowed": 720.00, "max_units": 1, "global_days": 90},
    "43239": {"desc": "Upper GI Endoscopy with Biopsy",                            "specialty": "Gastroenterology", "allowed": 310.00, "max_units": 1, "global_days": 0},
    "47562": {"desc": "Laparoscopic Cholecystectomy",                              "specialty": "General Surgery",   "allowed": 940.00, "max_units": 1, "global_days": 90},
    "70553": {"desc": "MRI Brain with and without Contrast",                        "specialty": "Radiology",         "allowed": 420.00, "max_units": 1, "global_days": 0},
    "71046": {"desc": "Chest X-Ray, 2 Views",                                      "specialty": "Radiology",         "allowed": 48.00,  "max_units": 1, "global_days": 0},
    "72148": {"desc": "MRI Lumbar Spine without Contrast",                          "specialty": "Radiology",         "allowed": 380.00, "max_units": 1, "global_days": 0},
    "74177": {"desc": "CT Abdomen and Pelvis with Contrast",                        "specialty": "Radiology",         "allowed": 360.00, "max_units": 1, "global_days": 0},
    "93000": {"desc": "Electrocardiogram with Interpretation",                      "specialty": "Cardiology",        "allowed": 29.00,  "max_units": 1, "global_days": 0},
    "93306": {"desc": "Echocardiography with Doppler",                              "specialty": "Cardiology",        "allowed": 295.00, "max_units": 1, "global_days": 0},
    "97110": {"desc": "Therapeutic Exercise",                                       "specialty": "Physical Therapy",  "allowed": 42.00,  "max_units": 8, "global_days": 0},
    "97530": {"desc": "Therapeutic Activities",                                     "specialty": "Physical Therapy",  "allowed": 44.00,  "max_units": 8, "global_days": 0},
    "97012": {"desc": "Traction Therapy",                                           "specialty": "Physical Therapy",  "allowed": 25.00,  "max_units": 4, "global_days": 0},
    "90837": {"desc": "Psychotherapy, 60 minutes",                                  "specialty": "Psychiatry",        "allowed": 134.00, "max_units": 1, "global_days": 0},
    "90834": {"desc": "Psychotherapy, 45 minutes",                                  "specialty": "Psychiatry",        "allowed": 103.00, "max_units": 1, "global_days": 0},
    "96413": {"desc": "Chemotherapy Administration, IV Infusion, First Hour",       "specialty": "Oncology",          "allowed": 145.00, "max_units": 1, "global_days": 0},
    "96415": {"desc": "Chemotherapy Administration, IV Infusion, Each Add'l Hour",  "specialty": "Oncology",          "allowed": 65.00,  "max_units": 5, "global_days": 0},
    "20610": {"desc": "Aspiration and/or Injection, Major Joint",                   "specialty": "Orthopedic Surgery", "allowed": 74.00, "max_units": 1, "global_days": 10},
    "96374": {"desc": "IV Push Injection",                                          "specialty": "Internal Medicine", "allowed": 55.00,  "max_units": 1, "global_days": 0},
    "80053": {"desc": "Comprehensive Metabolic Panel",                              "specialty": "Internal Medicine", "allowed": 14.00,  "max_units": 1, "global_days": 0},
    "85025": {"desc": "Complete Blood Count with Differential",                     "specialty": "Internal Medicine", "allowed": 10.00,  "max_units": 1, "global_days": 0},
    "36415": {"desc": "Routine Venipuncture",                                       "specialty": "Internal Medicine", "allowed": 3.00,   "max_units": 1, "global_days": 0},
    "G0289": {"desc": "Arthroscopy, Knee — Surgical",                               "specialty": "Orthopedic Surgery", "allowed": 580.00,"max_units": 1, "global_days": 90},
    "01402": {"desc": "Anesthesia — Total Knee Arthroplasty",                       "specialty": "Anesthesiology",    "allowed": 420.00, "max_units": 1, "global_days": 0},
    "99291": {"desc": "Critical Care, First 30-74 minutes",                         "specialty": "Internal Medicine", "allowed": 218.00, "max_units": 1, "global_days": 0},
}

ICD10_TABLE = {
    "M17.11": "Primary osteoarthritis, right knee",
    "M17.12": "Primary osteoarthritis, left knee",
    "M54.5":  "Low back pain",
    "M75.1":  "Rotator cuff syndrome",
    "J18.9":  "Pneumonia, unspecified",
    "I10":    "Essential hypertension",
    "I25.10": "Atherosclerotic heart disease",
    "E11.9":  "Type 2 diabetes mellitus without complications",
    "E11.65": "Type 2 diabetes mellitus with hyperglycemia",
    "F32.1":  "Major depressive disorder, single episode, moderate",
    "F41.1":  "Generalized anxiety disorder",
    "K80.20": "Calculus of gallbladder without cholecystitis",
    "S83.209A":"Unspecified tear of unspecified meniscus",
    "G89.29": "Other chronic pain",
    "G43.909": "Migraine, unspecified",
    "C34.11": "Malignant neoplasm of upper lobe, right bronchus",
    "Z23":    "Encounter for immunization",
    "Z12.31": "Encounter for screening mammogram",
    "N18.3":  "Chronic kidney disease, stage 3",
    "M23.611":"Spontaneous disruption of anterior cruciate ligament, right knee",
}

MEMBERS = [
    ("MBR-10001", "Eleanor Hawkins",   "Jan 12, 1958", "BlueCross Gold PPO"),
    ("MBR-10002", "Robert Chen",       "Mar 05, 1963", "Aetna Choice POS II"),
    ("MBR-10003", "Margaret O'Brien",  "Jul 22, 1955", "UnitedHealth Choice Plus"),
    ("MBR-10004", "James Whitfield",   "Nov 30, 1960", "Humana HMO Gold"),
    ("MBR-10005", "Patricia Morales",  "Feb 14, 1967", "Cigna Connect 250"),
    ("MBR-10006", "David Kowalski",    "Sep 08, 1952", "BlueCross Gold PPO"),
    ("MBR-10007", "Sandra Mitchell",   "Apr 19, 1970", "Aetna Choice POS II"),
    ("MBR-10008", "Thomas Nguyen",     "Dec 03, 1958", "UnitedHealth Choice Plus"),
    ("MBR-10009", "Barbara Foster",    "Jun 27, 1965", "Humana HMO Gold"),
    ("MBR-10010", "Richard Yamamoto",  "Aug 15, 1961", "Cigna Connect 250"),
    ("MBR-10011", "Dorothy Hamilton",  "May 07, 1953", "BlueCross Gold PPO"),
    ("MBR-10012", "Charles Rivera",    "Oct 21, 1969", "Aetna Choice POS II"),
    ("MBR-10013", "Helen Washington",  "Jan 30, 1956", "UnitedHealth Choice Plus"),
    ("MBR-10014", "Frank Thompson",    "Mar 17, 1964", "Humana HMO Gold"),
    ("MBR-10015", "Ruth Anderson",     "Jul 04, 1959", "Cigna Connect 250"),
    ("MBR-10016", "George Martinez",   "Nov 11, 1971", "BlueCross Gold PPO"),
    ("MBR-10017", "Alice Johnson",     "Feb 28, 1957", "Aetna Choice POS II"),
    ("MBR-10018", "Harold Brown",      "Sep 14, 1962", "UnitedHealth Choice Plus"),
    ("MBR-10019", "Ethel Davis",       "Apr 01, 1954", "Humana HMO Gold"),
    ("MBR-10020", "Walter Wilson",     "Dec 19, 1966", "Cigna Connect 250"),
]

# ── GENERATE PROVIDERS ────────────────────────────────────────────────────────

def gen_npi():
    return "1" + "".join([str(random.randint(0,9)) for _ in range(9)])

def generate_providers():
    providers = {}
    npis = []
    for i, (name, specialty) in enumerate(PROVIDER_NAMES):
        npi = gen_npi()
        npis.append(npi)
        group = random.choice(GROUPS)
        cred_status = random.choices(
            ["active", "active", "active", "active", "expired", "suspended"],
            weights=[50, 50, 50, 50, 8, 2]
        )[0]
        net_status = random.choices(
            ["in_network", "in_network", "in_network", "out_of_network", "terminated"],
            weights=[60, 60, 60, 15, 5]
        )[0]
        contract_start = date(2023, 1, 1)
        contract_end   = date(2026, 12, 31) if cred_status == "active" else date(2025, 6, 30)
        providers[npi] = {
            "npi":                  npi,
            "name":                 name,
            "specialty":            specialty,
            "group_npi":            group[1],
            "group_name":           group[0],
            "network_status":       net_status,
            "credentialing_status": cred_status,
            "credential_expiry":    str(contract_end),
            "contract_effective":   str(contract_start),
            "contract_end":         str(contract_end),
            "place_of_service":     random.choice(["11","22","21","24","19"]),
            "taxonomy_code":        f"207{random.choice(['R','X','Q','P','N'])}00000X",
        }
    return providers, npis

# ── GENERATE AUTHORIZATIONS ────────────────────────────────────────────────────

def generate_authorizations(members, npis):
    auths = {}
    auth_cpts = ["27447","29827","70553","93306","96413","96415","97110","97530","90837","43239","47562","29881"]
    for i in range(60):
        member = random.choice(members)
        cpt    = random.choice(auth_cpts)
        npi    = random.choice(npis)
        dos_start = date(2026, random.randint(1,5), random.randint(1,28))
        dos_end   = dos_start + timedelta(days=random.choice([30,60,90,180]))
        units_auth = random.choice([1,1,1,2,4,8,12])
        units_used = random.randint(0, units_auth)
        status = random.choices(
            ["active","active","active","expired","void"],
            weights=[60,60,60,15,5]
        )[0]
        if status == "expired":
            dos_end = date(2025, random.randint(6,12), 28)
        auth_num = f"PA-{2026}{str(i+1000)}"
        auth_icd = random.choice(list(ICD10_TABLE.keys()))
        auths[auth_num] = {
            "auth_number":      auth_num,
            "member_id":        member[0],
            "member_name":      member[1],
            "provider_npi":     npi,
            "cpt_code":         cpt,
            "diagnosis_code":   auth_icd,
            "dos_start":        str(dos_start),
            "dos_end":          str(dos_end),
            "units_authorized": units_auth,
            "units_used":       units_used,
            "units_remaining":  units_auth - units_used,
            "status":           status,
            "approved_date":    str(dos_start - timedelta(days=random.randint(3,14))),
            "requesting_provider": npi,
            "clinical_notes":   "Clinical documentation on file" if random.random() > 0.3 else "Pending documentation",
        }
    return auths

# ── GENERATE COB ──────────────────────────────────────────────────────────────

def generate_cob(members):
    cob = {}
    carriers = [
        ("Medicare Part B", "CMS", "primary"),
        ("Medicaid", "State Agency", "secondary"),
        ("Blue Shield of CA", "BS-CA-2891", "secondary"),
        ("Cigna Commercial", "CIG-GROUP-441", "secondary"),
        ("United Healthcare", "UHC-EMP-7712", "primary"),
        ("Aetna Commercial", "AET-GRP-3301", "secondary"),
        ("Tricare", "TRICARE-DEF", "primary"),
        ("Workers Comp", "WC-STATE-112", "primary"),
        ("Humana Gold Plus", "HUM-MA-5501", "primary"),
    ]
    cob_members = random.sample(members, 14)
    for member in cob_members:
        carrier = random.choice(carriers)
        eff_date = date(2025, random.randint(1,6), 1)
        cob[member[0]] = {
            "member_id":        member[0],
            "member_name":      member[1],
            "carrier_name":     carrier[0],
            "policy_number":    f"POL-{random.randint(100000,999999)}",
            "group_number":     carrier[1],
            "cob_order":        carrier[2],
            "plan_type":        "Medicare" if "Medicare" in carrier[0] else ("Medicaid" if "Medicaid" in carrier[0] else "Commercial"),
            "relationship":     random.choice(["self","self","self","spouse","dependent"]),
            "effective_date":   str(eff_date),
            "termination_date": str(date(2026, 12, 31)),
            "primary_eob_required": carrier[2] == "secondary",
            "crossover_eligible":   "Medicare" in carrier[0],
        }
    return cob

# ── GENERATE FEE SCHEDULE ─────────────────────────────────────────────────────

def generate_fee_schedule():
    schedule = {}
    for cpt, info in CPT_TABLE.items():
        schedule[cpt] = {
            "cpt_code":         cpt,
            "description":      info["desc"],
            "specialty":        info["specialty"],
            "allowed_amount":   info["allowed"],
            "max_units_per_day":info["max_units"],
            "global_period_days": info["global_days"],
            "modifier_impact": {
                "25":  0.0,
                "51": -0.50,
                "59":  0.0,
                "76":  0.0,
                "GT":  0.0,
                "LT": -0.10,
            },
            "auth_required":    info["allowed"] > 200 or info["global_days"] > 0,
            "effective_date":   "2026-01-01",
            "lcd_ncd_applies":  info["specialty"] in ["Radiology","Oncology","Neurology"],
        }
    return schedule

# ── GENERATE CLAIMS HISTORY ────────────────────────────────────────────────────

def generate_claims_history(members, npis):
    history = {}
    for member in members:
        mid = member[0]
        history[mid] = []
        n_claims = random.randint(3, 12)
        for i in range(n_claims):
            cpt = random.choice(list(CPT_TABLE.keys()))
            npi = random.choice(npis)
            dos = date(2026, random.randint(1,4), random.randint(1,28))
            allowed = CPT_TABLE[cpt]["allowed"]
            billed  = round(allowed * random.uniform(1.0, 2.5), 2)
            paid    = round(allowed * random.uniform(0.85, 1.0), 2)
            status  = random.choices(["paid","paid","paid","denied"],weights=[8,8,8,2])[0]
            history[mid].append({
                "icn":            f"ICN-{2026}{str(random.randint(100000,999999))}",
                "member_id":      mid,
                "provider_npi":   npi,
                "cpt_code":       cpt,
                "icd10":          random.choice(list(ICD10_TABLE.keys())),
                "dos":            str(dos),
                "received_date":  str(dos + timedelta(days=random.randint(1,14))),
                "processed_date": str(dos + timedelta(days=random.randint(15,45))),
                "billed_amount":  billed,
                "allowed_amount": allowed,
                "paid_amount":    paid if status == "paid" else 0,
                "status":         status,
                "denial_code":    "CO-50" if status == "denied" else None,
            })
    return history

# ── EDIT CODE DEFINITIONS ─────────────────────────────────────────────────────

EDIT_CODES = {
    # AUTHORIZATION
    "E-AUTH-001": {"category":"Authorization", "desc":"Prior authorization missing",           "carc":"CO-197", "rarc":"N517", "resolution":"deny_or_approve_if_exempt"},
    "E-AUTH-002": {"category":"Authorization", "desc":"Prior authorization expired",            "carc":"CO-197", "rarc":"N56",  "resolution":"deny_unless_retro"},
    "E-AUTH-003": {"category":"Authorization", "desc":"Service not covered under authorization","carc":"CO-197", "rarc":"N115", "resolution":"deny_or_resubmit"},
    "E-AUTH-004": {"category":"Authorization", "desc":"Authorization units/visits exceeded",    "carc":"CO-119", "rarc":"N362", "resolution":"deny_excess_units"},
    "E-AUTH-005": {"category":"Authorization", "desc":"Wrong provider on authorization",        "carc":"CO-197", "rarc":"N517", "resolution":"verify_or_deny"},
    # PROVIDER / BILLING
    "E-PROV-001": {"category":"Provider",      "desc":"Rendering provider not credentialed",    "carc":"CO-185", "rarc":"N570", "resolution":"deny_or_approve_if_credentialed"},
    "E-PROV-002": {"category":"Provider",      "desc":"Billing NPI and rendering NPI mismatch", "carc":"CO-16",  "rarc":"N286", "resolution":"verify_npi_or_deny"},
    "E-PROV-003": {"category":"Provider",      "desc":"Place of service mismatch",              "carc":"CO-5",   "rarc":"N30",  "resolution":"correct_pos_or_deny"},
    "E-PROV-004": {"category":"Provider",      "desc":"Group NPI not linked to individual NPI", "carc":"CO-16",  "rarc":"N286", "resolution":"verify_group_link"},
    "E-PROV-005": {"category":"Provider",      "desc":"Provider out of network — no OON benefit","carc":"CO-3",  "rarc":"N19",  "resolution":"deny_or_apply_oon"},
    # PRICING
    "E-PRICE-001":{"category":"Pricing",       "desc":"Billed amount exceeds fee schedule max", "carc":"CO-45",  "rarc":"N30",  "resolution":"reprice_to_fee_schedule"},
    "E-PRICE-002":{"category":"Pricing",       "desc":"Units billed exceed allowed maximum",    "carc":"CO-4",   "rarc":"M44",  "resolution":"reduce_units_or_deny"},
    "E-PRICE-003":{"category":"Pricing",       "desc":"Modifier required for correct pricing",  "carc":"CO-4",   "rarc":"M114", "resolution":"apply_modifier_or_deny"},
    "E-PRICE-004":{"category":"Pricing",       "desc":"Global surgery period — unbundled svc",  "carc":"CO-97",  "rarc":"N70",  "resolution":"deny_bundled_service"},
    "E-PRICE-005":{"category":"Pricing",       "desc":"Pricing exception review required",      "carc":"CO-45",  "rarc":"N30",  "resolution":"escalate_pricing_review"},
    # CODING
    "E-CODE-001": {"category":"Coding",        "desc":"ICD-10 diagnosis code invalid/inactive", "carc":"CO-16",  "rarc":"N30",  "resolution":"deny_or_correct_code"},
    "E-CODE-002": {"category":"Coding",        "desc":"CPT code not covered under plan",         "carc":"CO-96",  "rarc":"N63",  "resolution":"deny_not_covered"},
    "E-CODE-003": {"category":"Coding",        "desc":"ICD-10/CPT combination fails LCD/NCD",   "carc":"CO-167", "rarc":"N115", "resolution":"deny_lcd_ncd"},
    "E-CODE-004": {"category":"Coding",        "desc":"Age or sex conflict with diagnosis code", "carc":"CO-16",  "rarc":"N286", "resolution":"verify_demographics"},
    "E-CODE-005": {"category":"Coding",        "desc":"Principal diagnosis sequencing error",    "carc":"CO-16",  "rarc":"N30",  "resolution":"correct_sequencing"},
    # COB
    "E-COB-001":  {"category":"COB",           "desc":"Secondary ins on file — primary EOB req","carc":"CO-22",  "rarc":"N173", "resolution":"request_primary_eob"},
    "E-COB-002":  {"category":"COB",           "desc":"Medicare primary — crossover error",      "carc":"CO-22",  "rarc":"N173", "resolution":"process_crossover"},
    "E-COB-003":  {"category":"COB",           "desc":"COB savings calculation required",         "carc":"CO-22",  "rarc":"N173", "resolution":"calculate_cob_savings"},
    # DUPLICATE
    "E-DUP-001":  {"category":"Duplicate",     "desc":"Exact duplicate — same member/DOS/CPT",  "carc":"CO-18",  "rarc":"N522", "resolution":"deny_duplicate"},
    "E-DUP-002":  {"category":"Duplicate",     "desc":"Potential duplicate — same member/DOS",  "carc":"CO-18",  "rarc":"N522", "resolution":"investigate_or_deny"},
    # TIMELY FILING
    "E-TF-001":   {"category":"Timely Filing", "desc":"Claim received beyond 365-day limit",     "carc":"CO-29",  "rarc":"N35",  "resolution":"deny_timely_filing"},
    "E-TF-002":   {"category":"Timely Filing", "desc":"Corrected claim beyond 180-day limit",    "carc":"CO-29",  "rarc":"N35",  "resolution":"deny_timely_filing"},
    # MEDICAL NECESSITY
    "E-MN-001":   {"category":"Medical Necessity","desc":"Medical necessity — no clinical documentation","carc":"CO-50","rarc":"N115","resolution":"request_documentation"},
    "E-MN-002":   {"category":"Medical Necessity","desc":"Diagnosis does not support procedure — LCD/NCD violation","carc":"CO-50","rarc":"N130","resolution":"deny_medical_necessity"},
}

# ── GENERATE 100 PENDED CLAIMS ────────────────────────────────────────────────

CLAIM_DISTRIBUTION = [
    ("E-AUTH-001", 4), ("E-AUTH-002", 4), ("E-AUTH-003", 4), ("E-AUTH-004", 4), ("E-AUTH-005", 4),
    ("E-PROV-001", 4), ("E-PROV-002", 4), ("E-PROV-003", 4), ("E-PROV-004", 4), ("E-PROV-005", 4),
    ("E-PRICE-001",4), ("E-PRICE-002",4), ("E-PRICE-003",4), ("E-PRICE-004",4), ("E-PRICE-005",4),
    ("E-CODE-001", 3), ("E-CODE-002", 3), ("E-CODE-003", 3), ("E-CODE-004", 3), ("E-CODE-005", 3),
    ("E-COB-001",  3), ("E-COB-002",  3), ("E-COB-003",  3),
    ("E-DUP-001",  3), ("E-DUP-002",  3),
    ("E-TF-001",   2), ("E-TF-002",   2),
    ("E-MN-001",   3), ("E-MN-002",   3),
]  # total = 100

CLINICAL_CATEGORIES = {"Medical Necessity", "Authorization"}

def generate_pended_claims(providers, npis, members, auths):
    claims = []
    claim_num = 1000
    auth_list = list(auths.keys())
    featured_edits = set()

    for edit_code, count in CLAIM_DISTRIBUTION:
        edit = EDIT_CODES[edit_code]
        for _ in range(count):
            member  = random.choice(members)
            npi_rendering = random.choice(npis)
            npi_billing   = random.choice(npis)
            cpt     = random.choice(list(CPT_TABLE.keys()))
            icd_pri = random.choice(list(ICD10_TABLE.keys()))
            icd_sec = random.choice(list(ICD10_TABLE.keys()))
            dos     = date(2026, random.randint(1,4), random.randint(1,28))

            # Compute billed vs allowed
            allowed = CPT_TABLE[cpt]["allowed"]
            if edit_code == "E-PRICE-001":
                billed = round(allowed * random.uniform(2.0, 4.0), 2)   # way over fee schedule
            else:
                billed = round(allowed * random.uniform(1.0, 2.0), 2)

            units = 1
            if edit_code == "E-PRICE-002":
                units = CPT_TABLE[cpt]["max_units"] + random.randint(1,4)  # over limit

            days_pending = random.randint(1, 21)
            priority = "urgent" if days_pending > 14 else "routine"

            # For auth edits, attach an auth number
            auth_number = None
            if edit["category"] == "Authorization" and auth_list:
                auth_number = random.choice(auth_list)

            claim = {
                "icn":              f"ICN-2026-{claim_num}",
                "claim_type":       "Professional",
                "member_id":        member[0],
                "member_name":      member[1],
                "member_dob":       member[2],
                "plan":             member[3],
                "npi_billing":      npi_billing,
                "npi_rendering":    npi_rendering,
                "provider_name":    providers[npi_rendering]["name"],
                "provider_specialty": providers[npi_rendering]["specialty"],
                "group_name":       providers[npi_rendering]["group_name"],
                "dos":              str(dos),
                "received_date":    str(dos + timedelta(days=random.randint(1,14))),
                "pend_date":        str(dos + timedelta(days=random.randint(15,30))),
                "days_in_queue":    days_pending,
                "priority":         priority,
                "cpt_code":         cpt,
                "cpt_description":  CPT_TABLE[cpt]["desc"],
                "modifier":         random.choice([None, None, None, "25", "59", "GT"]),
                "icd10_principal":  icd_pri,
                "icd10_secondary":  icd_sec,
                "icd10_desc":       ICD10_TABLE[icd_pri],
                "place_of_service": providers[npi_rendering]["place_of_service"],
                "units_billed":     units,
                "billed_amount":    billed,
                "allowed_amount":   allowed,
                "auth_number":      auth_number,
                "edit_code":        edit_code,
                "edit_category":    edit["category"],
                "edit_description": edit["desc"],
                "carc_code":        edit["carc"],
                "rarc_code":        edit["rarc"],
                "resolution_path":  edit["resolution"],
                "status":           "pending",
                "resolution":       None,
                "payment_amount":   None,
                "denial_code":      None,
                "resolution_step":  None,
                "resolved_by":      None,
                "processing_ms":    None,
            }

            # Human-in-the-loop flags
            is_clinical = edit["category"] in CLINICAL_CATEGORIES
            is_high_value_denial = allowed >= 500 and "deny" in edit["resolution"]
            human_review = is_clinical or is_high_value_denial
            reasons = []
            if is_clinical:
                reasons.append(f"Clinical decision — {edit['category']} review required")
            if is_high_value_denial:
                reasons.append(f"High-value denial — allowed ${allowed:.0f} exceeds $500 threshold")
            claim["human_review_flag"]   = human_review
            claim["human_review_reason"] = "; ".join(reasons) if reasons else None

            # 1 featured claim per edit type for live demo showcase
            claim["is_featured"] = edit_code not in featured_edits
            if claim["is_featured"]:
                featured_edits.add(edit_code)

            claims.append(claim)
            claim_num += 1

    random.shuffle(claims)
    return claims

# ── GENERATE SOP OUTCOMES (for Gen 2 signal) ─────────────────────────────────

def generate_sop_outcomes():
    """Simulated historical SOP performance — powers the Gen 2 gap detection panel."""
    outcomes = []
    for edit_code, _ in CLAIM_DISTRIBUTION:
        total = random.randint(120, 800)
        auto_resolved = int(total * random.uniform(0.55, 0.82))
        escalated     = int(total * random.uniform(0.08, 0.22))
        human_override= total - auto_resolved - escalated
        override_pct  = round(human_override / total * 100, 1)
        avg_minutes   = round(random.uniform(2.1, 18.4), 1)
        outcomes.append({
            "edit_code":        edit_code,
            "edit_desc":        EDIT_CODES[edit_code]["desc"],
            "category":         EDIT_CODES[edit_code]["category"],
            "total_processed":  total,
            "auto_resolved":    auto_resolved,
            "escalated":        escalated,
            "human_override":   human_override,
            "override_pct":     override_pct,
            "avg_processing_min": avg_minutes,
            "sop_gap_flag":     override_pct > 30,
            "sop_gap_reason":   "Override rate above 30% — SOP rule may need update" if override_pct > 30 else None,
        })
    return sorted(outcomes, key=lambda x: x["override_pct"], reverse=True)

# ── GENERATE UPSTREAM PREDICTIONS (Gen 3 signal) ──────────────────────────────

def generate_upstream_predictions(providers, npis, members):
    """Simulated incoming claims likely to pend — powers the Gen 3 prediction panel."""
    predictions = []
    for i in range(8):
        npi    = random.choice(npis)
        member = random.choice(members)
        cpt    = random.choice(list(CPT_TABLE.keys()))
        edit   = random.choice(list(EDIT_CODES.keys()))
        pend_prob = random.randint(62, 94)
        predictions.append({
            "prediction_id":   f"PRED-{1000+i}",
            "member_id":       member[0],
            "member_name":     member[1],
            "provider_name":   providers[npi]["name"],
            "provider_npi":    npi,
            "cpt_code":        cpt,
            "cpt_desc":        CPT_TABLE[cpt]["desc"],
            "predicted_edit":  edit,
            "predicted_edit_desc": EDIT_CODES[edit]["desc"],
            "pend_probability":pend_prob,
            "basis":           f"{pend_prob}% of prior claims from this provider pended for {EDIT_CODES[edit]['desc']}",
            "action":          "Outreach triggered — provider notified to resolve before submission",
            "status":          random.choice(["outreach_sent","outreach_sent","resolved","pending_response"]),
        })
    return predictions

# ── GENERATE MULTI-EDIT CLAIMS (for trace demo) ───────────────────────────────

MULTI_EDIT_SCENARIOS = [
    {
        "label": "Auth + Provider + Pricing",
        "edits": ["E-AUTH-001", "E-PROV-001", "E-PRICE-001"],
        "cpt":   "27447",
        "member": ("MBR-10003", "Margaret O'Brien", "Jul 22, 1955", "UnitedHealth Choice Plus"),
        "note":  "Knee replacement — auth missing, rendering provider credentialing expired, billed 3x fee schedule",
    },
    {
        "label": "Coding + Auth Expired + Medical Necessity",
        "edits": ["E-CODE-003", "E-AUTH-002", "E-MN-001"],
        "cpt":   "96413",
        "member": ("MBR-10012", "Charles Rivera", "Oct 21, 1969", "Aetna Choice POS II"),
        "note":  "Chemo infusion — ICD/CPT fails LCD, auth expired, no clinical documentation",
    },
    {
        "label": "COB + Auth Units + Duplicate",
        "edits": ["E-COB-001", "E-AUTH-004", "E-DUP-001"],
        "cpt":   "97110",
        "member": ("MBR-10007", "Sandra Mitchell", "Apr 19, 1970", "Aetna Choice POS II"),
        "note":  "PT therapeutic exercise — secondary EOB missing, auth units exceeded, exact duplicate on file",
    },
    {
        "label": "Provider NPI + Global Period + Place of Service",
        "edits": ["E-PROV-002", "E-PRICE-004", "E-PROV-003"],
        "cpt":   "29881",
        "member": ("MBR-10016", "George Martinez", "Nov 11, 1971", "BlueCross Gold PPO"),
        "note":  "Knee arthroscopy — billing/rendering NPI mismatch, within 90-day global period, POS billed as office vs. ASC",
    },
    {
        "label": "Medical Necessity + Bad Code + Wrong Auth Service",
        "edits": ["E-MN-002", "E-CODE-001", "E-AUTH-003"],
        "cpt":   "70553",
        "member": ("MBR-10019", "Ethel Davis", "Apr 01, 1954", "Humana HMO Gold"),
        "note":  "Brain MRI — diagnosis fails LCD criteria, ICD-10 code invalid for DOS, CPT not covered under authorization",
    },
]

def generate_multi_edit_claims(providers, npis, auths):
    claims = []
    auth_list = list(auths.keys())
    for i, scenario in enumerate(MULTI_EDIT_SCENARIOS):
        member = scenario["member"]
        cpt    = scenario["cpt"]
        npi    = npis[i * 7 % len(npis)]
        dos    = date(2026, 3, random.randint(1, 28))
        allowed = CPT_TABLE[cpt]["allowed"]
        billed  = round(allowed * 2.4, 2)
        icd_pri = random.choice(list(ICD10_TABLE.keys()))

        edits_detail = []
        for ec in scenario["edits"]:
            e = EDIT_CODES[ec]
            is_clinical = e["category"] in CLINICAL_CATEGORIES
            is_high_value_denial = allowed >= 500 and "deny" in e["resolution"]
            human_review = is_clinical or is_high_value_denial
            reasons = []
            if is_clinical:
                reasons.append(f"Clinical decision — {e['category']} review required")
            if is_high_value_denial:
                reasons.append(f"High-value denial — allowed ${allowed:.0f} exceeds $500 threshold")
            edits_detail.append({
                "edit_code":        ec,
                "edit_category":    e["category"],
                "edit_description": e["desc"],
                "carc_code":        e["carc"],
                "rarc_code":        e["rarc"],
                "resolution_path":  e["resolution"],
                "human_review_flag":human_review,
                "human_review_reason": "; ".join(reasons) if reasons else None,
            })

        overall_hr = any(e["human_review_flag"] for e in edits_detail)
        claims.append({
            "icn":              f"ICN-2026-MULTI-{1000+i}",
            "label":            scenario["label"],
            "scenario_note":    scenario["note"],
            "claim_type":       "Professional",
            "member_id":        member[0],
            "member_name":      member[1],
            "member_dob":       member[2],
            "plan":             member[3],
            "npi_billing":      npi,
            "npi_rendering":    npi,
            "provider_name":    providers[npi]["name"],
            "provider_specialty": providers[npi]["specialty"],
            "group_name":       providers[npi]["group_name"],
            "dos":              str(dos),
            "received_date":    str(dos + timedelta(days=5)),
            "pend_date":        str(dos + timedelta(days=18)),
            "days_in_queue":    18,
            "priority":         "urgent",
            "cpt_code":         cpt,
            "cpt_description":  CPT_TABLE[cpt]["desc"],
            "modifier":         None,
            "icd10_principal":  icd_pri,
            "icd10_secondary":  random.choice(list(ICD10_TABLE.keys())),
            "icd10_desc":       ICD10_TABLE[icd_pri],
            "place_of_service": providers[npi]["place_of_service"],
            "units_billed":     1,
            "billed_amount":    billed,
            "allowed_amount":   allowed,
            "auth_number":      random.choice(auth_list),
            "edit_codes":       scenario["edits"],
            "edit_code":        scenario["edits"][0],
            "edit_category":    EDIT_CODES[scenario["edits"][0]]["category"],
            "edit_description": EDIT_CODES[scenario["edits"][0]]["desc"],
            "carc_code":        EDIT_CODES[scenario["edits"][0]]["carc"],
            "rarc_code":        EDIT_CODES[scenario["edits"][0]]["rarc"],
            "resolution_path":  EDIT_CODES[scenario["edits"][0]]["resolution"],
            "edits_detail":     edits_detail,
            "human_review_flag": overall_hr,
            "human_review_reason": "Multi-edit claim — one or more edits require human review",
            "is_featured":      True,
            "is_multi_edit":    True,
            "status":           "pending",
        })
    return claims

# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating Project John databases...")

    providers, npis = generate_providers()
    print(f"  Providers:       {len(providers)}")

    auths = generate_authorizations(MEMBERS, npis)
    print(f"  Authorizations:  {len(auths)}")

    cob = generate_cob(MEMBERS)
    print(f"  COB records:     {len(cob)}")

    fee_schedule = generate_fee_schedule()
    print(f"  Fee schedule:    {len(fee_schedule)} CPT codes")

    claims_history = generate_claims_history(MEMBERS, npis)
    total_hist = sum(len(v) for v in claims_history.values())
    print(f"  Claims history:  {total_hist} historical claims across {len(claims_history)} members")

    pended_claims = generate_pended_claims(providers, npis, MEMBERS, auths)
    featured_claims = [c for c in pended_claims if c["is_featured"]]
    human_review_claims = [c for c in pended_claims if c["human_review_flag"]]
    print(f"  Pended claims:   {len(pended_claims)} ({len(featured_claims)} featured, {len(human_review_claims)} need human review)")

    multi_edit_claims = generate_multi_edit_claims(providers, npis, auths)
    print(f"  Multi-edit claims: {len(multi_edit_claims)} scenarios")

    sop_outcomes = generate_sop_outcomes()
    print(f"  SOP outcomes:    {len(sop_outcomes)} edit types (Gen 2 data)")

    predictions = generate_upstream_predictions(providers, npis, MEMBERS)
    print(f"  Predictions:     {len(predictions)} upstream claims (Gen 3 data)")

    # Write all databases
    (BASE / "providers.json").write_text(json.dumps(providers, indent=2))
    (BASE / "authorizations.json").write_text(json.dumps(auths, indent=2))
    (BASE / "cob.json").write_text(json.dumps(cob, indent=2))
    (BASE / "fee_schedule.json").write_text(json.dumps(fee_schedule, indent=2))
    (BASE / "claims_history.json").write_text(json.dumps(claims_history, indent=2))
    (BASE / "claims_pend.json").write_text(json.dumps(pended_claims, indent=2))
    (BASE / "featured_claims.json").write_text(json.dumps(featured_claims, indent=2))
    (BASE / "human_review.json").write_text(json.dumps(human_review_claims, indent=2))
    (BASE / "multi_edit_claims.json").write_text(json.dumps(multi_edit_claims, indent=2))
    (BASE / "sop_outcomes.json").write_text(json.dumps(sop_outcomes, indent=2))
    (BASE / "predictions.json").write_text(json.dumps(predictions, indent=2))
    (BASE / "edit_codes.json").write_text(json.dumps(EDIT_CODES, indent=2))

    print("\nAll databases written to /data")
    print("Done.")
