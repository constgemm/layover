#!/usr/bin/env python3
"""
airdata — offline IATA↔ICAO reference tables for the Layover auto-populate parser.

Zero dependencies (pure Python stdlib data). Deliberately hand-curated rather than
pulled from a package: the parser only needs the airports and airlines that actually
turn up in the mailboxes, plus the common hubs, and a tiny static table keeps the
whole pipeline installable on the airtrail-host box with nothing but Python 3.9.

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


# --- Airport coordinates: IATA -> (lat, lon) ------------------------------
# Approximate (≈1 decimal is plenty) — used only for a coarse "was I near this
# airport?" proximity test against Dawarich location history (radius ~100 km), so
# metro-area precision is more than enough. Missing airports degrade to "unknown"
# in the validator; add rows as new routes appear. Not exhaustive by design.
AIRPORT_LATLON = {
    # DACH
    "MUC": (48.35, 11.79), "FRA": (50.04, 8.56), "TXL": (52.56, 13.29),
    "BER": (52.37, 13.50), "DUS": (51.29, 6.77), "HAM": (53.63, 9.99),
    "STR": (48.69, 9.22), "CGN": (50.87, 7.14), "NUE": (49.50, 11.08),
    "ZRH": (47.46, 8.55), "GVA": (46.24, 6.11), "BSL": (47.59, 7.53),
    "VIE": (48.11, 16.57), "SZG": (47.79, 13.00), "INN": (47.26, 11.34),
    # UK / Ireland
    "LHR": (51.47, -0.46), "LGW": (51.15, -0.19), "LCY": (51.51, 0.05),
    "STN": (51.89, 0.24), "LTN": (51.87, -0.37), "MAN": (53.35, -2.27),
    "EDI": (55.95, -3.37), "DUB": (53.42, -6.27),
    # France / Benelux
    "CDG": (49.01, 2.55), "ORY": (48.73, 2.36), "NCE": (43.66, 7.22),
    "LYS": (45.73, 5.08), "AMS": (52.31, 4.76), "BRU": (50.90, 4.48),
    "CRL": (50.46, 4.45), "LUX": (49.63, 6.21),
    # Iberia
    "MAD": (40.47, -3.56), "BCN": (41.30, 2.08), "PMI": (39.55, 2.74),
    "AGP": (36.67, -4.50), "VLC": (39.49, -0.48), "IBZ": (38.87, 1.37),
    "LPA": (27.93, -15.39), "TFS": (28.04, -16.57), "LIS": (38.77, -9.13),
    "OPO": (41.24, -8.68), "FAO": (37.01, -7.97),
    # Italy
    "FCO": (41.80, 12.24), "CIA": (41.80, 12.59), "MXP": (45.63, 8.72),
    "LIN": (45.45, 9.28), "BGY": (45.67, 9.70), "VCE": (45.51, 12.35),
    "NAP": (40.89, 14.29), "BLQ": (44.53, 11.30), "CTA": (37.47, 15.07),
    # Nordics
    "ARN": (59.65, 17.92), "BMA": (59.35, 17.94), "GOT": (57.67, 12.29),
    "CPH": (55.62, 12.65), "OSL": (60.19, 11.10), "BGO": (60.29, 5.22),
    "HEL": (60.32, 24.96), "KEF": (63.99, -22.61),
    # Central / Eastern Europe & Balkans
    "PRG": (50.10, 14.26), "WAW": (52.17, 20.97), "WMI": (52.45, 20.65),
    "KRK": (50.08, 19.79), "BUD": (47.44, 19.26), "OTP": (44.57, 26.09),
    "SOF": (42.70, 23.41), "BEG": (44.82, 20.29), "ZAG": (45.74, 16.07),
    "SPU": (43.54, 16.30), "DBV": (42.56, 18.27), "LJU": (46.22, 14.46),
    # Greece / Cyprus / Malta / Turkey
    "ATH": (37.94, 23.95), "SKG": (40.52, 22.97), "HER": (35.34, 25.18),
    "JMK": (37.44, 25.35), "JTR": (36.40, 25.48), "LCA": (34.88, 33.63),
    "PFO": (34.72, 32.49), "MLA": (35.86, 14.48), "IST": (41.26, 28.74),
    "SAW": (40.90, 29.31), "AYT": (36.90, 30.79),
    # Middle East / Africa
    "DOH": (25.27, 51.61), "DXB": (25.25, 55.36), "AUH": (24.43, 54.65),
    "TLV": (32.01, 34.89), "CAI": (30.11, 31.41), "JNB": (-26.13, 28.24),
    "CPT": (-33.97, 18.60), "NBO": (-1.32, 36.93), "ADD": (8.98, 38.80),
    "RAK": (31.61, -8.04), "CMN": (33.37, -7.59),
    # Americas
    "JFK": (40.64, -73.78), "EWR": (40.69, -74.17), "IAD": (38.94, -77.46),
    "BOS": (42.36, -71.01), "ORD": (41.98, -87.90), "ATL": (33.64, -84.43),
    "MIA": (25.79, -80.29), "FLL": (26.07, -80.15), "LAX": (33.94, -118.41),
    "SFO": (37.62, -122.38), "SEA": (47.45, -122.31), "DEN": (39.86, -104.67),
    "DFW": (32.90, -97.04), "BIL": (45.81, -108.54), "YYZ": (43.68, -79.61),
    "GRU": (-23.43, -46.47), "GIG": (-22.81, -43.25), "SCL": (-33.39, -70.79),
    "EZE": (-34.82, -58.54), "BOG": (4.70, -74.15), "LIM": (-12.02, -77.11),
    "MEX": (19.44, -99.07), "CUN": (21.04, -86.87),
    # Asia / Pacific
    "SIN": (1.36, 103.99), "HKG": (22.31, 113.91), "BKK": (13.69, 100.75),
    "DEL": (28.57, 77.10), "BOM": (19.09, 72.87), "NRT": (35.77, 140.39),
    "HND": (35.55, 139.78), "ICN": (37.46, 126.44), "PVG": (31.14, 121.81),
    "SYD": (-33.95, 151.18), "MEL": (-37.67, 144.84), "AKL": (-37.01, 174.79),
}

# reverse IATA<-ICAO for coordinate lookups by ICAO
_ICAO_IATA = {v: k for k, v in AIRPORT_IATA_ICAO.items()}


def airport_latlon(code):
    """Return (lat, lon) for an airport given IATA or ICAO; None if unknown."""
    if not code:
        return None
    c = code.strip().upper()
    if c in AIRPORT_LATLON:
        return AIRPORT_LATLON[c]
    iata = _ICAO_IATA.get(c)          # accept an ICAO in
    return AIRPORT_LATLON.get(iata) if iata else None


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
