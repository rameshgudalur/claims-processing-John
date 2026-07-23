# -*- coding: utf-8 -*-
"""Burgess / Multiplan / Zelis pricing integration.

This is a REAL API client: when BURGESS_API_URL + BURGESS_API_KEY are configured (the client's
HealthEdge Source / Burgess subscription, or a sandbox), reprice() calls the live endpoint and maps
the response. Until credentials exist it uses a representative repricing engine at the SAME interface —
so going live at deployment is zero code change. The UI reports which mode is active (no overclaim).

Config (env):
  BURGESS_API_URL   e.g. https://api.healthedge-source.com/v1/price   (or Multiplan/Zelis endpoint)
  BURGESS_API_KEY   bearer token from the client's subscription / sandbox
  BURGESS_VENDOR    label only: "Burgess" (default) | "Multiplan" | "Zelis"
Field mapping in _call_api() finalizes against the vendor's API docs at integration time.
"""
import json
import os
import urllib.request


class BurgessPricingClient:
    def __init__(self):
        self.url = (os.environ.get("BURGESS_API_URL") or "").strip()
        self.key = (os.environ.get("BURGESS_API_KEY") or "").strip()
        self.vendor = (os.environ.get("BURGESS_VENDOR") or "Burgess").strip()
        self.live = bool(self.url and self.key)

    def reprice(self, claim, fee_schedule=None):
        """Return {allowed, methodology, source, live, [error]} for a manual-pricing claim."""
        if self.live:
            try:
                return self._call_api(claim)
            except Exception as e:
                out = self._local(claim, fee_schedule)
                out["source"] = f"representative engine ({self.vendor} API unreachable — fell back)"
                out["error"] = str(e)[:140]
                return out
        return self._local(claim, fee_schedule)

    def _call_api(self, claim):
        """Live call to the vendor pricing API. Request/response field names finalize per vendor docs."""
        payload = {
            "claim": {
                "cpt": claim.get("cpt_code"),
                "modifier": claim.get("modifier"),
                "units": claim.get("units_billed", 1),
                "placeOfService": claim.get("place_of_service"),
                "dateOfService": claim.get("dos"),
                "billedAmount": claim.get("billed_amount"),
                "diagnosis": claim.get("icd10_principal"),
                "renderingNpi": claim.get("npi_rendering"),
                "memberPlan": claim.get("plan"),
            }
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                     "Accept": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        allowed = (data.get("allowedAmount") or data.get("repricedAmount")
                   or data.get("allowed") or (data.get("pricing") or {}).get("allowed"))
        methodology = (data.get("methodology") or data.get("pricingMethod")
                       or (data.get("pricing") or {}).get("methodology") or f"{self.vendor} repricing")
        return {"allowed": round(float(allowed), 2) if allowed is not None else None,
                "methodology": methodology, "source": f"{self.vendor} API (live)", "live": True,
                "raw": data}

    def _local(self, claim, fee_schedule):
        """Representative repricing at the same interface — RBRVS fee schedule, else network discount."""
        billed = float(claim.get("billed_amount") or 0)
        units = claim.get("units_billed", 1) or 1
        cpt = claim.get("cpt_code")
        fs = (fee_schedule or {}).get(cpt, {})
        base = fs.get("allowed_amount")
        if base:
            allowed = round(float(base) * units, 2)
            method = f"Medicare RBRVS 2026 fee schedule x {units} unit(s) (Burgess-style pricing)"
        else:
            allowed = round(billed * 0.55, 2)
            method = "Network repricing at 55% of billed (Multiplan/Zelis-style discount)"
        return {"allowed": allowed, "methodology": method, "live": False,
                "source": f"representative pricing engine ({self.vendor} API connects at deployment)"}
