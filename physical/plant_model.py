"""Deterministic irradiance -> AC power chain. Phase 1 core.

Step 4. Built on pvlib (solar geometry is never reimplemented). Two
irradiance-input paths, both supported (BUILD_SPEC explicitly allows using
measured POA directly for the plant model; transposition is only needed for
forecasting mode where only GHI/CSI is available):

  A) ``irradiance`` is a Series -> treated as MEASURED POA (front). Preferred
     path for Phase 1 validation (measured-input playback) and for the
     Intraday Engine composition, since SLD has a real POA sensor.
  B) ``irradiance`` is a DataFrame with a ``ghi`` column (optionally
     ``dni``/``dhi``) -> transposed to POA via pvlib using the configured
     tilt/azimuth (Erbs decomposition fills dni/dhi from ghi if absent).
     This is the forecasting-mode path (predicted future CSI x clearsky GHI
     has no POA equivalent yet).

Bifacial handling (SLD only): if ``poa_rear`` is supplied and the plant is
configured bifacial, the rear contribution is added to POA before the DC
conversion: ``poa_effective = poa_front + rear_gain_factor * poa_rear``
(an additive term with a config-documented coefficient, per build
instructions -- not a full bifaciality-model guess).

Chain:
    1. POA (front [+ rear]) from measured sensors or GHI transposition.
    2. Cell temperature: PV temp sensor (preferred, ``thermal_model:
       use_pv_temp_sensor``) or Faiman ambient+wind+POA estimate otherwise.
    3. DC power (STC-scaled from effective POA).
    4. Losses: soiling (measured ratio), temperature derating (physical.losses).
    5. AC + inverter/dispatch clipping.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pvlib

from physical import losses as losses_mod


REQUIRED_FIELDS = [
    ("location", "latitude"), ("location", "longitude"), ("location", "timezone"),
    ("nameplate", "dc_capacity"), ("nameplate", "inverter_clip_at"),
    ("array", "tilt_deg"), ("array", "azimuth_deg"),
    ("module", "temp_coefficient_pct_per_C"),
]


class PlantModel:
    """Deterministic plant model: measured inputs -> expected AC power.

    Must work *perfectly given known irradiance* before Phase 2+ matters.
    It is the validation anchor; a low single-digit % MAPE on measured-input
    playback (clear periods) is the Phase 1 gate (Step 5).
    """

    def __init__(self, config: dict):
        missing = []
        for section, field in REQUIRED_FIELDS:
            val = config.get(section, {}).get(field) if isinstance(config.get(section), dict) else None
            if val is None:
                missing.append(f"{section}.{field}")
        if missing:
            raise ValueError(
                f"PlantModel refuses to build against an incomplete config; missing: {missing}. "
                "This is intentional -- see config/plants/<id>.yaml."
            )

        self.config = config
        loc = config["location"]
        self.latitude = float(loc["latitude"])
        self.longitude = float(loc["longitude"])
        self.altitude = float(loc.get("altitude_m") or 0.0)
        self.tz = loc["timezone"]

        nameplate = config["nameplate"]
        self.dc_capacity = float(nameplate["dc_capacity"])
        # Effective STC-equivalent DC capacity, fitted from measured clear-sky
        # output (see data/geo_estimation-adjacent calibration in reports/step04.md
        # and scripts/calibrate_dc_capacity.py). SLD's "PV Theoretical Active
        # Power" nameplate reads as an AC/inverter-fleet rating, not true DC
        # watt-peak: regression of measured AC power against POA-scaled output
        # on clear, uncurtailed minutes implies an effective DC-equivalent
        # capacity ~1.2x the nameplate (consistent with a bifacial-oversized
        # array, ILR>1). Falls back to the plain nameplate if no fitted value
        # is configured, so other plants/configs work unchanged.
        self.dc_capacity_effective = float(nameplate.get("dc_capacity_effective_fitted") or self.dc_capacity)
        self.ac_clip = float(nameplate["inverter_clip_at"])

        array = config["array"]
        self.tilt = float(array["tilt_deg"])
        self.azimuth = float(array["azimuth_deg"])

        module = config["module"]
        self.temp_coeff = float(module["temp_coefficient_pct_per_C"])
        self.stc_cell_temp = float(module.get("stc", {}).get("cell_temp_c", 25))
        self.thermal_model = module.get("thermal_model") or "faiman"

        bifacial = config.get("bifacial") or {}
        self.is_bifacial = bool(bifacial.get("is_bifacial"))
        self.rear_gain_factor = float(bifacial.get("rear_gain_factor") or 0.0)

        self.location = pvlib.location.Location(self.latitude, self.longitude, tz=self.tz, altitude=self.altitude)

    # -- irradiance handling --------------------------------------------------
    def _poa_from_ghi(self, ghi: pd.Series, dni: pd.Series | None, dhi: pd.Series | None) -> pd.Series:
        idx = ghi.index
        if idx.tz is None:
            raise ValueError("irradiance timestamps must be timezone-aware")
        solpos = self.location.get_solarposition(idx)
        if dni is None or dhi is None:
            erbs = pvlib.irradiance.erbs(ghi.to_numpy(), solpos["zenith"].to_numpy(), idx)
            dni = pd.Series(erbs["dni"].to_numpy(), index=idx) if dni is None else dni
            dhi = pd.Series(erbs["dhi"].to_numpy(), index=idx) if dhi is None else dhi
        total = pvlib.irradiance.get_total_irradiance(
            surface_tilt=self.tilt, surface_azimuth=self.azimuth,
            solar_zenith=solpos["apparent_zenith"].to_numpy(),
            solar_azimuth=solpos["azimuth"].to_numpy(),
            dni=dni.to_numpy(), ghi=ghi.to_numpy(), dhi=dhi.to_numpy(),
            model="isotropic",
        )
        return pd.Series(np.asarray(total["poa_global"]), index=idx, name="poa")

    def _effective_poa(self, irradiance, poa_rear=None) -> pd.Series:
        if isinstance(irradiance, pd.DataFrame):
            poa_front = self._poa_from_ghi(
                irradiance["ghi"], irradiance.get("dni"), irradiance.get("dhi")
            )
        else:
            poa_front = irradiance.copy()
            poa_front.name = "poa"

        poa_front = poa_front.clip(lower=0.0)
        if self.is_bifacial and poa_rear is not None:
            rear = poa_rear.reindex(poa_front.index).clip(lower=0.0).fillna(0.0)
            return poa_front + self.rear_gain_factor * rear
        return poa_front

    def _cell_temp(self, poa_eff: pd.Series, pv_temp=None, ambient_temp=None, wind_speed=None) -> pd.Series:
        if pv_temp is not None:
            return pv_temp.reindex(poa_eff.index)
        if ambient_temp is None:
            raise ValueError("Need either pv_temp or ambient_temp (+ wind_speed) to estimate cell temperature")
        wind = wind_speed.reindex(poa_eff.index) if wind_speed is not None else pd.Series(1.0, index=poa_eff.index)
        return pvlib.temperature.faiman(poa_eff, ambient_temp.reindex(poa_eff.index), wind)

    # -- main entry point ------------------------------------------------------
    def predict_power(
        self,
        irradiance,
        pv_temp=None,
        ambient_temp=None,
        wind_speed=None,
        soiling_ratio=1.0,
        timestamps=None,
        poa_rear=None,
    ) -> pd.Series:
        """Return AC power Series in the units declared in plant_config.units.power (MW here)."""
        poa_eff = self._effective_poa(irradiance, poa_rear=poa_rear)
        cell_temp = self._cell_temp(poa_eff, pv_temp=pv_temp, ambient_temp=ambient_temp, wind_speed=wind_speed)

        dc_stc = self.dc_capacity_effective * (poa_eff / 1000.0)
        dc_derated = losses_mod.apply_temperature_derating(
            dc_stc, cell_temp, self.stc_cell_temp, self.temp_coeff
        )

        if isinstance(soiling_ratio, (int, float)):
            soiling_ratio = pd.Series(soiling_ratio, index=poa_eff.index)
        else:
            soiling_ratio = soiling_ratio.reindex(poa_eff.index).fillna(1.0)
        dc_final = losses_mod.apply_soiling(dc_derated, soiling_ratio)

        ac_power = losses_mod.apply_inverter_clip(dc_final, self.ac_clip)
        ac_power.name = "ac_power_pred"
        return ac_power
