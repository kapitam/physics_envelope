"""Clear-sky potential AC power envelope (physics only, CSI=1). Not a forecast."""

from __future__ import annotations

import argparse
import copy
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from config_load import load_plant_config
from physical.clearsky import clearsky_irradiance
from physical.plant_model import PlantModel

_stc_gamma_stub_warned = False


def _config_for_stc_envelope(config: dict, cell_temp_c: float) -> dict:
    """Deep-copy plant config; stub gamma=0 at STC when yaml leaves it null."""
    global _stc_gamma_stub_warned
    copy_cfg = copy.deepcopy(config)
    module = copy_cfg.setdefault("module", {})
    stc_module = config.get("module") or {}
    stc = (stc_module.get("stc") or {}).get("cell_temp_c", 25)
    stc = stc if stc is not None else 25
    gamma = module.get("temp_coefficient_pct_per_C")
    if gamma is None and abs(cell_temp_c - stc) < 1e-9:
        module["temp_coefficient_pct_per_C"] = 0.0
        if not _stc_gamma_stub_warned:
            warnings.warn(
                "module.temp_coefficient_pct_per_C is null; using 0.0 for this "
                "envelope run only because cell temp equals STC (derate factor 1.0). "
                "Not persisted to yaml.",
                stacklevel=2,
            )
            _stc_gamma_stub_warned = True
    elif gamma is None:
        raise ValueError(
            "module.temp_coefficient_pct_per_C is required when cell temperature "
            f"({cell_temp_c} °C) differs from STC ({stc} °C). "
            "Set gamma in plant yaml or run with --cell-temp matching STC (default 25)."
        )
    return copy_cfg


def make_index(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    freq: str = "15min",
    tz: str = "Asia/Bangkok",
) -> pd.DatetimeIndex:
    """Build a tz-aware DatetimeIndex from start/end (localize naive inputs to ``tz``)."""
    idx = pd.date_range(start, end, freq=freq, tz=tz)
    return idx


def compute_clearsky_envelope(
    config: dict,
    timestamps: pd.DatetimeIndex,
    soiling_ratio: float,
    cell_temp_c: float,
) -> pd.DataFrame:
    """Clear-sky GHI + potential AC MW (CSI=1, fixed soiling/temp) — not a forecast."""
    loc = config["location"]
    cs = clearsky_irradiance(
        timestamps,
        loc["latitude"],
        loc["longitude"],
        loc.get("altitude_m") or 0,
    )
    model = PlantModel(_config_for_stc_envelope(config, cell_temp_c))
    pv_temp = pd.Series(float(cell_temp_c), index=timestamps)
    p = model.predict_power(
        irradiance=cs[["ghi", "dni", "dhi"]],
        pv_temp=pv_temp,
        soiling_ratio=float(soiling_ratio),
    )
    out = pd.DataFrame(
        {
            "ghi_clear": cs["ghi"],
            "p_clear_mw": p.reindex(timestamps).to_numpy(),
        },
        index=timestamps,
    )
    return out


def plot_envelope(
    df: pd.DataFrame,
    path: str | Path,
    plant_id: str | None = None,
) -> None:
    """Plot clear-sky potential MW vs time."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    label = (plant_id or "UNKNOWN").upper()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(df.index, df["p_clear_mw"], label="Clear-sky potential (MW)", color="C1")
    ax.set_xlabel("Time")
    ax.set_ylabel("Power (MW)")
    ax.set_title(f"{label} clear-sky potential (physics envelope) — not a forecast")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clear-sky potential AC power envelope (config/plants/<id>.yaml)",
    )
    parser.add_argument(
        "--plant",
        default="sld",
        dest="plant_id",
        help="Plant config id: config/plants/<id>.yaml (sld, prr, stp, skp, or any new file)",
    )
    parser.add_argument("--start", required=True, help="Range start (local plant tz if naive)")
    parser.add_argument("--end", required=True, help="Range end (pandas date_range semantics)")
    parser.add_argument("--freq", default="15min", help="Output interval (default: 15min)")
    parser.add_argument("--soiling", type=float, default=1.0, help="Fixed soiling ratio (default: 1.0)")
    parser.add_argument(
        "--cell-temp",
        type=float,
        default=25.0,
        dest="cell_temp",
        help="Cell temp °C (default: 25)",
    )
    parser.add_argument("--out", required=True, help="Output CSV path")
    parser.add_argument("--plot", default=None, help="Optional PNG plot path")
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    config = load_plant_config(args.plant_id)
    tz = config["location"]["timezone"]
    timestamps = make_index(args.start, args.end, freq=args.freq, tz=tz)
    try:
        df = compute_clearsky_envelope(config, timestamps, args.soiling, args.cell_temp)
    except ValueError as exc:
        raise ValueError(f"Plant '{args.plant_id}' config incomplete for PlantModel: {exc}") from exc

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.index.name = "timestamp"
    df.to_csv(out_path)

    if args.plot:
        plant_id = config.get("plant", {}).get("id") or args.plant_id
        plot_envelope(df, args.plot, plant_id=plant_id)


if __name__ == "__main__":
    main()
