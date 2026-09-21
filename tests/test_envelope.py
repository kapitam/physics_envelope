"""Tests for clear-sky power envelope (no Plant_data required)."""

from __future__ import annotations

import pandas as pd
import pytest

from config_load import load_plant_config
from envelope import compute_clearsky_envelope


def _day_night_index(tz: str):
    return pd.date_range(
        "2025-04-15",
        "2025-04-16",
        freq="15min",
        tz=tz,
        inclusive="left",
    )


@pytest.mark.parametrize("plant_id", ["example"])
def test_clearsky_envelope_day_night(plant_id: str):
    config = load_plant_config(plant_id)
    timestamps = _day_night_index(config["location"]["timezone"])
    df = compute_clearsky_envelope(config, timestamps, 1.0, 25.0)

    assert list(df.columns) == ["ghi_clear", "p_clear_mw"]

    noon = df.loc[timestamps.hour == 12, "p_clear_mw"]
    assert noon.min() > 0

    night = df.loc[(timestamps.hour >= 0) & (timestamps.hour < 4), "p_clear_mw"]
    assert night.abs().max() <= 0.01
