"""
Live win-probability + betting-edge engine.

We compute OUR OWN in-game win probability from the live state, independent of
any book, then compare it to the market's implied probability (de-vigged) to
find edges — where our model and the market disagree.

The model (standard sports-analytics form): a logistic on the expected final
margin. Expected final margin = current margin + (pregame spread projected over
the remaining fraction of game). Early on, the spread dominates (little has
happened); late, the current score dominates (little time to change it). We
blend them by time elapsed, then push through a logistic whose scale matches
NFL scoring variance (~13.5 pts SD over a full game, shrinking with time left).

  p_win(fav) = 1 / (1 + exp(-expected_margin / sd_remaining))

Market implied prob comes from de-vigging the two moneylines (removing the
book's hold) so it's a fair apples-to-apples probability.

  edge = model_prob - market_prob   (positive = value on that side)

Nothing here is betting advice — it's a model-vs-market divergence read.
"""
from __future__ import annotations

import math
from typing import Optional

# NFL full-game scoring SD ~13.5; used to scale the logistic.
_GAME_SD = 13.5
_SECONDS_TOTAL = 3600.0


def _seconds_left(quarter: int, clock: str) -> float:
    """Seconds remaining in regulation from quarter + 'MM:SS' clock."""
    if not quarter:
        return _SECONDS_TOTAL
    try:
        mm, ss = clock.split(":")
        q_left = int(mm) * 60 + int(ss)
    except Exception:
        q_left = 0.0
    quarters_after = max(0, 4 - quarter)     # full quarters still to come
    return min(_SECONDS_TOTAL, quarters_after * 900.0 + q_left)


def live_win_prob(fav_margin: float, fav_spread: float,
                  quarter: int, clock: str) -> float:
    """Model P(favorite wins) from live state.
    fav_margin  = favorite's current score - underdog's (can be negative)
    fav_spread  = pregame points the fav was favored by (positive)
    Returns 0-1."""
    sec_left = _seconds_left(quarter, clock)
    frac_left = sec_left / _SECONDS_TOTAL          # 1.0 pregame -> 0 at end
    frac_done = 1.0 - frac_left

    # expected final margin: current score + spread's expectation over what's left,
    # weighted so the spread prior fades as the game plays out.
    remaining_spread_pull = fav_spread * frac_left
    # blend current margin (weighted by time done) with the spread prior
    expected_final = fav_margin + remaining_spread_pull

    # uncertainty shrinks with less time left (SD scales with sqrt of time left)
    sd = max(3.0, _GAME_SD * math.sqrt(max(frac_left, 0.02)))
    p = 1.0 / (1.0 + math.exp(-expected_final / sd))
    return max(0.01, min(0.99, p))


def american_to_prob(ml: Optional[float]) -> Optional[float]:
    """Convert an American moneyline to implied probability (with vig)."""
    if ml is None:
        return None
    ml = float(ml)
    if ml < 0:
        return (-ml) / ((-ml) + 100.0)
    return 100.0 / (ml + 100.0)


def devig(p_fav_raw: Optional[float], p_dog_raw: Optional[float]
          ) -> Optional[float]:
    """Remove the book hold: normalize the two implied probs to sum to 1.
    Returns the fair P(favorite) or None if inputs missing."""
    if p_fav_raw is None or p_dog_raw is None:
        return None
    tot = p_fav_raw + p_dog_raw
    if tot <= 0:
        return None
    return p_fav_raw / tot


def spread_to_prob(fav_spread: float) -> float:
    """Fallback market prob from the spread alone when moneylines are absent:
    a spread of S ~ P(fav) via the same logistic at a full game."""
    return max(0.01, min(0.99, 1.0 / (1.0 + math.exp(-fav_spread / _GAME_SD))))


def edge(model_p_fav: float, market_p_fav: Optional[float]) -> tuple[float, str]:
    """Model minus market on the favorite. Returns (edge_on_fav, side_note).
    Positive edge_on_fav = value on the FAVORITE; negative = value on the DOG."""
    if market_p_fav is None:
        return 0.0, ""
    e = model_p_fav - market_p_fav
    if abs(e) < 0.05:
        return round(e, 3), ""
    if e > 0:
        return round(e, 3), "value on FAVORITE"
    return round(e, 3), "value on UNDERDOG"
