"""
Pure-stdlib SVG visualizer for the Sovereign growth curve.
No matplotlib, no pandas — just the standard library.
"""

import csv
from pathlib import Path
from . import paths

# Legacy filenames from the early simulation visualizer. Prefer ledger.csv.
def _ledger_candidates():
    d = paths.data_dir()
    return (d / "ledger.csv", d / "sovereign_ledger.csv")

def _block_candidates():
    d = paths.data_dir()
    return (d / "chain.jsonl", d / "sovereign_blocks.csv")
OUTPUT = Path("sovereign_plot.svg")


def _collect_data():
    all_data = []
    # Historical simulation CSVs, then current ledger.csv if numeric columns remain.
    # Production ledger schema is index,ts,kind,content,hash,prev_hash; those rows are skipped.
    for candidate in (*_block_candidates(), *_ledger_candidates()):
        if not candidate.exists() or candidate.suffix == ".jsonl":
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if len(row) >= 3:
                        try:
                            all_data.append((row[0], float(row[1]), float(row[2])))
                        except ValueError:
                            continue
        except Exception:
            pass
    return all_data


def generate_svg(filename: str | Path = OUTPUT) -> bool:
    data = _collect_data()
    if not data:
        print("[!] No data found in blocks or ledger. Run the engine first.")
        return False

    width, height, padding = 1100, 620, 70
    sov_vals = [d[1] for d in data]
    energy_vals = [d[2] for d in data]

    max_val = max(max(sov_vals), max(energy_vals), 1)
    min_val = min(min(sov_vals), min(energy_vals))
    v_range = max_val - min_val if max_val != min_val else 1

    def sx(i):
        return padding + (i / (len(data) - 1 if len(data) > 1 else 1)) * (width - 2 * padding)

    def sy(v):
        return height - padding - ((v - min_val) / v_range) * (height - 2 * padding)

    lines = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" style="background:#050508;">',
        f'<text x="{width/2}" y="36" fill="#e0e0e0" font-family="monospace" font-size="22" text-anchor="middle">'
        f'SOVEREIGN ENGINE 2 — PERSISTENT GROWTH CURVE</text>',
        # Axes
        f'<line x1="{padding}" y1="{height-padding}" x2="{width-padding}" y2="{height-padding}" stroke="#222" stroke-width="1.5"/>',
        f'<line x1="{padding}" y1="{padding}" x2="{padding}" y2="{height-padding}" stroke="#222" stroke-width="1.5"/>',
    ]

    # Energy (cyan, thinner)
    e_pts = " ".join(f"{sx(i)},{sy(v)}" for i, v in enumerate(energy_vals))
    lines.append(f'<polyline points="{e_pts}" fill="none" stroke="#00e5ff" stroke-width="1.8" opacity="0.75"/>')

    # Sovereignty (lime, bold)
    s_pts = " ".join(f"{sx(i)},{sy(v)}" for i, v in enumerate(sov_vals))
    lines.append(f'<polyline points="{s_pts}" fill="none" stroke="#76ff03" stroke-width="3"/>')

    # Genesis marker
    lines.append(f'<circle cx="{sx(0)}" cy="{sy(sov_vals[0])}" r="6" fill="#ffd700"/>')
    lines.append(
        f'<text x="{sx(0)+12}" y="{sy(sov_vals[0])+4}" fill="#ffd700" '
        f'font-family="monospace" font-size="12">GENESIS</text>'
    )

    # Annotations
    current = sov_vals[-1]
    delta = current - sov_vals[0]
    lines.append(
        f'<text x="85" y="85" fill="#76ff03" font-family="monospace" font-size="17">'
        f'Persistent Sovereignty: {current:.2f}</text>'
    )
    lines.append(
        f'<text x="85" y="110" fill="#a5d6a7" font-family="monospace" font-size="13">'
        f'Session / History Delta: +{delta:.2f}</text>'
    )
    lines.append(
        f'<text x="85" y="140" fill="#00e5ff" font-family="monospace" font-size="17">'
        f'System Energy: {energy_vals[-1]:.2f}</text>'
    )
    lines.append(
        f'<text x="85" y="165" fill="#90a4ae" font-family="monospace" font-size="12">'
        f'Points: {len(data)}  |  Lime = Sovereignty  |  Cyan = Energy</text>'
    )

    lines.append("</svg>")

    with open(filename, "w") as f:
        f.write("\n".join(lines))

    print(f"[OK] Visualization written → {filename}")
    return True


if __name__ == "__main__":
    generate_svg()
