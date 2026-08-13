"""Copart Scout v1: score normalized auction records.

The data collector is intentionally separate from scoring. This lets us change
how listings are obtained without changing the buying logic.
"""
from dataclasses import dataclass
from typing import Any
import re
import yaml

CONFIG = yaml.safe_load(open("config.yaml", encoding="utf-8"))

@dataclass
class Listing:
    lot: str
    year: int
    make: str
    model: str
    mileage: int | None
    drive: str
    condition: str
    primary_damage: str
    secondary_damage: str
    state: str
    current_bid: float | None = None


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _contains(value: str, terms: list[str]) -> bool:
    text = _norm(value)
    return any(_norm(term) in text for term in terms)


def score_listing(v: Listing) -> tuple[int, list[str]]:
    """Return a 0-100 score and concise reasons. Hard exclusions score zero."""
    reasons: list[str] = []
    if v.make.lower() not in [x.lower() for x in CONFIG["vehicles"]["makes"]]:
        return 0, ["wrong make"]
    if v.model.lower() not in [x.lower() for x in CONFIG["vehicles"]["models"]]:
        return 0, ["wrong model"]
    if not (CONFIG["vehicles"]["min_year"] <= v.year <= CONFIG["vehicles"]["max_year"]):
        return 0, ["year outside buy box"]
    if v.mileage is not None and v.mileage > CONFIG["vehicles"]["max_miles"]:
        return 0, ["mileage too high"]
    if _contains(v.primary_damage, CONFIG["damage"]["excluded_primary"]):
        return 0, ["excluded primary damage"]
    if _contains(v.secondary_damage, CONFIG["damage"]["excluded_secondary"]):
        return 0, ["excluded secondary damage"]

    score = 0
    if _contains(v.primary_damage, CONFIG["damage"]["preferred_primary"]):
        score += 30
        reasons.append("hail")
    elif not v.primary_damage:
        score += 15
    else:
        score += 5
        reasons.append("non-hail damage")

    score += 20
    if v.mileage is None:
        score += 0
    elif v.mileage < 50000:
        score += 15
        reasons.append("low mileage")
    elif v.mileage < 80000:
        score += 10
    else:
        score += 5

    if _contains(v.drive, CONFIG["vehicles"]["drive_types"]):
        score += 10
        reasons.append("4WD/AWD")

    if _norm(v.condition) == _norm(CONFIG["vehicles"]["required_condition"]):
        score += 15
        reasons.append("run & drive")
    else:
        score += 3

    if v.state.upper() in CONFIG["geography"]["preferred_states"]:
        score += 10
        reasons.append("preferred state")
    elif v.state.upper() in CONFIG["geography"]["avoid_states"]:
        score -= 5
        reasons.append("salt state")
    else:
        score += 5

    if v.current_bid is not None and v.current_bid <= CONFIG["budget"]["max_all_in"]:
        score += 10
        reasons.append("within provisional budget")

    return max(0, min(100, score)), reasons


def load_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for row in rows:
        listing = Listing(
            lot=str(row.get("lot", "")), year=int(row.get("year", 0)),
            make=str(row.get("make", "")), model=str(row.get("model", "")),
            mileage=int(row["mileage"]) if row.get("mileage") not in (None, "") else None,
            drive=str(row.get("drive", "")), condition=str(row.get("condition", "")),
            primary_damage=str(row.get("primary_damage", "")),
            secondary_damage=str(row.get("secondary_damage", "")),
            state=str(row.get("state", "")),
            current_bid=float(row["current_bid"]) if row.get("current_bid") not in (None, "") else None,
        )
        score, reasons = score_listing(listing)
        scored.append({**row, "score": score, "reasons": ", ".join(reasons)})
    return sorted(scored, key=lambda x: x["score"], reverse=True)


if __name__ == "__main__":
    print("Copart Scout scoring module loaded. Data collector is the next component.")
