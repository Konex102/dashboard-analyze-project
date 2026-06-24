from __future__ import annotations

from cProfile import label
import io
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import matplotlib
matplotlib.use("Agg")          # non-interactive backend — required for servers
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
import numpy as np

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    Table,
    TableStyle,
    HRFlowable,
    KeepTogether,
    PageBreak,
)
from reportlab.pdfgen.canvas import Canvas as _RLCanvas

# ─── MARGIN SETTINGS ─────────────────────────────────────────────────────────
PW, PH = A4
ML = MR = 2 * cm
MT = 3 * cm
MB = 2 * cm
UW = PW - ML - MR  # Usable Width

# ─── COLOR SETTINGS ──────────────────────────────────────────────────────────
def _hx(h: str) -> colors.HexColor:
    return colors.HexColor(h)

BLUE       = _hx("#2563EB")
BLUE_DARK  = _hx("#1E3A8A")
BLUE_SOFT  = _hx("#EFF6FF")
BLUE_BORD  = _hx("#BFDBFE")
RED        = _hx("#DC2626")
RED_SOFT   = _hx("#FEE2E2")
GREEN      = _hx("#059669")
GREEN_SOFT = _hx("#ECFDF5")
NAVY       = _hx("#D97706")
NAVY_SOFT  = _hx("#FEF3C7")
GREY_LT    = _hx("#F1F5F9")
GREY_MID   = _hx("#6B7280")
GREY_DK    = _hx("#374151")
DARK       = _hx("#111827")
WHITE      = colors.white
BORDER     = _hx("#E5E7EB")
ALT_ROW    = _hx("#F8FAFF")

# ─── MATPLOTLIB CHART PALETTE ────────────────────────────────────────────────
_PAL = ["#2563EB", "#DC2626", "#059669", "#D97706",
        "#7C3AED", "#0891B2", "#DB2777", "#65A30D"]

_MPL_RC = {
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.alpha":        0.25,
    "grid.linestyle":    "--",
    "grid.linewidth":    0.6,
    "font.size":         8,
    "axes.labelsize":    8,
    "xtick.labelsize":   7,
    "ytick.labelsize":   7,
    "figure.facecolor":  "white",
    "axes.facecolor":    "white",
}

_DEFAULT_DPI = 300  # HD resolution (from 220)
_PX_TO_PX = _DEFAULT_DPI/72

def _px_to_in(px: int, dpi: int = 300) -> float:
    return px / dpi

# Matplotlib function image builder

def _build_trend_png(
    df: pd.DataFrame,
    x_col: str,
    y_cols: list[str],
    title: str,
    dt_series: pd.Series | None = None,
    w: int = 900,
    h: int = 300,
) -> bytes | None:
    if not x_col or not y_cols:
        return None
    try:
        d = df.copy()
        d["_x"] = dt_series if dt_series is not None else d[x_col]
        d = d.dropna(subset=["_x"]).sort_values("_x")

        with plt.rc_context(_MPL_RC):
            fig, ax = plt.subplots(figsize=(_px_to_in(w), _px_to_in(h)), dpi=300)
            for i, col in enumerate(y_cols):
                ax.plot(d["_x"], d[col], label=col,
                        color=_PAL[i % len(_PAL)], linewidth=1.5)
            ax.set_title(title, fontsize=10, fontweight="bold", pad=6)
            if len(y_cols) > 1:
                ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22),
                          ncol=min(4, len(y_cols)), fontsize=7, frameon=False)
            else:
                ax.set_ylabel(y_cols[0], fontsize=8)
            fig.autofmt_xdate(rotation=30, ha="right")
            plt.tight_layout()
            return _fig_bytes(fig)
    except Exception as e:
        import traceback
        print(f"Error in _build_trend_png: {e}")
        traceback.print_exc()
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def _build_bar_png(
    df: pd.DataFrame,
    x_col: str,
    y_cols: list[str],
    title: str,
    w: int = 900,
    h: int = 380,
) -> bytes | None:
    if not x_col or not y_cols:
        return None
    try:
        x_vals = df[x_col].astype(str)
        n_groups = len(x_vals)
        n_bars = len(y_cols)
        x_idx = np.arange(n_groups)
        bar_w = 0.7 / n_bars

        with plt.rc_context(_MPL_RC):
            fig, ax = plt.subplots(figsize=(_px_to_in(w), _px_to_in(h)), dpi=300)
            for i, col in enumerate(y_cols):
                offset = (i - n_bars / 2 + 0.5) * bar_w
                ax.bar(x_idx + offset, df[col], width=bar_w * 0.9,
                       label=col, color=_PAL[i % len(_PAL)])
            ax.set_title(title, fontsize=10, fontweight="bold", pad=6)
            ax.set_xticks(x_idx)
            ax.set_xticklabels(x_vals, rotation=30, ha="right", fontsize=7)
            if n_bars > 1:
                ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.25),
                          ncol=min(4, n_bars), fontsize=7, frameon=False)
            plt.tight_layout()
            return _fig_bytes(fig)
    except Exception as e:
        import traceback
        print(f"Error in _build_bar_png: {e}")
        traceback.print_exc()
        try:
            plt.close("all")
        except Exception:
            pass
        return None

def _build_donut_png(
    labels: list[str],
    values: list[float],
    colors_: list[str],
    title: str = "",
    w: int = 1200,
    h: int = 600,
) -> bytes | None:
    pairs = [(l, v, c) for l, v, c in zip(labels, values, colors_) if v > 0]
    if not pairs:
        print(f"[DEBUG] _build_donut_png: No positive values to display. Values: {values}")
        return None

    try:
        lbs   = [p[0] for p in pairs]
        vals  = [p[1] for p in pairs]
        clrs  = [p[2] for p in pairs]

        _RC = {**_MPL_RC, "axes.grid": False, "figure.facecolor": "white"}
        with plt.rc_context(_RC):
            # Use consistent figure size for all donut charts - 9.0 inches wide, 4.5 inches tall
            fig = plt.figure(figsize=(10.0, 5.5), dpi=150, facecolor="white")
        
            ax = fig.add_axes([0.10, 0.25, 0.80, 0.60])

            # Create pie chart with only non-zero values
            wedges, texts, autotexts = ax.pie(
                vals,
                labels=None,
                colors=clrs,
                autopct=lambda p: f"{p:.1f}%",
                startangle=90,
                wedgeprops=dict(width=0.55, edgecolor="white", linewidth=2.5),
                pctdistance=0.76,
            )
            for autotext in autotexts:
                autotext.set_fontsize(13)
                autotext.set_color("white")
                autotext.set_fontweight("bold")

            if title:
                fig.text(
                    0.5, 0.96, title,
                    ha="center", va="top",
                    fontsize=12, fontweight="bold",
                    color="#111827"
                )
            
            # Create legend with label and count - always use 3 columns for consistency
            legend_labels = [f"{l}  {v:,.0f}" for l, v in zip(lbs, vals)]
            ax.legend(
                wedges, legend_labels,
                loc="lower center", 
                bbox_to_anchor=(0.5, -0.20),
                ncol=2,
                fontsize=10, 
                frameon=False,
            )

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", pad_inches=0.1)
            plt.close(fig)
            buf.seek(0)
            png_bytes = buf.read()
            print(f"[DEBUG] _build_donut_png: Successfully generated chart (9.0x4.5in, {len(pairs)} categories)")
            return png_bytes
        
    except Exception as e:
        import traceback
        print(f"[ERROR] _build_donut_png: Exception occurred - {e}")
        traceback.print_exc()
        try:
            plt.close("all")
        except Exception:
            pass
        return None

def _build_spv_bar_png(
    df: pd.DataFrame,
    sp_col: str,
    pv_col: str,
    label: str,
    w: int = 900,
    h: int = 380,
) -> bytes | None:
    try:
        d = df.copy()
        d["_sp"] = pd.to_numeric(d[sp_col], errors="coerce")
        d["_pv"] = pd.to_numeric(d[pv_col], errors="coerce")
        d = d.dropna(subset=["_sp", "_pv"])
        if d.empty:
            return None

        total  = len(d)
        normal = int((d["_pv"] == d["_sp"]).sum())
        lower  = int((d["_pv"] <  d["_sp"]).sum())
        higher = int((d["_pv"] >  d["_sp"]).sum())

        cats   = ["Dalam SP", "Lebih Rendah", "Lebih Tinggi"]
        counts = [normal, lower, higher]
        clrs   = ["#3B82F6", "#F59E0B", "#EF4444"]

        with plt.rc_context(_MPL_RC):
            fig, ax = plt.subplots(figsize=(_px_to_in(w), _px_to_in(h)), dpi=300)
            bars = ax.bar(cats, counts, color=clrs, width=0.5)
            for bar, v in zip(bars, counts):
                pct = v / total * 100 if total else 0
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + total * 0.005,
                        f"{v:,}\n({pct:.1f}%)",
                        ha="center", va="bottom", fontsize=7)
            ax.set_title(f"SP vs PV – {label}", fontsize=10,
                         fontweight="bold", pad=6)
            ax.set_ylabel("Records", fontsize=8)
            ax.yaxis.set_major_formatter(
                mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
            plt.tight_layout()
            return _fig_bytes(fig)
    except Exception as e:
        import traceback
        print(f"Error in _build_spv_bar_png: {e}")
        traceback.print_exc()
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def _build_user_plot_png(
    df: pd.DataFrame,
    cfg: dict,
    dt_series: pd.Series | None,
    w: int = 680,
    h: int = 290,
) -> bytes | None:
    """Build a chart from a single plot_config dict and return PNG bytes."""
    ct     = cfg.get("chart_type", "line")
    x_col  = cfg.get("x")
    y_cols = cfg.get("y") or []
    if isinstance(y_cols, str):
        y_cols = [y_cols]
    title = cfg.get("title") or f"{ct.title()} Chart"

    if ct == "line":
        return _build_trend_png(df, x_col, y_cols, title, dt_series, w=w, h=h)
    if ct == "bar":
        return _build_bar_png(df, x_col, y_cols, title, w=w, h=h)
    return None


# ReportLab Image helper

def _fig_bytes(fig,dpi : int = 220) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight",dpi=dpi)
    plt.close(fig)
    buf.seek(0)
    return buf.read()

def _png_to_rl_image(
    png: bytes | None,
    width_pt: float,
    height_pt: float,
) -> Image | None:
    if not png:
        print("[DEBUG] _png_to_rl_image: No PNG data provided")
        return None
    try:
        img = Image(io.BytesIO(png), width=width_pt, height=height_pt)
        print(f"[DEBUG] _png_to_rl_image: Successfully converted {len(png)} bytes to ReportLab Image ({width_pt:.1f}pt x {height_pt:.1f}pt)")
        return img
    except Exception as e:
        import traceback
        print(f"[ERROR] _png_to_rl_image: Failed to convert PNG - {e}")
        traceback.print_exc()
        return None


# PARAGRAPH STYLE

def _ps(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName   = kw.pop("fontName",   "Helvetica-Bold"),
        fontSize   = kw.pop("fontSize",   9),
        textColor  = kw.pop("textColor",  DARK),
        leading    = kw.pop("leading",    13),
        spaceAfter = kw.pop("spaceAfter", 0),
        **kw,
    )

S = {
    "doc_title"  : _ps("doc_title", fontName="Helvetica-Bold", fontSize=30,
                        textColor=DARK, leading=34, leftIndent=0),
    "doc_sub"    : _ps("doc_sub",   fontSize=12, textColor=GREY_MID, leading=15),
    "section"    : _ps("section",   fontName="Helvetica-Bold", fontSize=8,
                        textColor=WHITE, leading=10, leftIndent=6),
    "body"       : _ps("body",      fontSize=8.5, leading=12, spaceAfter=3),
    "muted"      : _ps("muted",     fontName="Helvetica-Oblique", fontSize=8,
                        textColor=GREY_MID, leading=11, spaceAfter=3),
    "kv_key"     : _ps("kv_key",    fontName="Helvetica-Bold", fontSize=8,
                        textColor=GREY_MID, leading=11, alignment=TA_RIGHT),
    "kv_val"     : _ps("kv_val",    fontSize=8, textColor=DARK, leading=11),
    "th"         : _ps("th",        fontName="Helvetica-Bold", fontSize=7.5,
                        textColor=BLUE, leading=10, alignment=TA_CENTER),
    "td"         : _ps("td",        fontSize=7.5, textColor=GREY_DK,
                        leading=10, alignment=TA_CENTER),
    "td_l"       : _ps("td_l",      fontName="Helvetica-Bold", fontSize=7.5,
                        textColor=DARK, leading=10, alignment=TA_LEFT),
    "caption"    : _ps("caption",   fontName="Helvetica-Oblique", fontSize=7.5,
                        textColor=GREY_MID, leading=10, alignment=TA_CENTER,
                        spaceAfter=4),
    "metric_v"   : _ps("metric_v",  fontName="Helvetica-Bold", fontSize=15,
                        textColor=BLUE, leading=17, alignment=TA_CENTER),
    "metric_v_r" : _ps("metric_v_r", fontName="Helvetica-Bold", fontSize=15,
                        textColor=RED, leading=17, alignment=TA_CENTER),
    "metric_v_g" : _ps("metric_v_g", fontName="Helvetica-Bold", fontSize=15,
                        textColor=GREEN, leading=17, alignment=TA_CENTER),
    "metric_v_a" : _ps("metric_v_a", fontName="Helvetica-Bold", fontSize=15,
                        textColor=NAVY, leading=17, alignment=TA_CENTER),
    "metric_l"   : _ps("metric_l",  fontName="Helvetica-Bold", fontSize=6.5,
                        textColor=GREY_MID, leading=9, alignment=TA_CENTER),
    "metric_s"   : _ps("metric_s",  fontSize=7, textColor=GREY_MID,
                        leading=9, alignment=TA_CENTER),
    "pair_hdr"   : _ps("pair_hdr",  fontName="Helvetica-Bold", fontSize=8.5,
                        textColor=BLUE_DARK, leading=11, leftIndent=6),
    "seg_th"     : _ps("seg_th",    fontName="Helvetica-Bold", fontSize=7,
                        textColor=WHITE, leading=9, alignment=TA_CENTER),
    "seg_td"     : _ps("seg_td",    fontSize=7, textColor=GREY_DK,
                        leading=9, alignment=TA_CENTER),
    "seg_auto"   : _ps("seg_auto",  fontName="Helvetica-Bold", fontSize=7,
                        textColor=BLUE, leading=9, alignment=TA_CENTER),
    "seg_manual" : _ps("seg_manual", fontName="Helvetica-Bold", fontSize=7,
                        textColor=RED, leading=9, alignment=TA_CENTER),
}


# ─── NUMBERED CANVAS (page headers/footers) ──────────────────────────────────

class _NumberedCanvas(_RLCanvas):
    def __init__(self, *args, header_data: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._hd: dict           = header_data or {}
        self._saved_states: list = []

    def showPage(self):
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_states)
        for page_num, state in enumerate(self._saved_states, start=1):
            self.__dict__.update(state)
            _draw_running_layer(self, self._hd, page_num, total)
            super().showPage()
        super().save()


def _draw_running_layer(c, hd: dict, page_num: int, total: int) -> None:
    c.saveState()

    band_y = PH - MT + 0.45 * cm
    band_h = 1.05 * cm

    logo_path = hd.get("logo_path")
    text_x = ML + 0.4 * cm
    if logo_path and os.path.exists(logo_path):
        try:
            lw = 2.8 * cm
            lh = band_h * 0.76
            lx = ML + 0.22 * cm
            ly = band_y + (band_h - lh) / 2
            c.drawImage(logo_path, lx, ly, width=lw, height=lh,
                        preserveAspectRatio=True, mask="auto")
            text_x = lx + lw + 0.35 * cm
        except Exception:
            pass

    c.setStrokeColor(RED)
    c.setLineWidth(1.5)
    c.line(text_x - 0.15 * cm, band_y + 0.12 * cm,
           text_x - 0.15 * cm, band_y + band_h - 0.12 * cm)

    subject  = hd.get("subject_name", "")
    mill     = hd.get("mill_unit", "")
    hdr_text = " · ".join(p for p in [subject, mill] if p) or "Analisis Report"
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(DARK)
    c.drawString(text_x, band_y + band_h / 2 - 0.15 * cm, hdr_text)

    rec = hd.get("recorded_range", "")
    if rec:
        c.setFont("Helvetica", 7)
        c.setFillColor(BLUE_BORD)
        rw = c.stringWidth(rec, "Helvetica", 7)
        c.drawString(PW - MR - rw - 0.25 * cm,
                     band_y + band_h / 2 - 0.13 * cm, rec)

    c.setStrokeColor(BLUE)
    c.setLineWidth(0.5)
    c.line(ML, band_y - 0.06 * cm, PW - MR, band_y - 0.06 * cm)

    footer_y = MB - 1.1 * cm
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.4)
    c.line(ML, footer_y + 0.7 * cm, PW - MR, footer_y + 0.7 * cm)

    c.setFont("Helvetica", 7)
    c.setFillColor(DARK)
    c.drawString(ML, footer_y + 0.2 * cm, str(hd.get("display_name", ""))[:55])

    pg  = f"Page {page_num} of {total}"
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(BLUE)
    pw_ = c.stringWidth(pg, "Helvetica-Bold", 7.5)
    c.drawString(PW - MR - pw_, footer_y + 0.2 * cm, pg)

    c.restoreState()


# ─── SMALL HELPERS ───────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _fmt_num(v) -> str:
    try:
        f = float(v)
        if f != f:
            return "-"
        return f"{f:,.2f}" if abs(f) >= 1_000 else f"{f:.4g}"
    except (TypeError, ValueError):
        return str(v) if v is not None else "-"


def _fmt_secs(secs: float) -> str:
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _sp(pt: float = 8) -> Spacer:
    return Spacer(1, pt)


# ─── LAYOUT HELPERS ──────────────────────────────────────────────────────────

def _section_block(title: str) -> Table:
    t = Table([[Paragraph(title.upper(), S["section"])]], colWidths=[UW])
    t.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, -1), BLUE),
        ("ROWHEIGHT",      (0, 0), (-1, -1), 18),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",    (0, 0), (-1, -1), 8),
        ("TOPPADDING",     (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    return t


def _metric_cell(label: str, value: str, sub: str = "",
                 style_key: str = "metric_v") -> list:
    return [
        _sp(5),
        Paragraph(label.upper(), S["metric_l"]),
        _sp(3),
        Paragraph(value, S[style_key]),
        Paragraph(sub, S["metric_s"]),
        _sp(5),
    ]


def _metric_row(cards: list[tuple]) -> Table:
    n   = len(cards)
    cw  = UW / n
    row = [_metric_cell(*c) for c in cards]
    t   = Table([row], colWidths=[cw] * n)
    style_commands = [
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND",    (0, 0), (-1, -1), WHITE),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]
    accent_colors = [BLUE, GREEN, NAVY, RED]
    for index in range(n):
        color = accent_colors[index % len(accent_colors)]
        style_commands.append(("LINEABOVE", (index, 0), (index, 0), 3, color))
    t.setStyle(TableStyle(style_commands))
    return t


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    data = [[Paragraph(_esc(k), S["kv_key"]), Paragraph(_esc(str(v)), S["kv_val"])]
            for k, v in rows]
    kw   = UW * 0.36
    t    = Table(data, colWidths=[kw, UW - kw])
    style = [
        ("INNERGRID",     (0, 0), (-1, -1), 0.4, BORDER),
        ("BOX",           (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ]
    for i in range(0, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), ALT_ROW))
    t.setStyle(TableStyle(style))
    return t


# ─── COVER PAGE ──────────────────────────────────────────────────────────────

def _build_cover(
    display_name:   str,
    dataset_info:   dict,
    total_rows:     int,
    total_cols:     int,
    recorded_range: str | None,
    mill_unit:      str | None,
    generated_at:   str,
    logo_path:      str | None,
) -> list:
    story: list = []
    di = dataset_info or {}

    def _pick_di(*keys):
        for k in keys:
            for dk in di:
                if dk.strip().upper() == k.upper():
                    v = str(di[dk]).strip()
                    if v:
                        return v
        return ""

    mill     = _pick_di("Mill", "MILL UNIT", "UNIT_MILL")
    name     = _pick_di("NAME", "AUTOMATION_NAME", "DATASET_NAME")
    subtitle = " · ".join(p for p in [mill, name] if p) or mill_unit or display_name

    story.append(Paragraph("ANALISIS REPORT", S["doc_title"]))
    story.append(_sp(4))
    story.append(Paragraph(subtitle, S["doc_sub"]))
    story.append(_sp(6))

    _range_ps = _ps("range_cover", fontName="Helvetica-Bold", fontSize=10,
                    textColor=BLUE, leading=14)
    _meta_ps  = _ps("meta_cover",  fontSize=8.5, textColor=GREY_MID,
                    fontName="Helvetica", leading=12)

    if recorded_range:
        story.append(Paragraph(f"Data Range : {recorded_range}", _range_ps))
        story.append(_sp(3))
    story.append(Paragraph(f"File : {display_name}", _meta_ps))
    story.append(_sp(2))
    story.append(Paragraph(f"Generated : {generated_at}", _meta_ps))
    story.append(_sp(10))
    story.append(HRFlowable(width=UW, thickness=2, color=BLUE, spaceAfter=10))
    return story


# ─── SECTION BUILDERS ────────────────────────────────────────────────────────

def _build_stats_section(stats: dict | None) -> list:
    story: list = [_section_block("Summary Statistik"), _sp(5)]

    if not stats:
        story.append(Paragraph("Tidak ada kolom numerik.", S["muted"]))
        story.append(_sp(8))
        return story

    metrics = ["mean", "median", "min", "max", "std"]
    header  = [Paragraph(h, S["th"]) for h in
               ["Column", "Mean", "Median", "Min", "Max", "Std Dev"]]
    data    = [header]

    for col in stats["mean"]:
        row = [Paragraph(_esc(col), S["td_l"])]
        for m in metrics:
            row.append(Paragraph(_fmt_num(stats[m].get(col)), S["td"]))
        data.append(row)

    cw = [UW * 0.28] + [UW * 0.144] * 5
    t  = Table(data, colWidths=cw, repeatRows=1)
    style = [
        ("BACKGROUND",    (0, 0), (-1, 0), GREY_LT),
        ("LINEBELOW",     (0, 0), (-1, 0), 1.5, BLUE_BORD),
        ("INNERGRID",     (0, 1), (-1, -1), 0.4, BORDER),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("LINEBEFORE",    (0, 0), (0, -1), 2.5, RED),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), ALT_ROW))
    t.setStyle(TableStyle(style))

    story.append(t)
    story.append(_sp(10))
    return story


def _build_duration_section(duration: dict | None) -> list:
    story: list = [_section_block("Hour Record"), _sp(6)]

    if not duration:
        story.append(Paragraph("Tidak ada data waktu.", S["muted"]))
        story.append(_sp(8))
        return story

    d  = duration
    td = d.get("total_duration", {})

    story.append(_metric_row([
        ("Total Duration", td.get("human_readable", "–"), "HH:MM:SS",       "metric_v"),
        ("Total Minutes",  _fmt_num(td.get("minutes")),   "minutes",         "metric_v_g"),
        ("Valid Records",  f"{d.get('total_records', 0):,}",
         f"skipped: {d.get('invalid_rows_skipped', 0)}", "metric_v_a"),
    ]))
    story.append(_sp(6))
    story.append(_kv_table([
        ("Timestamp Column", d.get("timestamp_column", "–")),
        ("Start Time",       d.get("start_time",       "–")),
        ("End Time",         d.get("end_time",         "–")),
    ]))
    story.append(_sp(10))
    return story


def _build_counting_section(counting: dict | None) -> list:
    story: list = [PageBreak(), _section_block("Auto / Manual Record"), _sp(6)]

    if not counting:
        print("[DEBUG] Auto/Manual section: counting_result is None - state_column may not be set or data processing failed")
        story.append(Paragraph("Kolom state tidak terdeteksi atau data tidak tersedia untuk analisis auto/manual.", S["muted"]))
        story.append(_sp(8))
        return story

    s        = counting.get("summary", {})
    auto_d   = s.get("total_auto_counting",   {})
    manual_d = s.get("total_manual_counting", {})
    total_d  = s.get("total_recorded",        {})
    segs     = counting.get("segments", [])

    story.append(_metric_row([
        ("Total Recorded", total_d.get("human",  "–"), "HH:MM:SS",        "metric_v"),
        ("Auto Mode",      auto_d.get("human",   "–"),
         f"{auto_d.get('percentage', 0)}%",   "metric_v"),
        ("Manual Mode",    manual_d.get("human", "–"),
         f"{manual_d.get('percentage', 0)}%", "metric_v_r"),
        ("Segments",       str(len(segs)),              "transitions",     "metric_v_a"),
    ]))
    story.append(_sp(6))

    # Donut chart via Matplotlib
    auto_s   = auto_d.get("seconds",   0)
    manual_s = manual_d.get("seconds", 0)
    print(f"[DEBUG] Creating auto/manual donut chart: auto_s={auto_s}, manual_s={manual_s}")
    png = _build_donut_png(
        ["Auto", "Manual"], [auto_s, manual_s],
        ["#1A56DB", "#DC2626"], "Auto / Manual",
        title   = f"Distribusi Penggunaan Mode Auto / Manual",
    )
    if png:
        print(f"[DEBUG] PNG generated for auto/manual ({len(png)} bytes)")
        img = _png_to_rl_image(png, UW * 0.95, UW * 0.48)
        if img:
            print("[DEBUG] Auto/manual image added to story")
            t = Table([[img]], colWidths=[UW])
            t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
            story.append(t)
            story.append(Paragraph("Auto / Manual Record Distribution", S["caption"]))
            story.append(_sp(6))
        else:
            print("[ERROR] Auto/Manual section: Failed to convert PNG to ReportLab Image")
    else:
        print("[ERROR] Auto/Manual section: PNG generation returned None - auto_s and manual_s may both be zero")

    # Segment detail table (max 50 rows)
    story.append(Paragraph(
        f"Segment Details (menampilkan {min(50, len(segs))} dari {len(segs)} segment)",
        S["muted"],
    ))
    story.append(_sp(4))

    seg_header = [Paragraph(h, S["seg_th"]) for h in
                  ["Seg", "State", "Label", "Start", "End", "Duration", "Records"]]
    seg_data   = [seg_header]
    for seg in segs[:50]:
        lbl_style = "seg_auto" if seg.get("label") == "Auto" else "seg_manual"
        seg_data.append([
            Paragraph(str(seg.get("segment",       "")), S["seg_td"]),
            Paragraph(str(seg.get("state",         "")), S["seg_td"]),
            Paragraph(str(seg.get("label",         "")), S[lbl_style]),
            Paragraph(str(seg.get("start_time",    "")), S["seg_td"]),
            Paragraph(str(seg.get("end_time",      "")), S["seg_td"]),
            Paragraph(str(seg.get("duration_human","")), S["seg_td"]),
            Paragraph(f"{seg.get('record_count', 0):,}", S["seg_td"]),
        ])

    seg_cw    = [UW * r for r in [0.06, 0.07, 0.10, 0.22, 0.22, 0.14, 0.10]]
    seg_t     = Table(seg_data, colWidths=seg_cw, repeatRows=1)
    seg_style = [
        ("BACKGROUND",    (0, 0), (-1, 0), BLUE_DARK),
        ("INNERGRID",     (0, 1), (-1, -1), 0.3, BORDER),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(2, len(seg_data), 2):
        seg_style.append(("BACKGROUND", (0, i), (-1, i), ALT_ROW))
    seg_t.setStyle(TableStyle(seg_style))

    story.append(seg_t)
    story.append(_sp(10))
    return story


def _build_spv_section(
    df: pd.DataFrame,
    spv_pairs: list[dict],
    fn_compute_spv: Callable | None,
) -> list:
    story: list = [PageBreak(), _section_block("Range Value Analysis"), _sp(6)]

    if not spv_pairs:
        print("[DEBUG] Range Value Analysis section: spv_pairs is empty - set_point_column and process_value_column may not be configured")
        story.append(Paragraph("Tidak ada pasangan nilai range untuk analisis (set_point_column dan process_value_column tidak dikonfigurasi).", S["muted"]))
        story.append(_sp(8))
        return story

    pair_count = 0
    for pair in spv_pairs:
        sp_c  = pair.get("sp")
        pv_c  = pair.get("pv")
        label = pair.get("label") or f"{sp_c} vs {pv_c}"

        if not sp_c or not pv_c:
            print(f"[DEBUG] Range Value Analysis: Skipping pair - sp_c={sp_c}, pv_c={pv_c}")
            continue

        print(f"[DEBUG] Computing SPV for pair: {label}")
        sv = fn_compute_spv(df, sp_c, pv_c) if fn_compute_spv else None
        if not sv:
            print(f"[DEBUG] Range Value Analysis: fn_compute_spv returned None for pair {label}")
            continue

        pair_count += 1
        ph_t = Table([[Paragraph(label, S["pair_hdr"])]], colWidths=[UW])
        ph_t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), BLUE_SOFT),
            ("BOX",           (0, 0), (-1, -1), 0.8, BLUE_BORD),
            ("LINEBEFORE",    (0, 0), (0, -1),  3,   BLUE),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(KeepTogether([ph_t]))
        story.append(_sp(5))

        story.append(_metric_row([
            ("Total Records",   f"{sv['total_records']:,}",     "data points",  "metric_v"),
            ("Dalam Set Point", f"{sv['normal_count']:,}",
             f"{sv['normal_pct']}%", "metric_v"),
            ("Lebih Rendah",    f"{sv['lower_count']:,}",
             f"{sv['lower_pct']}%",  "metric_v_a"),
            ("Lebih Tinggi",    f"{sv['higher_count']:,}",
             f"{sv['higher_pct']}%", "metric_v_r"),
        ]))
        story.append(_sp(6))

        # Donut via Matplotlib
        print(f"[DEBUG] Creating donut chart for {label}: normal={sv['normal_count']}, lower={sv['lower_count']}, higher={sv['higher_count']}")
        png = _build_donut_png(
            labels  = ["Dalam SP", "Lebih Rendah", "Lebih Tinggi"],
            values  = [sv["normal_count"], sv["lower_count"], sv["higher_count"]],
            colors_ = ["#1A56DB", "#D97706", "#DC2626"],
            title   = f"Distribusi SP & PV - {label}",
        )
        
        if png:
            print(f"[DEBUG] PNG generated successfully ({len(png)} bytes)")
            img = _png_to_rl_image(png, UW * 0.95,UW * 0.48)
            if img:
                print(f"[DEBUG] Image added to story for {label}")
                t = Table([[img]], colWidths=[UW])
                t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
                story.append(t)
                story.append(Paragraph(
                    f"Distribusi Nilai Range {label}", S["caption"]))
            else:
                print(f"[ERROR] Range Value Analysis: Failed to convert PNG to ReportLab Image for {label}")
        else:
            print(f"[ERROR] Range Value Analysis: PNG generation returned None for {label}")
        
        story.append(_sp(10))
    
    if pair_count == 0:
        print("[DEBUG] Range Value Analysis: No valid pairs were processed")
        story.append(Paragraph("Tidak ada data range value yang dapat diproses.", S["muted"]))
        story.append(_sp(8))
    
    return story


def _build_plots_section(
    df: pd.DataFrame,
    plot_configs: list[dict],
    timestamp_col: str | None,
    date_col: str | None,
    fn_datetime_data: Callable | None,
) -> list:
    story: list = [PageBreak(), _section_block("Grafik Visualisasi"), _sp(8)]

    if not plot_configs:
        story.append(Paragraph("Tidak ada konfigurasi plot.", S["muted"]))
        story.append(_sp(8))
        return story

    # Pre-compute datetime series once
    dt_series = None
    if timestamp_col and timestamp_col in df.columns and fn_datetime_data:
        try:
            dt_series = fn_datetime_data(df, timestamp_col, date_col)
        except Exception:
            pass

    col_w = (UW - 0.6 * cm) / 2
    col_h = 5.8 * cm
    px_w  = max(int(col_w * _PX_TO_PX), 600)
    px_h  = max(int(col_h * _PX_TO_PX), 320)

    items: list[tuple] = []
    for cfg in plot_configs:
        png = _build_user_plot_png(df, cfg, dt_series, w=px_w * 2, h=px_h * 2)
        if png is None:
            continue
        img = _png_to_rl_image(png, col_w, col_h)
        cap = (cfg.get("title") or
               f"{cfg.get('chart_type','').title()} – "
               f"{cfg.get('x','')} × {', '.join(cfg.get('y') or [])}")
        items.append((img, cap))

    if not items:
        story.append(Paragraph("Tidak ada grafik yang berhasil di-render.", S["muted"]))
        story.append(_sp(8))
        return story

    if len(items) % 2:
        items.append((None, ""))

    for i in range(0, len(items), 2):
        left_img,  left_cap  = items[i]
        right_img, right_cap = items[i + 1]

        def _cell(img, cap):
            if img is None:
                return [_sp(col_h)]
            return [img, _sp(3), Paragraph(cap, S["caption"])]

        row = [_cell(left_img, left_cap), _cell(right_img, right_cap)]
        t   = Table([row], colWidths=[col_w + 0.3 * cm, col_w + 0.3 * cm])
        t.setStyle(TableStyle([
            ("BOX",           (0, 0), (0, 0), 0.5, BORDER),
            ("BOX",           (1, 0), (1, 0), 0.5, BORDER),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)
        story.append(_sp(6))

    return story


# ─── MAIN ENTRY POINT ────────────────────────────────────────────────────────

def build_pdf_reportlab(
    df: pd.DataFrame,
    *,
    display_name:         str           = "dataset",
    dataset_info:         dict | None   = None,
    logo_path:            str | None    = None,
    timestamp_col:        str | None    = None,
    date_col:             str | None    = None,
    state_col:            str | None    = None,
    spv_pairs:            list[dict] | None = None,
    plot_configs:         list[dict] | None = None,
    duration_result:      dict | None   = None,
    counting_result:      dict | None   = None,
    fn_datetime_data:     Callable | None = None,
    fn_summary_statistics: Callable | None = None,
    fn_compute_spv:       Callable | None = None,
    stat_columns:         list[str] | None = None,
) -> bytes:
    di  = dataset_info or {}
    now = datetime.now().strftime("%H:%M:%S, %d-%m-%Y")

    def _pick(*keys: str) -> str:
        for k in keys:
            for dk in di:
                if dk.strip().upper() == k.upper():
                    v = str(di[dk]).strip()
                    if v:
                        return v
        return ""

    subject_name = (_pick("AUTOMATION_NAME", "DATASET_NAME", "NAME") or
                    os.path.splitext(display_name)[0])
    mill_unit    = _pick("MILL UNIT", "UNIT_MILL", "MILL", "UNIT")

    # Recorded range from duration result or raw data
    recorded_range: str | None = None
    if duration_result:
        recorded_range = (f"{duration_result.get('start_time','')} – "
                          f"{duration_result.get('end_time','')}")
    elif timestamp_col and timestamp_col in df.columns and fn_datetime_data:
        try:
            p = fn_datetime_data(df, timestamp_col, date_col).dropna().sort_values()
            if len(p) >= 2:
                recorded_range = (f"{p.iloc[0].strftime('%d %b %Y %H:%M')} – "
                                  f"{p.iloc[-1].strftime('%d %b %Y %H:%M')}")
        except Exception:
            pass

    # Summary statistics
    stat_df = df
    if stat_columns:
        valid = [c for c in stat_columns if c in df.columns]
        if valid:
            stat_df = df[valid]

    stats = fn_summary_statistics(stat_df) if fn_summary_statistics else None
    if stats is None:
        num = stat_df.select_dtypes(include="number")
        if not num.empty:
            stats = {k: getattr(num, k)().to_dict()
                     for k in ("mean", "median", "min", "max", "std")}

    if stats and stat_columns and not any(stats.values()):
        stats = None

    header_data = {
        "logo_path":      logo_path,
        "subject_name":   subject_name,
        "mill_unit":      mill_unit,
        "recorded_range": recorded_range or "",
        "display_name":   display_name,
        "generated_at":   now,
    }

    story: list = []
    story += _build_cover(
        display_name   = display_name,
        dataset_info   = di,
        total_rows     = len(df),
        total_cols     = len(df.columns),
        recorded_range = recorded_range,
        mill_unit      = mill_unit or None,
        generated_at   = now,
        logo_path      = logo_path,
    )
    story += _build_stats_section(stats)
    story += _build_duration_section(duration_result)
    story += _build_counting_section(counting_result)
    story += _build_spv_section(df, spv_pairs or [], fn_compute_spv)
    if plot_configs:
        story += _build_plots_section(
            df, plot_configs, timestamp_col, date_col, fn_datetime_data
        )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize     = A4,
        leftMargin   = ML,
        rightMargin  = MR,
        topMargin    = MT,
        bottomMargin = MB,
        title        = f"Analisis Report – {display_name}",
        author       = "Dashboard Analytics",
    )

    def _canvas_maker(filename, **kwargs):
        return _NumberedCanvas(filename, header_data=header_data, **kwargs)

    doc.build(story, canvasmaker=_canvas_maker)
    return buf.getvalue()