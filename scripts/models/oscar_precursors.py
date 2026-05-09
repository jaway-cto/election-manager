"""
Oscar precursor predictor — Best Picture, Best Director, Acting categories.

Trains a logistic regression on which precursor wins (BAFTA, Globe Drama,
Globe Comedy, SAG ensemble, DGA, PGA, Critics' Choice) predict the eventual
Oscar win. History 2000-2025.

Usage:
    python -m models.oscar_precursors                  # show in-sample fit
    python -m models.oscar_precursors --predict 2027 \
        --bafta="<title>" --pga="<title>" --dga="<title>" --sag-cast="<title>"
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ============================================================================
# Historical Best Picture data — precursor winners and Oscar winner
# Sources: Wikipedia "Academy Award for Best Picture", precursor pages on
# Wikipedia. PGA/DGA/SAG-Ensemble/BAFTA-Best-Film/CCA-Best-Picture only.
# ============================================================================

BEST_PICTURE_HISTORY = [
    # year, oscar_winner, bafta, pga, dga (best film), sag_cast, critics_choice, globe_drama, globe_comedy
    # All values are film titles (or None if no precursor that year went to a non-listed film)
    (2000, "Gladiator",         "Gladiator",      "Gladiator",       "Crouching Tiger",      "Traffic",                "Gladiator",       "Gladiator",          "Almost Famous"),
    (2001, "A Beautiful Mind",  "Lord of the Rings", "Moulin Rouge",   "Lord of the Rings",   "Gosford Park",           "A Beautiful Mind","A Beautiful Mind",   "Moulin Rouge"),
    (2002, "Chicago",           "The Pianist",    "Chicago",         "Chicago",              "Chicago",                "Chicago",         "The Hours",          "Chicago"),
    (2003, "LOTR: Return of the King", "LOTR: Return of the King", "LOTR: Return of the King", "LOTR: Return of the King", "LOTR: Return of the King", "LOTR: Return of the King", "LOTR: Return of the King", "Lost in Translation"),
    (2004, "Million Dollar Baby","The Aviator",   "The Aviator",     "Million Dollar Baby",  "Sideways",               "Sideways",        "The Aviator",        "Sideways"),
    (2005, "Crash",             "Brokeback Mountain","Brokeback Mountain", "Brokeback Mountain", "Crash",            "Crash",           "Brokeback Mountain", "Walk the Line"),
    (2006, "The Departed",      "The Queen",      "Little Miss Sunshine", "The Departed",   "Little Miss Sunshine",   "The Departed",    "Babel",              "Dreamgirls"),
    (2007, "No Country for Old Men", "Atonement", "No Country for Old Men", "No Country for Old Men", "No Country for Old Men", "No Country for Old Men", "Atonement", "Sweeney Todd"),
    (2008, "Slumdog Millionaire","Slumdog Millionaire","Slumdog Millionaire","Slumdog Millionaire","Slumdog Millionaire","Slumdog Millionaire","Slumdog Millionaire","Vicky Cristina Barcelona"),
    (2009, "The Hurt Locker",   "The Hurt Locker","The Hurt Locker", "The Hurt Locker",      "Inglourious Basterds",   "The Hurt Locker", "Avatar",             "The Hangover"),
    (2010, "The King's Speech", "The King's Speech","The King's Speech","The King's Speech", "The King's Speech",      "The Social Network","The Social Network","The Kids Are All Right"),
    (2011, "The Artist",        "The Artist",     "The Artist",      "The Artist",           "The Help",               "The Artist",      "The Descendants",    "The Artist"),
    (2012, "Argo",              "Argo",           "Argo",            "Argo",                 "Argo",                   "Argo",            "Argo",               "Les Miserables"),
    (2013, "12 Years a Slave",  "12 Years a Slave","Gravity",        "Gravity",              "American Hustle",        "12 Years a Slave","12 Years a Slave",   "American Hustle"),
    (2014, "Birdman",           "Boyhood",        "Birdman",         "Birdman",              "Birdman",                "Boyhood",         "Boyhood",            "The Grand Budapest Hotel"),
    (2015, "Spotlight",         "The Revenant",   "The Big Short",   "The Revenant",         "Spotlight",              "Spotlight",       "The Revenant",       "The Martian"),
    (2016, "Moonlight",         "La La Land",     "La La Land",      "La La Land",           "Hidden Figures",         "La La Land",      "Moonlight",          "La La Land"),
    (2017, "The Shape of Water","Three Billboards","The Shape of Water","The Shape of Water","Three Billboards",       "The Shape of Water","Three Billboards","Lady Bird"),
    (2018, "Green Book",        "Roma",           "Green Book",      "Roma",                 "Black Panther",          "Roma",            "Bohemian Rhapsody",  "Green Book"),
    (2019, "Parasite",          "1917",           "1917",            "1917",                 "Parasite",               "1917",            "1917",               "Once Upon a Time in Hollywood"),
    (2020, "Nomadland",         "Nomadland",      "Nomadland",       "Nomadland",            "The Trial of the Chicago 7", "Nomadland",   "Nomadland",          "Borat Subsequent Moviefilm"),
    (2021, "CODA",              "The Power of the Dog","CODA",       "The Power of the Dog", "CODA",                   "The Power of the Dog","The Power of the Dog","West Side Story"),
    (2022, "Everything Everywhere All at Once", "All Quiet on the Western Front", "Everything Everywhere All at Once", "Everything Everywhere All at Once", "Everything Everywhere All at Once", "Everything Everywhere All at Once", "The Fabelmans", "The Banshees of Inisherin"),
    (2023, "Oppenheimer",       "Oppenheimer",    "Oppenheimer",     "Oppenheimer",          "Oppenheimer",            "Oppenheimer",     "Oppenheimer",        "Poor Things"),
    (2024, "Anora",             "Conclave",       "Anora",           "Anora",                "Conclave",               "Anora",           "The Brutalist",      "Emilia Perez"),
    (2025, "?",                 "?",              "?",               "?",                    "?",                      "?",               "?",                  "?"),  # in-progress
]


PRECURSOR_NAMES = ["bafta", "pga", "dga", "sag_cast", "cca", "globe_drama", "globe_comedy"]


def _to_features(row: tuple, candidate: str) -> list[int]:
    """For a candidate film and a (year, oscar, bafta, pga, dga, sag, cca, globe_d, globe_c) row,
    return binary features: did the candidate win each precursor?"""
    _, _oscar, *precs = row
    return [1 if p == candidate else 0 for p in precs]


def _candidates_for_year(row: tuple) -> set[str]:
    """All distinct films appearing as any winner in a year's row."""
    _, _oscar, *precs = row
    s = set(precs) | {row[1]}
    return {c for c in s if c and c != "?"}


def build_xy() -> tuple[np.ndarray, np.ndarray, list[tuple]]:
    """Build training matrix.
    Each row in the data table contributes one training example per
    candidate-that-won-something. Label y=1 if that candidate won the Oscar.
    """
    X_rows = []
    y = []
    meta = []
    for row in BEST_PICTURE_HISTORY:
        if row[1] == "?":
            continue
        for c in _candidates_for_year(row):
            X_rows.append(_to_features(row, c))
            y.append(1 if c == row[1] else 0)
            meta.append((row[0], c))
    return np.array(X_rows, dtype=float), np.array(y, dtype=int), meta


# ============================================================================
# Logistic regression — pure numpy, no sklearn dep required
# ============================================================================

def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X: np.ndarray, y: np.ndarray, *, l2: float = 1.0,
                 lr: float = 0.3, n_iter: int = 5000) -> tuple[np.ndarray, float]:
    """Returns (weights, intercept). L2 reg, batch GD."""
    n, p = X.shape
    w = np.zeros(p)
    b = 0.0
    for _ in range(n_iter):
        z = X @ w + b
        h = _sigmoid(z)
        grad_w = X.T @ (h - y) / n + (l2 / n) * w
        grad_b = (h - y).mean()
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def predict_proba(w: np.ndarray, b: float, x: np.ndarray) -> float:
    return float(_sigmoid(np.array(x) @ w + b))


# ============================================================================
# Calibration / sanity check — leave-one-year-out
# ============================================================================

def loyo_eval() -> dict:
    rows = [r for r in BEST_PICTURE_HISTORY if r[1] != "?"]
    correct = 0
    for held in rows:
        train = [r for r in rows if r is not held]
        X = []
        y = []
        for row in train:
            for c in _candidates_for_year(row):
                X.append(_to_features(row, c))
                y.append(1 if c == row[1] else 0)
        X = np.array(X, dtype=float); y = np.array(y, dtype=int)
        w, b = fit_logistic(X, y)
        # For held year, normalise predictions across candidates
        cands = _candidates_for_year(held)
        scores = {c: predict_proba(w, b, _to_features(held, c)) for c in cands}
        total = sum(scores.values())
        scores = {c: s/total for c, s in scores.items()}
        pick = max(scores, key=scores.get)
        if pick == held[1]:
            correct += 1
    return {"n": len(rows), "correct": correct, "acc": correct / len(rows)}


# ============================================================================
# CLI: predict for a specified upcoming year
# ============================================================================

def predict_year(precursors: dict, candidates: list[str]) -> dict[str, float]:
    """precursors: dict of precursor_name -> winning film title
       candidates: list of films competing for Oscar BP this year.
    Returns: {film: probability}, normalised across candidates.
    """
    X, y, _ = build_xy()
    w, b = fit_logistic(X, y)
    feature_order = PRECURSOR_NAMES
    out = {}
    for c in candidates:
        feats = [1 if precursors.get(name) == c else 0 for name in feature_order]
        out[c] = predict_proba(w, b, feats)
    total = sum(out.values()) or 1.0
    return {c: p/total for c, p in out.items()}


def feature_weights() -> dict[str, float]:
    X, y, _ = build_xy()
    w, b = fit_logistic(X, y)
    return {name: float(wi) for name, wi in zip(PRECURSOR_NAMES, w)} | {"_intercept": float(b)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--predict", help="Comma-separated candidate films")
    ap.add_argument("--bafta")
    ap.add_argument("--pga")
    ap.add_argument("--dga")
    ap.add_argument("--sag-cast")
    ap.add_argument("--cca")
    ap.add_argument("--globe-drama")
    ap.add_argument("--globe-comedy")
    args = ap.parse_args()

    print("Feature weights (logit space):")
    for k, v in feature_weights().items():
        print(f"  {k:<14}  {v:>+6.3f}")

    print(f"\nLeave-one-year-out evaluation:")
    res = loyo_eval()
    print(f"  {res['correct']} / {res['n']} = {res['acc']:.1%} top-1 accuracy")

    if args.predict:
        cands = [c.strip() for c in args.predict.split(",")]
        precs = {
            "bafta": args.bafta, "pga": args.pga, "dga": args.dga,
            "sag_cast": args.sag_cast, "cca": args.cca,
            "globe_drama": args.globe_drama, "globe_comedy": args.globe_comedy,
        }
        precs = {k: v for k, v in precs.items() if v}
        if not precs:
            print("\n(no precursor flags set; predicting on neutral input)")
        out = predict_year(precs, cands)
        print(f"\nPredictions (precursors: {precs}):")
        for c, p in sorted(out.items(), key=lambda x: -x[1]):
            print(f"  {p*100:5.1f}%  {c}")


if __name__ == "__main__":
    main()
