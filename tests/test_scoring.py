"""Tests for the scoring logic and the row loader.

Listings are built with keyword arguments so that adding or reordering a
Listing field cannot silently misassign values in these tests.
"""
import logging

import pytest

from src.scout import Listing, get_config, load_records, score_listing


def make_listing(**overrides) -> Listing:
    """A listing that satisfies the shipped buy box, with per-test overrides."""
    base = dict(
        lot="1", year=2022, make="Toyota", model="Tundra", mileage=45000,
        drive="4WD", condition="Run & Drive", primary_damage="Hail",
        secondary_damage="", state="TX", current_bid=10000,
    )
    return Listing(**{**base, **overrides})


# --- Original behaviour, preserved ---

def test_hail_tundra_scores_well():
    score, reasons = score_listing(make_listing())
    assert score >= 90
    assert "hail" in reasons


def test_flood_is_excluded():
    score, _ = score_listing(make_listing(primary_damage="Flood"))
    assert score == 0


# --- Fix 1: model matching tolerates Copart trim and body text ---

@pytest.mark.parametrize("model", ["TUNDRA SR5", "Tundra CrewMax", "SEQUOIA LIMITED"])
def test_model_with_trim_text_is_accepted(model):
    assert score_listing(make_listing(model=model))[0] > 0


def test_unrelated_model_is_still_rejected():
    score, reasons = score_listing(make_listing(model="Tacoma"))
    assert (score, reasons) == (0, ["wrong model"])


def test_make_with_extra_text_is_accepted():
    assert score_listing(make_listing(make="TOYOTA MOTOR CORP"))[0] > 0


# --- Fixes 2 and 3: weights come from config, and a perfect lot hits the ceiling
#     exactly rather than being clipped by the clamp ---

def test_perfect_listing_equals_sum_of_configured_weights():
    total = sum(get_config()["scoring"]["weights"].values())
    assert score_listing(make_listing())[0] == total


def test_scoring_responds_to_config_weight_changes():
    """Halving the damage weight must lower the score; it was hardcoded before."""
    cfg = get_config()
    tweaked = {**cfg, "scoring": {"weights": {**cfg["scoring"]["weights"], "damage": 15}}}
    assert score_listing(make_listing(), tweaked)[0] < score_listing(make_listing())[0]


def test_top_band_can_discriminate():
    """A lot missing several bonuses must rank below a fully qualified one."""
    best = score_listing(make_listing())[0]
    lesser = score_listing(make_listing(drive="2WD", condition="Enhanced Vehicles"))[0]
    assert lesser < best


# --- Fix 4: a malformed row is skipped, not fatal ---

def test_malformed_row_is_skipped_and_logged(caplog):
    rows = [
        {"lot": "good", "year": 2022, "make": "Toyota", "model": "TUNDRA SR5",
         "mileage": "45,000", "drive": "4WD", "condition": "Run & Drive",
         "primary_damage": "Hail", "state": "TX", "current_bid": "$10,000"},
        {"lot": "bad", "year": "not-a-year", "make": "Toyota", "model": "Tundra"},
    ]
    with caplog.at_level(logging.WARNING):
        out = load_records(rows)
    assert [r["lot"] for r in out] == ["good"]
    assert out[0]["score"] > 0          # "45,000" and "$10,000" parsed correctly
    assert "bad" in caplog.text


def test_unknown_mileage_is_not_disqualifying():
    rows = [{"lot": "x", "year": 2022, "make": "Toyota", "model": "Tundra",
             "mileage": "", "drive": "4WD", "condition": "Run & Drive",
             "primary_damage": "Hail", "state": "TX"}]
    assert load_records(rows)[0]["score"] > 0


# --- Fix 5: config loads regardless of working directory ---

def test_config_loads_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert get_config()["vehicles"]["makes"]


# --- Fix 6: state values are normalized before comparison ---

def test_padded_state_still_counts_as_preferred():
    _, reasons = score_listing(make_listing(state=" tx "))
    assert "preferred state" in reasons


def test_avoid_state_is_penalized():
    assert score_listing(make_listing(state="MN"))[0] < score_listing(make_listing())[0]


# --- Fix 7: output.top_n is honoured ---

def test_results_are_capped_at_configured_top_n():
    top_n = get_config()["output"]["top_n"]
    rows = [{"lot": str(i), "year": 2022, "make": "Toyota", "model": "Tundra",
             "mileage": 45000, "drive": "4WD", "condition": "Run & Drive",
             "primary_damage": "Hail", "state": "TX"} for i in range(top_n + 5)]
    assert len(load_records(rows)) == top_n
    assert len(load_records(rows, limit=0)) == top_n + 5   # 0 means no cap
