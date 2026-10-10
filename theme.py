"""Farbschema + gemeinsame Konstanten fuer SysMon."""

THEME = {
    "bg":        "#0a0e13",
    "panel":     "#111821",
    "panel_hi":  "#16202b",
    "border":    "#1e2b38",
    "grid":      "#1a2632",
    "text":      "#cfdcea",
    "text_hi":   "#ffffff",
    "dim":       "#65788b",

    # Metrik-Farben (an den Task-Manager angelehnt)
    "cpu":       "#3fb8e6",
    "cpu_fill":  "#123c50",
    "mem":       "#5b7fe8",
    "mem_fill":  "#16234a",
    "disk":      "#4fc06a",
    "disk_fill":  "#123526",
    "net":       "#e8548a",
    "net_fill":  "#3d1526",
    "gpu":       "#b768dc",
    "gpu_fill":  "#2d1740",
    "vram":      "#7a5cd6",
    "vram_fill": "#221a44",

    "ok":        "#4fc06a",
    "warn":      "#e8b33a",
    "crit":      "#e85454",
}

MONO = ("Cascadia Mono", "Consolas", "Courier New")


def font(size=10, bold=False, mono=True):
    fam = "Consolas" if mono else "Segoe UI"
    return (fam, size, "bold" if bold else "normal")


def heat(pct):
    """Farbverlauf gruen -> gelb -> rot je nach Auslastung."""
    if pct < 60:
        return THEME["ok"]
    if pct < 85:
        return THEME["warn"]
    return THEME["crit"]


def human_bytes(n, unit="B"):
    for suf in ("", "K", "M", "G", "T"):
        if abs(n) < 1024:
            return f"{n:,.1f} {suf}{unit}".replace(",", ".")
        n /= 1024.0
    return f"{n:,.1f} P{unit}"


def mbps(bytes_per_s):
    return bytes_per_s / (1024.0 * 1024.0)
