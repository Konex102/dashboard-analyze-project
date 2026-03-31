import csv as csv_module
import json
import os
import re
import shutil
import uuid
import warnings
from typing import Literal, List, Optional
import io
import openpyxl

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from fastapi import FastAPI, File, HTTPException, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse, FileResponse
from pydantic import BaseModel

from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import plotly.io as pio

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, HRFlowable, KeepTogether,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

upload_dir = "/tmp/uploads"
report_dir = "/tmp/reports"
os.makedirs(upload_dir, exist_ok=True)
os.makedirs(report_dir, exist_ok=True)

metadata_path = os.path.join(upload_dir, "_metadata.json")
jobs_path     = os.path.join(report_dir, "_jobs.json")

allowed_extensions = {".csv", ".xlsx"}
csv_fallback_encodings = ("utf-8", "utf-16", "latin-1")
_DATE_PREFIX_RE = re.compile(r"^\s*(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})(?:\D|$)")

_DATE_FORMATS = [
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%y %H:%M",
    "%d-%m-%Y",
    "%d-%m-%y",
]

# COLOR PALETTE
BRAND_BLUE      = colors.HexColor("#1A56DB")
BRAND_BLUE_DARK = colors.HexColor("#0F3FA6")
BRAND_LIGHT     = colors.HexColor("#EBF0FF")
ACCENT_GREEN    = colors.HexColor("#057A55")
ACCENT_AMBER    = colors.HexColor("#B45309")
ACCENT_RED      = colors.HexColor("#DC2626")
ROW_ALT         = colors.HexColor("#F8FAFF")
BORDER_COLOR    = colors.HexColor("#D1D9F0")
TEXT_DARK       = colors.HexColor("#111827")
TEXT_MID        = colors.HexColor("#374151")
TEXT_MUTED      = colors.HexColor("#6B7280")
WHITE           = colors.white

PAGE_W, PAGE_H = A4
MARGIN         = 1.8 * cm

# Metadata Helper
def _load_metadata() -> dict:
    if not os.path.exists(metadata_path):
        return {}
    try:
        with open(metadata_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_metadata(metadata: dict) -> None:
    tmp = f"{metadata_path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, metadata_path)

def _set_metadata(filename: str, original_filename: str | None) -> None:
    meta = _load_metadata()
    meta[filename] = {"original_filename": original_filename or filename}
    _save_metadata(meta)

def _remove_metadata(filename: str) -> None:
    meta = _load_metadata()
    if filename in meta:
        del meta[filename]
        _save_metadata(meta)

# Background Report Tracking
def _load_jobs() -> dict:
    if not os.path.exists(jobs_path):
        return {}
    try:
        with open(jobs_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}
    
def _save_jobs(jobs: dict) -> None:
    tmp = f"{jobs_path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(jobs, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, jobs_path)

def _set_job(job_id: str, status: str, detail: str = "", filename: str = "") -> None:
    jobs = _load_jobs()
    jobs[job_id] = {"status": status, "detail": detail, "filename": filename}
    _save_jobs(jobs)

# Pydantic Models
class AnalyzeRequest(BaseModel):
    filename: str
    sheet_name: str | int = 0
class PlotRequest(BaseModel):
    filename: str
    chart_type: Literal["line", "bar", "area", "scatter", "heatmap", "box"] = "line"
    x: str | None = None
    y: str | list[str] | None = None
    color: str | None = None
    size: str | None = None
    names: str | None = None
    values: str | None = None
    z: str | None = None
    title: str | None = None
    sheet_name: str | int = 0
    slider_range: bool = False
    date_column: str | None = None

class timestampRequest(BaseModel):
    filename: str
    timestamp_column: str
    date_column: str | None = None
    sheet_name: str | int = 0

class autoCounting(BaseModel):
    filename: str
    timestamp_column: str
    state_column: str
    date_column: str | None = None
    sheet_name: str | int = 0

class SPVRequest(BaseModel):
    filename: str
    set_point_column: str
    process_value_column: str
    sheet_name: str | int = 0

class ReportRequest(BaseModel):
    filename: str
    sheet_name: str | int = 0
    timestamp_column: str | None = None
    date_column: str | None = None
    state_column: str | None = None
    set_point_column: str | None = None
    process_value_column: str | None = None

# File Helper
META_KEY_RE  = re.compile(r"^[A-Z][A-Z0-9_]{0,29}$")
CSV_ENCODING = ("utf-8", "utf-16", "latin-1")

def _sanitize_storage_name(filename: str) -> str:
    base = os.path.basename(filename or "")
    if not base:
        raise HTTPException(status_code=400, detail="File Kosong")
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Only CSV and XLSX files are allowed.")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem).strip("_") or "dataset"
    return f"{safe}_{uuid.uuid4().hex[:8]}{ext}"

def _resolve_file_path(filename: str) -> str:
    safe = os.path.basename(filename)
    path = os.path.join(upload_dir, safe)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"File not found: {safe}")
    return path

def _read_csv(file_path: str, encoding: str) -> str:
    with open(file_path, encoding=encoding, newline="") as fh:
        content = fh.read()
    return content.replace("\r\n", "\n").replace("\r", "\n")

def _sniff_delimiter(content: str) -> str:
    try:
        dialect = csv_module.Sniffer().sniff(content[:2048], delimiters=",;\t|")
        return dialect.delimiter
    except csv_module.Error:
        header = content.split("\n")[0]
        counts = {d: header.count(d) for d in (";", ",", "\t")}
        return max(counts, key=counts.get)

def _sniff_decimal(content: str, delimiter: str, skip_rows: int) -> str:
    lines = content.split("\n")
    for line in lines[skip_rows + 1:][:30]:
        for part in line.strip().split(delimiter):
            part = part.strip().strip('"')
            if re.match(r"^\d+,\d+$", part):
                return ","
    return "."

def _detect_content(content: str, delimiter: str) -> tuple[dict, int]:
    info: dict = {}
    skip = 0
    for line in content.split("\n"):
        if not line.strip():
            break
        parts     = line.split(delimiter)
        non_empty = [p.strip() for p in parts if p.strip()]
        if len(non_empty) == 2 and META_KEY_RE.match(non_empty[0]):
            info[non_empty[0]] = non_empty[1]
            skip += 1
        else:
            break
    return info, skip

def _detect_file_info(file_path: str, sheet_name: str | int = 0) -> tuple[dict, int]:
    _, ext = os.path.splitext(file_path.lower())
    if ext == ".csv":
        for enc in csv_fallback_encodings:
            try:
                content = _read_csv(file_path, enc)
                return _detect_content(content, _sniff_delimiter(content))
            except Exception:
                continue
        return {}, 0
    if ext == ".xlsx":
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            ws = (
                wb.worksheets[sheet_name]
                if isinstance(sheet_name, int) and sheet_name < len(wb.worksheets)
                else wb[sheet_name] if sheet_name in wb.sheetnames
                else wb.active
            )
            info, skip = {}, 0
            for row in ws.iter_rows(values_only=True):
                non_empty = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if len(non_empty) == 2 and META_KEY_RE.match(non_empty[0]):
                    info[non_empty[0]] = non_empty[1]
                    skip += 1
                else:
                    break
            wb.close()
            return info, skip
        except Exception:
            return {}, 0
    return {}, 0

def _read_dataframe(file_path: str, sheet_name: str | int = 0) -> pd.DataFrame:
    _, ext = os.path.splitext(file_path.lower())
    if ext == ".csv":
        content = None
        for enc in CSV_ENCODING:
            try:
                content = _read_csv(file_path, enc)
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            raise HTTPException(status_code=400, detail="Format CSV tidak didukung")
        delimiter = _sniff_delimiter(content)
        _, skip   = _detect_content(content, delimiter)
        decimal   = _sniff_decimal(content, delimiter, skip)
        df = pd.read_csv(
            io.StringIO(content),
            sep=delimiter,
            decimal=decimal,
            skiprows=skip if skip else None,
            engine="python",
        )
        df = df.dropna(axis=1, how="all")
        df = df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed:\d+$")]
        return df
    if ext == ".xlsx":
        try:
            _, skip = _detect_file_info(file_path, sheet_name=sheet_name)
            df = pd.read_excel(
                file_path, sheet_name=sheet_name,
                skiprows=skip if skip else None, engine="openpyxl",
            )
            df = df.dropna(axis=1, how="all")
            df = df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed: \d+$")]
            return df
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="File Tidak Support")

# DATETIME HELPER FUNCTION
def _prefer_dayfirst(raw: pd.Series, default: bool = True) -> bool:
    samples = raw.dropna().astype(str).str.strip()
    if samples.empty:
        return default
    day_votes = month_votes = 0
    for val in samples.head(200):
        m = _DATE_PREFIX_RE.match(val)
        if not m:
            continue
        first_txt, second_txt, _ = m.groups()
        if len(first_txt) == 4:
            continue
        first, second = int(first_txt), int(second_txt)
        if first > 12 >= second:
            day_votes += 1
        elif second > 12 >= first:
            month_votes += 1
    if day_votes or month_votes:
        return day_votes >= month_votes
    return default

def _parse_format_time(raw: pd.Series, default_dayfirst: bool = True) -> pd.Series:
    cleaned  = raw.astype("string").str.strip().replace(r"^\s*$", pd.NA, regex=True)
    dayfirst = _prefer_dayfirst(cleaned, default=default_dayfirst)

    normalized = (
        cleaned.fillna("").astype(str)
        .str.replace(r"[./]", "-", regex=True)
        .str.replace("T", " ", regex=False)
        .str.strip()
    )

    parsed = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    for fmt in _DATE_FORMATS:
        mask = parsed.isna()
        if not mask.any():
            break
        candidate   = pd.to_datetime(normalized[mask], format=fmt, errors="coerce")
        parsed[mask] = candidate

    inferred = pd.to_datetime(cleaned, dayfirst=dayfirst, errors="coerce", format="mixed")
    parsed   = parsed.fillna(inferred)
    return parsed

def _parse_datetime_series(raw: pd.Series, default_dayfirst: bool = True) -> pd.Series:
    dayfirst = _prefer_dayfirst(raw, default=default_dayfirst)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        parsed    = pd.to_datetime(raw, dayfirst=dayfirst,     errors="coerce")
        alternate = pd.to_datetime(raw, dayfirst=not dayfirst, errors="coerce")
    return alternate if alternate.notna().sum() > parsed.notna().sum() else parsed

def _fix_midnight_rollover(parsed: pd.Series) -> pd.Series:
    time_only = parsed.dt.hour * 3600 + parsed.dt.minute * 60 + parsed.dt.second
    rollover  = (time_only < time_only.shift(1)).fillna(False).cumsum()
    return parsed + pd.to_timedelta(rollover, unit="D")

def _datetime_data(df: pd.DataFrame, time_col: str, date_col: str | None) -> pd.Series:
    raw = df[time_col].astype(str).str.strip()
    if date_col and date_col in df.columns:
        date_raw    = df[date_col].astype(str).str.strip().replace(r"^\s*$", pd.NA, regex=True)
        date_series = _parse_datetime_series(date_raw).ffill()
        combined    = date_series.dt.strftime("%Y-%m-%d") + " " + raw
        return _parse_datetime_series(combined, default_dayfirst=False)
    parsed = _parse_datetime_series(raw)
    if parsed.notna().sum() >= 2:
        return parsed if parsed.dt.date.nunique() > 1 else _fix_midnight_rollover(parsed)
    parsed = pd.to_datetime(raw, format="%H:%M:%S", errors="coerce")
    if parsed.notna().sum() >= 2:
        return _fix_midnight_rollover(parsed)
    return parsed

# ANALYSIS DATA HELPER
def _summary_statistics(df: pd.DataFrame) -> dict | None:
    num = df.select_dtypes(include="number")
    if num.empty:
        return None
    return {
        "mean":   num.mean().to_dict(),
        "median": num.median().to_dict(),
        "min":    num.min().to_dict(),
        "max":    num.max().to_dict(),
        "std":    num.std().to_dict(),
    }

def _as_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else value

def _validate_columns(df: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [c for c in columns if c and c not in df.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown {label} column(s): {', '.join(missing)}")

def _maybe_sort_datetime(df: pd.DataFrame, x_col: str | None, date_col: str | None = None) -> pd.DataFrame:
    if not x_col:
        return df
    tmp    = df.copy()
    parsed = _datetime_data(tmp, x_col, date_col)
    if parsed.notna().sum() == 0:
        return df
    tmp[x_col] = parsed
    return tmp.sort_values(by=x_col)

def _build_figure(request: PlotRequest, df: pd.DataFrame):
    y_cols      = _as_list(request.y)
    x_col       = request.x
    simple_cols = [x_col, request.color, request.size, request.names, request.values, request.z]
    _validate_columns(df, [c for c in simple_cols if c], "parameter")
    _validate_columns(df, y_cols, "y")

    chart_df = df
    if request.chart_type in {"line", "area"}:
        chart_df = _maybe_sort_datetime(chart_df, x_col, request.date_column)

    ct = request.chart_type
    if ct == "line":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Line chart requires `x` and at least one `y`.")
        fig = px.line(chart_df, x=x_col, y=y_cols, color=request.color, title=request.title or "Line Chart")
    elif ct == "bar":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Bar chart requires `x` and at least one `y`.")
        fig = px.bar(chart_df, x=x_col, y=y_cols if len(y_cols) > 1 else y_cols[0],
                     color=request.color, title=request.title or "Bar Chart", barmode="group")
    elif ct == "scatter":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Scatter chart requires `x` and at least one `y`.")
        fig = px.scatter(chart_df, x=x_col, y=y_cols if len(y_cols) > 1 else y_cols[0],
                         color=request.color, size=request.size, title=request.title or "Scatter Plot")
    elif ct == "area":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Area chart requires `x` and at least one `y`.")
        fig = px.area(chart_df, x=x_col, y=y_cols if len(y_cols) > 1 else y_cols[0],
                      color=request.color, title=request.title or "Area Chart")
    elif ct == "box":
        if not y_cols:
            raise HTTPException(status_code=400, detail="Box chart requires at least one `y`.")
        fig = px.box(chart_df, x=x_col, y=y_cols if len(y_cols) > 1 else y_cols[0],
                     color=request.color, title=request.title or "Box Plot")
    elif ct == "heatmap":
        if x_col and y_cols:
            fig = px.density_heatmap(chart_df, x=x_col, y=y_cols[0], z=request.z,
                                     title=request.title or "Heatmap", color_continuous_scale="Viridis")
        else:
            num_df = chart_df.select_dtypes(include="number")
            if num_df.shape[1] < 2:
                raise HTTPException(status_code=400,
                    detail="Heatmap without `x`/`y` requires at least two numeric columns.")
            corr = num_df.corr(numeric_only=True)
            fig  = go.Figure(data=go.Heatmap(
                z=corr.values, x=list(corr.columns), y=list(corr.index),
                colorscale="RdBu", zmid=0,
            ))
            fig.update_layout(title=request.title or "Correlation Heatmap")
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported chart type: {ct}")

    fig.update_layout(template="plotly_white")
    return fig

# REPORT HELPER
def _fmt_secs(secs: float) -> str:
    h, rem = divmod(int(secs), 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def _fmt_num(value) -> str:
    try:
        f = float(value)
        if f != f:
            return "-"
        return f"{f:,.2f}" if abs(f) >= 1000 else f"{f:.4g}"
    except (TypeError, ValueError):
        return str(value) if value is not None else "-"

def _plotly_to_image(fig: go.Figure, width: int = 700, height: int = 300) -> bytes | None:
    try:
        return pio.to_image(fig, format="png", width=width, height=height, scale=2)
    except Exception:
        return None

def _build_donut_figure(labels: list[str], values: list[float], color_map: dict[str, str]) -> go.Figure:
    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.48,
        marker_colors=[color_map.get(l, "#999") for l in labels],
        textinfo="label+percent",
        hoverinfo="label+value+percent",
    ))
    fig.update_layout(
        margin=dict(t=20, b=40, l=20, r=20),
        showlegend=True,
        legend=dict(orientation="h", y=-0.12, font=dict(size=10)),
        paper_bgcolor="white",
        plot_bgcolor="white",
        height=300,
    )
    return fig

def _styles_report(page_width: float) -> dict:
    base   = getSampleStyleSheet()
    usable = page_width - 2 * MARGIN  # noqa: F841

    def _s(name, parent="Normal", **kw) -> ParagraphStyle:
        s = ParagraphStyle(name, parent=base[parent])
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    return {
        "report_title": _s(
            "report_title", "Title",
            fontSize=20, textColor=WHITE,
            alignment=TA_CENTER, spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "report_subtitle": _s(
            "report_subtitle",
            fontSize=10, textColor=colors.HexColor("#FFFFFF"),
            alignment=TA_CENTER, spaceAfter=0,
            fontName="Helvetica",
        ),
        "section-title": _s(
            "section_title",
            fontSize=16, textColor=WHITE,
            fontName="Helvetica-Bold", spaceAfter=0, leftIndent=6,
        ),
        "body":         _s("body",    fontSize=11, textColor=TEXT_DARK, leading=14, spaceAfter=4),
        "label":        _s("label",   fontSize=8,  textColor=TEXT_MUTED, fontName="Helvetica", spaceAfter=0),
        "value_large":  _s("value_large", fontSize=16, textColor=BRAND_BLUE_DARK, fontName="Helvetica", spaceAfter=0),
        "kv_label":     _s("kv_label",  fontSize=9, textColor=TEXT_MUTED, fontName="Helvetica"),
        "kv_value":     _s("kv_value",  fontSize=9, textColor=TEXT_DARK,  fontName="Helvetica"),
        "table_header": _s("table_header", fontSize=8, textColor=WHITE,
                            fontName="Helvetica", alignment=TA_CENTER),
        "table_cell":   _s("table_cell",   fontSize=8, textColor=TEXT_DARK,
                            alignment=TA_CENTER, leading=11),
        "table_cell_left": _s("table_cell_left", fontSize=8, textColor=TEXT_DARK,
                               alignment=TA_LEFT, leading=11),
        "footer":  _s("footer",  fontSize=8, textColor=TEXT_MUTED, alignment=TA_CENTER),
        "no_data": _s("no_data", fontSize=9, textColor=TEXT_MUTED, alignment=TA_CENTER,
                       spaceAfter=6, fontName="Helvetica"),
    }

def _section_header(title: str, styles: dict) -> list:
    tbl = Table(
        [[Paragraph(title, styles["section-title"])]],
        colWidths=[PAGE_W - 2 * MARGIN],
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, -1), BRAND_BLUE),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [BRAND_BLUE]),
        ("TOPPADDING",     (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 6),
        ("LEFTPADDING",    (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [4]),
    ]))
    return [tbl, Spacer(1, 6)]

def _kv_table(rows: list[tuple[str, str]], styles: dict, col_widths=None) -> Table:
    usable     = PAGE_W - 2 * MARGIN
    col_widths = col_widths or [usable * 0.42, usable * 0.58]
    data = [[Paragraph(k, styles["kv_label"]), Paragraph(str(v), styles["kv_value"])] for k, v in rows]
    tbl  = Table(data, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, ROW_ALT]),
        ("GRID",           (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 8),
    ]))
    return tbl

def _stat_table(stats: dict, styles: dict) -> Table:
    usable  = PAGE_W - 2 * MARGIN
    headers = ["Column", "Mean", "Median", "Min", "Max", "Std Dev"]
    col_w   = [usable * 0.28] + [usable * 0.144] * 5
    data    = [[Paragraph(h, styles["table_header"]) for h in headers]]
    for col in stats["mean"]:
        data.append([
            Paragraph(col, styles["table_cell_left"]),
            Paragraph(_fmt_num(stats["mean"].get(col)),   styles["table_cell"]),
            Paragraph(_fmt_num(stats["median"].get(col)), styles["table_cell"]),
            Paragraph(_fmt_num(stats["min"].get(col)),    styles["table_cell"]),
            Paragraph(_fmt_num(stats["max"].get(col)),    styles["table_cell"]),
            Paragraph(_fmt_num(stats["std"].get(col)),    styles["table_cell"]),
        ])
    tbl = Table(data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  BRAND_BLUE_DARK),
        ("TOPPADDING",     (0, 0), (-1, 0),  7),
        ("BOTTOMPADDING",  (0, 0), (-1, 0),  7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ("TOPPADDING",     (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 1), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
        ("GRID",           (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return tbl

def _segment_table(segments: list[dict], styles: dict) -> Table:
    usable  = PAGE_W - 2 * MARGIN
    headers = ["Seg", "State", "Label", "Start Time", "End Time", "Duration", "Records"]
    col_w   = [usable * f for f in (0.06, 0.07, 0.10, 0.20, 0.20, 0.18, 0.09)]
    data    = [[Paragraph(h, styles["table_header"]) for h in headers]]
    for seg in segments:
        label       = seg.get("label", "")
        label_style = ParagraphStyle(
            "seg_label", fontSize=8, alignment=TA_CENTER, fontName="Helvetica-Bold",
            textColor=BRAND_BLUE if label == "Auto" else ACCENT_RED,
        )
        data.append([
            Paragraph(str(seg.get("segment", "")),     styles["table_cell"]),
            Paragraph(str(seg.get("state", "")),       styles["table_cell"]),
            Paragraph(label,                            label_style),
            Paragraph(seg.get("start_time", ""),       styles["table_cell"]),
            Paragraph(seg.get("end_time", ""),         styles["table_cell"]),
            Paragraph(seg.get("duration_human", ""),   styles["table_cell"]),
            Paragraph(str(seg.get("record_count", "")), styles["table_cell"]),
        ])
    tbl = Table(data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  BRAND_BLUE_DARK),
        ("TOPPADDING",     (0, 0), (-1, 0),  7),
        ("BOTTOMPADDING",  (0, 0), (-1, 0),  7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ("TOPPADDING",     (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 1), (-1, -1), 4),
        ("LEFTPADDING",    (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 4),
        ("GRID",           (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return tbl

def _metric_cards(cards: list[dict], styles: dict) -> Table:
    usable = PAGE_W - 2 * MARGIN
    cell_w = usable / len(cards)

    def _card(card: dict):
        ls = ParagraphStyle("mc_l", fontSize=7.5, textColor=TEXT_MUTED, fontName="Helvetica")
        vs = ParagraphStyle("mc_v", fontSize=14,  textColor=TEXT_DARK,  fontName="Helvetica")
        ss = ParagraphStyle("mc_s", fontSize=7.5, textColor=TEXT_MUTED, fontName="Helvetica")
        inner = [[Paragraph(card["label"], ls)], [Paragraph(card["value"], vs)]]
        if card.get("sub"):
            inner.append([Paragraph(card["sub"], ss)])
        t = Table(inner, colWidths=[cell_w - 0.6 * cm])
        t.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))
        return t

    outer = Table([[_card(c) for c in cards]], colWidths=[cell_w] * len(cards))
    outer.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), WHITE),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return outer

# PDF BUILDER SETUP
def _build_pdf(request: ReportRequest, out_path: str) -> str:
    from datetime import datetime as _dt

    file_path    = _resolve_file_path(request.filename)
    df           = _read_dataframe(file_path, sheet_name=request.sheet_name)
    file_info, _ = _detect_file_info(file_path, sheet_name=request.sheet_name)
    meta         = _load_metadata()
    display_name = meta.get(request.filename, {}).get("original_filename", request.filename)
    stats        = _summary_statistics(df)

    styles = _styles_report(PAGE_W)
    story  = []
    usable = PAGE_W - 2 * MARGIN

    # COVER PDF SETUP
    cover_data = [
        [Paragraph("INTERACTIVE DASHBOARD", styles["report_title"])],
        [Paragraph("Analysis Report",        styles["report_subtitle"])],
        [Paragraph(
            f'Generated: {_dt.now().strftime("%d %B %Y  %H:%M")}  |  File: {display_name}',
            styles["report_subtitle"],
        )],
    ]
    cover_tbl = Table(cover_data, colWidths=[usable])
    cover_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), BRAND_BLUE_DARK),
        ("TOPPADDING",    (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING",   (0, 0), (-1, -1), 14),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 14),
        ("ROUNDEDCORNERS", [6]),
    ]))
    story.append(cover_tbl)
    story.append(Spacer(1, 14))

    # DATASET INFO SETUP
    if file_info:
        story += _section_header("Informasi Dataset", styles)
        info_rows = list(file_info.items()) + [
            ("Total Rows",    f"{len(df):,}"),
            ("Total Columns", str(len(df.columns))),
        ]
        story.append(_kv_table(info_rows, styles))
        story.append(Spacer(1, 14))

    # ── Summary Statistics ────────────────────────────────────────────────────
    story += _section_header("SUMMARY", styles)
    if stats:
        story.append(_stat_table(stats, styles))
    else:
        story.append(Paragraph("Kolom Angka tidak ada di Dataset", styles["no_data"]))
    story.append(Spacer(1, 18))

    # HOUR RECORD SETUP
    story += _section_header("HOUR RECORD", styles)
    duration_result = None
    if request.timestamp_column and request.timestamp_column in df.columns:
        try:
            parsed = _datetime_data(df, request.timestamp_column, request.date_column)
            valid  = parsed.dropna().sort_values()
            if len(valid) >= 2:
                start_t    = valid.iloc[0]
                end_t      = valid.iloc[-1]
                total_secs = (end_t - start_t).total_seconds()
                duration_result = {
                    "timestamp_column": request.timestamp_column,
                    "start_time":  start_t.strftime("%d-%m-%Y %H:%M:%S"),
                    "end_time":    end_t.strftime("%d-%m-%Y %H:%M:%S"),
                    "total_records":   len(valid),
                    "invalid_skipped": int(parsed.isna().sum()),
                    "total_seconds":   total_secs,
                    "human_readable":  _fmt_secs(total_secs),
                    "minutes": round(total_secs / 60, 2),
                    "hours":   round(total_secs / 3600, 4),
                }
        except Exception:
            pass

    if duration_result:
        dr = duration_result
        story.append(_metric_cards([
            {"label": "Total Duration (HH:MM:SS)", "value": dr["human_readable"],
             "sub": f"{dr['hours']} hours", "color": "#1A56DB"},
            {"label": "Total Minutes",  "value": f"{dr['minutes']:,.1f}",
             "sub": "minutes",          "color": "#0369A1"},
            {"label": "Valid Records",  "value": f"{dr['total_records']:,}",
             "sub": f"skipped: {dr['invalid_skipped']}", "color": "#047857"},
        ], styles))
        story.append(Spacer(1, 6))
        story.append(_kv_table([
            ("Timestamp Column", dr["timestamp_column"]),
            ("Start Time",       dr["start_time"]),
            ("End Time",         dr["end_time"]),
        ], styles))
    else:
        story.append(Paragraph("Tidak Waktu Tercatat di Dataset", styles["no_data"]))
    story.append(Spacer(1, 18))

    # AUTO/MANUAL RECORD
    story += _section_header("AUTO/MANUAL RECORD", styles)
    counting_result = None
    if (request.timestamp_column and request.timestamp_column in df.columns
            and request.state_column and request.state_column in df.columns):
        try:
            df_c = df.copy()
            df_c["_time"]  = _datetime_data(df_c, request.timestamp_column, request.date_column)
            df_c["_state"] = pd.to_numeric(df_c[request.state_column], errors="coerce")
            df_c = df_c.dropna(subset=["_time", "_state"]).sort_values("_time").reset_index(drop=True)
            if len(df_c) >= 2:
                df_c["_chg"]   = df_c["_state"] != df_c["_state"].shift(1)
                df_c["_group"] = df_c["_chg"].cumsum()
                segs = []
                for gid, gdf in df_c.groupby("_group"):
                    sv = int(gdf["_state"].iloc[0])
                    st = gdf["_time"].iloc[0]
                    li = gdf.index[-1]
                    et = df_c["_time"].iloc[li + 1] if li + 1 < len(df_c) else gdf["_time"].iloc[-1]
                    ds = (et - st).total_seconds()
                    segs.append({
                        "segment": int(gid), "state": sv,
                        "label": "Auto" if sv == 1 else "Manual",
                        "start_time": st.strftime("%d-%m-%Y %H:%M:%S"),
                        "end_time":   et.strftime("%d-%m-%Y %H:%M:%S"),
                        "duration_seconds": ds, "duration_human": _fmt_secs(ds),
                        "record_count": len(gdf),
                    })
                auto_s   = sum(s["duration_seconds"] for s in segs if s["state"] == 1)
                manual_s = sum(s["duration_seconds"] for s in segs if s["state"] == 0)
                total_s  = auto_s + manual_s
                counting_result = {
                    "segments":      segs,
                    "auto_seconds":  auto_s,  "manual_seconds": manual_s, "total_seconds": total_s,
                    "auto_human":    _fmt_secs(auto_s),
                    "manual_human":  _fmt_secs(manual_s),
                    "total_human":   _fmt_secs(total_s),
                    "auto_pct":   round(auto_s   / total_s * 100, 2) if total_s else 0.0,
                    "manual_pct": round(manual_s / total_s * 100, 2) if total_s else 0.0,
                }
        except Exception:
            pass

    if counting_result:
        cr = counting_result
        story.append(_metric_cards([
            {"label": "Total Recorded",    "value": cr["total_human"],  "sub": "HH:MM:SS",             "color": "#374151"},
            {"label": "Auto Mode Total",   "value": cr["auto_human"],   "sub": f"{cr['auto_pct']}%",   "color": "#1A56DB"},
            {"label": "Manual Mode Total", "value": cr["manual_human"], "sub": f"{cr['manual_pct']}%", "color": "#DC2626"},
            {"label": "Total Segments",    "value": str(len(cr["segments"])), "sub": "transitions",    "color": "#B45309"},
        ], styles))
        story.append(Spacer(1, 8))

        if cr["auto_seconds"] > 0 or cr["manual_seconds"] > 0:
            dl, dv, dc = [], [], {}
            if cr["auto_seconds"]   > 0: dl.append("Auto");   dv.append(cr["auto_seconds"]);   dc["Auto"]   = "#1A56DB"
            if cr["manual_seconds"] > 0: dl.append("Manual"); dv.append(cr["manual_seconds"]); dc["Manual"] = "#DC2626"
            img_bytes = _plotly_to_image(_build_donut_figure(dl, dv, dc), width=500, height=260)
            if img_bytes:
                img = Image(io.BytesIO(img_bytes), width=12 * cm, height=6.5 * cm)
                img.hAlign = "CENTER"
                story.append(img)
                story.append(Spacer(1, 6))

        story.append(Paragraph(
            f"Segment Details (showing {min(50, len(cr['segments']))} of {len(cr['segments'])} segments)",
            styles["label"],
        ))
        story.append(Spacer(1, 4))
        story.append(_segment_table(cr["segments"][:50], styles))
    else:
        story.append(Paragraph("Tidak Timestamp Yang Terdeteksi untuk Auto/Manual", styles["no_data"]))
    story.append(Spacer(1, 18))

    # RANGE VALUE ANALYSIS VALUE
    story += _section_header("Range Value Analysis", styles)
    spv_result = None
    if (request.set_point_column and request.set_point_column in df.columns
            and request.process_value_column and request.process_value_column in df.columns):
        try:
            df_s = df.copy()
            df_s["_sp"] = pd.to_numeric(df_s[request.set_point_column],     errors="coerce")
            df_s["_pv"] = pd.to_numeric(df_s[request.process_value_column], errors="coerce")
            df_s = df_s.dropna(subset=["_sp", "_pv"])
            if not df_s.empty:
                df_s["_dev"]    = df_s["_pv"] - df_s["_sp"]
                df_s["_status"] = df_s["_dev"].apply(
                    lambda d: "normal" if d == 0 else ("lower" if d < 0 else "higher"))
                total    = len(df_s)
                normal_c = int((df_s["_status"] == "normal").sum())
                lower_c  = int((df_s["_status"] == "lower").sum())
                higher_c = int((df_s["_status"] == "higher").sum())
                abs_dev  = df_s["_dev"].abs()
                pct      = lambda n: round(n / total * 100, 2) if total else 0.0  # noqa: E731
                spv_result = {
                    "set_point_column":     request.set_point_column,
                    "process_value_column": request.process_value_column,
                    "total_records": total,
                    "normal_count": normal_c, "normal_pct": pct(normal_c),
                    "lower_count":  lower_c,  "lower_pct":  pct(lower_c),
                    "higher_count": higher_c, "higher_pct": pct(higher_c),
                    "avg_deviation": round(float(abs_dev.mean()), 4),
                    "max_deviation": round(float(abs_dev.max()),  4),
                    "min_deviation": round(float(abs_dev.min()),  4),
                }
        except Exception:
            pass

    if spv_result:
        sv = spv_result
        story.append(_metric_cards([
            {"label": "Total Records",    "value": f"{sv['total_records']:,}", "sub": "data points",          "color": "#374151"},
            {"label": "Within Set Point", "value": f"{sv['normal_count']:,}", "sub": f"{sv['normal_pct']}%", "color": "#1A56DB"},
            {"label": "Below Set Point",  "value": f"{sv['lower_count']:,}",  "sub": f"{sv['lower_pct']}%",  "color": "#B45309"},
            {"label": "Above Set Point",  "value": f"{sv['higher_count']:,}", "sub": f"{sv['higher_pct']}%", "color": "#DC2626"},
        ], styles))
        story.append(Spacer(1, 8))

        half      = usable / 2 - 0.2 * cm
        left_tbl  = _kv_table([
            ("Set Point Column",     sv["set_point_column"]),
            ("Process Value Column", sv["process_value_column"]),
        ], styles, col_widths=[half * 0.45, half * 0.55])
        right_tbl = _kv_table([
            ("Avg Absolute Deviation", _fmt_num(sv["avg_deviation"])),
            ("Max Absolute Deviation", _fmt_num(sv["max_deviation"])),
            ("Min Absolute Deviation", _fmt_num(sv["min_deviation"])),
        ], styles, col_widths=[half * 0.55, half * 0.45])
        two_col = Table([[left_tbl, right_tbl]], colWidths=[half + 0.2 * cm, half])
        two_col.setStyle(TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 0), ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ]))
        story.append(two_col)
        story.append(Spacer(1, 8))

        di = [("Dalam SP",     sv["normal_count"], "#1A56DB"),
              ("Lebih Rendah", sv["lower_count"],  "#F59E0B"),
              ("Lebih Tinggi", sv["higher_count"], "#EF4444")]
        dl = [x[0] for x in di if x[1] > 0]
        dv = [x[1] for x in di if x[1] > 0]
        dc = {x[0]: x[2] for x in di if x[1] > 0}
        if dl:
            img_bytes = _plotly_to_image(_build_donut_figure(dl, dv, dc), width=500, height=260)
            if img_bytes:
                img = Image(io.BytesIO(img_bytes), width=12 * cm, height=6.5 * cm)
                img.hAlign = "CENTER"
                story.append(img)
    else:
        story.append(Paragraph(
            "No Set Point / Process Value columns selected or insufficient numeric data.",
            styles["no_data"],
        ))

    story.append(Spacer(1, 20))

    # FOOTER SETUP
    story.append(HRFlowable(width=usable, thickness=0.5, color=BORDER_COLOR))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Generated by Interactive Dashboard  •  {_dt.now().strftime('%d %B %Y %H:%M')}  •  {display_name}",
        styles["footer"],
    ))

    # WRITE PDF SETUP
    doc = SimpleDocTemplate(
        out_path,
        pageSize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title=f"Report Analisis – {display_name}",
        author="USERS",
    )
    doc.build(story)

    safe_stem = re.sub(r"[^\w\-]", "_", os.path.splitext(display_name)[0])
    from datetime import datetime as _dt2
    return f"report_{safe_stem}_{_dt2.now().strftime('%d%m%Y_%H%M')}.pdf"

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/files")
async def list_uploaded_files():
    files = sorted([
        n for n in os.listdir(upload_dir)
        if os.path.isfile(os.path.join(upload_dir, n))
        and os.path.splitext(n)[1].lower() in allowed_extensions
    ])
    meta    = _load_metadata()
    details = [{"filename": n, "display_name": meta.get(n, {}).get("original_filename", n)} for n in files]
    return {"files": files, "file_details": details}

@app.delete("/files/{filename}")
async def delete_uploaded_file(filename: str):
    safe = os.path.basename(filename)
    path = _resolve_file_path(safe)
    try:
        os.remove(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {exc}") from exc
    _remove_metadata(safe)
    return {"status": "deleted", "filename": safe}

@app.post("/upload")
async def upload_file(files: List[UploadFile] = File(...)):
    results = []
    for file in files:
        storage_name = _sanitize_storage_name(file.filename or "")
        file_path    = os.path.join(upload_dir, storage_name)
        try:
            with open(file_path, "wb") as out:
                shutil.copyfileobj(file.file, out)
            df = _read_dataframe(file_path, sheet_name=0)
            _set_metadata(storage_name, file.filename)
        except HTTPException as exc:
            if os.path.exists(file_path):
                os.remove(file_path)
            results.append({"status": "Gagal", "error": exc.detail, "original_filename": file.filename})
            continue
        except Exception as exc:
            if os.path.exists(file_path):
                os.remove(file_path)
            results.append({"status": "Gagal", "error": str(exc), "original_filename": file.filename})
            continue
        results.append({
            "status": "Berhasil",
            "filename": storage_name,
            "original_filename": file.filename,
            "rows": int(len(df)),
            "columns": df.columns.tolist(),
            "numeric_columns": df.select_dtypes(include="number").columns.tolist(),
        })
    return {"results": results}

@app.get("/dataset/{filename}/info")
async def get_dataset_info(filename: str, sheet_name: str | int = 0):
    file_path = _resolve_file_path(filename)
    info, _   = _detect_file_info(file_path, sheet_name=sheet_name)
    return {"filename": os.path.basename(filename), "info": info}

@app.get("/dataset/{filename}/columns")
async def get_dataset_columns(filename: str, sheet_name: str | int = 0):
    file_path = _resolve_file_path(filename)
    df        = _read_dataframe(file_path, sheet_name=sheet_name)
    return {
        "filename":            os.path.basename(filename),
        "columns":             df.columns.tolist(),
        "numeric_columns":     df.select_dtypes(include="number").columns.tolist(),
        "categorical_columns": df.select_dtypes(include=["object", "category", "bool"]).columns.tolist(),
        "datetime_columns":    df.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns.tolist(),
    }

@app.post("/analyze")
async def analyze_file(request: AnalyzeRequest):
    file_path = _resolve_file_path(request.filename)
    df        = _read_dataframe(file_path, sheet_name=request.sheet_name)
    stats     = _summary_statistics(df)
    if stats is None:
        raise HTTPException(status_code=400, detail="Tidak ada Data Terdeteksi Dalam File Upload!")
    return {"filename": os.path.basename(request.filename), "rows": int(len(df)), "summary_statistics": stats}

@app.post("/plot")
async def build_plot(request: PlotRequest):
    file_path = _resolve_file_path(request.filename)
    df        = _read_dataframe(file_path, sheet_name=request.sheet_name)
    if df.empty:
        raise HTTPException(status_code=400, detail="Dataset is empty.")
    figure = _build_figure(request, df)
    return {
        "filename":   os.path.basename(request.filename),
        "chart_type": request.chart_type,
        "figure":     json.loads(figure.to_json()),
        "config":     {"responsive": True, "displaylogo": False},
    }

@app.post("/duration")
async def timestamp_calculation(request: timestampRequest):
    file_path = _resolve_file_path(request.filename)
    df        = _read_dataframe(file_path, sheet_name=request.sheet_name)
    if request.timestamp_column not in df.columns:
        raise HTTPException(status_code=400,
            detail=f"Kolom '{request.timestamp_column}' tidak ada. Tersedia: {df.columns.tolist()}")
    parsed = _datetime_data(df, request.timestamp_column, request.date_column)
    if parsed.notna().sum() < 2:
        raise HTTPException(status_code=400, detail="Kolom Tidak Valid")
    invalid_count = int(parsed.isna().sum())
    parsed        = parsed.dropna().sort_values()
    start_time    = parsed.iloc[0]
    end_time      = parsed.iloc[-1]
    total_secs    = (end_time - start_time).total_seconds()
    h, rem        = divmod(int(total_secs), 3600)
    m, s          = divmod(rem, 60)
    return {
        "filename":             request.filename,
        "timestamp_column":     request.timestamp_column,
        "total_records":        int(len(parsed)),
        "invalid_rows_skipped": invalid_count,
        "start_time": start_time.strftime("%d-%m-%Y %H:%M:%S"),
        "end_time":   end_time.strftime("%d-%m-%Y %H:%M:%S"),
        "total_duration": {
            "second":         total_secs,
            "minutes":        round(total_secs / 60, 2),
            "human_readable": f"{h:02d}:{m:02d}:{s:02d}",
        },
    }

@app.post("/counting")
async def counting_auto_mode(request: autoCounting):
    file_path = _resolve_file_path(request.filename)
    df        = _read_dataframe(file_path, sheet_name=request.sheet_name)
    for col in [request.timestamp_column, request.state_column]:
        if col not in df.columns:
            raise HTTPException(status_code=400,
                detail=f"Kolom tidak ditemukan. Tersedia: {df.columns.tolist()}")
    df          = df.copy()
    df["time"]  = _datetime_data(df, request.timestamp_column, request.date_column)
    df["state"] = pd.to_numeric(df[request.state_column], errors="coerce")
    df = df.dropna(subset=["time", "state"]).sort_values("time").reset_index(drop=True)
    if df.empty or len(df) < 2:
        raise HTTPException(status_code=400, detail="Data Auto/Manual belum lengkap.")
    df["_state_change"] = df["state"] != df["state"].shift(1)
    df["_group"]        = df["_state_change"].cumsum()
    segments: list[dict] = []
    for gid, gdf in df.groupby("_group"):
        sv = int(gdf["state"].iloc[0])
        st = gdf["time"].iloc[0]
        li = gdf.index[-1]
        et = df["time"].iloc[li + 1] if li + 1 < len(df) else gdf["time"].iloc[-1]
        ds = (et - st).total_seconds()
        h, rem = divmod(int(ds), 3600)
        m, s   = divmod(rem, 60)
        segments.append({
            "segment": int(gid), "state": sv,
            "label": "Auto" if sv == 1 else "Manual",
            "start_time": st.strftime("%d-%m-%Y %H:%M:%S"),
            "end_time":   et.strftime("%d-%m-%Y %H:%M:%S"),
            "duration_seconds": ds, "duration_human": f"{h:02d}:{m:02d}:{s:02d}",
            "record_count": len(gdf),
        })
    auto_t   = sum(s["duration_seconds"] for s in segments if s["state"] == 1)
    manual_t = sum(s["duration_seconds"] for s in segments if s["state"] == 0)
    total_t  = auto_t + manual_t
    return {
        "filename":       request.filename,
        "total_segments": len(segments),
        "segments":       segments,
        "summary": {
            "total_auto_counting":   {"seconds": auto_t,   "human": _fmt_secs(auto_t),   "percentage": round(auto_t   / total_t * 100, 2) if total_t else 0.0},
            "total_manual_counting": {"seconds": manual_t, "human": _fmt_secs(manual_t), "percentage": round(manual_t / total_t * 100, 2) if total_t else 0.0},
            "total_recorded":        {"seconds": total_t,  "human": _fmt_secs(total_t)},
        },
    }

@app.post("/spv-analysis")
async def spv_analysis(request: SPVRequest):
    file_path = _resolve_file_path(request.filename)
    df        = _read_dataframe(file_path, sheet_name=request.sheet_name)
    for col in [request.set_point_column, request.process_value_column]:
        if col not in df.columns:
            raise HTTPException(status_code=400,
                detail=f"Kolom '{col}' tidak ditemukan. Tersedia: {df.columns.tolist()}")
    df = df.copy()
    df["_sp"] = pd.to_numeric(df[request.set_point_column],     errors="coerce")
    df["_pv"] = pd.to_numeric(df[request.process_value_column], errors="coerce")
    df = df.dropna(subset=["_sp", "_pv"]).reset_index(drop=True)
    if df.empty:
        raise HTTPException(status_code=400, detail="Tidak ada nilai numerik valid pada kolom yang dipilih.")
    df["_deviation"] = df["_pv"] - df["_sp"]
    df["_status"]    = df["_deviation"].apply(lambda d: "normal" if d == 0 else ("lower" if d < 0 else "higher"))
    total        = len(df)
    normal_count = int((df["_status"] == "normal").sum())
    lower_count  = int((df["_status"] == "lower").sum())
    higher_count = int((df["_status"] == "higher").sum())
    pct          = lambda n: round(n / total * 100, 2) if total else 0.0  # noqa: E731
    abs_devs     = df["_deviation"].abs()
    return {
        "filename":             request.filename,
        "set_point_column":     request.set_point_column,
        "process_value_column": request.process_value_column,
        "total_records":        total,
        "summary": {
            "normal":  {"count": normal_count,  "percentage": pct(normal_count)},
            "lower":   {"count": lower_count,   "percentage": pct(lower_count)},
            "higher":  {"count": higher_count,  "percentage": pct(higher_count)},
        },
        "statistics": {
            "avg_deviation": round(float(abs_devs.mean()), 4),
            "max_deviation": round(float(abs_devs.max()),  4),
            "min_deviation": round(float(abs_devs.min()),  4),
        },
    }

def _run_report_job(job_id: str, request: ReportRequest) -> None:
    out_path = os.path.join(report_dir, f"{job_id}.pdf")
    try:
        download_name = _build_pdf(request, out_path)
        _set_job(job_id, status="done", filename=download_name)
    except Exception as exc:
        _set_job(job_id, status="error", detail=str(exc))


@app.post("/generate-report")
async def generate_report_sync(request: ReportRequest):
    out_path = os.path.join(report_dir, f"sync_{uuid.uuid4().hex[:8]}.pdf")
    try:
        download_name = _build_pdf(request, out_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    def _iter():
        with open(out_path, "rb") as fh:
            yield from fh
        os.remove(out_path)

    return StreamingResponse(
        _iter(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )

@app.post("/generate-report/async")
async def generate_report_async(request: ReportRequest, background_tasks: BackgroundTasks):
    job_id = uuid.uuid4().hex
    _set_job(job_id, status="pending")
    background_tasks.add_task(_run_report_job, job_id, request)
    return {"job_id": job_id, "status": "pending"}

@app.get("/generate-report/status/{job_id}")
async def report_job_status(job_id: str):
    jobs = _load_jobs()
    job  = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **job}

@app.get("/generate-report/download/{job_id}")
async def report_job_download(job_id: str):
    jobs = _load_jobs()
    job  = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=425, detail=f"Report not ready yet: {job['status']}")
    out_path = os.path.join(report_dir, f"{job_id}.pdf")
    if not os.path.exists(out_path):
        raise HTTPException(status_code=404, detail="PDF file missing")
    return FileResponse(
        out_path,
        media_type="application/pdf",
        filename=job.get("filename", f"report_{job_id}.pdf"),
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)