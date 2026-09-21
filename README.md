# physics_envelope

Standalone clear-sky **physics envelope**: theoretical GHI (pvlib Ineichen) plus a deterministic `PlantModel` chain at CSI=1. This is **not a forecast** — it is an upper-bound potential given clear sky and fixed soiling/cell temperature.

## Git (nested repo inside Solar Forecast)

This folder is its own Git repository. From the parent `Solar Forecast` tree:

```bash
cd physics_envelope
git init
# add a root .gitignore (caches, venvs, .env) if it is not already there
git add .
git commit -m "Initial commit: standalone clear-sky physics envelope."
```

Do **not** `git add physics_envelope/` from the parent unless you intend a submodule (`.gitmodules`). Generated files under `out/` stay ignored via `out/.gitignore`.

## Setup

From this directory:

```bash
pip install -r requirements.txt
```

## Run envelope CLI

```bash
cd physics_envelope
python envelope.py --plant skp --start 2025-04-15 --end 2025-04-16 --out out/skp.csv --plot out/skp.png
```

Equivalent module form (with `PYTHONPATH=.` or from this directory):

```bash
PYTHONPATH=. python -m envelope --plant skp --start 2025-04-15 --end 2025-04-16 --out out/skp.csv --plot out/skp.png
```

Plant configs live in `config/plants/<id>.yaml` (sld, prr, stp, skp).

### Options

- `--freq` — output interval (default `15min`)
- `--soiling` — fixed soiling ratio, clean = 1.0 (default)
- `--cell-temp` — fixed cell temperature °C (default 25, STC)

### Notes

- If `module.temp_coefficient_pct_per_C` (gamma) is null in yaml, the CLI stubs **0.0 only when cell temp equals STC** (25 °C by default) for that run; yaml is not modified. Use a real gamma or match STC when running non-default cell temps.
- AC output is clipped at **`inverter_clip_at`** (inverter nameplate), not the PPA `dispatch_cap`.

## Tests

```bash
cd physics_envelope
PYTHONPATH=. pytest tests/test_envelope.py -q
```
