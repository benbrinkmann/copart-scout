# copart-scout
Agent to scan copart auctions for good deals.

## Layout

- `config.yaml` — the buy box: vehicles, damage rules, geography, budget, scoring weights.
- `src/scout.py` — scoring. Takes normalized listing records and ranks them.
- `tests/` — run with `pytest` from any directory.

The data collector is deliberately not part of this module. Keeping acquisition
separate from scoring means the buying logic stays testable while the way
listings are obtained changes.

## Scoring

Every point value is derived from `scoring.weights` in `config.yaml`. Each
category awards a fraction of its configured weight, so a listing that is
perfect on every axis scores exactly the sum of the weights (100 as shipped).
Editing a weight changes the scoring with no code change.

Hard exclusions (wrong make or model, year outside the range, mileage over the
cap, excluded damage types) return a score of 0 regardless of weights.

## Known limitations

- **The economics term is provisional.** It awards points when the current bid
  is at or below `budget.max_all_in`. Current bid is a weak signal early in an
  auction, and `max_all_in` should net out buyer fees, gate fees, and transport
  before the comparison is meaningful. Treat the economics points as a
  placeholder until real landed-cost figures are wired in.
- `budget.target_discount_pct` is defined but not yet used; it is reserved for
  the landed-cost work above.
- Unknown mileage is not disqualifying. Such a listing simply earns no mileage
  points. Copart odometer brands ("NOT ACTUAL", "EXEMPT") are not yet modeled.
- Listings are not de-duplicated by lot number across runs.
