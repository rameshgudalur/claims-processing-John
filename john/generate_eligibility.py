# -*- coding: utf-8 -*-
"""Generate a real member-eligibility dataset (the 6th source database) from the members that
appear across the claim sets. Real coverage spans, status, demographics — queried at resolution."""
import hashlib
import json

members = {}
for f in ["claims_pend", "featured_claims", "multi_edit_claims", "human_review"]:
    try:
        data = json.load(open(f"data/{f}.json", encoding="utf-8"))
    except FileNotFoundError:
        continue
    for c in data:
        mid = c.get("member_id")
        if mid and mid not in members:
            members[mid] = {"name": c.get("member_name"), "dob": c.get("member_dob"), "plan": c.get("plan")}

try:
    hist = json.load(open("data/claims_history.json", encoding="utf-8"))
    for mid in hist:
        members.setdefault(mid, {"name": None, "dob": None, "plan": "Aetna Choice POS II"})
except FileNotFoundError:
    pass

FEMALE = {"eleanor", "margaret", "patricia", "sandra", "dorothy", "helen", "ruth", "ethel",
          "mary", "linda", "barbara", "susan", "jessica", "nancy", "karen", "betty", "carol"}

elig = {}
for mid, m in sorted(members.items()):
    h = int(hashlib.md5(mid.encode()).hexdigest(), 16)
    first = (m.get("name") or "").split(" ")[0].lower()
    sex = "F" if first in FEMALE else "M"
    # All active and covering 2025-2026 so existing pend outcomes are unaffected; eligibility is a
    # real, queried source DB. (Eligibility-driven scenarios are introduced via interventions.)
    elig[mid] = {
        "member_id": mid,
        "name": m["name"],
        "dob": m["dob"],
        "sex": sex,
        "plan": m["plan"] or "Aetna Choice POS II",
        "status": "active",
        "coverage_spans": [{"effective": "2025-01-01", "term": "2026-12-31", "product": m["plan"] or "Commercial"}],
        "group_number": f"GRP-{10000 + h % 90000}",
        "subscriber_id": mid,
        "relationship": "subscriber",
    }

json.dump(elig, open("data/eligibility.json", "w", encoding="utf-8"), indent=2)
print(f"wrote {len(elig)} eligibility records -> data/eligibility.json")
