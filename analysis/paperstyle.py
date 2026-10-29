"""
analysis/paperstyle.py

Shared matplotlib style for every paper figure: Helvetica, 8 pt text, fonts
embedded as TrueType (IEEE PDF eXpress), and figures drawn at the final IEEE
column width so LaTeX includes them at \\columnwidth without rescaling (text in
the PDF then prints at the size set here).

Usage (before creating any figure):
    from analysis.paperstyle import apply, COL_W
    apply()
    fig, ax = plt.subplots(figsize=(COL_W, 2.1))
"""
import matplotlib as mpl

COL_W = 3.45   # in; IEEEtran conference column is 3.5 in (252 pt)


def apply() -> None:
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
        "mathtext.fontset": "custom",
        "mathtext.rm": "Helvetica",
        "mathtext.it": "Helvetica:italic",
        "mathtext.bf": "Helvetica:bold",
        "mathtext.fallback": "stixsans",   # Greek etc. in a sans face
        "font.size": 8,
        "axes.labelsize": 8,
        "axes.titlesize": 8,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "legend.fontsize": 7.5,
        "legend.title_fontsize": 7.5,
        "legend.frameon": False,
        "legend.handlelength": 1.6,
        "legend.borderaxespad": 0.3,
        "lines.linewidth": 1.3,
        "lines.markersize": 4,
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "grid.linewidth": 0.4,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
    })
