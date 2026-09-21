"""Load plant YAML from config/plants/ (standalone; no simple_forecasting)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def load_plant_config(plant_id: str) -> dict:
    """Read ``config/plants/<plant_id>.yaml`` (id lowercased)."""
    path = ROOT / "config" / "plants" / f"{plant_id.lower()}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Plant config not found: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
