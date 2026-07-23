"""
Project John — Claims Pend Processing Demo
Flask backend  |  Port 5002
"""
import json, random, time
from pathlib import Path
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATA = Path(__file__).parent / "data"

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
pended_claims   = load("claims_pend.json")
featured_claims = load("featured_claims.json")
human_review    = load("human_review.json")
sop_outcomes      = load("sop_outcomes.json")
predictions       = load("predictions.json")
edit_codes        = load("edit_codes.json")
multi_edit_claims = load("multi_edit_claims.json") if (DATA / "multi_edit_claims.json").exists() else []

# Index pended claims by ICN for fast lookup
claims_index = {c["icn"]: c for c in pended_claims}

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
         "template":"LCD lookup: CPT {cpt} subject to LCD L33787 (or applicable LCD). Billed diagnosis {icd10} not in LCD's covered ICD-10 list. Medical necessity not established under LCD criteria.",
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

def get_kg_rules(edit_code, claim, ctx):
    """Return KG rules for this edit, with claim data filled into templates."""
    rules = KG_RULES.get(edit_code, [])
    result = []
    adj = round(claim.get("billed_amount", 0) - claim.get("allowed_amount", 0), 2)
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

# ── SOP Resolution Logic ─────────────────────────────────────────────────────

RESOLUTION_RULES = {
    # Authorization
    "deny_or_approve_if_exempt": {
        "steps": [
            "Query Authorization DB — check if auth number exists for CPT + member",
            "Check plan benefit design — is this service exempt from auth requirement?",
            "If exempt: approve at fee schedule rate",
            "If not exempt and no auth: deny with CO-197 / N517",
        ],
        "outcome_logic": lambda c: "approve" if c["allowed_amount"] < 150 else "deny",
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
        "outcome_logic": lambda c: "partial_pay",
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
            "Query knowledge graph — retrieve applicable LCD/NCD for CPT + diagnosis",
            "Check if diagnosis supports medical necessity per LCD/NCD criteria",
            "If diagnosis fails LCD/NCD criteria: deny CO-167 / N115",
            "Provide ABN-related language if Medicare beneficiary",
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
        "outcome_logic": lambda c: "deny",
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
    "request_info": {"label": "ADR Sent",         "color": "blue"},
    "escalate":     {"label": "Escalated",        "color": "orange"},
    "human_review": {"label": "Human Review",     "color": "purple"},
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
}

def resolve_claim(claim):
    """Run SOP logic and return resolution + reasoning steps."""
    edit_code = claim["edit_code"]
    resolution_path = claim["resolution_path"]
    rule = RESOLUTION_RULES.get(resolution_path)

    if not rule:
        outcome = "escalate"
        steps = ["No SOP match — escalating to senior examiner"]
    else:
        outcome = rule["outcome_logic"](claim)
        steps = rule["steps"]
        sop_ref = rule["sop_ref"]

    # Override to human_review if flagged
    if claim["human_review_flag"] and outcome in ("deny", "approve"):
        outcome = "human_review"

    # Compute payment
    allowed = claim["allowed_amount"]
    units   = claim["units_billed"]
    if outcome == "approve":
        payment = round(allowed * units, 2)
    elif outcome == "partial_pay":
        payment = round(allowed * random.uniform(0.4, 0.75), 2)
    else:
        payment = 0.0

    # DB queries triggered
    dbs_queried = DB_QUERIES.get(claim["edit_category"], ["Eligibility DB"])

    return {
        "icn":            claim["icn"],
        "outcome":        outcome,
        "outcome_label":  RESOLUTION_LABELS.get(outcome, {}).get("label", outcome),
        "outcome_color":  RESOLUTION_LABELS.get(outcome, {}).get("color", "gray"),
        "payment_amount": payment,
        "carc":           claim["carc_code"],
        "rarc":           claim["rarc_code"],
        "sop_ref":        rule["sop_ref"] if rule else "N/A",
        "sop_steps":      steps,
        "dbs_queried":    dbs_queried,
        "human_review":   claim["human_review_flag"],
        "human_review_reason": claim.get("human_review_reason"),
        "processing_ms":  random.randint(180, 950),
    }

# ── API Routes ────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    total  = len(pended_claims)
    hr     = sum(1 for c in pended_claims if c["human_review_flag"])
    feat   = sum(1 for c in pended_claims if c.get("is_featured"))
    cats   = {}
    for c in pended_claims:
        cats[c["edit_category"]] = cats.get(c["edit_category"], 0) + 1
    return jsonify({
        "total_pended":        total,
        "human_review_count":  hr,
        "featured_count":      feat,
        "by_category":         cats,
        "total_providers":     len(providers),
        "total_authorizations":len(authorizations),
        "total_cob_records":   len(cob),
        "fee_schedule_codes":  len(fee_schedule),
        "edit_types":          len(edit_codes),
    })

@app.route("/api/pend-queue")
def api_pend_queue():
    page  = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 100))
    featured_only = request.args.get("featured") == "true"
    subset = [c for c in pended_claims if c.get("is_featured")] if featured_only else pended_claims
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

@app.route("/api/process-batch")
def api_process_batch():
    """Process all featured claims (1 per edit type) for the live demo queue."""
    featured = [c for c in pended_claims if c.get("is_featured")]
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
    return jsonify({"results": results, "count": len(results)})

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
    sample = list(providers.values())[:20]
    return jsonify({"records": sample, "total": len(providers)})

@app.route("/api/db/providers/<npi>")
def api_db_provider(npi):
    p = providers.get(npi)
    if not p:
        return jsonify({"error": "NPI not found"}), 404
    return jsonify(p)

@app.route("/api/db/authorizations")
def api_db_authorizations():
    sample = list(authorizations.values())[:20]
    return jsonify({"records": sample, "total": len(authorizations)})

@app.route("/api/db/authorizations/<auth_num>")
def api_db_authorization(auth_num):
    a = authorizations.get(auth_num)
    if not a:
        return jsonify({"error": "Auth number not found"}), 404
    return jsonify(a)

@app.route("/api/db/cob")
def api_db_cob():
    return jsonify({"records": list(cob.values()), "total": len(cob)})

@app.route("/api/db/cob/<member_id>")
def api_db_cob_member(member_id):
    c = cob.get(member_id)
    if not c:
        return jsonify({"error": "No COB record for member"}), 404
    return jsonify(c)

@app.route("/api/db/fee-schedule")
def api_db_fee_schedule():
    return jsonify({"records": list(fee_schedule.values()), "total": len(fee_schedule)})

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

@app.route("/api/db/eligibility/<member_id>")
def api_db_eligibility(member_id):
    from generate_data import MEMBERS
    member = next((m for m in MEMBERS if m[0] == member_id), None)
    if not member:
        return jsonify({"error": "Member not found"}), 404
    dob_year = int(member[2].split(", ")[1])
    age = 2026 - dob_year
    return jsonify({
        "member_id":    member[0],
        "member_name":  member[1],
        "dob":          member[2],
        "plan":         member[3],
        "age":          age,
        "eligibility_status": "active",
        "effective_date": "2026-01-01",
        "termination_date": "2026-12-31",
        "group_number": f"GRP-{random.randint(10000, 99999)}",
        "subscriber_id": member[0],
    })

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
        "eligibility": {
            "member_id":   claim["member_id"],
            "plan":        claim["plan"],
            "status":      "active",
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
        total = len(pended_claims)
        auto = human = traced = 0
        for c in pended_claims:
            try:
                res = resolve_claim(c)
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
            d = c.get("days_in_queue", 0) or 0
            b = ("< 15 days" if d < 15 else "15-30 days" if d < 30 else "30-60 days" if d < 60 else "60+ days")
            dq[b] = dq.get(b, 0) + 1
            if len(audit) < 60:
                audit.append({"icn": c["icn"], "member": c.get("member_name", ""),
                              "edit_code": c.get("edit_code", ""), "category": cat,
                              "outcome": res["outcome_label"], "outcome_key": o,
                              "carc": res["carc"], "rarc": res["rarc"], "sop": res["sop_ref"],
                              "human_review": res["human_review"]})
        auto_pct = round(100 * auto / total) if total else 0
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
            "audit": audit,
        }
    out = dict(_OBS_CACHE)
    out["updated"] = time.strftime("%H:%M:%S")
    return jsonify(out)


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
                o = resolve_claim(c)["outcome"]
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


if __name__ == "__main__":
    print("Project John — Claims Pend Processing Demo")
    print(f"  {len(pended_claims)} pended claims loaded")
    print(f"  {len(providers)} providers  |  {len(authorizations)} authorizations  |  {len(cob)} COB records")
    print("Server starting on http://localhost:5002")
    app.run(debug=True, port=5002)
