"""Shared colour tokens for maps and the website.

Sequential encodings use a single hue, light -> dark (blue for the offshore
biomass field, orange for the second sequential context = stranded mass).
Risk tiers use the reserved status palette and always ship with a text label,
never colour alone.
"""

BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
             "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281",
             "#0d366b"]

ORANGE_RAMP = ["#fde3d4", "#fbd0b8", "#f8bb9a", "#f5a67c", "#f2905f",
               "#ef7b45", "#eb6834", "#d95926", "#bd4c20", "#a03f1a",
               "#833214", "#66260f"]

STATUS = {
    "minimal": "#0ca30c",
    "low": "#fab219",
    "moderate": "#ec835a",
    "high": "#d03b3b",
}

INK = {
    "surface": "#fcfcfb",
    "surface_dark": "#1a1a19",
    "primary": "#0b0b0b",
    "secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "land": "#e8e7e1",
    "land_edge": "#898781",
}
