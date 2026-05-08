"""
Populate LAST election seat counts per council.

Sources:
  - House of Commons Library 2022 local-elections handbook (le2022.xlsx)
    -- definitive 'Elected' flag, council-level aggregable
    -- covers London (2022), mets, most districts, unitaries that polled 2022
  - Open Council Data history2016-2025.csv  (fallback)
    -- post-election composition for any council/year, used for counties
       (last all-out 2021) and for any council missing from HoC file

Output: last_seats.json -- dict { council_alias: [Con,Lab,LD,Grn,Ref,SNP,PC,Ind,Oth] }
"""

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

ROOT = Path(__file__).parent

PARTIES = ["Con", "Lab", "LD", "Grn", "Ref", "SNP", "PC", "Ind", "Oth"]

# Map HoC party_group code -> our PARTIES index
HOC_MAP = {
    "CON": 0, "LAB": 1, "LD": 2, "GREEN": 3, "REF": 4,
    "SNP": 5, "PC": 6, "IND": 7,
    # everything else (OTH and minor groups) -> 8
}

# Map OCD column -> our PARTIES index (OCD has no 'ind' column; everything non-listed is 'other')
OCD_MAP = {"con": 0, "lab": 1, "ld": 2, "green": 3, "ref": 4, "snp": 5, "pc": 6, "other": 8}
# (OCD does not separate independent from other; whole 'other' bucket lands in our 'Oth')


def normalise(name: str) -> str:
    """Normalise council names to a canonical key for lookup."""
    n = name
    # Remove suffixes
    n = re.sub(r"\s*\(Mayor\)\s*$", "", n)
    n = re.sub(r"\s*\(by-election\)\s*$", "", n)
    n = re.sub(r"\s*\(shadow\)\s*$", "", n)
    n = re.sub(r"\s*CC\s*$", "", n)
    n = re.sub(r"\s*City Council\s*$", "", n)
    # Specific aliases
    n = n.replace("Hull (Kingston upon Hull)", "Kingston upon Hull")
    n = n.replace("St Helens", "St. Helens")
    # Strip ward-level suffixes from by-elections so we don't accidentally match council-wide rows
    n = re.sub(r"\s+ward\s*$", "", n, flags=re.IGNORECASE)
    n = n.replace(" — ", " ").replace("—", " ")
    n = n.replace(" – ", " ").replace("–", " ")
    n = n.replace("&", "and")
    n = re.sub(r"\s+", " ", n).strip().lower()
    return n


def load_hoc_2022() -> dict[str, list[int]]:
    """Aggregate HoC 2022 elected counts per council (using 'Elected' flag)."""
    wb = openpyxl.load_workbook(ROOT / "le2022.xlsx", data_only=True, read_only=True)
    ws = wb["Candidates-results"]
    seats: dict[str, list[int]] = defaultdict(lambda: [0] * 9)
    for r in ws.iter_rows(min_row=3, values_only=True):
        if not r or r[5] is None:
            continue
        la_name = str(r[5])
        elected = r[16]
        party_grp = r[19]
        if elected != 1:
            continue
        idx = HOC_MAP.get(party_grp, 8)
        seats[normalise(la_name)][idx] += 1
    return dict(seats)


def load_ocd(year: int) -> dict[str, list[int]]:
    """Composition snapshot for a given year from OCD CSV."""
    out: dict[str, list[int]] = {}
    with open(ROOT / "ocd_history.csv", newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for row in rd:
            if int(row["year"]) != year:
                continue
            arr = [0] * 9
            for col, idx in OCD_MAP.items():
                v = row.get(col, "0")
                arr[idx] = int(v) if v and v.strip() else 0
            out[normalise(row["authority"])] = arr
    return out


def main() -> None:
    from councils_data import ENGLAND, SCOTLAND, WALES

    hoc22 = load_hoc_2022()
    ocd21 = load_ocd(2021)
    ocd22 = load_ocd(2022)
    ocd25 = load_ocd(2025)

    # Year mapping per council: counties 2021, everything else 2022 (default).
    # Some councils last polled 2024/2025 in their cycle (e.g. delayed unitaries) — handled ad hoc later.
    out: dict[str, list[int]] = {}
    misses: list[tuple[str, str]] = []

    def lookup(name: str, ctype: str) -> tuple[list[int] | None, str]:
        key = normalise(name)
        if ctype == "County":
            return (ocd21.get(key), "OCD-2021") if ocd21.get(key) else (None, "OCD-2021-MISS")
        # default: HoC 2022
        if key in hoc22:
            return hoc22[key], "HoC-2022"
        # fallback: OCD 2022 composition
        if key in ocd22:
            return ocd22[key], "OCD-2022"
        return None, "MISS"

    for council in ENGLAND + SCOTLAND + WALES:
        name, ctype = council[0], council[1]
        seats, src = lookup(name, ctype)
        if seats is None:
            misses.append((name, src))
        else:
            out[name] = seats

    # Diagnostics
    print(f"Populated: {len(out)}  Missing: {len(misses)}")
    for n, why in misses:
        # Try to suggest a near-match in HoC
        nk = normalise(n)
        suggestions = [k for k in hoc22 if nk[:6] in k or k[:6] in nk][:3]
        print(f"  MISS [{why}] {n!r} -> suggestions: {suggestions}")

    with open(ROOT / "last_seats.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {ROOT / 'last_seats.json'}")


if __name__ == "__main__":
    main()
