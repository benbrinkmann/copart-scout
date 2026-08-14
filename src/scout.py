"""Copart Scout v1: score normalized auction records.

The data collector is intentionally separate from scoring. This lets us change
how listings are obtained without changing the buying logic.

Scoring design
--------------
Every point value comes from ``scoring.weights`` in config.yaml. Each category
awards a fraction of its configured weight, and the fractions per category sum
to at most 1.0, so a perfect listing scores exactly the sum of the weights
(100 with the shipped config). Change a weight in the config and the scoring
changes with it; no code edit is required.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import logging
import re
import yaml

log = logging.getLogger(__name__)

# Config lives at the repo root, one level above this file. Resolving the path
# relative to __file__ (rather than the process working directory) means the
# module imports correctly no matter where the interpreter was started.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

# Cache for the lazily loaded default config. Loading on first use rather than
# at import time keeps a missing or malformed config from breaking the import.
_config_cache: dict[str, Any] | None = None


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Read and parse a config file. Uses a context manager so the handle closes."""
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def get_config() -> dict[str, Any]:
    """Return the default config, loading it once and reusing it thereafter."""
    global _config_cache
    if _config_cache is None:
        _config_cache = load_config()
    return _config_cache


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
    """True if any term appears anywhere inside value (substring match).

    Used for damage fields, where Copart wording varies ("WATER/FLOOD" must
    match the configured term "Flood").
    """
    text = _norm(value)
    return any(_norm(term) in text for term in terms)


def _contains_word(value: str, terms: list[str]) -> bool:
    """True if any term appears in value as a whole word.

    Copart make/model fields carry trim and body text, so a real lot reads
    "TUNDRA CREWMAX" rather than "Tundra". Whole-word matching accepts that
    while still rejecting an unrelated model that merely shares a substring.
    The lookarounds are used instead of \\b because they behave correctly for
    terms that begin or end with a non-word character, such as "F-150".
    """
    text = _norm(value)
    for term in terms:
        term_norm = _norm(term)
        if not term_norm:
            continue
        if re.search(rf"(?<!\w){re.escape(term_norm)}(?!\w)", text):
            return True
    return False


def _to_int(value: Any) -> int | None:
    """Parse an integer from feed data, tolerating "45,000", " 45000 " and blanks.

    Returns None when the value is absent or not parseable, which callers treat
    as "unknown" rather than as an error.
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        # float() first so that "45000.0" from a spreadsheet export still parses.
        return int(float(text))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    """Parse a currency-ish float, tolerating "$10,500" and blanks. None if unknown."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def score_listing(
    v: Listing, config: dict[str, Any] | None = None
) -> tuple[int, list[str]]:
    """Return a 0-100 score and concise reasons. Hard exclusions score zero.

    Pass an explicit config to score against an alternate buy box; omit it to
    use config.yaml.
    """
    cfg = config if config is not None else get_config()
    weights = cfg["scoring"]["weights"]
    reasons: list[str] = []

    def award(category: str, fraction: float) -> int:
        """Award a fraction of a category's configured weight, rounded to a whole point."""
        return round(weights[category] * fraction)

    # --- Hard exclusions: any one of these disqualifies the lot outright. ---
    if not _contains_word(v.make, cfg["vehicles"]["makes"]):
        return 0, ["wrong make"]
    if not _contains_word(v.model, cfg["vehicles"]["models"]):
        return 0, ["wrong model"]
    if not (cfg["vehicles"]["min_year"] <= v.year <= cfg["vehicles"]["max_year"]):
        return 0, ["year outside buy box"]
    # Unknown mileage is not disqualifying; it simply earns no mileage points below.
    if v.mileage is not None and v.mileage > cfg["vehicles"]["max_miles"]:
        return 0, ["mileage too high"]
    if _contains(v.primary_damage, cfg["damage"]["excluded_primary"]):
        return 0, ["excluded primary damage"]
    if _contains(v.secondary_damage, cfg["damage"]["excluded_secondary"]):
        return 0, ["excluded secondary damage"]

    score = 0

    # --- Damage: full weight for the preferred type, partial otherwise. ---
    if _contains(v.primary_damage, cfg["damage"]["preferred_primary"]):
        score += award("damage", 1.0)
        reasons.append("hail")
    elif not v.primary_damage:
        score += award("damage", 0.5)  # no damage listed
    else:
        score += award("damage", 0.17)  # allowed but not preferred
        reasons.append("non-hail damage")

    # --- Vehicle: the make/model match earns half the weight, drive type the
    # other half. Splitting one weight keeps the category totals in the config
    # authoritative; previously the drive bonus was extra points with no weight. ---
    score += award("vehicle", 0.5)
    if _contains(v.drive, cfg["vehicles"]["drive_types"]):
        score += award("vehicle", 0.5)
        reasons.append("4WD/AWD")

    # --- Mileage: banded. Unknown mileage earns nothing rather than guessing. ---
    if v.mileage is None:
        pass
    elif v.mileage < 50000:
        score += award("mileage", 1.0)
        reasons.append("low mileage")
    elif v.mileage < 80000:
        score += award("mileage", 0.67)
    else:
        score += award("mileage", 0.33)

    # --- Condition ---
    if _norm(v.condition) == _norm(cfg["vehicles"]["required_condition"]):
        score += award("condition", 1.0)
        reasons.append("run & drive")
    else:
        score += award("condition", 0.2)

    # --- Geography: normalize first so " tx " is treated as TX. ---
    state = _norm(v.state).upper()
    if state in cfg["geography"]["preferred_states"]:
        score += award("geography", 1.0)
        reasons.append("preferred state")
    elif state in cfg["geography"]["avoid_states"]:
        score -= award("geography", 0.5)  # penalty for road-salt regions
        reasons.append("salt state")
    else:
        score += award("geography", 0.5)

    # --- Economics: provisional only. Current bid ignores buyer fees, gate fees
    # and transport, and means little before bidding opens. See README. ---
    if v.current_bid is not None and v.current_bid <= cfg["budget"]["max_all_in"]:
        score += award("economics", 1.0)
        reasons.append("within provisional budget")

    # Clamp defensively: weights are user-editable and may not sum to 100.
    return max(0, min(100, score)), reasons


def load_records(
    rows: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Score rows and return them sorted best first.

    Rows that cannot be parsed are logged and skipped so that one malformed
    record does not discard the whole batch.

    limit caps how many rows are returned. Pass an integer to override, or
    leave as None to use output.top_n from the config. Pass 0 to return all.
    """
    cfg = config if config is not None else get_config()
    if limit is None:
        limit = cfg.get("output", {}).get("top_n", 0)

    scored = []
    for index, row in enumerate(rows):
        try:
            year = _to_int(row.get("year"))
            if year is None:
                # Year drives a hard exclusion, so treat it as required.
                raise ValueError("missing or unparseable year")
            listing = Listing(
                lot=str(row.get("lot", "")),
                year=year,
                make=str(row.get("make", "")),
                model=str(row.get("model", "")),
                mileage=_to_int(row.get("mileage")),
                drive=str(row.get("drive", "")),
                condition=str(row.get("condition", "")),
                primary_damage=str(row.get("primary_damage", "")),
                secondary_damage=str(row.get("secondary_damage", "")),
                state=str(row.get("state", "")),
                current_bid=_to_float(row.get("current_bid")),
            )
            score, reasons = score_listing(listing, cfg)
        except (ValueError, TypeError, KeyError) as exc:
            log.warning(
                "skipping row %d (lot %s): %s", index, row.get("lot", "?"), exc
            )
            continue
        scored.append({**row, "score": score, "reasons": ", ".join(reasons)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    # limit of 0 or negative means "no cap"; guard the slice against a bad config value.
    return scored[:limit] if limit and limit > 0 else scored


if __name__ == "__main__":
    print("Copart Scout scoring module loaded. Data collector is the next component.")
