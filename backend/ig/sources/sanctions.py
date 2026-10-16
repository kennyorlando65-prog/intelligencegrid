"""Sanctions sources: OFAC SDN, EU consolidated FSD, UN Security Council.

Three separate jurisdictions, three separate XML schemas, deliberately kept
as separate sources rather than pre-merged. The reason is analytical: being
listed by the UN is a materially different condition from being listed by
one national treasury, and a merged list destroys that distinction. The
graph preserves ``list_source`` on every node so a connection can be read as
"sanctioned by the EU" rather than the much weaker "sanctioned".

All three files are 20-30 MB and are parsed with ``defusedxml`` -- these are
third-party documents, and parsing untrusted XML with the standard library
is a known attack surface (billion laughs, external entity expansion).

Country inference is heuristic. Sanctions records frequently list only a
city or a demonym, so the address text is scanned for known country names.
Inferred countries are flagged, never asserted.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET  # noqa: S405 - only used for type hints
from datetime import datetime, timezone
from typing import Any

from defusedxml.ElementTree import parse as safe_parse

from ..geo import find as find_countries
from ..models import SanctionItem, utcnow
from ..quality import content_hash, summarize_text
from .base import Source

OFAC_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
EU_URL = (
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/"
    "xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
)
UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"

# OFAC SDN entity types
_SDN_TYPES = {"individual": "individual", "entity": "entity", "vessel": "vessel", "aircraft": "aircraft"}


class _XmlListSource(Source):
    """Shared machinery for the three XML sanctions lists."""

    list_source = "unknown"
    url = ""
    host_key = ""

    async def _fetch_xml(self) -> ET.Element | None:
        res = await self.client.fetch(
            self.url,
            rpm=30,
            host_key=self.host_key,
            cache_ttl=6 * 3600,
        )
        if not res.ok:
            raise RuntimeError(res.error or f"HTTP {res.status}")
        self.archive(res.url, res.content, content_type="application/xml", note=self.name)
        try:
            root = safe_parse(io.BytesIO(res.content)).getroot()
        except Exception as exc:  # noqa: BLE001 - defusedxml raises several types
            raise RuntimeError(f"XML parse failed: {type(exc).__name__}") from exc
        return root

    @staticmethod
    def _localname(tag: str) -> str:
        """Strip the namespace, which differs between all three lists."""
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    @staticmethod
    def _find_all(element: Any, name: str) -> list[Any]:
        out = []
        for child in element.iter():
            if _XmlListSource._localname(child.tag) == name:
                out.append(child)
        return out

    @staticmethod
    def _find_one(element: Any, name: str) -> Any | None:
        for child in element.iter():
            if _XmlListSource._localname(child.tag) == name:
                return child
        return None

    @staticmethod
    def _text(element: Any, name: str) -> str:
        node = _XmlListSource._find_one(element, name) if element is not None else None
        if node is None or node.text is None:
            return ""
        return node.text.strip()

    def _base_item(
        self,
        *,
        name: str,
        entity_type: str,
        aliases: list[str],
        programs: list[str],
        addresses: list[str],
        identifiers: dict[str, str],
        listed_at: datetime | None = None,
        source_url: str = "",
        deterministic_key: str = "",
    ) -> SanctionItem:
        inferred: list[str] = []
        if addresses:
            inferred = find_countries(" ".join(addresses))
        flags = ["country_inferred_from_address"] if inferred else []
        if not aliases:
            flags.append("no_aliases")
        # Entities with no alias and no address are hard to reconcile across
        # lists -- worth flagging rather than silently under-matching.
        if not aliases and not addresses:
            flags.append("weak_identity")
        return SanctionItem(
            id=content_hash(self.list_source, deterministic_key or name)[:16],
            name=summarize_text(name, 200),
            entity_type=entity_type,
            list_source=self.list_source,
            programs=programs[:10],
            countries=inferred,
            aliases=aliases[:20],
            identifiers=identifiers,
            addresses=[summarize_text(a, 200) for a in addresses[:10]],
            listed_at=listed_at,
            collected_at=utcnow(),
            source_url=source_url or self.url,
            confidence=0.9 if entity_type in ("individual", "entity") else 0.8,
            quality_flags=flags,
        )


class OfacSdnSource(_XmlListSource):
    """US Treasury OFAC Specially Designated Nationals."""

    name = "ofac_sdn"
    label = "OFAC SDN list (US Treasury)"
    module = "sanctions"
    list_source = "OFAC-SDN"
    url = OFAC_URL
    host_key = "ofac"
    max_staleness_seconds = 72 * 3600
    note = "~19,000 records. United States jurisdiction."

    async def collect(self) -> list[SanctionItem]:
        root = await self._fetch_xml()
        if root is None:
            return []
        publish_date = self._text(root, "Publish_Date")
        out: list[SanctionItem] = []
        for entry in root:
            if self._localname(entry.tag) != "sdnEntry":
                continue
            uid = self._text(entry, "uid")
            last = self._text(entry, "lastName")
            first = self._text(entry, "firstName")
            sdn_type = self._text(entry, "sdnType").lower()
            name = f"{first} {last}".strip() if first else last
            if not name:
                continue

            aliases = [
                self._text(a, "lastName") or self._text(a, "firstName")
                for a in self._find_all(entry, "aka")
            ]
            programs = [self._text(p, "program") for p in self._find_all(entry, "programList")]
            addresses = []
            for addr in self._find_all(entry, "address"):
                parts = [
                    self._text(addr, f)
                    for f in ("address1", "address2", "city", "stateOrProvince", "country")
                ]
                joined = ", ".join(p for p in parts if p)
                if joined:
                    addresses.append(joined)

            identifiers: dict[str, str] = {}
            for idnode in self._find_all(entry, "id"):
                id_type = self._text(idnode, "idType")
                id_num = self._text(idnode, "idNumber")
                if id_type and id_num:
                    identifiers[id_type[:40]] = id_num[:60]

            out.append(
                self._base_item(
                    name=name,
                    entity_type=_SDN_TYPES.get(sdn_type, "unknown"),
                    aliases=[a for a in aliases if a],
                    programs=[p for p in programs if p],
                    addresses=addresses,
                    identifiers=identifiers,
                    listed_at=_parse_ofac_date(publish_date),
                    deterministic_key=f"ofac-{uid or name}",
                )
            )
        return out


class EuSanctionsSource(_XmlListSource):
    """EU consolidated financial sanctions list."""

    name = "eu_sanctions"
    label = "EU consolidated sanctions list (FSD)"
    module = "sanctions"
    list_source = "EU-FSD"
    url = EU_URL
    host_key = "eu-fsd"
    max_staleness_seconds = 72 * 3600
    note = "~25 MB. European Union jurisdiction, all regimes combined."

    async def collect(self) -> list[SanctionItem]:
        root = await self._fetch_xml()
        if root is None:
            return []
        generated = root.attrib.get("generationDate", "")
        out: list[SanctionItem] = []
        for entity in root:
            if self._localname(entity.tag) != "sanctionEntity":
                continue
            logical_id = entity.attrib.get("logicalId", "")
            subjects = self._find_all(entity, "subjectType")
            code = subjects[0].attrib.get("code", "") if subjects else ""
            entity_type = {
                "person": "individual",
                "enterprise": "entity",
                "vessel": "vessel",
                "aircraft": "aircraft",
            }.get(code, "unknown")

            names: list[str] = []
            for name_alias in self._find_all(entity, "nameAlias"):
                whole = name_alias.attrib.get("wholeName", "").strip()
                if whole:
                    names.append(whole)
            if not names:
                continue
            primary, *rest = names

            programs = [
                r.attrib.get("regulationSummary", "") or r.attrib.get("programme", "")
                for r in self._find_all(entity, "regulation")
            ]
            addresses = []
            for addr in self._find_all(entity, "address"):
                parts = []
                for street in self._find_all(addr, "street"):
                    if street.text:
                        parts.append(street.text.strip())
                for field in ("city", "region", "zipCode", "countryDescription"):
                    node = self._find_one(addr, field)
                    if node is not None and node.text:
                        parts.append(node.text.strip())
                if parts:
                    addresses.append(", ".join(parts))

            identifiers: dict[str, str] = {}
            for idnode in self._find_all(entity, "identification"):
                number = idnode.attrib.get("number", "")
                id_type = idnode.attrib.get("identificationTypeCode", "")
                if number:
                    identifiers[id_type[:40] or "id"] = number[:60]

            listed_at = None
            for pub in self._find_all(entity, "publicationUrl"):
                listed_at = _parse_iso_date(pub.attrib.get("publicationDate", ""))
                if listed_at:
                    break

            out.append(
                self._base_item(
                    name=primary,
                    entity_type=entity_type,
                    aliases=rest,
                    programs=[p for p in programs if p],
                    addresses=addresses,
                    identifiers=identifiers,
                    listed_at=listed_at or _parse_iso_date(generated),
                    deterministic_key=f"eu-{logical_id or primary}",
                )
            )
        return out


class UnSanctionsSource(_XmlListSource):
    """UN Security Council consolidated sanctions list."""

    name = "un_sanctions"
    label = "UN Security Council consolidated list"
    module = "sanctions"
    list_source = "UN-SC"
    url = UN_URL
    host_key = "un-sc"
    max_staleness_seconds = 96 * 3600
    note = "Only list with genuinely global jurisdiction."

    async def collect(self) -> list[SanctionItem]:
        root = await self._fetch_xml()
        if root is None:
            return []
        out: list[SanctionItem] = []
        date_generated = ""
        date_node = self._find_one(root, "DATE_GENERATED")
        if date_node is not None and date_node.text:
            date_generated = date_node.text.strip()

        for tag, entity_type in (
            ("INDIVIDUAL", "individual"),
            ("ENTITY", "entity"),
        ):
            for entry in self._find_all(root, tag):
                data_id = self._text(entry, "DATAID")
                first = self._text(entry, "FIRST_NAME")
                second = self._text(entry, "SECOND_NAME")
                third = self._text(entry, "THIRD_NAME")
                entity_name = self._text(entry, "FIRST_NAME") if tag == "ENTITY" else ""
                name = (
                    " ".join(p for p in (first, second, third) if p).strip()
                    if tag == "INDIVIDUAL"
                    else entity_name
                )
                if not name:
                    continue

                aliases = []
                for alias in self._find_all(entry, "ALIAS"):
                    a_first = self._text(alias, "FIRST_NAME")
                    a_second = self._text(alias, "SECOND_NAME")
                    a_name = self._text(alias, "ALIAS_NAME")
                    combined = a_name or " ".join(p for p in (a_first, a_second) if p)
                    if combined:
                        aliases.append(combined)

                programs = []
                for listed in self._find_all(entry, "LISTED_ON"):
                    if listed.text:
                        programs.append(listed.text.strip())
                for ref in self._find_all(entry, "REFERENCE_NUMBER"):
                    if ref.text:
                        programs.append(ref.text.strip())

                addresses = []
                for addr in self._find_all(entry, "ADDRESS"):
                    parts = [
                        self._text(addr, f)
                        for f in (
                            "STREET",
                            "CITY",
                            "STATE_PROVINCE",
                            "ZIP_CODE",
                            "COUNTRY",
                        )
                    ]
                    joined = ", ".join(p for p in parts if p)
                    if joined:
                        addresses.append(joined)

                identifiers: dict[str, str] = {}
                for doc in self._find_all(entry, "INDIVIDUAL_DOCUMENT"):
                    number = self._text(doc, "NUMBER")
                    doc_type = self._text(doc, "TYPE_OF_DOCUMENT")
                    if number:
                        identifiers[doc_type[:40] or "document"] = number[:60]

                listing_date = ""
                for dl in self._find_all(entry, "LISTED_ON"):
                    if dl.text:
                        listing_date = dl.text.strip()
                        break

                out.append(
                    self._base_item(
                        name=name,
                        entity_type=entity_type,
                        aliases=aliases,
                        programs=[p for p in programs if p],
                        addresses=addresses,
                        identifiers=identifiers,
                        listed_at=_parse_any_date(listing_date)
                        or _parse_any_date(date_generated),
                        deterministic_key=f"un-{data_id or name}",
                    )
                )
        return out


def _parse_ofac_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _parse_iso_date(value: str) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _parse_any_date(value: str) -> datetime | None:
    """Try the several date shapes the UN list uses across record types."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:len(fmt) + 4].strip(), fmt).replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            continue
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match:
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None
    return None


# -- cross-list analysis ----------------------------------------------------


def cross_list_matches(items: list[SanctionItem]) -> list[dict]:
    """Find names appearing on more than one jurisdiction's list.

    A person or entity sanctioned by the UN *and* OFAC *and* the EU is a
    materially stronger signal than one on a single national list. Exact
    name matching under-reports (spelling variants), so this is labelled as
    exact-match only rather than presented as complete.
    """
    from collections import defaultdict

    by_name: dict[str, list[SanctionItem]] = defaultdict(list)
    for item in items:
        key = re.sub(r"[^a-z ]", "", item.name.lower()).strip()
        key = " ".join(key.split())
        if key:
            by_name[key].append(item)

    out = []
    for group in by_name.values():
        lists = sorted({g.list_source for g in group})
        if len(lists) > 1:
            out.append(
                {
                    "name": group[0].name,
                    "lists": lists,
                    "list_count": len(lists),
                    "entity_type": group[0].entity_type,
                    "programs": sorted({p for g in group for p in g.programs}),
                    "match_basis": "exact normalised name",
                    "records": [
                        {"id": g.id, "list": g.list_source, "type": g.entity_type}
                        for g in group
                    ],
                }
            )
    out.sort(key=lambda r: -r["list_count"])
    return out


def sanctions_by_program(items: list[SanctionItem], top_n: int = 25) -> list[dict]:
    from collections import Counter

    counter: Counter[str] = Counter()
    for item in items:
        for program in item.programs:
            if program:
                counter[program] += 1
    return [{"program": p, "count": c} for p, c in counter.most_common(top_n)]


def sanctions_by_country(items: list[SanctionItem], top_n: int = 30) -> list[dict]:
    from collections import Counter

    counter: Counter[str] = Counter()
    inferred = 0
    for item in items:
        for iso in item.countries:
            counter[iso] += 1
            if "country_inferred_from_address" in item.quality_flags:
                inferred += 1
    return [
        {"country_iso2": iso, "count": c, "basis": "inferred from address"}
        for iso, c in counter.most_common(top_n)
    ] or []
