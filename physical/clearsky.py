"""Theoretical clear-sky irradiance + the clear-sky index (CSI).

Step 3. Uses pvlib's ``Location.get_clearsky`` (Ineichen-Perez default,
swappable to Simplified Solis via ``model="simplified_solis"``).
CSI = measured_ghi / theoretical_ghi is the spine of the whole system.

CRITICAL: timestamps must be tz-aware in the SAME timezone as the plant
location before being passed to pvlib. A silent timezone bug here corrupts
every CSI value -- ``clearsky_irradiance`` raises if given a naive index.

Dynamic-AOD hook (FORECAST_SYSTEM_PLAN.md v2 Step 19+; default OFF): the
default Ineichen model uses a static, climatological monthly Linke
turbidity table, which silently absorbs Feb-Apr haze-season aerosol loading
into what looks like "clear sky" -- biasing the CSI target low during haze
without a real cloud. ``kasten96_linke_turbidity`` lets a live/forecast AOD
(e.g. from CAMS) replace that static table on a per-timestep basis. It is
off by default and only activates when an ``aod700`` series is supplied.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import pvlib


def _get_location(latitude: float, longitude: float, altitude: float = 0.0, tz: str = "UTC") -> pvlib.location.Location:
    return pvlib.location.Location(latitude, longitude, tz=tz, altitude=altitude)


def clearsky_irradiance(
    timestamps: pd.DatetimeIndex,
    latitude: float,
    longitude: float,
    altitude: float = 0.0,
    model: str = "ineichen",
) -> pd.DataFrame:
    """Theoretical clear-sky GHI/DNI/DHI for the plant location.

    Returns a DataFrame with columns ['ghi', 'dni', 'dhi'], indexed by
    ``timestamps``.
    """
    if not isinstance(timestamps, pd.DatetimeIndex):
        timestamps = pd.DatetimeIndex(timestamps)
    if timestamps.tz is None:
        raise ValueError("timestamps must be timezone-aware (silent tz bugs corrupt every CSI value)")
    loc = _get_location(latitude, longitude, altitude, tz=str(timestamps.tz))
    cs = loc.get_clearsky(timestamps, model=model)
    return cs[["ghi", "dni", "dhi"]]


def clear_sky_index(
    measured_ghi: pd.Series,
    timestamps: pd.DatetimeIndex | None,
    latitude: float,
    longitude: float,
    altitude: float = 0.0,
    model: str = "ineichen",
    min_clearsky_ghi: float = 20.0,
) -> pd.Series:
    """CSI = measured_ghi / theoretical_clearsky_ghi.

    Drops (returns NaN for) nighttime points where theoretical GHI is below
    ``min_clearsky_ghi`` W/m^2, to avoid division-by-near-zero blowups.
    """
    idx = timestamps if timestamps is not None else measured_ghi.index
    cs = clearsky_irradiance(idx, latitude, longitude, altitude, model=model)
    cs_ghi = cs["ghi"].reindex(measured_ghi.index)
    csi = measured_ghi / cs_ghi.where(cs_ghi >= min_clearsky_ghi)
    csi.name = "csi"
    return csi


def kasten96_linke_turbidity(
    timestamps: pd.DatetimeIndex,
    latitude: float,
    longitude: float,
    altitude: float,
    aod700: pd.Series,
    precipitable_water_cm: float | pd.Series = 2.0,
) -> pd.Series:
    """Linke turbidity from AOD700 + precipitable water (Kasten 1996 via pvlib).

    ``aod700`` should be issue-time-gated (only using the most recent
    available CAMS forecast at each timestamp) when used live -- this
    function itself is timestamp-agnostic and just does the physics.
    """
    loc = _get_location(latitude, longitude, altitude, tz=str(timestamps.tz))
    solpos = loc.get_solarposition(timestamps)
    airmass_rel = pvlib.atmosphere.get_relative_airmass(solpos["apparent_zenith"])
    pressure = pvlib.atmosphere.alt2pres(altitude)
    airmass_abs = pvlib.atmosphere.get_absolute_airmass(airmass_rel, pressure)
    pwat = precipitable_water_cm if isinstance(precipitable_water_cm, pd.Series) else pd.Series(
        precipitable_water_cm, index=timestamps
    )
    lt = pvlib.atmosphere.kasten96_lt(airmass_abs, pwat.reindex(timestamps).to_numpy(), aod700.reindex(timestamps).to_numpy())
    return pd.Series(lt, index=timestamps, name="linke_turbidity")


def clearsky_irradiance_dynamic_aod(
    timestamps: pd.DatetimeIndex,
    latitude: float,
    longitude: float,
    altitude: float = 0.0,
    aod700: pd.Series | None = None,
    precipitable_water_cm: float | pd.Series = 2.0,
    enabled: bool = False,
) -> pd.DataFrame:
    """Clear-sky irradiance using a per-timestep, AOD-derived Linke turbidity
    (``kasten96_lt``) instead of the static climatological table, when
    ``enabled`` and an ``aod700`` series are supplied. Falls back to the
    standard ``clearsky_irradiance`` (static table) otherwise -- this is the
    documented default/off state referenced throughout the build spec.
    """
    if not enabled or aod700 is None:
        return clearsky_irradiance(timestamps, latitude, longitude, altitude, model="ineichen")

    loc = _get_location(latitude, longitude, altitude, tz=str(timestamps.tz))
    solpos = loc.get_solarposition(timestamps)
    lt = kasten96_linke_turbidity(timestamps, latitude, longitude, altitude, aod700, precipitable_water_cm)
    airmass_rel = pvlib.atmosphere.get_relative_airmass(solpos["apparent_zenith"])
    pressure = pvlib.atmosphere.alt2pres(altitude)
    dni_extra = pvlib.irradiance.get_extra_radiation(timestamps)
    cs = pvlib.clearsky.ineichen(
        apparent_zenith=solpos["apparent_zenith"],
        airmass_absolute=pvlib.atmosphere.get_absolute_airmass(airmass_rel, pressure),
        linke_turbidity=lt,
        altitude=altitude,
        dni_extra=dni_extra,
    )
    return cs[["ghi", "dni", "dhi"]]
