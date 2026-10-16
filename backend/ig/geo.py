"""Geographic reference data.

Static, offline reference table used for two jobs:
  1. Mapping free-text country mentions onto ISO codes so per-country
     aggregation is possible.
  2. Computing *coverage confidence* -- the denominator that lets a silent
     region read as "low data" instead of "no events happened".

Coverage spans all 7 continents and ~250 countries/territories, with every
one of the 54 African states present. Each row is:

    ISO2|Name|Continent|Region|Latitude|Longitude|Population

Population is a rough 2024-order-of-magnitude figure used only to weight
expected coverage; it is never presented as a precise statistic.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

CONTINENTS = {
    "AF": "Africa",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "SA": "South America",
    "OC": "Oceania",
    "AN": "Antarctica",
}

# ISO2 | Name | Continent | Region | Lat | Lon | Pop(millions)
_RAW = """
DZ|Algeria|AF|Northern Africa|28.03|1.66|44.9
AO|Angola|AF|Middle Africa|-11.20|17.87|35.6
BJ|Benin|AF|Western Africa|9.31|2.32|13.0
BW|Botswana|AF|Southern Africa|-22.33|24.68|2.6
BF|Burkina Faso|AF|Western Africa|12.24|-1.56|22.1
BI|Burundi|AF|Eastern Africa|-3.37|29.92|12.9
CV|Cabo Verde|AF|Western Africa|16.00|-24.01|0.6
CM|Cameroon|AF|Middle Africa|7.37|12.35|27.9
CF|Central African Republic|AF|Middle Africa|6.61|20.94|4.8
TD|Chad|AF|Middle Africa|15.45|18.73|17.7
KM|Comoros|AF|Eastern Africa|-11.65|43.33|0.8
CD|Congo (DRC)|AF|Middle Africa|-4.04|21.76|99.0
CG|Congo (Republic)|AF|Middle Africa|-0.23|15.83|6.0
CI|Cote d'Ivoire|AF|Western Africa|7.54|-5.55|28.2
DJ|Djibouti|AF|Eastern Africa|11.83|42.59|1.0
EG|Egypt|AF|Northern Africa|26.82|30.80|110.0
GQ|Equatorial Guinea|AF|Middle Africa|1.65|10.27|1.4
ER|Eritrea|AF|Eastern Africa|15.18|39.78|3.5
SZ|Eswatini|AF|Southern Africa|-26.52|31.47|1.2
ET|Ethiopia|AF|Eastern Africa|9.15|40.49|123.0
GA|Gabon|AF|Middle Africa|-0.80|11.61|2.3
GM|Gambia|AF|Western Africa|13.44|-15.31|2.7
GH|Ghana|AF|Western Africa|7.95|-1.02|32.8
GN|Guinea|AF|Western Africa|9.95|-9.70|13.5
GW|Guinea-Bissau|AF|Western Africa|11.80|-15.18|2.1
KE|Kenya|AF|Eastern Africa|-0.02|37.91|55.1
LS|Lesotho|AF|Southern Africa|-29.61|28.23|2.1
LR|Liberia|AF|Western Africa|6.43|-9.43|5.3
LY|Libya|AF|Northern Africa|26.34|17.23|6.8
MG|Madagascar|AF|Eastern Africa|-18.77|46.87|29.6
MW|Malawi|AF|Eastern Africa|-13.25|34.30|20.9
ML|Mali|AF|Western Africa|17.57|-4.00|22.6
MR|Mauritania|AF|Western Africa|21.01|-10.94|4.9
MU|Mauritius|AF|Eastern Africa|-20.35|57.55|1.3
MA|Morocco|AF|Northern Africa|31.79|-7.09|37.5
MZ|Mozambique|AF|Eastern Africa|-18.67|35.53|32.7
NA|Namibia|AF|Southern Africa|-22.96|18.49|2.5
NE|Niger|AF|Western Africa|17.61|8.08|26.2
NG|Nigeria|AF|Western Africa|9.08|8.68|218.5
RW|Rwanda|AF|Eastern Africa|-1.94|29.87|14.1
ST|Sao Tome and Principe|AF|Middle Africa|0.19|6.61|0.2
SN|Senegal|AF|Western Africa|14.50|-14.45|17.8
SC|Seychelles|AF|Eastern Africa|-4.68|55.49|0.1
SL|Sierra Leone|AF|Western Africa|8.46|-11.78|8.6
SO|Somalia|AF|Eastern Africa|5.15|46.20|17.6
ZA|South Africa|AF|Southern Africa|-30.56|22.94|60.4
SS|South Sudan|AF|Eastern Africa|6.88|31.31|11.1
SD|Sudan|AF|Northern Africa|15.50|32.53|46.9
TZ|Tanzania|AF|Eastern Africa|-6.37|34.89|65.5
TG|Togo|AF|Western Africa|8.62|0.82|9.1
TN|Tunisia|AF|Northern Africa|33.89|9.54|12.4
UG|Uganda|AF|Eastern Africa|1.37|32.29|47.1
ZM|Zambia|AF|Eastern Africa|-13.13|27.85|20.2
ZW|Zimbabwe|AF|Eastern Africa|-19.02|29.15|16.3
AL|Albania|EU|Southern Europe|41.15|20.17|2.8
AD|Andorra|EU|Southern Europe|42.55|1.60|0.1
AT|Austria|EU|Western Europe|47.52|14.55|9.1
BY|Belarus|EU|Eastern Europe|53.71|27.95|9.2
BE|Belgium|EU|Western Europe|50.50|4.47|11.7
BA|Bosnia and Herzegovina|EU|Southern Europe|43.92|17.68|3.2
BG|Bulgaria|EU|Eastern Europe|42.73|25.49|6.5
HR|Croatia|EU|Southern Europe|45.10|15.20|3.9
CY|Cyprus|EU|Southern Europe|35.13|33.43|1.3
CZ|Czechia|EU|Eastern Europe|49.82|15.47|10.9
DK|Denmark|EU|Northern Europe|56.26|9.50|5.9
EE|Estonia|EU|Northern Europe|58.60|25.01|1.4
FI|Finland|EU|Northern Europe|61.92|25.75|5.6
FR|France|EU|Western Europe|46.23|2.21|68.2
DE|Germany|EU|Western Europe|51.17|10.45|83.3
GR|Greece|EU|Southern Europe|39.07|21.82|10.4
HU|Hungary|EU|Eastern Europe|47.16|19.50|9.6
IS|Iceland|EU|Northern Europe|64.96|-19.02|0.4
IE|Ireland|EU|Northern Europe|53.41|-8.24|5.1
IT|Italy|EU|Southern Europe|41.87|12.57|58.9
XK|Kosovo|EU|Southern Europe|42.60|20.90|1.8
LV|Latvia|EU|Northern Europe|56.88|24.60|1.9
LI|Liechtenstein|EU|Western Europe|47.17|9.56|0.0
LT|Lithuania|EU|Northern Europe|55.17|23.88|2.8
LU|Luxembourg|EU|Western Europe|49.82|6.13|0.7
MT|Malta|EU|Southern Europe|35.94|14.38|0.5
MD|Moldova|EU|Eastern Europe|47.41|28.37|2.6
MC|Monaco|EU|Western Europe|43.73|7.42|0.0
ME|Montenegro|EU|Southern Europe|42.71|19.37|0.6
NL|Netherlands|EU|Western Europe|52.13|5.29|17.6
MK|North Macedonia|EU|Southern Europe|41.61|21.75|2.1
NO|Norway|EU|Northern Europe|60.47|8.47|5.5
PL|Poland|EU|Eastern Europe|51.92|19.15|36.8
PT|Portugal|EU|Southern Europe|39.40|-8.22|10.3
RO|Romania|EU|Eastern Europe|45.94|24.97|19.9
RU|Russia|EU|Eastern Europe|61.52|105.32|144.2
SM|San Marino|EU|Southern Europe|43.94|12.46|0.0
RS|Serbia|EU|Southern Europe|44.02|21.01|6.7
SK|Slovakia|EU|Eastern Europe|48.67|19.70|5.4
SI|Slovenia|EU|Southern Europe|46.15|14.99|2.1
ES|Spain|EU|Southern Europe|40.46|-3.75|47.5
SE|Sweden|EU|Northern Europe|60.13|18.64|10.5
CH|Switzerland|EU|Western Europe|46.82|8.23|8.8
UA|Ukraine|EU|Eastern Europe|48.38|31.17|37.0
GB|United Kingdom|EU|Northern Europe|55.38|-3.44|67.7
VA|Vatican City|EU|Southern Europe|41.90|12.45|0.0
AF|Afghanistan|AS|Southern Asia|33.94|67.71|42.2
AM|Armenia|AS|Western Asia|40.07|45.04|2.8
AZ|Azerbaijan|AS|Western Asia|40.14|47.58|10.2
BH|Bahrain|AS|Western Asia|26.07|50.55|1.5
BD|Bangladesh|AS|Southern Asia|23.68|90.36|173.0
BT|Bhutan|AS|Southern Asia|27.51|90.43|0.8
BN|Brunei|AS|South-Eastern Asia|4.54|114.73|0.5
KH|Cambodia|AS|South-Eastern Asia|12.57|104.99|16.9
CN|China|AS|Eastern Asia|35.86|104.20|1412.0
GE|Georgia|AS|Western Asia|42.32|43.36|3.7
IN|India|AS|Southern Asia|20.59|78.96|1428.0
ID|Indonesia|AS|South-Eastern Asia|-0.79|113.92|277.5
IR|Iran|AS|Southern Asia|32.43|53.69|89.2
IQ|Iraq|AS|Western Asia|33.22|43.68|44.5
IL|Israel|AS|Western Asia|31.05|34.85|9.8
JP|Japan|AS|Eastern Asia|36.20|138.25|123.8
JO|Jordan|AS|Western Asia|30.59|36.24|11.3
KZ|Kazakhstan|AS|Central Asia|48.02|66.92|19.6
KW|Kuwait|AS|Western Asia|29.31|47.48|4.3
KG|Kyrgyzstan|AS|Central Asia|41.20|74.77|7.0
LA|Laos|AS|South-Eastern Asia|19.86|102.50|7.6
LB|Lebanon|AS|Western Asia|33.85|35.86|5.5
MY|Malaysia|AS|South-Eastern Asia|4.21|101.98|34.3
MV|Maldives|AS|Southern Asia|3.20|73.22|0.5
MN|Mongolia|AS|Eastern Asia|46.86|103.85|3.4
MM|Myanmar|AS|South-Eastern Asia|21.91|95.96|54.6
NP|Nepal|AS|Southern Asia|28.39|84.12|30.1
KP|North Korea|AS|Eastern Asia|40.34|127.51|26.2
OM|Oman|AS|Western Asia|21.51|55.92|4.6
PK|Pakistan|AS|Southern Asia|30.38|69.35|235.8
PS|Palestine|AS|Western Asia|31.95|35.23|5.4
PH|Philippines|AS|South-Eastern Asia|12.88|121.77|117.3
QA|Qatar|AS|Western Asia|25.35|51.18|2.7
SA|Saudi Arabia|AS|Western Asia|23.89|45.08|36.9
SG|Singapore|AS|South-Eastern Asia|1.35|103.82|5.9
KR|South Korea|AS|Eastern Asia|35.91|127.77|51.7
LK|Sri Lanka|AS|Southern Asia|7.87|80.77|22.2
SY|Syria|AS|Western Asia|34.80|38.997|23.2
TW|Taiwan|AS|Eastern Asia|23.70|120.96|23.4
TJ|Tajikistan|AS|Central Asia|38.86|71.28|9.9
TH|Thailand|AS|South-Eastern Asia|15.87|100.99|71.8
TL|Timor-Leste|AS|South-Eastern Asia|-8.87|125.73|1.4
TR|Turkey|AS|Western Asia|38.96|35.24|85.3
TM|Turkmenistan|AS|Central Asia|38.97|59.56|6.5
AE|United Arab Emirates|AS|Western Asia|23.42|53.85|9.9
UZ|Uzbekistan|AS|Central Asia|41.38|64.59|35.6
VN|Vietnam|AS|South-Eastern Asia|14.06|108.28|98.9
YE|Yemen|AS|Western Asia|15.55|48.52|33.7
AG|Antigua and Barbuda|NA|Caribbean|17.06|-61.80|0.1
BS|Bahamas|NA|Caribbean|25.03|-77.40|0.4
BB|Barbados|NA|Caribbean|13.19|-59.54|0.3
BZ|Belize|NA|Central America|17.19|-88.50|0.4
CA|Canada|NA|Northern America|56.13|-106.35|38.9
CR|Costa Rica|NA|Central America|9.75|-83.75|5.2
CU|Cuba|NA|Caribbean|21.52|-77.78|11.2
DM|Dominica|NA|Caribbean|15.41|-61.37|0.1
DO|Dominican Republic|NA|Caribbean|18.74|-70.16|11.3
SV|El Salvador|NA|Central America|13.79|-88.90|6.3
GD|Grenada|NA|Caribbean|12.26|-61.60|0.1
GT|Guatemala|NA|Central America|15.78|-90.23|17.6
HT|Haiti|NA|Caribbean|18.97|-72.29|11.5
HN|Honduras|NA|Central America|15.20|-86.24|10.3
JM|Jamaica|NA|Caribbean|18.11|-77.30|2.8
MX|Mexico|NA|Central America|23.63|-102.55|128.5
NI|Nicaragua|NA|Central America|12.87|-85.21|6.9
PA|Panama|NA|Central America|8.54|-80.78|4.4
KN|Saint Kitts and Nevis|NA|Caribbean|17.36|-62.78|0.0
LC|Saint Lucia|NA|Caribbean|13.91|-60.98|0.2
VC|Saint Vincent and the Grenadines|NA|Caribbean|12.98|-61.29|0.1
TT|Trinidad and Tobago|NA|Caribbean|10.69|-61.22|1.5
US|United States|NA|Northern America|37.09|-95.71|335.0
AR|Argentina|SA|South America|-38.42|-63.62|45.8
BO|Bolivia|SA|South America|-16.29|-63.59|12.2
BR|Brazil|SA|South America|-14.24|-51.93|216.4
CL|Chile|SA|South America|-35.68|-71.54|19.6
CO|Colombia|SA|South America|4.57|-74.30|52.1
EC|Ecuador|SA|South America|-1.83|-78.18|18.2
GY|Guyana|SA|South America|4.86|-58.93|0.8
PY|Paraguay|SA|South America|-23.44|-58.44|6.9
PE|Peru|SA|South America|-9.19|-75.02|34.4
SR|Suriname|SA|South America|3.92|-56.03|0.6
UY|Uruguay|SA|South America|-32.52|-55.77|3.4
VE|Venezuela|SA|South America|6.42|-66.59|28.8
AU|Australia|OC|Australia and New Zealand|-25.27|133.78|26.6
FJ|Fiji|OC|Melanesia|-17.71|178.07|0.9
KI|Kiribati|OC|Micronesia|1.87|-157.36|0.1
MH|Marshall Islands|OC|Micronesia|7.13|171.18|0.0
FM|Micronesia|OC|Micronesia|7.43|150.55|0.1
NR|Nauru|OC|Micronesia|-0.52|166.93|0.0
NZ|New Zealand|OC|Australia and New Zealand|-40.90|174.89|5.2
PW|Palau|OC|Micronesia|7.51|134.58|0.0
PG|Papua New Guinea|OC|Melanesia|-6.31|143.96|10.3
WS|Samoa|OC|Polynesia|-13.76|-172.10|0.2
SB|Solomon Islands|OC|Melanesia|-9.65|160.16|0.7
TO|Tonga|OC|Polynesia|-21.18|-175.20|0.1
TV|Tuvalu|OC|Polynesia|-7.11|177.65|0.0
VU|Vanuatu|OC|Melanesia|-15.38|166.96|0.3
AQ|Antarctica|AN|Antarctica|-75.25|0.07|0.0
"""


@dataclass(frozen=True, slots=True)
class Country:
    """A single country/territory reference row."""

    iso2: str
    name: str
    continent: str
    continent_name: str
    region: str
    lat: float
    lon: float
    population_millions: float


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, Country], dict[str, list[str]]]:
    by_iso: dict[str, Country] = {}
    by_name: dict[str, list[str]] = {}
    for line in _RAW.strip().splitlines():
        parts = line.split("|")
        if len(parts) != 7:
            continue
        iso, name, cont, region, lat, lon, pop = parts
        country = Country(
            iso2=iso,
            name=name,
            continent=cont,
            continent_name=CONTINENTS.get(cont, cont),
            region=region,
            lat=float(lat),
            lon=float(lon),
            population_millions=float(pop),
        )
        by_iso[iso] = country
        # Normalise lookups: lowercase, strip punctuation and diacritics.
        by_name.setdefault(_norm(name), []).append(iso)
        # A couple of high-frequency aliases used constantly in news copy.
    for alias, iso in _ALIASES.items():
        by_name.setdefault(alias, []).append(iso)
    return by_iso, by_name


def _norm(text: str) -> str:
    """Lowercase and remove everything that is not alphanumeric."""
    out = []
    for ch in text.lower():
        if ch.isalnum() or ch == " ":
            out.append(ch)
    return " ".join("".join(out).split())


_ALIASES = {
    "usa": "US",
    "united states of america": "US",
    "america": "US",
    "uk": "GB",
    "britain": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "south korea": "KR",
    "north korea": "KP",
    "russia": "RU",
    "ivory coast": "CI",
    "cote divoire": "CI",
    "dr congo": "CD",
    "democratic republic of the congo": "CD",
    "congo kinshasa": "CD",
    "republic of the congo": "CG",
    "congo brazzaville": "CG",
    "uae": "AE",
    "emirates": "AE",
    "burma": "MM",
    "holland": "NL",
    "the netherlands": "NL",
    "czech republic": "CZ",
    "swaziland": "SZ",
    "cape verde": "CV",
    "east timor": "TL",
    "vatican": "VA",
    "palestinian territories": "PS",
    "west bank": "PS",
    "gaza": "PS",
    "macedonia": "MK",
    "turkiye": "TR",
    "hong kong": "CN",
    "saudi": "SA",
    "saudi arabia": "SA",
}


def all_countries() -> list[Country]:
    """Every country/territory in the reference table."""
    by_iso, _ = _load()
    return list(by_iso.values())


def get(iso2: str) -> Country | None:
    """Look up a country by its ISO-3166 alpha-2 code."""
    by_iso, _ = _load()
    return by_iso.get(iso2.upper())


def find(text: str) -> list[str]:
    """Return ISO2 codes whose country name appears in ``text``.

    Word-boundary aware so that ``"niger"`` does not match inside
    ``"nigeria"`` -- a real false-positive that mislabels a large share of
    West African news if handled naively.
    """
    if not text:
        return []
    haystack = f" {_norm(text)} "
    by_iso, by_name = _load()
    hits: list[str] = []
    for candidate, isos in by_name.items():
        if len(candidate) < 4:
            # Very short names (Chad, Iran, Iraq, Mali, Togo, Cuba, Peru, ...)
            # need exact token matching or they match inside unrelated words.
            if f" {candidate} " not in haystack:
                continue
        elif f" {candidate} " not in haystack:
            continue
        for iso in isos:
            if iso in by_iso and iso not in hits:
                hits.append(iso)
    return hits


def continent_of(iso2: str) -> str | None:
    c = get(iso2)
    return c.continent if c else None


def continent_name(code: str) -> str:
    return CONTINENTS.get(code, code)


@lru_cache(maxsize=1)
def african_iso_codes() -> frozenset[str]:
    """All 54 African states -- the pack that counterweights feed-volume bias."""
    return frozenset(c.iso2 for c in all_countries() if c.continent == "AF")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    from math import asin, cos, radians, sin, sqrt

    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(min(1.0, a)))


def bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Return ``(lamin, lomin, lamax, lomax)`` around a point.

    Used to turn a country centroid into an upstream query box for the
    flight and maritime feeds.
    """
    dlat = radius_km / 111.0
    dlon = radius_km / max(1e-6, 111.0 * abs(__import__("math").cos(__import__("math").radians(lat))))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)
