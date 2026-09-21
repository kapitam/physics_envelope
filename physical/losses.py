"""Separately auditable loss functions. Step 4.

Each loss is a small pure function so it can be tested in isolation and
shown to stakeholders independently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def apply_soiling(dc_power, soiling_ratio):
    """Multiplicative soiling loss. ``soiling_ratio`` == 1.0 means clean.

    NOTE: SLD's raw sensor ("Soiling Ratio" WMS file, canonical
    ``soiling_loss_pct``) reports a 0-100 LOSS PERCENT (median 0 = clean),
    not a clean=1.0 multiplier as BUILD_SPEC's role table assumed. Convert
    with ``soiling_ratio_from_loss_pct`` before calling this function.
    """
    return dc_power * soiling_ratio


def soiling_ratio_from_loss_pct(soiling_loss_pct):
    """Convert the raw 0-100 soiling LOSS percent reading to a clean=1.0 multiplier."""
    pct = np.clip(np.asarray(soiling_loss_pct, dtype=float), 0.0, 100.0)
    ratio = 1.0 - pct / 100.0
    if isinstance(soiling_loss_pct, pd.Series):
        return pd.Series(ratio, index=soiling_loss_pct.index, name="soiling_ratio")
    return ratio


def apply_temperature_derating(dc_power, cell_temp_c, stc_cell_temp_c, temp_coeff_pct_per_c):
    """Linear thermal derating: P = P_stc * (1 + coeff/100 * (T_cell - T_stc))."""
    factor = 1.0 + (temp_coeff_pct_per_c / 100.0) * (np.asarray(cell_temp_c, dtype=float) - stc_cell_temp_c)
    factor = np.clip(factor, 0.0, None)  # never let derating go negative (extreme cold-side floor)
    result = dc_power * factor
    return result


def apply_inverter_clip(ac_power, ac_capacity):
    """Hard cap at ``ac_capacity`` (and floor at 0 -- no negative AC generation)."""
    if isinstance(ac_power, pd.Series):
        return ac_power.clip(lower=0.0, upper=ac_capacity)
    return np.clip(ac_power, 0.0, ac_capacity)
