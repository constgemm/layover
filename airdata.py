#!/usr/bin/env python3
"""
airdata — offline IATA↔ICAO reference tables for the Layover auto-populate parser.

Zero dependencies (pure Python stdlib data). Deliberately hand-curated rather than
pulled from a package: the parser only needs the airports and airlines that actually
turn up in the mailboxes, plus the common hubs, and a tiny static table keeps the
whole pipeline installable on the vmgpu box with nothing but Python 3.9.

Exports:
    AIRPORT_IATA_ICAO   dict  IATA -> ICAO      (e.g. "ZRH" -> "LSZH")
    AIRLINE_IATA_ICAO   dict  IATA -> ICAO      (e.g. "LX"  -> "SWR")
    AIRPORT_NAME_IATA   dict  display name -> IATA  (British Airways e-ticket names)
    airport_icao(code)  -> ICAO or None   (accepts IATA or ICAO in)
    airline_icao(code)  -> ICAO or None   (accepts IATA or ICAO in)
    name_to_iata(name)  -> IATA or None   (BA display name lookup, punctuation-tolerant)
"""

# --- Airports: IATA -> ICAO ------------------------------------------------
# Every airport seen in the current mailbox corpus, plus the common European
# and long-haul hubs so the parser degrades gracefully on the next new route.
AIRPORT_IATA_ICAO = {
    # --- German-speaking / DACH ---
    "MUC": "EDDM", "FRA": "EDDF", "TXL": "EDDT", "BER": "EDDB", "SXF": "EDDB",
    "DUS": "EDDL", "HAM": "EDDH", "STR": "EDDS", "CGN": "EDDK", "NUE": "EDDN",
    "LEJ": "EDDP", "BRE": "EDDW", "HAJ": "EDDV", "DTM": "EDLW", "FMO": "EDDG",
    "ZRH": "LSZH", "GVA": "LSGG", "BSL": "LFSB", "BRN": "LSZB",
    "VIE": "LOWW", "SZG": "LOWS", "INN": "LOWI", "GRZ": "LOWG",
    # --- UK / Ireland ---
    "LHR": "EGLL", "LGW": "EGKK", "LCY": "EGLC", "STN": "EGSS", "LTN": "EGGW",
    "MAN": "EGCC", "BHX": "EGBB", "EDI": "EGPH", "GLA": "EGPF", "BRS": "EGGD",
    "NCL": "EGNT", "DUB": "EIDW", "ORK": "EICK",
    # --- France / Benelux ---
    "CDG": "LFPG", "ORY": "LFPO", "NCE": "LFMN", "LYS": "LFLL", "MRS": "LFML",
    "TLS": "LFBO", "BOD": "LFBD", "NTE": "LFRS",
    "AMS": "EHAM", "EIN": "EHEH", "RTM": "EHRD", "BRU": "EBBR", "CRL": "EBCI",
    "LUX": "ELLX",
    # --- Iberia ---
    "MAD": "LEMD", "BCN": "LEBL", "PMI": "LEPA", "AGP": "LEMG", "VLC": "LEVC",
    "SVQ": "LEZL", "BIO": "LEBB", "ALC": "LEAL", "IBZ": "LEIB", "LPA": "GCLP",
    "TFS": "GCTS", "TFN": "GCXO", "ACE": "GCRR",
    "LIS": "LPPT", "OPO": "LPPR", "FAO": "LPFR", "FNC": "LPMA",
    # --- Italy ---
    "FCO": "LIRF", "CIA": "LIRA", "MXP": "LIMC", "LIN": "LIML", "BGY": "LIME",
    "VCE": "LIPZ", "NAP": "LIRN", "BLQ": "LIPE", "PSA": "LIRP", "CTA": "LICC",
    "PMO": "LICJ", "BRI": "LIBD", "TRN": "LIMF", "VRN": "LIPX", "CAG": "LIEE",
    # --- Nordics ---
    "ARN": "ESSA", "BMA": "ESSB", "GOT": "ESGG", "MMX": "ESMS",
    "CPH": "EKCH", "BLL": "EKBI", "AAL": "EKYT",
    "OSL": "ENGM", "TRF": "ENTO", "BGO": "ENBR", "SVG": "ENZV", "TRD": "ENVA",
    "HEL": "EFHK", "KEF": "BIKF", "RVN": "EFRO",
    # --- Central / Eastern Europe & Balkans ---
    "PRG": "LKPR", "WAW": "EPWA", "WMI": "EPMO", "KRK": "EPKK", "GDN": "EPGD",
    "BUD": "LHBP", "OTP": "LROP", "SOF": "LBSF", "BEG": "LYBE",
    "ZAG": "LDZA", "SPU": "LDSP", "DBV": "LDDU", "PUY": "LDPL",
    "LJU": "LJLJ", "SKP": "LWSK", "TIA": "LATI", "SJJ": "LQSA",
    # --- Greece / Cyprus / Malta / Turkey ---
    "ATH": "LGAV", "SKG": "LGTS", "HER": "LGIR", "RHO": "LGRP", "CFU": "LGKR",
    "JMK": "LGMK", "JTR": "LGSR", "CHQ": "LGSA",
    "LCA": "LCLK", "PFO": "LCPH", "MLA": "LMML",
    "IST": "LTFM", "SAW": "LTFJ", "AYT": "LTAI", "ESB": "LTAC", "ADB": "LTBJ",
    # --- Middle East / Africa ---
    "DOH": "OTHH", "DXB": "OMDB", "AUH": "OMAA", "TLV": "LLBG", "AMM": "OJAI",
    "CAI": "HECA", "JNB": "FAOR", "CPT": "FACT", "DUR": "FALE", "NBO": "HKJK",
    "ADD": "HAAB", "MRU": "FIMP", "RAK": "GMMX", "CMN": "GMMN",
    # --- Americas ---
    "JFK": "KJFK", "EWR": "KEWR", "LGA": "KLGA", "IAD": "KIAD", "BOS": "KBOS",
    "ORD": "KORD", "MDW": "KMDW", "ATL": "KATL", "MIA": "KMIA", "FLL": "KFLL",
    "LAX": "KLAX", "SFO": "KSFO", "SEA": "KSEA", "DEN": "KDEN", "DFW": "KDFW",
    "IAH": "KIAH", "PHX": "KPHX", "LAS": "KLAS", "BIL": "KBIL",
    "YYZ": "CYYZ", "YUL": "CYUL", "YVR": "CYVR",
    "GRU": "SBGR", "GIG": "SBGL", "SCL": "SCEL", "EZE": "SAEZ", "BOG": "SKBO",
    "LIM": "SPJC", "PTY": "MPTO", "MEX": "MMMX", "CUN": "MMUN",
    # --- Asia / Pacific ---
    "SIN": "WSSS", "HKG": "VHHH", "BKK": "VTBS", "DEL": "VIDP", "BOM": "VABB",
    "NRT": "RJAA", "HND": "RJTT", "ICN": "RKSI", "PVG": "ZSPD", "PEK": "ZBAA",
    "SYD": "YSSY", "MEL": "YMML", "AKL": "NZAA",
}

# --- Airlines: IATA -> ICAO ------------------------------------------------
AIRLINE_IATA_ICAO = {
    "LH": "DLH",  # Lufthansa
    "LX": "SWR",  # SWISS
    "WK": "EDW",  # Edelweiss Air
    "OS": "AUA",  # Austrian
    "EN": "DLA",  # Air Dolomiti
    "EW": "EWG",  # Eurowings
    "SN": "BEL",  # Brussels Airlines
    "4Y": "OCN",  # Eurowings Discover / Discover Airlines
    "BA": "BAW",  # British Airways
    "TP": "TAP",  # TAP Air Portugal
    "BT": "BTI",  # airBaltic
    "KL": "KLM",  # KLM
    "AF": "AFR",  # Air France
    "AY": "FIN",  # Finnair
    "SK": "SAS",  # SAS
    "LO": "LOT",  # LOT Polish
    "IB": "IBE",  # Iberia
    "VY": "VLG",  # Vueling
    "FR": "RYR",  # Ryanair
    "W6": "WZZ",  # Wizz Air
    "U2": "EZY",  # easyJet
    "DS": "EZS",  # easyJet Switzerland
    "DE": "CFG",  # Condor
    "QR": "QTR",  # Qatar Airways
    "EK": "UAE",  # Emirates
    "EY": "ETD",  # Etihad
    "TK": "THY",  # Turkish
    "A3": "AEE",  # Aegean
    "AZ": "ITY",  # ITA Airways
    "OU": "CTN",  # Croatia Airlines
    "JU": "ASL",  # Air Serbia
    "OK": "CSA",  # Czech Airlines
    "FB": "LZB",  # Bulgaria Air
    "RO": "ROT",  # TAROM
    "SU": "AFL",  # Aeroflot
    "PS": "AUI",  # Ukraine Int'l
    "UA": "UAL",  # United
    "AA": "AAL",  # American
    "DL": "DAL",  # Delta
    "AC": "ACA",  # Air Canada
    "LA": "LAN",  # LATAM
    "AV": "AVA",  # Avianca
    "ET": "ETH",  # Ethiopian
    "SA": "SAA",  # South African
    "MS": "MSR",  # EgyptAir
    "SQ": "SIA",  # Singapore
    "CX": "CPA",  # Cathay Pacific
    "TG": "THA",  # Thai
}

# --- British Airways e-ticket display names -> IATA ------------------------
# BA prints friendly airport names in its e-ticket itinerary rather than codes.
AIRPORT_NAME_IATA = {
    "zurich": "ZRH", "geneva": "GVA", "basel": "BSL",
    "heathrow (london)": "LHR", "london heathrow": "LHR", "heathrow": "LHR",
    "gatwick (london)": "LGW", "london gatwick": "LGW", "gatwick": "LGW",
    "london city": "LCY", "city (london)": "LCY",
    "stansted (london)": "STN", "luton (london)": "LTN",
    "munich": "MUC", "frankfurt": "FRA", "hamburg": "HAM",
    "dusseldorf": "DUS", "cologne": "CGN", "stuttgart": "STR", "berlin": "BER",
    "brandenburg (berlin)": "BER", "tegel (berlin)": "TXL",
    "vienna": "VIE", "salzburg": "SZG",
    "arlanda": "ARN", "arlanda (stockholm)": "ARN", "stockholm": "ARN",
    "gothenburg": "GOT", "copenhagen": "CPH", "oslo": "OSL", "helsinki": "HEL",
    "larnaca": "LCA", "paphos": "PFO", "thessaloniki": "SKG", "athens": "ATH",
    "amsterdam": "AMS", "brussels": "BRU", "paris": "CDG",
    "charles de gaulle (paris)": "CDG", "nice": "NCE", "lisbon": "LIS",
    "porto": "OPO", "faro": "FAO", "madrid": "MAD", "barcelona": "BCN",
    "milan": "MXP", "malpensa (milan)": "MXP", "linate (milan)": "LIN",
    "rome": "FCO", "fiumicino (rome)": "FCO", "venice": "VCE", "naples": "NAP",
    "prague": "PRG", "warsaw": "WAW", "budapest": "BUD",
    "zagreb": "ZAG", "split": "SPU", "dubrovnik": "DBV",
    "new york": "JFK", "washington": "IAD", "boston": "BOS", "chicago": "ORD",
    "miami": "MIA", "denver": "DEN", "santiago": "SCL", "cape town": "CPT",
    "johannesburg": "JNB", "doha": "DOH", "dubai": "DXB",
}


def airport_icao(code):
    """Return the ICAO for an airport given IATA or ICAO; None if unknown."""
    if not code:
        return None
    c = code.strip().upper()
    if len(c) == 4 and c in AIRPORT_IATA_ICAO.values():
        return c
    return AIRPORT_IATA_ICAO.get(c)


def airline_icao(code):
    """Return the ICAO for an airline given IATA or ICAO; None if unknown."""
    if not code:
        return None
    c = code.strip().upper()
    if len(c) == 3 and c in AIRLINE_IATA_ICAO.values():
        return c
    return AIRLINE_IATA_ICAO.get(c)


def name_to_iata(name):
    """Map a British Airways display name (e.g. 'Heathrow (London)') to IATA."""
    if not name:
        return None
    key = " ".join(name.split()).strip().lower()
    return AIRPORT_NAME_IATA.get(key)
