"""Static, offline reference tables used to make the flight list human-friendly.

These are lookups of published ICAO designators — nothing is guessed. Unknown codes simply
fall back to the raw value. Extend the tables freely; keep names short (the flight list
column is ~130 px wide).
"""

from __future__ import annotations

from zurich_air.data.aircraft import Aircraft

# ICAO airline designator (first three letters of a callsign) -> short operator name.
OPERATORS: dict[str, str] = {
    "AAL": "American",
    "ACA": "Air Canada",
    "AEA": "Air Europa",
    "AEE": "Aegean",
    "AFR": "Air France",
    "AIC": "Air India",
    "ASL": "Air Serbia",
    "AUA": "Austrian",
    "BAW": "British Airways",
    "BEL": "Brussels",
    "BTI": "airBaltic",
    "CCA": "Air China",
    "CFG": "Condor",
    "CLH": "Lufthansa City",
    "CLX": "Cargolux",
    "CPA": "Cathay Pacific",
    "CTN": "Croatia",
    "DAL": "Delta",
    "DLA": "Air Dolomiti",
    "DLH": "Lufthansa",
    "EDW": "Edelweiss",
    "EIN": "Aer Lingus",
    "EJU": "easyJet",
    "ELY": "El Al",
    "ETD": "Etihad",
    "ETH": "Ethiopian",
    "EWG": "Eurowings",
    "EZS": "easyJet",
    "EZY": "easyJet",
    "FDX": "FedEx",
    "FIN": "Finnair",
    "GEC": "Lufthansa Cargo",
    "GSW": "Chair",
    "IBE": "Iberia",
    "ICE": "Icelandair",
    "ITY": "ITA Airways",
    "KLM": "KLM",
    "LGL": "Luxair",
    "LOT": "LOT Polish",
    "MSR": "EgyptAir",
    "NOZ": "Norwegian",
    "NSZ": "Norwegian",
    "OAW": "Helvetic",
    "OCN": "Discover",
    "PGT": "Pegasus",
    "QTR": "Qatar",
    "RAM": "Royal Air Maroc",
    "RYR": "Ryanair",
    "SAS": "SAS",
    "SAZ": "Rega",
    "SIA": "Singapore",
    "SWR": "Swiss",
    "SXS": "SunExpress",
    "TAP": "TAP Portugal",
    "THY": "Turkish",
    "UAE": "Emirates",
    "UAL": "United",
    "UPS": "UPS",
    "VLG": "Vueling",
    "WZZ": "Wizz Air",
}

# ICAO aircraft type designator -> short model name.
AIRCRAFT_TYPES: dict[str, str] = {
    # Airbus
    "A318": "A318",
    "A319": "A319",
    "A320": "A320",
    "A321": "A321",
    "A19N": "A319neo",
    "A20N": "A320neo",
    "A21N": "A321neo",
    "A332": "A330-200",
    "A333": "A330-300",
    "A338": "A330-800",
    "A339": "A330-900",
    "A343": "A340-300",
    "A346": "A340-600",
    "A359": "A350-900",
    "A35K": "A350-1000",
    "A388": "A380",
    "A400": "A400M",
    "BCS1": "A220-100",
    "BCS3": "A220-300",
    # Boeing
    "B737": "737-700",
    "B738": "737-800",
    "B739": "737-900",
    "B37M": "737 MAX 7",
    "B38M": "737 MAX 8",
    "B39M": "737 MAX 9",
    "B744": "747-400",
    "B748": "747-8",
    "B752": "757-200",
    "B763": "767-300",
    "B772": "777-200",
    "B77L": "777-200LR",
    "B77W": "777-300ER",
    "B788": "787-8",
    "B789": "787-9",
    "B78X": "787-10",
    "MD11": "MD-11",
    # Regional
    "AT72": "ATR 72",
    "AT75": "ATR 72-500",
    "AT76": "ATR 72-600",
    "CRJ9": "CRJ900",
    "CRJX": "CRJ1000",
    "DH8D": "Dash 8-400",
    "E170": "E170",
    "E75L": "E175",
    "E190": "E190",
    "E195": "E195",
    "E290": "E190-E2",
    "E295": "E195-E2",
    # Business & general aviation
    "C25A": "Citation CJ2",
    "C25B": "Citation CJ3",
    "C25C": "Citation CJ4",
    "C56X": "Citation XLS",
    "C68A": "Citation Latitude",
    "C700": "Citation Longitude",
    "CL35": "Challenger 350",
    "F2TH": "Falcon 2000",
    "FA7X": "Falcon 7X",
    "FA8X": "Falcon 8X",
    "GL7T": "Global 7500",
    "GLEX": "Global Express",
    "GLF6": "G650",
    "PC12": "PC-12",
    "PC24": "PC-24",
    "C172": "Cessna 172",
    "DA40": "DA40",
    "DA42": "DA42",
    "SR22": "SR22",
    "P28A": "PA-28",
    # Helicopters
    "A109": "AW109",
    "A139": "AW139",
    "A169": "AW169",
    "AS50": "H125",
    "EC30": "H130",
    "EC35": "H135",
    "EC45": "H145",
    "R22": "R22",
    "R44": "R44",
}

WIDEBODY_TYPES: frozenset[str] = frozenset(
    {
        "A332",
        "A333",
        "A338",
        "A339",
        "A343",
        "A346",
        "A359",
        "A35K",
        "A388",
        "B744",
        "B748",
        "B763",
        "B772",
        "B77L",
        "B77W",
        "B788",
        "B789",
        "B78X",
        "MD11",
    }
)

HELICOPTER_TYPES: frozenset[str] = frozenset(
    {"A109", "A139", "A169", "AS50", "EC30", "EC35", "EC45", "R22", "R44"}
)


def operator_name(callsign: str | None) -> str | None:
    """Operator for an airline-style callsign (three letters + flight number), if known."""
    if not callsign or len(callsign) < 4:
        return None
    prefix = callsign[:3]
    if not prefix.isalpha() or not callsign[3].isdigit():
        return None
    return OPERATORS.get(prefix.upper())


def type_name(type_code: str | None) -> str | None:
    """Model name for an ICAO type designator; the raw code if not in the table."""
    if not type_code:
        return None
    return AIRCRAFT_TYPES.get(type_code.upper(), type_code.upper())


def is_helicopter(aircraft: Aircraft) -> bool:
    """True for rotorcraft, by ADS-B emitter category or known type designator."""
    if aircraft.category == "A7":
        return True
    return bool(aircraft.type_code) and aircraft.type_code.upper() in HELICOPTER_TYPES
