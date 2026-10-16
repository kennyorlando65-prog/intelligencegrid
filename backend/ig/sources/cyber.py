"""Cybersecurity sources: CISA Known Exploited Vulnerabilities + NVD.

CISA KEV and NVD answer different questions and the distinction matters:

* **KEV** is a curated list of vulnerabilities *confirmed exploited in the
  wild*, with a federal remediation deadline. Every entry is a real, active
  threat. It is small (~1,700 records) and authoritative.
* **NVD** is the universal CVE catalogue with CVSS scoring. It is enormous
  (~200,000 records) and contains mostly theoretical risk.

Treating them as one feed is a classic error: it either floods the analyst
with 200k unfiltered CVEs, or hides the joined signal -- "this CVE is on
KEV *and* scores 9.8 *and* is being used by ransomware". That join is the
whole product here.

The NVD 1.1 API is dead (returns HTML 403); only the 2.0 endpoint is used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import CyberItem, Severity, utcnow
from ..quality import content_hash, summarize_text
from .base import Source

CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def cvss_to_severity(score: float | None) -> Severity:
    """Map a CVSS base score to the platform's severity ladder."""
    if score is None:
        return Severity.INFO
    if score >= 9.0:
        return Severity.CRITICAL
    if score >= 7.0:
        return Severity.HIGH
    if score >= 4.0:
        return Severity.MEDIUM
    if score > 0:
        return Severity.LOW
    return Severity.INFO


class CisaKevSource(Source):
    """The authoritative exploited-in-the-wild list."""

    name = "cisa_kev"
    label = "CISA Known Exploited Vulnerabilities"
    module = "threats"
    max_staleness_seconds = 48 * 3600
    note = (
        "Curated list of CVEs confirmed exploited in the wild, with federal "
        "remediation deadlines. Every entry represents an active threat."
    )

    async def collect(self) -> list[CyberItem]:
        data, res = await self.client.get_json(
            CISA_KEV_URL,
            rpm=30,
            host_key="cisa",
            cache_ttl=3600,
        )
        if not res.ok:
            raise RuntimeError(res.error or f"HTTP {res.status}")
        self.archive(res.url, res.content, content_type="application/json", note="cisa:kev")

        vulns = (data or {}).get("vulnerabilities") or []
        out: list[CyberItem] = []
        for v in vulns:
            cve = (v.get("cveID") or "").strip()
            if not cve:
                continue
            date_added = _parse_date(v.get("dateAdded"))
            due = _parse_date(v.get("dueDate"))
            ransomware = (v.get("knownRansomwareCampaignUse") or "").lower() == "known"
            out.append(
                CyberItem(
                    id=content_hash("kev", cve)[:16],
                    cve_id=cve,
                    title=summarize_text(v.get("vulnerabilityName") or cve, 200),
                    description=summarize_text(v.get("shortDescription") or "", 500),
                    published_at=date_added,
                    collected_at=utcnow(),
                    # KEV membership implies high real-world risk regardless
                    # of any CVSS score, so it gets a floor of HIGH.
                    severity=Severity.CRITICAL if ransomware else Severity.HIGH,
                    in_kev=True,
                    kev_date_added=date_added,
                    kev_due_date=due,
                    ransomware_used=ransomware,
                    vendor=v.get("vendorProject") or "",
                    product=v.get("product") or "",
                    required_action=summarize_text(v.get("requiredAction") or "", 400),
                    source_url=(
                        "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"
                    ),
                    confidence=0.95,
                )
            )
        return out


class NvdSource(Source):
    """Recent CVEs from NVD, kept tightly bounded.

    Only the most recent window is fetched. Pulling the full catalogue into
    a serverless function is neither possible nor useful; the value is in
    *new* vulnerabilities, and the join against KEV.
    """

    name = "nvd"
    label = "NVD CVE feed (recent window)"
    module = "threats"
    max_staleness_seconds = 12 * 3600
    note = (
        "Recent CVEs with CVSS scores. Joined against the CISA KEV list so "
        "exploited-in-the-wild status is visible on each record."
    )

    RESULTS_PER_PAGE = 100
    PAGES = 2

    async def collect(self) -> list[CyberItem]:
        out: list[CyberItem] = []
        for page in range(self.PAGES):
            start = page * self.RESULTS_PER_PAGE
            url = (
                f"{NVD_API}?resultsPerPage={self.RESULTS_PER_PAGE}"
                f"&startIndex={start}"
            )
            data, res = await self.client.get_json(
                url,
                rpm=self.settings.nvd_rpm,
                host_key="nvd",
                cache_ttl=1800,
            )
            if not res.ok:
                if not out:
                    raise RuntimeError(res.error or f"HTTP {res.status}")
                break
            if page == 0:
                self.archive(res.url, res.content, content_type="application/json", note="nvd")
            for wrapper in (data or {}).get("vulnerabilities") or []:
                item = self._to_item(wrapper)
                if item is not None:
                    out.append(item)
        return out

    def _to_item(self, wrapper: dict[str, Any]) -> CyberItem | None:
        cve = wrapper.get("cve") or {}
        cve_id = cve.get("id")
        if not cve_id:
            return None
        desc = ""
        for d in cve.get("descriptions") or []:
            if d.get("lang") == "en":
                desc = d.get("value") or ""
                break
        score = _extract_cvss(cve.get("metrics") or {})
        published = _parse_iso(cve.get("published"))
        return CyberItem(
            id=content_hash("nvd", cve_id)[:16],
            cve_id=cve_id,
            title=cve_id,
            description=summarize_text(desc, 500),
            published_at=published,
            collected_at=utcnow(),
            cvss_score=score,
            severity=cvss_to_severity(score),
            in_kev=False,
            source_url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
            confidence=0.85,
            quality_flags=[]
            if score is not None
            else ["no_cvss_score"],
        )


def _extract_cvss(metrics: dict[str, Any]) -> float | None:
    """Pull the best available base score.

    Preference order matters: NVD v3.1 is the current standard, v3.0 and
    v2 are retained for older records. Taking whatever appears first in the
    dict would silently mix scoring versions.
    """
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key)
        if not entries:
            continue
        entry = entries[0]
        data = entry.get("cvssData") or {}
        score = data.get("baseScore")
        if score is None:
            score = entry.get("baseScore")
        if isinstance(score, (int, float)):
            return float(score)
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


# -- joining and analysis ---------------------------------------------------


def merge_cyber(kev: list[CyberItem], nvd: list[CyberItem]) -> list[CyberItem]:
    """Join KEV and NVD on CVE id.

    KEV wins on severity, action and dates because it encodes real-world
    exploitation which no CVSS score can express. The NVD record contributes
    its description and score when the KEV entry lacks them.

    Records present only in NVD are kept and marked ``in_kev=False`` -- the
    absence of exploitation is not the absence of a vulnerability, and
    hiding un-exploited CVEs would remove the early-warning value.
    """
    by_cve: dict[str, CyberItem] = {}
    for item in nvd:
        by_cve[item.cve_id] = item

    for item in kev:
        existing = by_cve.get(item.cve_id)
        if existing is None:
            by_cve[item.cve_id] = item
            continue
        merged = item.model_copy(
            update={
                "description": item.description or existing.description,
                "cvss_score": existing.cvss_score if existing.cvss_score is not None else None,
                "quality_flags": list(
                    {*item.quality_flags, *existing.quality_flags, "kev_joined"}
                ),
                "confidence": round(min(0.99, max(item.confidence, existing.confidence) + 0.04), 4),
            }
        )
        if merged.cvss_score is not None and merged.severity.rank < Severity.HIGH.rank:
            merged.severity = cvss_to_severity(merged.cvss_score)
        by_cve[item.cve_id] = merged

    out = list(by_cve.values())
    out.sort(
        key=lambda c: (
            -int(c.in_kev),
            -int(c.ransomware_used),
            -(c.cvss_score or 0.0),
        )
    )
    return out


def kev_deadline_pressure(items: list[CyberItem], now: datetime | None = None) -> list[dict]:
    """Rank exploited CVEs by how close their remediation deadline is.

    Overdue items are surfaced first because the deadline has already
    passed -- that is the actionable state, not a future one.
    """
    now = now or utcnow()
    out = []
    for item in items:
        if not item.in_kev or item.kev_due_date is None:
            continue
        days = (item.kev_due_date - now).days
        out.append(
            {
                "cve_id": item.cve_id,
                "vendor": item.vendor,
                "product": item.product,
                "due_date": item.kev_due_date.date().isoformat(),
                "days_remaining": days,
                "overdue": days < 0,
                "ransomware_used": item.ransomware_used,
                "cvss_score": item.cvss_score,
                "required_action": item.required_action,
            }
        )
    out.sort(key=lambda r: r["days_remaining"])
    return out


def cve_activity_by_day(items: list[CyberItem], days: int = 30) -> list[dict]:
    """Zero-filled daily CVE publication counts."""
    from ..store import daily_series

    return daily_series(items, "published_at", days=days)
