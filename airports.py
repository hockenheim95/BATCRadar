"""ICAO lookup: iata, name, lat, lon, tz."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import threading
import urllib.request
from pathlib import Path

from paths import data_file

# icao: (iata, name, lat, lon, tz)
AIRPORTS: dict[str, tuple[str, str, float, float, str]] = {
    "EDDF": ("FRA", "Frankfurt", 50.0379, 8.5622, "Europe/Berlin"),
    "EDDM": ("MUC", "Munich", 48.3538, 11.7861, "Europe/Berlin"),
    "EDDH": ("HAM", "Hamburg", 53.6304, 9.9882, "Europe/Berlin"),
    "EDDL": ("DUS", "Dusseldorf", 51.2895, 6.7668, "Europe/Berlin"),
    "EDDB": ("BER", "Berlin", 52.3667, 13.5033, "Europe/Berlin"),
    "EDDS": ("STR", "Stuttgart", 48.6899, 9.2220, "Europe/Berlin"),
    "EDDK": ("CGN", "Cologne", 50.8659, 7.1427, "Europe/Berlin"),
    "EDDN": ("NUE", "Nuremberg", 49.4987, 11.0781, "Europe/Berlin"),
    "LOWW": ("VIE", "Vienna", 48.1103, 16.5697, "Europe/Vienna"),
    "LSZH": ("ZRH", "Zurich", 47.4581, 8.5481, "Europe/Zurich"),
    "LSGG": ("GVA", "Geneva", 46.2381, 6.1089, "Europe/Zurich"),
    "EHAM": ("AMS", "Amsterdam", 52.3105, 4.7683, "Europe/Amsterdam"),
    "EBBR": ("BRU", "Brussels", 50.9014, 4.4844, "Europe/Brussels"),
    "LFPG": ("CDG", "Paris", 49.0097, 2.5479, "Europe/Paris"),
    "LFPO": ("ORY", "Paris Orly", 48.7233, 2.3794, "Europe/Paris"),
    "EGLL": ("LHR", "London", 51.4700, -0.4543, "Europe/London"),
    "EGKK": ("LGW", "Gatwick", 51.1537, -0.1821, "Europe/London"),
    "EGCC": ("MAN", "Manchester", 53.3537, -2.2750, "Europe/London"),
    "EIDW": ("DUB", "Dublin", 53.4213, -6.2701, "Europe/Dublin"),
    "LEMD": ("MAD", "Madrid", 40.4983, -3.5676, "Europe/Madrid"),
    "LEBL": ("BCN", "Barcelona", 41.2974, 2.0833, "Europe/Madrid"),
    "LEPA": ("PMI", "Palma De Mallorca", 39.5517, 2.7389, "Europe/Madrid"),
    "LEAL": ("ALC", "Alicante", 38.2822, -0.5582, "Europe/Madrid"),
    "LEIB": ("IBZ", "Ibiza", 38.8729, 1.3731, "Europe/Madrid"),
    "LEVC": ("VLC", "Valencia", 39.4893, -0.4816, "Europe/Madrid"),
    "LIRF": ("FCO", "Rome", 41.8003, 12.2389, "Europe/Rome"),
    "LIMC": ("MXP", "Milan", 45.6306, 8.7281, "Europe/Rome"),
    "LPPT": ("LIS", "Lisbon", 38.7742, -9.1342, "Europe/Lisbon"),
    "LPPR": ("OPO", "Porto", 41.2481, -8.6814, "Europe/Lisbon"),
    "LPMA": ("FNC", "Funchal", 32.6979, -16.7745, "Atlantic/Madeira"),
    "LPPS": ("PXO", "Porto Santo", 33.0734, -16.3500, "Atlantic/Madeira"),
    "EKCH": ("CPH", "Copenhagen", 55.6180, 12.6508, "Europe/Copenhagen"),
    "ENGM": ("OSL", "Oslo", 60.1939, 11.1004, "Europe/Oslo"),
    "ESSA": ("ARN", "Stockholm", 59.6519, 17.9186, "Europe/Stockholm"),
    "EFHK": ("HEL", "Helsinki", 60.3172, 24.9633, "Europe/Helsinki"),
    "EPWA": ("WAW", "Warsaw", 52.1657, 20.9671, "Europe/Warsaw"),
    "LKPR": ("PRG", "Prague", 50.1008, 14.2600, "Europe/Prague"),
    "LHBP": ("BUD", "Budapest", 47.4298, 19.2611, "Europe/Budapest"),
    "LROP": ("OTP", "Bucharest", 44.5711, 26.0850, "Europe/Bucharest"),
    "LTFM": ("IST", "Istanbul", 41.2753, 28.7519, "Europe/Istanbul"),
    "LGAV": ("ATH", "Athens", 37.9364, 23.9445, "Europe/Athens"),
    "GCLP": ("LPA", "Gran Canaria", 27.9319, -15.3866, "Atlantic/Canary"),
    "GCTS": ("TFS", "Tenerife South", 28.0445, -16.5725, "Atlantic/Canary"),
    "KLAX": ("LAX", "Los Angeles", 33.9425, -118.4081, "America/Los_Angeles"),
    "KSFO": ("SFO", "San Francisco", 37.6213, -122.3790, "America/Los_Angeles"),
    "KSJC": ("SJC", "San Jose", 37.3626, -121.9290, "America/Los_Angeles"),
    "KSAN": ("SAN", "San Diego", 32.7338, -117.1933, "America/Los_Angeles"),
    "KBUR": ("BUR", "Burbank", 34.2006, -118.3585, "America/Los_Angeles"),
    "KVNY": ("VNY", "Van Nuys", 34.2098, -118.4900, "America/Los_Angeles"),
    "KOAK": ("OAK", "Oakland", 37.7213, -122.2208, "America/Los_Angeles"),
    "KSMF": ("SMF", "Sacramento", 38.6954, -121.5908, "America/Los_Angeles"),
    "KSNA": ("SNA", "Santa Ana", 33.6757, -117.8682, "America/Los_Angeles"),
    "KONT": ("ONT", "Ontario", 34.0560, -117.6012, "America/Los_Angeles"),
    "KPSP": ("PSP", "Palm Springs", 33.8297, -116.5067, "America/Los_Angeles"),
    "KFAT": ("FAT", "Fresno", 36.7762, -119.7181, "America/Los_Angeles"),
    "KSEA": ("SEA", "Seattle", 47.4502, -122.3088, "America/Los_Angeles"),
    "KPDX": ("PDX", "Portland", 45.5898, -122.5951, "America/Los_Angeles"),
    "KPHX": ("PHX", "Phoenix", 33.4343, -112.0116, "America/Phoenix"),
    "KTUS": ("TUS", "Tucson", 32.1161, -110.9410, "America/Phoenix"),
    "KLAS": ("LAS", "Las Vegas", 36.0840, -115.1537, "America/Los_Angeles"),
    "KRNO": ("RNO", "Reno", 39.4991, -119.7681, "America/Los_Angeles"),
    "KSLC": ("SLC", "Salt Lake City", 40.7899, -111.9791, "America/Denver"),
    "KDEN": ("DEN", "Denver", 39.8561, -104.6737, "America/Denver"),
    "KABQ": ("ABQ", "Albuquerque", 35.0402, -106.6092, "America/Denver"),
    "KDFW": ("DFW", "Dallas", 32.8998, -97.0403, "America/Chicago"),
    "KDAL": ("DAL", "Dallas Love", 32.8471, -96.8518, "America/Chicago"),
    "KIAH": ("IAH", "Houston", 29.9902, -95.3368, "America/Chicago"),
    "KHOU": ("HOU", "Houston Hobby", 29.6454, -95.2789, "America/Chicago"),
    "KAUS": ("AUS", "Austin", 30.1945, -97.6699, "America/Chicago"),
    "KSAT": ("SAT", "San Antonio", 29.5337, -98.4698, "America/Chicago"),
    "KMSY": ("MSY", "New Orleans", 29.9934, -90.2580, "America/Chicago"),
    "KSTL": ("STL", "St. Louis", 38.7487, -90.3700, "America/Chicago"),
    "KMCI": ("MCI", "Kansas City", 39.2976, -94.7139, "America/Chicago"),
    "KMSP": ("MSP", "Minneapolis", 44.8820, -93.2218, "America/Chicago"),
    "KORD": ("ORD", "Chicago", 41.9742, -87.9073, "America/Chicago"),
    "KMDW": ("MDW", "Chicago Midway", 41.7868, -87.7522, "America/Chicago"),
    "KDTW": ("DTW", "Detroit", 42.2124, -83.3534, "America/Detroit"),
    "KCLE": ("CLE", "Cleveland", 41.4117, -81.8498, "America/New_York"),
    "KCVG": ("CVG", "Cincinnati", 39.0488, -84.6678, "America/New_York"),
    "KATL": ("ATL", "Atlanta", 33.6407, -84.4277, "America/New_York"),
    "KCLT": ("CLT", "Charlotte", 35.2140, -80.9431, "America/New_York"),
    "KRDU": ("RDU", "Raleigh", 35.8776, -78.7875, "America/New_York"),
    "KBNA": ("BNA", "Nashville", 36.1245, -86.6782, "America/Chicago"),
    "KMCO": ("MCO", "Orlando", 28.4312, -81.3081, "America/New_York"),
    "KTPA": ("TPA", "Tampa", 27.9755, -82.5332, "America/New_York"),
    "KMIA": ("MIA", "Miami", 25.7959, -80.2870, "America/New_York"),
    "KFLL": ("FLL", "Fort Lauderdale", 26.0726, -80.1527, "America/New_York"),
    "KJFK": ("JFK", "New York", 40.6413, -73.7781, "America/New_York"),
    "KEWR": ("EWR", "Newark", 40.6895, -74.1745, "America/New_York"),
    "KLGA": ("LGA", "LaGuardia", 40.7769, -73.8740, "America/New_York"),
    "KBOS": ("BOS", "Boston", 42.3656, -71.0096, "America/New_York"),
    "KPHL": ("PHL", "Philadelphia", 39.8721, -75.2411, "America/New_York"),
    "KBWI": ("BWI", "Baltimore", 39.1754, -76.6684, "America/New_York"),
    "KIAD": ("IAD", "Washington Dulles", 38.9531, -77.4565, "America/New_York"),
    "KDCA": ("DCA", "Washington", 38.8512, -77.0402, "America/New_York"),
    "KMKE": ("MKE", "Milwaukee", 42.9472, -87.8966, "America/Chicago"),
    "KIND": ("IND", "Indianapolis", 39.7173, -86.2944, "America/Indiana/Indianapolis"),
    "KSDF": ("SDF", "Louisville", 38.1744, -85.7360, "America/Kentucky/Louisville"),
    "KMEM": ("MEM", "Memphis", 35.0424, -89.9767, "America/Chicago"),
    "KBHM": ("BHM", "Birmingham", 33.5629, -86.7535, "America/Chicago"),
    "KOKC": ("OKC", "Oklahoma City", 35.3931, -97.6007, "America/Chicago"),
    "KTUL": ("TUL", "Tulsa", 36.1984, -95.8881, "America/Chicago"),
    "KOMA": ("OMA", "Omaha", 41.3032, -95.8941, "America/Chicago"),
    "KELP": ("ELP", "El Paso", 31.8070, -106.3778, "America/Denver"),
    "KGEG": ("GEG", "Spokane", 47.6199, -117.5338, "America/Los_Angeles"),
    "KBOI": ("BOI", "Boise", 43.5644, -116.2228, "America/Boise"),
    "PANC": ("ANC", "Anchorage", 61.1743, -149.9962, "America/Anchorage"),
    "PAFA": ("FAI", "Fairbanks", 64.8151, -147.8560, "America/Anchorage"),
    "PHNL": ("HNL", "Honolulu", 21.3187, -157.9225, "Pacific/Honolulu"),
    "PHJR": ("JRF", "Kalaeloa", 21.3074, -158.0703, "Pacific/Honolulu"),
    "PHNG": ("HNG", "Kaneohe Bay", 21.4505, -157.7680, "Pacific/Honolulu"),
    "PHMK": ("MKK", "Molokai", 21.1529, -157.0963, "Pacific/Honolulu"),
    "PHNY": ("LNY", "Lanai", 20.7856, -156.9514, "Pacific/Honolulu"),
    "PHOG": ("OGG", "Kahului", 20.8986, -156.4305, "Pacific/Honolulu"),
    "PHKO": ("KOA", "Kona", 19.7388, -156.0456, "Pacific/Honolulu"),
    "PHLI": ("LIH", "Lihue", 21.9760, -159.3390, "Pacific/Honolulu"),
    "PHTO": ("ITO", "Hilo", 19.7214, -155.0485, "Pacific/Honolulu"),
    "PGUM": ("GUM", "Guam", 13.4834, 144.7970, "Pacific/Guam"),
    "TJSJ": ("SJU", "San Juan", 18.4394, -66.0020, "America/Puerto_Rico"),
    "CYVR": ("YVR", "Vancouver", 49.1947, -123.1840, "America/Vancouver"),
    "CYYZ": ("YYZ", "Toronto", 43.6777, -79.6248, "America/Toronto"),
    "CYUL": ("YUL", "Montreal", 45.4706, -73.7408, "America/Toronto"),
    "CYYC": ("YYC", "Calgary", 51.1136, -114.0203, "America/Edmonton"),
    "CYEG": ("YEG", "Edmonton", 53.3097, -113.5800, "America/Edmonton"),
    "MMMX": ("MEX", "Mexico City", 19.4363, -99.0721, "America/Mexico_City"),
    "MMGL": ("GDL", "Guadalajara", 20.5218, -103.3112, "America/Mexico_City"),
    "MMUN": ("CUN", "Cancun", 21.0365, -86.8770, "America/Cancun"),
    "MMTJ": ("TIJ", "Tijuana", 32.5411, -116.9700, "America/Tijuana"),
    "MMCZ": ("CZM", "Cozumel", 20.5224, -86.9256, "America/Cancun"),
    "RJTT": ("HND", "Tokyo Haneda", 35.5494, 139.7798, "Asia/Tokyo"),
    "RJAA": ("NRT", "Tokyo Narita", 35.7647, 140.3864, "Asia/Tokyo"),
    "VHHH": ("HKG", "Hong Kong", 22.3080, 113.9185, "Asia/Hong_Kong"),
    "WSSS": ("SIN", "Singapore", 1.3644, 103.9915, "Asia/Singapore"),
    "YSSY": ("SYD", "Sydney", -33.9399, 151.1753, "Australia/Sydney"),
    "YMML": ("MEL", "Melbourne", -37.6733, 144.8433, "Australia/Melbourne"),
    "NZAA": ("AKL", "Auckland", -37.0082, 174.7850, "Pacific/Auckland"),
    "OMDB": ("DXB", "Dubai", 25.2532, 55.3657, "Asia/Dubai"),
    "OTHH": ("DOH", "Doha", 25.2731, 51.6081, "Asia/Qatar"),
    "FACT": ("CPT", "Cape Town", -33.9648, 18.6017, "Africa/Johannesburg"),
    "FAOR": ("JNB", "Johannesburg", -26.1392, 28.2460, "Africa/Johannesburg"),
    "SBGR": ("GRU", "Sao Paulo", -23.4356, -46.4731, "America/Sao_Paulo"),
    "SAEZ": ("EZE", "Buenos Aires", -34.8222, -58.5358, "America/Argentina/Buenos_Aires"),
}

PREFIX_TZ = {
    "PH": "Pacific/Honolulu",
    "PA": "America/Anchorage",
    "TJ": "America/Puerto_Rico",
    "CY": "America/Toronto",
    "MM": "America/Mexico_City",
    "BI": "Atlantic/Reykjavik",
    "EB": "Europe/Brussels",
    "ED": "Europe/Berlin",
    "EE": "Europe/Tallinn",
    "EF": "Europe/Helsinki",
    "EG": "Europe/London",
    "EH": "Europe/Amsterdam",
    "EI": "Europe/Dublin",
    "EK": "Europe/Copenhagen",
    "EL": "Europe/Luxembourg",
    "EN": "Europe/Oslo",
    "EP": "Europe/Warsaw",
    "ES": "Europe/Stockholm",
    "ET": "Europe/Berlin",
    "EV": "Europe/Riga",
    "EY": "Europe/Vilnius",
    "GC": "Atlantic/Canary",
    "GM": "Africa/Casablanca",
    "LE": "Europe/Madrid",
    "LF": "Europe/Paris",
    "LG": "Europe/Athens",
    "LH": "Europe/Budapest",
    "LI": "Europe/Rome",
    "LJ": "Europe/Ljubljana",
    "LK": "Europe/Prague",
    "LM": "Europe/Malta",
    "LO": "Europe/Vienna",
    "LR": "Europe/Bucharest",
    "LS": "Europe/Zurich",
    "LT": "Europe/Istanbul",
    "LU": "Europe/Chisinau",
    "LW": "Europe/Skopje",
    "LY": "Europe/Belgrade",
    "LZ": "Europe/Bratislava",
    "UK": "Europe/Kyiv",
    "LB": "Europe/Sofia",
    "LC": "Asia/Nicosia",
    "LD": "Europe/Zagreb",
    "LL": "Asia/Jerusalem",
    "LN": "Europe/Paris",
    "LQ": "Europe/Sarajevo",
    "UG": "Asia/Tbilisi",
    "UT": "Asia/Tashkent",
    "OM": "Asia/Dubai",
    "OE": "Asia/Riyadh",
    "OT": "Asia/Qatar",
    "OB": "Asia/Bahrain",
    "OK": "Asia/Kuwait",
    "OJ": "Asia/Amman",
    "OL": "Asia/Beirut",
    "OS": "Asia/Damascus",
    "OR": "Asia/Baghdad",
    "OA": "Asia/Kabul",
    "OI": "Asia/Tehran",
    "OP": "Asia/Karachi",
    "VI": "Asia/Kolkata",
    "VT": "Asia/Bangkok",
    "VV": "Asia/Ho_Chi_Minh",
    "WM": "Asia/Kuala_Lumpur",
    "WS": "Asia/Singapore",
    "WI": "Asia/Jakarta",
    "RJ": "Asia/Tokyo",
    "RK": "Asia/Seoul",
    "RC": "Asia/Taipei",
    "VH": "Asia/Hong_Kong",
    "VM": "Asia/Macau",
    "ZB": "Asia/Shanghai",
    "ZG": "Asia/Shanghai",
    "ZH": "Asia/Shanghai",
    "ZL": "Asia/Shanghai",
    "ZS": "Asia/Shanghai",
    "ZU": "Asia/Shanghai",
    "ZW": "Asia/Urumqi",
    "ZY": "Asia/Shanghai",
    "YM": "Australia/Sydney",
    "YB": "Australia/Brisbane",
    "YS": "Australia/Sydney",
    "YP": "Australia/Perth",
    "NZ": "Pacific/Auckland",
    "HE": "Africa/Cairo",
    "HK": "Africa/Nairobi",
    "FA": "Africa/Johannesburg",
    "DA": "Africa/Algiers",
    "DT": "Africa/Tunis",
    "GM": "Africa/Casablanca",
}


def _tz_portugal(lat: float | None, lon: float | None) -> str:
    if lon is not None and lon <= -23.0:
        return "Atlantic/Azores"
    if lon is not None and lon <= -15.4 and (lat is None or lat <= 34.8):
        return "Atlantic/Madeira"
    return "Europe/Lisbon"


def _tz_guess(icao: str, lat: float | None, lon: float | None) -> str:
    code = (icao or "").upper()
    if code.startswith("PH"):
        return "Pacific/Honolulu"
    if code.startswith("PA"):
        return "America/Anchorage"
    if code.startswith("TJ"):
        return "America/Puerto_Rico"
    if code.startswith("LP"):
        return _tz_portugal(lat, lon)
    pref = PREFIX_TZ.get(code[:2], "")
    if pref and not code.startswith("K"):
        return pref
    if lon is None:
        return pref or ""
    if not (code.startswith("K") or code.startswith("C")):
        return pref
    if lon <= -122.4 and lat is not None and lat >= 49:
        return "America/Vancouver"
    if lon <= -117.0:
        return "America/Los_Angeles"
    if lon <= -104.0:
        return "America/Denver"
    if lon <= -87.0:
        return "America/Chicago"
    if lon <= -67.0:
        return "America/New_York"
    return pref


def _as_result(icao: str) -> tuple[str, str, str, float | None, float | None, str]:
    hit = AIRPORTS.get(icao)
    iata, city, airport = _names_for(icao, hit[1] if hit else "")
    if hit:
        _iata, _name, lat, lon, tz = hit
        return icao, iata, city, lat, lon, tz
    return icao, iata, city, None, None, _tz_guess(icao, None, None)


_IATA_INDEX: dict[str, str] | None = None
_IATA_LOCK = threading.Lock()
_IATA_BY_ICAO: dict[str, str] = {}
_PLACES: dict[str, tuple[str, str, str, str]] = {}
_IATA_READY = False
_IATA_CACHE = data_file("iata.json")
_PLACES_CACHE = data_file("places.json")
_IATA_SRC = "https://davidmegginson.github.io/ourairports-data/airports.csv"

_AIRPORT_TAIL = re.compile(
    r"\s+(international|intl|regional|municipal|muni|airport|airfield|airstrip|heliport).*$",
    re.I,
)
_SKIP_PLACE = re.compile(
    r"\b(raf|rnas|afb|aaf|mcas|nas|ab|air base|heliport|airfield|airport)\b",
    re.I,
)

# ICAO nationality prefix → ISO 3166-1 alpha-2 (2-letter first, then 1-letter).
_ICAO_ISO = {
    "AG": "SB", "AN": "NR", "AY": "PG",
    "BG": "GL", "BI": "IS", "BK": "XK",
    "C": "CA",
    "DA": "DZ", "DB": "BJ", "DF": "BF", "DG": "GH", "DI": "CI", "DN": "NG",
    "DR": "NE", "DT": "TN", "DX": "TG",
    "EB": "BE", "ED": "DE", "EE": "EE", "EF": "FI", "EG": "GB", "EH": "NL",
    "EI": "IE", "EK": "DK", "EL": "LU", "EN": "NO", "EP": "PL", "ES": "SE",
    "ET": "DE", "EV": "LV", "EY": "LT",
    "FA": "ZA", "FB": "BW", "FC": "CG", "FD": "SZ", "FE": "CF", "FG": "GQ",
    "FH": "SH", "FI": "MU", "FJ": "IO", "FK": "CM", "FL": "ZM", "FM": "MG",
    "FN": "AO", "FO": "GA", "FP": "ST", "FQ": "MZ", "FS": "SC", "FT": "TD",
    "FV": "ZW", "FW": "MW", "FX": "LS", "FY": "NA", "FZ": "CD",
    "GA": "ML", "GB": "GM", "GC": "ES", "GE": "ES", "GF": "SL", "GG": "GW",
    "GL": "LR", "GM": "MA", "GO": "SN", "GQ": "MR", "GS": "EH", "GU": "GN",
    "GV": "CV",
    "HA": "ET", "HB": "BI", "HC": "SO", "HD": "DJ", "HE": "EG", "HH": "ER",
    "HK": "KE", "HL": "LY", "HR": "RW", "HS": "SD", "HT": "TZ", "HU": "UG",
    "K": "US",
    "LA": "AL", "LB": "BG", "LC": "CY", "LD": "HR", "LE": "ES", "LF": "FR",
    "LG": "GR", "LH": "HU", "LI": "IT", "LJ": "SI", "LK": "CZ", "LL": "IL",
    "LM": "MT", "LN": "MC", "LO": "AT", "LP": "PT", "LQ": "BA", "LR": "RO",
    "LS": "CH", "LT": "TR", "LU": "MD", "LV": "PS", "LW": "MK", "LX": "GI",
    "LY": "RS", "LZ": "SK",
    "MB": "TC", "MD": "DO", "MG": "GT", "MH": "HN", "MK": "JM", "MM": "MX",
    "MN": "NI", "MP": "PA", "MR": "CR", "MS": "SV", "MT": "HT", "MU": "CU",
    "MW": "KY", "MY": "BS", "MZ": "BZ",
    "NC": "CK", "NF": "FJ", "NG": "KI", "NI": "NU", "NL": "WF", "NS": "AS",
    "NT": "PF", "NV": "VU", "NW": "NC", "NZ": "NZ",
    "OA": "AF", "OB": "BH", "OE": "SA", "OI": "IR", "OJ": "JO", "OK": "KW",
    "OL": "LB", "OM": "AE", "OO": "OM", "OP": "PK", "OR": "IQ", "OS": "SY",
    "OT": "QA", "OY": "YE",
    "PA": "US", "PG": "GU", "PH": "US", "PJ": "UM", "PK": "MH", "PL": "KI",
    "PM": "US", "PO": "US", "PP": "US", "PT": "FM", "PW": "UM",
    "RC": "TW", "RJ": "JP", "RK": "KR", "RO": "JP", "RP": "PH",
    "SA": "AR", "SB": "BR", "SC": "CL", "SD": "BR", "SE": "EC", "SF": "FK",
    "SG": "PY", "SI": "BR", "SJ": "BR", "SK": "CO", "SL": "BO", "SM": "SR",
    "SN": "BR", "SO": "GF", "SP": "PE", "SS": "BR", "SU": "UY", "SV": "VE",
    "SW": "BR", "SY": "GY",
    "TA": "AG", "TB": "BB", "TD": "DM", "TF": "GP", "TG": "GD", "TI": "VI",
    "TJ": "PR", "TK": "KN", "TL": "LC", "TN": "AW", "TQ": "AI", "TT": "TT",
    "TU": "VG", "TV": "VC", "TX": "BM",
    "UA": "KZ", "UB": "AZ", "UC": "KG", "UD": "AM", "UE": "RU", "UG": "GE",
    "UH": "RU", "UI": "RU", "UK": "UA", "UL": "RU", "UM": "BY", "UN": "RU",
    "UO": "RU", "UR": "RU", "US": "RU", "UT": "UZ", "UU": "RU", "UW": "RU",
    "VA": "IN", "VC": "LK", "VD": "KH", "VE": "IN", "VG": "BD", "VH": "HK",
    "VI": "IN", "VL": "LA", "VM": "MO", "VN": "NP", "VO": "IN", "VQ": "BT",
    "VR": "MV", "VT": "TH", "VV": "VN", "VY": "MM",
    "WA": "ID", "WB": "MY", "WI": "ID", "WM": "MY", "WP": "TL", "WQ": "ID",
    "WR": "ID", "WS": "SG",
    "Y": "AU",
    "ZK": "KP", "ZM": "MN", "Z": "CN",
}

_ISO_NAMES = {
    "AE": "United Arab Emirates", "AF": "Afghanistan", "AG": "Antigua and Barbuda",
    "AI": "Anguilla", "AL": "Albania", "AM": "Armenia", "AO": "Angola",
    "AR": "Argentina", "AS": "American Samoa", "AT": "Austria", "AU": "Australia",
    "AW": "Aruba", "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina",
    "BB": "Barbados", "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso",
    "BG": "Bulgaria", "BH": "Bahrain", "BI": "Burundi", "BJ": "Benin",
    "BM": "Bermuda", "BN": "Brunei", "BO": "Bolivia", "BR": "Brazil",
    "BS": "Bahamas", "BT": "Bhutan", "BW": "Botswana", "BY": "Belarus",
    "BZ": "Belize", "CA": "Canada", "CD": "DR Congo", "CF": "Central African Republic",
    "CG": "Congo", "CH": "Switzerland", "CI": "Côte d'Ivoire", "CK": "Cook Islands",
    "CL": "Chile", "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CU": "Cuba", "CV": "Cape Verde", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DJ": "Djibouti", "DK": "Denmark",
    "DM": "Dominica", "DO": "Dominican Republic", "DZ": "Algeria", "EC": "Ecuador",
    "EE": "Estonia", "EG": "Egypt", "EH": "Western Sahara", "ER": "Eritrea",
    "ES": "Spain", "ET": "Ethiopia", "FI": "Finland", "FJ": "Fiji",
    "FK": "Falkland Islands", "FM": "Micronesia", "FR": "France", "GA": "Gabon",
    "GB": "United Kingdom", "GD": "Grenada", "GE": "Georgia", "GF": "French Guiana",
    "GH": "Ghana", "GI": "Gibraltar", "GL": "Greenland", "GM": "Gambia",
    "GN": "Guinea", "GP": "Guadeloupe", "GQ": "Equatorial Guinea", "GR": "Greece",
    "GT": "Guatemala", "GU": "Guam", "GW": "Guinea-Bissau", "GY": "Guyana",
    "HK": "Hong Kong", "HN": "Honduras", "HR": "Croatia", "HT": "Haiti",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel",
    "IN": "India", "IO": "British Indian Ocean Territory", "IQ": "Iraq",
    "IR": "Iran", "IS": "Iceland", "IT": "Italy", "JM": "Jamaica", "JO": "Jordan",
    "JP": "Japan", "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia",
    "KI": "Kiribati", "KM": "Comoros", "KN": "Saint Kitts and Nevis",
    "KP": "North Korea", "KR": "South Korea", "KW": "Kuwait", "KY": "Cayman Islands",
    "KZ": "Kazakhstan", "LA": "Laos", "LB": "Lebanon", "LC": "Saint Lucia",
    "LK": "Sri Lanka", "LR": "Liberia", "LS": "Lesotho", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya", "MA": "Morocco",
    "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro", "MG": "Madagascar",
    "MH": "Marshall Islands", "MK": "North Macedonia", "ML": "Mali",
    "MM": "Myanmar", "MN": "Mongolia", "MO": "Macau", "MQ": "Martinique",
    "MR": "Mauritania", "MT": "Malta", "MU": "Mauritius", "MV": "Maldives",
    "MW": "Malawi", "MX": "Mexico", "MY": "Malaysia", "MZ": "Mozambique",
    "NA": "Namibia", "NC": "New Caledonia", "NE": "Niger", "NF": "Norfolk Island",
    "NG": "Nigeria", "NI": "Nicaragua", "NL": "Netherlands", "NO": "Norway",
    "NP": "Nepal", "NR": "Nauru", "NU": "Niue", "NZ": "New Zealand", "OM": "Oman",
    "PA": "Panama", "PE": "Peru", "PF": "French Polynesia", "PG": "Papua New Guinea",
    "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PR": "Puerto Rico",
    "PS": "Palestine", "PT": "Portugal", "PW": "Palau", "PY": "Paraguay",
    "QA": "Qatar", "RE": "Réunion", "RO": "Romania", "RS": "Serbia", "RU": "Russia",
    "RW": "Rwanda", "SA": "Saudi Arabia", "SB": "Solomon Islands", "SC": "Seychelles",
    "SD": "Sudan", "SE": "Sweden", "SG": "Singapore", "SH": "Saint Helena",
    "SI": "Slovenia", "SK": "Slovakia", "SL": "Sierra Leone", "SM": "San Marino",
    "SN": "Senegal", "SO": "Somalia", "SR": "Suriname", "SS": "South Sudan",
    "ST": "São Tomé and Príncipe", "SV": "El Salvador", "SY": "Syria",
    "SZ": "Eswatini", "TC": "Turks and Caicos Islands", "TD": "Chad", "TG": "Togo",
    "TH": "Thailand", "TJ": "Tajikistan", "TL": "Timor-Leste", "TM": "Turkmenistan",
    "TN": "Tunisia", "TO": "Tonga", "TR": "Turkey", "TT": "Trinidad and Tobago",
    "TV": "Tuvalu", "TW": "Taiwan", "TZ": "Tanzania", "UA": "Ukraine",
    "UG": "Uganda", "UM": "United States", "US": "United States", "UY": "Uruguay",
    "UZ": "Uzbekistan", "VC": "Saint Vincent and the Grenadines", "VE": "Venezuela",
    "VG": "British Virgin Islands", "VI": "U.S. Virgin Islands", "VN": "Vietnam",
    "VU": "Vanuatu", "WF": "Wallis and Futuna", "WS": "Samoa", "XK": "Kosovo",
    "YE": "Yemen", "YT": "Mayotte", "ZA": "South Africa", "ZM": "Zambia",
    "ZW": "Zimbabwe",
}


def _iso_from_icao(icao: str) -> str:
    raw = (icao or "").strip().upper()
    if len(raw) < 2:
        return ""
    return _ICAO_ISO.get(raw[:2], "") or _ICAO_ISO.get(raw[0], "")


def _country_for_icao(icao: str, iso: str = "") -> str:
    code = (iso or "").strip().upper() or _iso_from_icao(icao)
    return _ISO_NAMES.get(code, "")


def _city_from_airport(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    if "/" in text:
        text = text.split("/", 1)[0].strip()
    text = _AIRPORT_TAIL.sub("", text).strip(" -–,")
    if text.isupper() and len(text) > 3:
        text = text.title()
    return text


def _parse_oa_csv(blob: str) -> tuple[dict[str, str], dict[str, tuple[str, str, str, str]]]:
    iata_map: dict[str, str] = {}
    places: dict[str, tuple[str, str, str, str]] = {}
    for row in csv.DictReader(io.StringIO(blob)):
        icao = (row.get("gps_code") or row.get("ident") or "").strip().upper()
        iata = (row.get("iata_code") or "").strip().upper()
        city = (row.get("municipality") or "").strip()
        if "(" in city:
            city = city.split("(", 1)[0].strip()
        airport = (row.get("name") or "").strip()
        iso = (row.get("iso_country") or "").strip().upper()
        if len(icao) != 4:
            continue
        if len(iata) == 3 and iata.isalpha():
            iata_map[icao] = iata
        if city or airport:
            places[icao] = (iata if len(iata) == 3 else "", city, airport, iso)
    return iata_map, places


def _apply_iata_map(mapping: dict[str, str]) -> None:
    global _IATA_BY_ICAO, _IATA_READY, _IATA_INDEX
    _IATA_BY_ICAO = mapping
    _IATA_READY = True
    _IATA_INDEX = None


def _apply_places(places: dict[str, tuple[str, str, str, str]]) -> None:
    global _PLACES
    _PLACES = places


def _download_iata() -> None:
    try:
        with urllib.request.urlopen(_IATA_SRC, timeout=40) as resp:
            blob = resp.read().decode("utf-8", "replace")
        parsed, places = _parse_oa_csv(blob)
        data_file("iata.json", write=True).write_text(
            json.dumps(parsed, separators=(",", ":")), encoding="utf-8"
        )
        data_file("places.json", write=True).write_text(
            json.dumps({k: list(v) for k, v in places.items()}, separators=(",", ":")),
            encoding="utf-8",
        )
        with _IATA_LOCK:
            _apply_iata_map(parsed)
            _apply_places(places)
        print(f"  IATA: {len(parsed)} Airports, {len(places)} Orte gecacht.", flush=True)
    except Exception as exc:
        print(f"  IATA: Download fehlgeschlagen ({exc})", flush=True)


def _ensure_iata() -> dict[str, str]:
    global _IATA_READY
    with _IATA_LOCK:
        if _IATA_READY:
            return _IATA_BY_ICAO
        if _IATA_CACHE.is_file():
            try:
                raw = json.loads(_IATA_CACHE.read_text(encoding="utf-8"))
                _apply_iata_map({str(k).upper(): str(v).upper() for k, v in raw.items()})
            except Exception:
                _IATA_READY = True
        else:
            _IATA_READY = True
        if _PLACES_CACHE.is_file() and not _PLACES:
            try:
                raw = json.loads(_PLACES_CACHE.read_text(encoding="utf-8"))
                places = {}
                for key, val in raw.items():
                    if isinstance(val, list) and len(val) >= 3:
                        iso = str(val[3]).upper() if len(val) >= 4 else ""
                        places[str(key).upper()] = (str(val[0]), str(val[1]), str(val[2]), iso)
                _apply_places(places)
            except Exception:
                pass
    threading.Thread(target=_download_iata, daemon=True, name="iata").start()
    return _IATA_BY_ICAO


def _places_row(icao: str) -> tuple[str, str, str, str] | None:
    _ensure_iata()
    return _PLACES.get(icao)


def guess_iata(icao: str) -> str:
    code = (icao or "").strip().upper()
    if len(code) != 4:
        return code if len(code) == 3 else ""
    if code in AIRPORTS:
        return AIRPORTS[code][0]
    mapped = _ensure_iata().get(code)
    if mapped:
        return mapped
    if code[0] in "KC" and code[1:].isalpha():
        return code[1:]
    if code.startswith(("PH", "PA", "TJ")):
        return code[1:]
    return ""


def _iata_index() -> dict[str, str]:
    global _IATA_INDEX
    extra = _ensure_iata()
    if _IATA_INDEX is None:
        idx = {row[0]: icao for icao, row in AIRPORTS.items()}
        for icao, iata in extra.items():
            if iata and iata not in idx:
                idx[iata] = icao
        _IATA_INDEX = idx
    return _IATA_INDEX


_MSFS_NAME_HOOK = None


def set_msfs_name_provider(fn) -> None:
    """fn(icao) -> MSFS facility name or '' (queues lookup when unknown)."""
    global _MSFS_NAME_HOOK
    _MSFS_NAME_HOOK = fn


def _msfs_name(icao: str) -> str:
    if not _MSFS_NAME_HOOK:
        return ""
    try:
        return (_MSFS_NAME_HOOK(icao) or "").strip()
    except Exception:
        return ""


def _clean_city(city: str, airport: str) -> str:
    """'Luton, Luton' -> 'Luton'; 'Luton' + 'London Luton Airport' -> 'London Luton'."""
    # OurAirports appends county/region after a comma ("Manchester, Greater Manchester").
    out = (city or "").split(",", 1)[0].strip()
    base = _city_from_airport(airport)
    if out and base and base.lower() != out.lower() and base.lower().endswith(" " + out.lower()):
        if len(base.split()) <= len(out.split()) + 1:
            out = base
    return out


def _names_for(icao: str, nav_name: str = "") -> tuple[str, str, str]:
    oa = _places_row(icao)
    known = AIRPORTS.get(icao)
    iata = (known[0] if known else "") or (oa[0] if oa else "") or guess_iata(icao)
    airport = (oa[2] if oa and oa[2] else "") or nav_name
    city = (oa[1] if oa and oa[1] else "") or _city_from_airport(nav_name)
    if "(" in city:
        city = city.split("(", 1)[0].strip()
    if not city and known:
        city = _city_from_airport(known[1]) or known[1]
    if not city:
        city = iata or icao
    if not airport and known:
        airport = known[1]
    msfs = _msfs_name(icao)
    if msfs:
        # MSFS names are often suburbs (Schwechat, Vantaa): only a fallback.
        if not (oa and oa[2]):
            airport = msfs
        if not (oa and oa[1]):
            city = _city_from_airport(msfs) or msfs
    city = _clean_city(city, airport)
    return iata, city, airport


def names(icao: str) -> tuple[str, str, str]:
    """IATA, city, airport name."""
    code = (icao or "").strip().upper()
    if len(code) != 4:
        return "", "", ""
    nav = ""
    try:
        from navdata import airport_info
        hit = airport_info(code)
        if hit:
            nav = hit[1] or ""
    except Exception:
        pass
    return _names_for(code, nav)


def _haversine_km(lat: float, lon: float, alat: float, alon: float) -> float:
    dlat = math.radians(alat - lat)
    dlon = math.radians(alon - lon)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat)) * math.cos(math.radians(alat)) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def nearest(lat: float, lon: float, max_nm: float = 12.0) -> tuple[str, str, str, float | None, float | None, str] | None:
    best = None
    best_nm = max_nm

    def consider(icao: str, iata: str, name: str, alat: float | None, alon: float | None, tz: str) -> None:
        nonlocal best, best_nm
        if alat is None or alon is None:
            return
        nm = _haversine_km(lat, lon, alat, alon) / 1.852
        if nm < best_nm:
            best_nm = nm
            best = (icao, iata, name, alat, alon, tz)

    for icao, (iata, name, alat, alon, tz) in AIRPORTS.items():
        consider(icao, iata, name, alat, alon, tz)
    try:
        from navdata import nearby_airports

        for icao, name, alat, alon in nearby_airports(lat, lon, max_deg=0.4):
            hit = lookup(icao)
            if hit:
                consider(*hit)
            else:
                consider(icao, "", name, alat, alon, "")
    except Exception:
        pass
    return best


def nearest_cities(lat: float, lon: float, count: int = 6, max_km: float = 400.0) -> list[dict]:
    """Distinct city names near lat/lon, closest first."""
    _ensure_iata()
    points: list[tuple[str, str, float, float]] = []
    for icao, (_iata, name, alat, alon, _tz) in AIRPORTS.items():
        points.append((icao, name, alat, alon))
    try:
        from navdata import nearby_airports

        points.extend(nearby_airports(lat, lon))
    except Exception:
        pass
    best: dict[str, tuple[float, str, str]] = {}
    for icao, name, alat, alon in points:
        oa = _places_row(icao)
        if oa and oa[1]:
            label = _clean_city(oa[1], oa[2] or name)
        else:
            label = _city_from_airport(name) or (name or "").strip()
        if not label or _SKIP_PLACE.search(label):
            continue
        km = _haversine_km(lat, lon, alat, alon)
        if km > max_km:
            continue
        iso = oa[3] if oa and len(oa) > 3 else ""
        country = _country_for_icao(icao, iso)
        key = label.casefold()
        prev = best.get(key)
        if prev is None or km < prev[0]:
            best[key] = (km, label, country)
    ranked = sorted(best.values(), key=lambda row: row[0])[: max(0, count)]
    return [{"name": name, "km": round(km, 1), "country": country} for km, name, country in ranked]


def lookup(code: str) -> tuple[str, str, str, float | None, float | None, str] | None:
    raw = (code or "").strip().upper()
    if not raw or not raw.isalnum():
        return None
    if len(raw) == 3:
        mapped = _iata_index().get(raw)
        if mapped:
            return lookup(mapped)
        for prefix in ("K", "C", "P"):
            guess = prefix + raw
            nav = _from_nav(guess)
            if nav:
                return nav
            if guess in AIRPORTS:
                return _as_result(guess)
        return raw, raw, raw, None, None, ""
    if len(raw) != 4:
        return None
    nav = _from_nav(raw)
    if nav:
        return nav
    return _as_result(raw)


def _from_nav(icao: str) -> tuple[str, str, str, float | None, float | None, str] | None:
    try:
        from navdata import airport_info
    except Exception:
        return None
    hit = airport_info(icao)
    if not hit:
        return None
    code, name, lat, lon = hit
    iata, city, _airport = _names_for(code, name)
    tz = _tz_guess(code, lat, lon)
    if not tz and code in AIRPORTS:
        tz = AIRPORTS[code][4]
    return code, iata, city, lat, lon, tz


def warmup() -> None:
    threading.Thread(target=_ensure_iata, daemon=True, name="iata").start()
