"""
Resonance Sensor — measures how efficiently energy converts into sovereignty.
"""

import csv

from . import paths


def _ledger_file():
    # Prefer a legacy simulation CSV if present. Production ledger.csv
    # uses index,ts,kind,content,hash,prev_hash; non-numeric rows are skipped.
    data_dir = paths.data_dir()
    for name in ("sovereign_ledger.csv", "ledger.csv"):
        candidate = data_dir / name
        if candidate.exists():
            return candidate
    return data_dir / "sovereign_ledger.csv"


class ResonanceSensor:
    def analyze(self) -> tuple[float, str]:
        """
        Returns (score 0-100, status string).
        Higher score = more sovereignty gained per unit of energy spent.
        """
        ledger = _ledger_file()
        if not ledger.exists():
            return 50.0, "INITIALIZING"

        try:
            with open(ledger, "r", encoding="utf-8") as f:
                data = []
                for row in csv.reader(f):
                    if len(row) < 3:
                        continue
                    try:
                        data.append((row[0], float(row[1]), float(row[2])))
                    except ValueError:
                        continue

            if len(data) < 8:
                return 50.0, "STABILIZING"

            recent = data[-12:]
            sov_vals = [row[1] for row in recent]
            energy_vals = [row[2] for row in recent]

            dv = sov_vals[-1] - sov_vals[0]
            de = energy_vals[-1] - energy_vals[0]

            if de <= 0:
                # Cooling phase — treat as high harmony
                return 88.0, "COOLING_HARMONY"

            # Scaled efficiency
            resonance = (dv / de) * 800
            score = max(0.0, min(100.0, resonance))

            if score > 75:
                status = "HARMONIC"
            elif score > 40:
                status = "NEUTRAL"
            else:
                status = "DISSIPATING"

            return round(score, 1), status

        except Exception:
            return 50.0, "SENSORY_GLITCH"
