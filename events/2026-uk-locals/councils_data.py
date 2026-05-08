"""
Council data for 2026-05-08 tracker.
Format: (council, type, sub_region, seats_up, total_seats,
         pre[Con,Lab,LD,Grn,Ref,Ind,Oth], last[same], control_before)
last[] left as zeros where exact 2022 seat counts not yet researched —
user/researcher to fill in.
"""

Z = [0]*7  # placeholder

# ========= COUNTY COUNCILS (6, all-out) =========
COUNTIES = [
    ("East Sussex CC",    "County", "South East", 50, 50, [24,6,10,5,0,5,0], Z, "CON min"),
    ("Essex CC",          "County", "East",       78, 78, [52,7,6,2,0,8,3], Z, "CON maj"),
    ("Hampshire CC",      "County", "South East", 78, 78, [55,4,14,0,0,5,0], Z, "CON maj"),
    ("Norfolk CC",        "County", "East",       84, 84, [54,14,9,4,0,3,0], Z, "CON maj"),
    ("Suffolk CC",        "County", "East",       70, 70, [53,11,7,4,0,0,0], Z, "CON maj"),
    ("West Sussex CC",    "County", "South East", 70, 70, [51,5,9,3,0,2,0], Z, "CON maj"),
    # Added after API cross-check — agent missed these:
    ("Hertfordshire CC",  "County", "East",       78, 78, Z, Z, "CON"),
    ("Gloucestershire CC", "County", "South West", 56, 56, Z, Z, "CON"),
]

# ========= LONDON BOROUGHS (32, all-out, last 2022) =========
LONDON = [
    ("Barking & Dagenham", "London", "London", 51, 51, [0,51,0,0,0,0,0], Z, "LAB"),
    ("Barnet",             "London", "London", 63, 63, [22,41,0,0,0,0,0], Z, "LAB"),
    ("Bexley",             "London", "London", 45, 45, [31,14,0,0,0,0,0], Z, "CON"),
    ("Brent",              "London", "London", 57, 57, [10,47,0,0,0,0,0], Z, "LAB"),
    ("Bromley",            "London", "London", 58, 58, [36,9,13,0,0,0,0], Z, "CON"),
    ("Camden",             "London", "London", 55, 55, [5,47,1,2,0,0,0], Z, "LAB"),
    ("Croydon (Mayor)",    "London", "London", 70, 70, [33,34,0,3,0,0,0], Z, "CON min/Mayor CON"),
    ("Ealing",             "London", "London", 70, 70, [11,56,3,0,0,0,0], Z, "LAB"),
    ("Enfield",            "London", "London", 63, 63, [22,41,0,0,0,0,0], Z, "LAB"),
    ("Greenwich",          "London", "London", 55, 55, [8,47,0,0,0,0,0], Z, "LAB"),
    ("Hackney (Mayor)",    "London", "London", 57, 57, [1,50,0,4,0,2,0], Z, "LAB"),
    ("Hammersmith & Fulham","London","London", 50, 50, [14,36,0,0,0,0,0], Z, "LAB"),
    ("Haringey",           "London", "London", 57, 57, [0,41,14,2,0,0,0], Z, "LAB"),
    ("Harrow",             "London", "London", 55, 55, [31,24,0,0,0,0,0], Z, "CON"),
    ("Havering",           "London", "London", 55, 55, [18,8,0,0,0,5,24], Z, "Residents"),
    ("Hillingdon",         "London", "London", 53, 53, [32,21,0,0,0,0,0], Z, "CON"),
    ("Hounslow",           "London", "London", 62, 62, [9,50,0,0,0,3,0], Z, "LAB"),
    ("Islington",          "London", "London", 51, 51, [0,47,0,4,0,0,0], Z, "LAB"),
    ("Kensington & Chelsea","London","London", 50, 50, [35,13,2,0,0,0,0], Z, "CON"),
    ("Kingston upon Thames","London","London", 48, 48, [6,9,33,0,0,0,0], Z, "LD"),
    ("Lambeth",            "London", "London", 63, 63, [2,56,0,5,0,0,0], Z, "LAB"),
    ("Lewisham (Mayor)",   "London", "London", 54, 54, [0,47,2,5,0,0,0], Z, "LAB"),
    ("Merton",             "London", "London", 57, 57, [17,33,4,0,0,3,0], Z, "LAB"),
    ("Newham (Mayor)",     "London", "London", 66, 66, [0,66,0,0,0,0,0], Z, "LAB"),
    ("Redbridge",          "London", "London", 63, 63, [12,51,0,0,0,0,0], Z, "LAB"),
    ("Richmond upon Thames","London","London", 54, 54, [3,0,48,3,0,0,0], Z, "LD"),
    ("Southwark",          "London", "London", 63, 63, [0,53,9,1,0,0,0], Z, "LAB"),
    ("Sutton",             "London", "London", 55, 55, [18,0,33,0,0,4,0], Z, "LD"),
    ("Tower Hamlets (Mayor)","London","London", 45, 45, [2,19,0,0,0,0,24], Z, "Aspire"),
    ("Waltham Forest",     "London", "London", 60, 60, [13,44,3,0,0,0,0], Z, "LAB"),
    ("Wandsworth",         "London", "London", 58, 58, [22,35,0,0,0,1,0], Z, "LAB"),
    ("Westminster",        "London", "London", 54, 54, [23,31,0,0,0,0,0], Z, "LAB"),
]

# ========= METROPOLITAN BOROUGHS (32; mix all-out and thirds) =========
METS = [
    ("Barnsley",          "Met", "Yorkshire & Humber", 21, 63, [4,47,4,0,0,8,0], Z, "LAB"),
    ("Birmingham",        "Met", "West Midlands", 101, 101, [22,65,9,3,0,2,0], Z, "LAB"),
    ("Bolton",            "Met", "North West",   20, 60, [19,21,5,0,0,15,0], Z, "LAB min"),
    ("Bradford",          "Met", "Yorkshire & Humber", 90, 90, [18,50,8,6,0,8,0], Z, "LAB"),
    ("Bury",              "Met", "North West",   17, 51, [17,27,4,2,0,1,0], Z, "LAB"),
    ("Calderdale",        "Met", "Yorkshire & Humber", 54, 54, [17,26,5,2,0,4,0], Z, "LAB"),
    ("Coventry",          "Met", "West Midlands", 54, 54, [14,35,0,1,0,4,0], Z, "LAB"),
    ("Dudley",            "Met", "West Midlands", 25, 72, [35,28,0,0,0,9,0], Z, "CON min"),
    ("Gateshead",         "Met", "North East",   66, 66, [0,50,10,4,0,2,0], Z, "LAB"),
    ("Kirklees",          "Met", "Yorkshire & Humber", 69, 69, [18,26,9,8,0,8,0], Z, "LAB min"),
    ("Knowsley",          "Met", "North West",   15, 45, [0,41,2,0,0,2,0], Z, "LAB"),
    ("Leeds",             "Met", "Yorkshire & Humber", 33, 99, [19,56,8,6,0,10,0], Z, "LAB"),
    ("Manchester",        "Met", "North West",   32, 96, [0,75,13,6,0,2,0], Z, "LAB"),
    ("Newcastle upon Tyne","Met","North East",   78, 78, [0,39,21,4,0,14,0], Z, "LAB min"),
    ("North Tyneside (Mayor)","Met","North East", 20, 60, [12,41,4,0,0,3,0], Z, "LAB"),
    ("Oldham",            "Met", "North West",   20, 60, [11,22,13,1,0,13,0], Z, "LAB min"),
    ("Rochdale",          "Met", "North West",   20, 60, [8,38,6,0,0,8,0], Z, "LAB"),
    ("Salford (Mayor)",   "Met", "North West",   20, 60, [7,45,1,0,0,7,0], Z, "LAB"),
    ("Sandwell",          "Met", "West Midlands", 72, 72, [9,60,0,0,0,3,0], Z, "LAB"),
    ("Sefton",            "Met", "North West",   66, 66, [12,42,8,1,0,3,0], Z, "LAB"),
    ("Sheffield",         "Met", "Yorkshire & Humber", 28, 84, [5,33,27,14,0,5,0], Z, "NOC"),
    ("Solihull",          "Met", "West Midlands", 51, 51, [30,4,5,9,0,3,0], Z, "CON"),
    ("South Tyneside",    "Met", "North East",   54, 54, [4,27,0,12,0,11,0], Z, "LAB min"),
    ("St Helens",         "Met", "North West",   48, 48, [6,32,4,1,0,5,0], Z, "LAB"),
    ("Stockport",         "Met", "North West",   21, 63, [9,17,27,0,0,4,6], Z, "LD min"),
    ("Sunderland",        "Met", "North East",   75, 75, [12,47,7,0,0,9,0], Z, "LAB"),
    ("Tameside",          "Met", "North West",   19, 57, [12,38,0,4,0,3,0], Z, "LAB"),
    ("Trafford",          "Met", "North West",   21, 63, [19,36,5,3,0,0,0], Z, "LAB"),
    ("Wakefield",         "Met", "Yorkshire & Humber", 63, 63, [13,41,0,0,0,9,0], Z, "LAB"),
    ("Walsall",           "Met", "West Midlands", 60, 60, [33,20,0,0,0,7,0], Z, "CON min"),
    ("Wigan",             "Met", "North West",   25, 75, [4,60,2,0,0,9,0], Z, "LAB"),
    ("Wolverhampton",     "Met", "West Midlands", 20, 60, [16,41,0,0,0,3,0], Z, "LAB"),
]

# ========= UNITARY AUTHORITIES =========
UNITARIES = [
    ("Blackburn with Darwen", "Unitary", "North West",   17, 51, [12,32,0,0,0,7,0], Z, "LAB"),
    ("East Surrey (shadow)",  "Unitary", "South East",   0, 0, Z, Z, "NEW"),
    ("Halton",                "Unitary", "North West",   19, 54, [2,47,4,0,0,1,0], Z, "LAB"),
    ("Hartlepool",            "Unitary", "North East",   12, 36, [5,12,0,0,8,11,0], Z, "LAB"),
    ("Hull (Kingston upon Hull)","Unitary","Yorkshire & Humber", 19, 57, [0,17,38,0,0,2,0], Z, "LD"),
    ("Isle of Wight",         "Unitary", "South East",   39, 39, [8,2,4,7,0,18,0], Z, "Ind/Grn"),
    ("Milton Keynes",         "Unitary", "South East",   60, 60, [18,24,15,0,0,0,0], Z, "LAB"),
    ("North East Lincolnshire","Unitary","Yorkshire & Humber", 14, 42, [20,13,4,0,2,3,0], Z, "CON min"),
    ("Peterborough",          "Unitary", "East",         20, 60, [14,22,5,5,4,10,0], Z, "LAB min"),
    ("Plymouth",              "Unitary", "South West",   19, 57, [19,30,0,0,2,6,0], Z, "LAB"),
    ("Portsmouth",            "Unitary", "South East",   14, 42, [9,9,17,0,0,7,0], Z, "LD min"),
    ("Reading",               "Unitary", "South East",   16, 48, [8,30,5,5,0,0,0], Z, "LAB"),
    ("Southampton",           "Unitary", "South East",   17, 51, [14,25,6,0,0,6,0], Z, "LAB"),
    ("Southend-on-Sea",       "Unitary", "East",         17, 51, [17,16,7,0,0,11,0], Z, "NOC"),
    ("Swindon",               "Unitary", "South West",   57, 57, [18,33,4,0,0,2,0], Z, "LAB"),
    ("Thurrock",              "Unitary", "East",         49, 49, [17,22,0,0,0,10,0], Z, "LAB"),
    ("West Surrey (shadow)",  "Unitary", "South East",   0, 0, Z, Z, "NEW"),
    ("Wokingham",             "Unitary", "South East",   18, 54, [14,4,33,0,0,3,0], Z, "LD"),
    # Added after API cross-check:
    ("Somerset",              "Unitary", "South West",   110, 110, Z, Z, "LD"),
    ("West Northamptonshire", "Unitary", "East Midlands", 78, 78, Z, Z, "CON"),
]

# ========= DISTRICT COUNCILS =========
# All-out (3)
DISTRICTS_ALLOUT = [
    ("Huntingdonshire",        "District", "East",         52, 52, Z, Z, "NOC coalition"),
    ("Newcastle-under-Lyme",   "District", "West Midlands", 44, 44, Z, Z, "CON"),
    ("South Cambridgeshire",   "District", "East",         45, 45, Z, Z, "LD"),
]

# Halves (7) — seats up = ~half
DISTRICTS_HALVES = [
    ("Adur",                   "District", "South East",   15, 29, Z, Z, "LAB"),
    ("Cheltenham",             "District", "South West",   20, 40, Z, Z, "LD"),
    ("Fareham",                "District", "South East",   16, 32, Z, Z, "CON"),
    ("Gosport",                "District", "South East",   14, 28, Z, Z, "LD min"),
    ("Hastings",               "District", "South East",   16, 32, Z, Z, "Grn/Ind"),
    ("Nuneaton & Bedworth",    "District", "West Midlands", 19, 38, Z, Z, "LAB min"),
    ("Oxford",                 "District", "South East",   24, 48, Z, Z, "LAB min"),
]

# Thirds (38) — seats up = ~one third
DISTRICTS_THIRDS = [
    ("Basildon",               "District", "East",         14, 42, Z, Z, "LAB min"),
    ("Basingstoke & Deane",    "District", "South East",   18, 54, Z, Z, "Ind/LD"),
    ("Brentwood",              "District", "East",         13, 39, Z, Z, "LD/Lab"),
    ("Broxbourne",             "District", "East",         10, 30, Z, Z, "CON"),
    ("Burnley",                "District", "North West",   15, 45, Z, Z, "Ind/Grn/LD"),
    ("Cambridge",              "District", "East",         14, 42, Z, Z, "LAB"),
    ("Cannock Chase",          "District", "West Midlands", 12, 36, Z, Z, "LAB"),
    ("Cherwell",               "District", "South East",   16, 48, Z, Z, "LD/Grn"),
    ("Chorley",                "District", "North West",   14, 42, Z, Z, "LAB"),
    ("Colchester",             "District", "East",         17, 51, Z, Z, "LD/Lab"),
    ("Crawley",                "District", "South East",   12, 36, Z, Z, "LAB"),
    ("Eastleigh",              "District", "South East",   13, 39, Z, Z, "LD"),
    ("Epping Forest",          "District", "East",         18, 54, Z, Z, "CON min"),
    ("Exeter",                 "District", "South West",   13, 39, Z, Z, "LAB"),
    ("Harlow",                 "District", "East",         11, 33, Z, Z, "CON"),
    ("Hart",                   "District", "South East",   11, 33, Z, Z, "LD/Ind"),
    ("Havant",                 "District", "South East",   12, 36, Z, Z, "Lab/LD/Grn"),
    ("Hyndburn",               "District", "North West",   12, 35, Z, Z, "LAB"),
    ("Ipswich",                "District", "East",         16, 48, Z, Z, "LAB"),
    ("Lincoln",                "District", "East Midlands", 11, 33, Z, Z, "LAB"),
    ("Norwich",                "District", "East",         13, 39, Z, Z, "LAB min"),
    ("Pendle",                 "District", "North West",   11, 33, Z, Z, "Ind min"),
    ("Preston",                "District", "North West",   16, 48, Z, Z, "LAB"),
    ("Redditch",               "District", "West Midlands", 9, 27, Z, Z, "LAB"),
    ("Rochford",               "District", "East",         13, 39, Z, Z, "CON/Ind"),
    ("Rugby",                  "District", "West Midlands", 14, 42, Z, Z, "LAB min"),
    ("Rushmoor",               "District", "South East",   13, 39, Z, Z, "LAB min"),
    ("St Albans",              "District", "East",         19, 56, Z, Z, "LD"),
    ("Stevenage",              "District", "East",         13, 39, Z, Z, "LAB"),
    ("Tamworth",               "District", "West Midlands", 10, 30, Z, Z, "LAB min"),
    ("Three Rivers",           "District", "East",         13, 39, Z, Z, "LD min"),
    ("Tunbridge Wells",        "District", "South East",   13, 39, Z, Z, "LD"),
    ("Watford",                "District", "East",         12, 36, Z, Z, "LD + Mayor"),
    ("Welwyn Hatfield",        "District", "East",         16, 48, Z, Z, "LAB/LD"),
    ("West Lancashire",        "District", "North West",   15, 45, Z, Z, "LAB min"),
    ("West Oxfordshire",       "District", "South East",   16, 49, Z, Z, "LD/Lab/Grn"),
    ("Winchester",             "District", "South East",   15, 45, Z, Z, "LD"),
    ("Worthing",               "District", "South East",   12, 37, Z, Z, "LAB"),
    # Added after API cross-check:
    ("Chelmsford",             "District", "East",         19, 57, Z, Z, "LD"),
    ("Chesterfield",           "District", "East Midlands", 16, 48, Z, Z, "LAB"),
    ("East Hertfordshire",     "District", "East",         16, 50, Z, Z, "CON"),
    ("Hertsmere",              "District", "East",         13, 39, Z, Z, "CON"),
    ("Stratford-on-Avon",      "District", "West Midlands", 12, 36, Z, Z, "LD/Grn"),
]

ENGLAND = COUNTIES + LONDON + METS + UNITARIES + DISTRICTS_ALLOUT + DISTRICTS_HALVES + DISTRICTS_THIRDS

# ========= SCOTLAND — none =========
SCOTLAND: list[tuple] = []

# ========= WALES — single by-election =========
# Newport City Council, Rogerstone North ward — 1 seat
# Pre-poll council: Lab 33, Oth 8, Con 6, Grn 2, LD 1, Vacancy 1 (51 total)
# Map to PARTIES order Con,Lab,LD,Grn,Ref,Ind,Oth — vacancy folded into Oth
WALES = [
    ("Newport — Rogerstone North (by-election)", "Principal (by-elec)", "South Wales",
     1, 51, [6,33,1,2,0,0,9], [1,0,0,0,0,0,0], "LAB (council); CON (seat)"),
]
