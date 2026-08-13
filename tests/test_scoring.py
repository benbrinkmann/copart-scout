from src.scout import Listing, score_listing


def test_hail_tundra_scores_well():
    v = Listing("1", 2022, "Toyota", "Tundra", 45000, "4WD", "Run & Drive", "Hail", "", "TX", 10000)
    score, reasons = score_listing(v)
    assert score >= 90
    assert "hail" in reasons


def test_flood_is_excluded():
    v = Listing("2", 2022, "Toyota", "Tundra", 45000, "4WD", "Run & Drive", "Flood", "", "TX", 5000)
    score, reasons = score_listing(v)
    assert score == 0
