import csv as csv_module
import json
import os
import re
import shutil
import uuid
import warnings
from typing import Literal, List
import io
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, HRFlowable, KeepTogether,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

upload_dir = "/tmp/uploads"
os.makedirs(upload_dir, exist_ok=True)
metadata_path = os.path.join(upload_dir, "_metadata.json")

allowed_extensions = {".csv", ".xlsx"}
csv_fallback_encodings = ("utf-8", "utf-16", "latin-1")
_DATE_PREFIX_RE = re.compile(r"^\s*(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})(?:\D|$)")

# ── Report colour palette (matches dashboard blue theme) ──────────────────────
_BLUE_DARK  = colors.HexColor("#1e3a5f")
_BLUE_MID   = colors.HexColor("#2f6df5")
_BLUE_LIGHT = colors.HexColor("#dbe7ff")
_GREY_LIGHT = colors.HexColor("#f4f7ff")
_GREY_TEXT  = colors.HexColor("#6b7280")
_WHITE      = colors.white


# ══════════════════════════════════════════════════════════════════════════════
#  METADATA HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_metadata() -> dict:
    if not os.path.exists(metadata_path):
        return {}
    try:
        with open(metadata_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_metadata(metadata: dict) -> None:
    tmp_path = f"{metadata_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, metadata_path)


def _set_metadata(filename: str, original_filename: str | None) -> None:
    metadata = _load_metadata()
    metadata[filename] = {"original_filename": original_filename or filename}
    _save_metadata(metadata)


def _remove_metadata(filename: str) -> None:
    metadata = _load_metadata()
    if filename in metadata:
        del metadata[filename]
        _save_metadata(metadata)


# ══════════════════════════════════════════════════════════════════════════════
#  PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  FILE / DATAFRAME HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize_storage_name(filename: str) -> str:
    base_name = os.path.basename(filename or "")
    if not base_name:
        raise HTTPException(status_code=400, detail="File Kosong")
    file_stem, file_ext = os.path.splitext(base_name)
    file_ext = file_ext.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Only CSV and XLSX files are allowed.")
    safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in file_stem).strip("_")
    if not safe_stem:
        safe_stem = "dataset"
    short_id = uuid.uuid4().hex[:8]
    return f"{safe_stem}_{short_id}{file_ext}"


def _resolve_file_path(filename: str) -> str:
    safe_name = os.path.basename(filename)
    file_path = os.path.join(upload_dir, safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")
    return file_path


_META_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,29}$")


def _detect_file_info(file_path: str, sheet_name: str | int = 0) -> tuple[dict, int]:
    info: dict = {}
    skip_rows: int = 0
    _, ext = os.path.splitext(file_path.lower())

    if ext == ".csv":
        for encoding in csv_fallback_encodings:
            try:
                with open(file_path, encoding=encoding, newline="") as fh:
                    reader = csv_module.reader(fh, delimiter="\t")
                    for row in reader:
                        non_empty = [cell.strip() for cell in row if cell.strip()]
                        if len(non_empty) == 2 and _META_KEY_RE.match(non_empty[0]):
                            info[non_empty[0]] = non_empty[1]
                            skip_rows += 1
                        else:
                            break
                break
            except Exception:
                info = {}
                skip_rows = 0
        return info, skip_rows

    if ext == ".xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            if isinstance(sheet_name, int):
                ws = wb.worksheets[sheet_name] if sheet_name < len(wb.worksheets) else wb.active
            else:
                ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb.active

            for row in ws.iter_rows(values_only=True):
                non_empty = [
                    str(cell).strip()
                    for cell in row
                    if cell is not None and str(cell).strip()
                ]
                if len(non_empty) == 2 and _META_KEY_RE.match(non_empty[0]):
                    info[non_empty[0]] = non_empty[1]
                    skip_rows += 1
                else:
                    break
            wb.close()
        except Exception:
            info = {}
            skip_rows = 0
        return info, skip_rows

    return info, skip_rows


def _detect_csv_encoding(file_path: str) -> str | None:
    for encoding in csv_fallback_encodings:
        try:
            with open(file_path, encoding=encoding) as fh:
                fh.read(4096)
            return encoding
        except UnicodeDecodeError:
            continue
    return None


def _read_dataframe(file_path: str, sheet_name: str | int = 0) -> pd.DataFrame:
    _, file_ext = os.path.splitext(file_path.lower())

    if file_ext == ".csv":
        _, skip = _detect_file_info(file_path)
        encoding = _detect_csv_encoding(file_path)
        if encoding is None:
            raise HTTPException(status_code=400, detail="CSV encoding tidak didukung.")
        if encoding.lower().replace("-", "") in ("utf16", "utf_16"):
            sep, engine = "\t", "c"
        else:
            sep, engine = None, "python"

        df = pd.read_csv(
            file_path,
            encoding=encoding,
            sep=sep,
            engine=engine,
            skiprows=skip if skip else None,
        )
        df = df.dropna(axis=1, how="all")
        df = df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed: \d+$")]
        return df

    if file_ext == ".xlsx":
        try:
            _, skip = _detect_file_info(file_path, sheet_name=sheet_name)
            df = pd.read_excel(file_path, sheet_name=sheet_name, skiprows=skip if skip else None)
            df = df.dropna(axis=1, how="all")
            df = df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed: \d+$")]
            return df
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    raise HTTPException(status_code=400, detail="Unsupported file format.")


# ══════════════════════════════════════════════════════════════════════════════
#  DATETIME HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _prefer_dayfirst(raw: pd.Series, default: bool = True) -> bool:
    samples = raw.dropna().astype(str).str.strip()
    if samples.empty:
        return default

    dayfirst_votes = 0
    monthfirst_votes = 0

    for value in samples.head(200):
        match = _DATE_PREFIX_RE.match(value)
        if not match:
            continue
        first_text, second_text, _ = match.groups()
        if len(first_text) == 4:
            continue
        first = int(first_text)
        second = int(second_text)
        if first > 12 >= second:
            dayfirst_votes += 1
        elif second > 12 >= first:
            monthfirst_votes += 1

    if dayfirst_votes or monthfirst_votes:
        return dayfirst_votes >= monthfirst_votes
    return default


def _parse_datetime_series(raw: pd.Series, default_dayfirst: bool = True) -> pd.Series:
    dayfirst = _prefer_dayfirst(raw, default=default_dayfirst)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        parsed = pd.to_datetime(raw, dayfirst=dayfirst, errors="coerce")
        alternate = pd.to_datetime(raw, dayfirst=not dayfirst, errors="coerce")

    if alternate.notna().sum() > parsed.notna().sum():
        return alternate
    return parsed


def _fix_midnight_rollover(parsed: pd.Series) -> pd.Series:
    time_only = parsed.dt.hour * 3600 + parsed.dt.minute * 60 + parsed.dt.second
    rollover = (time_only < time_only.shift(1)).fillna(False).cumsum()
    return parsed + pd.to_timedelta(rollover, unit="D")


def _datetime_data(df: pd.DataFrame, time_col: str, date_col: str | None) -> pd.Series:
    raw = df[time_col].astype(str).str.strip()

    if date_col and date_col in df.columns:
        date_raw = df[date_col].astype(str).str.strip()
        date_raw = date_raw.replace(r"^\s*$", pd.NA, regex=True)
        date_series = _parse_datetime_series(date_raw)
        date_series = date_series.ffill()
        combined = date_series.dt.strftime("%d-%m-%Y") + " " + raw
        parsed = pd.to_datetime(combined, format="%d-%m-%Y %H:%M:%S", errors="coerce")
        if parsed.notna().sum() >= 2:
            return parsed

    parsed = _parse_datetime_series(raw)
    if parsed.notna().sum() >= 2:
        if parsed.dt.date.nunique() > 1:
            return parsed
        return _fix_midnight_rollover(parsed)

    parsed = pd.to_datetime(raw, format="%H:%M:%S", errors="coerce")
    if parsed.notna().sum() >= 2:
        return _fix_midnight_rollover(parsed)

    return parsed


# ══════════════════════════════════════════════════════════════════════════════
#  ANALYTICS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _summary_statistics(df: pd.DataFrame) -> dict | None:
    numeric_cols = df.select_dtypes(include="number").columns
    if numeric_cols.empty:
        return None
    numeric_df = df[numeric_cols]
    return {
        "mean":   numeric_df.mean().to_dict(),
        "median": numeric_df.median().to_dict(),
        "min":    numeric_df.min().to_dict(),
        "max":    numeric_df.max().to_dict(),
        "std":    numeric_df.std().to_dict(),
    }


def _as_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return value


def _validate_columns(df: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [col for col in columns if col and col not in df.columns]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown {label} column(s): {', '.join(missing)}",
        )


def _maybe_sort_datetime(
    df: pd.DataFrame, x_col: str | None, date_col: str | None = None
) -> pd.DataFrame:
    if not x_col:
        return df
    sorted_df = df.copy()
    parsed = _datetime_data(sorted_df, x_col, date_col)
    if parsed.notna().sum() == 0:
        return df
    sorted_df[x_col] = parsed
    return sorted_df.sort_values(by=x_col)


def _build_figure(request: PlotRequest, df: pd.DataFrame):
    y_cols = _as_list(request.y)
    x_col  = request.x
    simple_cols = [x_col, request.color, request.size, request.names, request.values, request.z]
    _validate_columns(df, [col for col in simple_cols if col], "parameter")
    _validate_columns(df, y_cols, "y")

    chart_df = df
    if request.chart_type in {"line", "area"}:
        chart_df = _maybe_sort_datetime(chart_df, x_col, request.date_column)

    if request.chart_type == "line":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Line chart requires `x` and at least one `y`.")
        figure = px.line(chart_df, x=x_col, y=y_cols, color=request.color,
                         title=request.title or "Line Chart")

    elif request.chart_type == "bar":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Bar chart requires `x` and at least one `y`.")
        y_arg = y_cols if len(y_cols) > 1 else y_cols[0]
        figure = px.bar(chart_df, x=x_col, y=y_arg, color=request.color,
                        title=request.title or "Bar Chart", barmode="group")

    elif request.chart_type == "scatter":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Scatter chart requires `x` and at least one `y`.")
        y_arg = y_cols if len(y_cols) > 1 else y_cols[0]
        figure = px.scatter(chart_df, x=x_col, y=y_arg, color=request.color,
                            size=request.size, title=request.title or "Scatter Plot")

    elif request.chart_type == "area":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Area chart requires `x` and at least one `y`.")
        y_arg = y_cols if len(y_cols) > 1 else y_cols[0]
        figure = px.area(chart_df, x=x_col, y=y_arg, color=request.color,
                         title=request.title or "Area Chart")

    elif request.chart_type == "box":
        if not y_cols:
            raise HTTPException(status_code=400, detail="Box chart requires at least one `y`.")
        y_arg = y_cols if len(y_cols) > 1 else y_cols[0]
        figure = px.box(chart_df, x=x_col, y=y_arg, color=request.color,
                        title=request.title or "Box Plot")

    elif request.chart_type == "heatmap":
        if x_col and y_cols:
            figure = px.density_heatmap(
                chart_df, x=x_col, y=y_cols[0], z=request.z,
                title=request.title or "Heatmap",
                color_continuous_scale="Viridis",
            )
        else:
            numeric_df = chart_df.select_dtypes(include="number")
            if numeric_df.shape[1] < 2:
                raise HTTPException(
                    status_code=400,
                    detail="Heatmap without `x`/`y` requires at least two numeric columns.",
                )
            corr = numeric_df.corr(numeric_only=True)
            figure = go.Figure(data=go.Heatmap(
                z=corr.values, x=list(corr.columns), y=list(corr.index),
                colorscale="RdBu", zmid=0,
            ))
            figure.update_layout(title=request.title or "Correlation Heatmap")

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported chart type: {request.chart_type}")

    figure.update_layout(template="plotly_white")
    return figure


# ══════════════════════════════════════════════════════════════════════════════
#  REPORT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _col_label(col: str) -> str:
    """Convert snake_case column name to Title Case label."""
    return col.replace("_", " ").title()


def _build_report_styles() -> dict:
    """Return a dict of named ParagraphStyles for the PDF report."""
    base = getSampleStyleSheet()

    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontSize=20,
            textColor=_BLUE_DARK,
            spaceAfter=4,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["Normal"],
            fontSize=10,
            textColor=_GREY_TEXT,
            spaceAfter=14,
            alignment=TA_CENTER,
        ),
        "section": ParagraphStyle(
            "SectionHeader",
            parent=base["Heading2"],
            fontSize=12,
            textColor=_BLUE_DARK,
            spaceBefore=14,
            spaceAfter=6,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#374151"),
            leading=14,
        ),
        "kv_key": ParagraphStyle(
            "KVKey",
            parent=base["Normal"],
            fontSize=9,
            textColor=_GREY_TEXT,
            fontName="Helvetica",
        ),
        "kv_val": ParagraphStyle(
            "KVVal",
            parent=base["Normal"],
            fontSize=9,
            textColor=_BLUE_DARK,
            fontName="Helvetica-Bold",
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=base["Normal"],
            fontSize=7,
            textColor=_GREY_TEXT,
            alignment=TA_CENTER,
        ),
        "th": ParagraphStyle(
            "TH",
            parent=base["Normal"],
            textColor=_WHITE,
            fontSize=8,
            fontName="Helvetica-Bold",
        ),
    }


def _info_table(pairs: list[tuple[str, str]], styles: dict) -> Table:
    """Two-column key/value info table (no borders, alternating background)."""
    data = [
        [
            Paragraph(k, styles["kv_key"]),
            Paragraph(str(v), styles["kv_val"]),
        ]
        for k, v in pairs
    ]
    tbl = Table(data, colWidths=[5 * cm, 11 * cm])
    row_styles = []
    for i in range(len(data)):
        bg = _GREY_LIGHT if i % 2 == 0 else _WHITE
        row_styles.append(("BACKGROUND", (0, i), (-1, i), bg))

    tbl.setStyle(TableStyle([
        *row_styles,
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
    ]))
    return tbl


def _stats_table(stats: dict, styles: dict) -> Table:
    """Summary statistics table with styled header and alternating rows."""
    headers = ["Column", "Mean", "Median", "Min", "Max", "Std Dev"]
    header_row = [Paragraph(f"<b>{h}</b>", styles["th"]) for h in headers]
    rows = [header_row]

    col_names = list(stats["mean"].keys())
    for i, col in enumerate(col_names):
        row = [
            Paragraph(_col_label(col),                        styles["body"]),
            Paragraph(f"{stats['mean'].get(col, 0):,.3f}",   styles["body"]),
            Paragraph(f"{stats['median'].get(col, 0):,.3f}", styles["body"]),
            Paragraph(f"{stats['min'].get(col, 0):,.3f}",    styles["body"]),
            Paragraph(f"{stats['max'].get(col, 0):,.3f}",    styles["body"]),
            Paragraph(f"{stats['std'].get(col, 0):,.3f}",    styles["body"]),
        ]
        rows.append(row)

    alt_styles = [
        ("BACKGROUND", (0, i), (-1, i), _GREY_LIGHT if i % 2 == 0 else _WHITE)
        for i in range(1, len(rows))
    ]

    col_widths = [5 * cm, 2.8 * cm, 2.8 * cm, 2.4 * cm, 2.4 * cm, 2.6 * cm]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _BLUE_MID),
        ("TEXTCOLOR",     (0, 0), (-1, 0), _WHITE),
        *alt_styles,
        ("ALIGN",         (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN",         (0, 0), (0, -1), "LEFT"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ("LINEBELOW",     (0, 0), (-1, 0), 1.5, _BLUE_DARK),
    ]))
    return tbl


def _catalogue_table(df: pd.DataFrame, styles: dict) -> Table:
    """Column catalogue table listing dtype, non-null count, and unique values."""
    headers = ["#", "Column Name", "Dtype", "Non-Null", "Unique Values"]
    header_row = [Paragraph(h, styles["th"]) for h in headers]
    rows = [header_row]

    for idx, col in enumerate(df.columns):
        row = [
            Paragraph(str(idx + 1),              styles["body"]),
            Paragraph(col,                        styles["body"]),
            Paragraph(str(df[col].dtype),         styles["body"]),
            Paragraph(f"{df[col].notna().sum():,}", styles["body"]),
            Paragraph(f"{df[col].nunique():,}",   styles["body"]),
        ]
        rows.append(row)

    alt_styles = [
        ("BACKGROUND", (0, i), (-1, i), _GREY_LIGHT if i % 2 == 0 else _WHITE)
        for i in range(1, len(rows))
    ]

    tbl = Table(
        rows,
        colWidths=[1 * cm, 6.5 * cm, 3 * cm, 3 * cm, 3.5 * cm],
        repeatRows=1,
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _BLUE_DARK),
        ("TEXTCOLOR",     (0, 0), (-1, 0), _WHITE),
        *alt_styles,
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ("LINEBELOW",     (0, 0), (-1, 0), 1.5, _BLUE_DARK),
    ]))
    return tbl


def _make_chart_image(
    df: pd.DataFrame,
    numeric_cols,
    width_cm: float = 16,
    height_cm: float = 8,
) -> Image | None:
    """Generate a multi-line Plotly overview chart and return as ReportLab Image."""
    try:
        y_cols = list(numeric_cols[:4])   # cap at 4 series for readability
        if not y_cols:
            return None

        fig = px.line(
            df.reset_index(drop=True),
            y=y_cols,
            title="Data Overview",
            template="plotly_white",
        )
        fig.update_layout(
            margin=dict(t=40, b=30, l=50, r=20),
            legend=dict(orientation="h", y=-0.25),
            font=dict(family="Arial", size=11),
            plot_bgcolor="#f4f7ff",
            paper_bgcolor="white",
        )
        fig.update_traces(line=dict(width=1.5))

        img_bytes = pio.to_image(fig, format="png", width=900, height=450, scale=2)
        stream = io.BytesIO(img_bytes)
        return Image(stream, width=width_cm * cm, height=height_cm * cm)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  API ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/files")
async def list_uploaded_files():
    files = sorted([
        name for name in os.listdir(upload_dir)
        if os.path.isfile(os.path.join(upload_dir, name))
        and os.path.splitext(name)[1].lower() in allowed_extensions
    ])
    metadata = _load_metadata()
    details = [
        {
            "filename":     name,
            "display_name": metadata.get(name, {}).get("original_filename", name),
        }
        for name in files
    ]
    return {"files": files, "file_details": details}


@app.delete("/files/{filename}")
async def delete_uploaded_file(filename: str):
    safe_name = os.path.basename(filename)
    file_path = _resolve_file_path(safe_name)
    try:
        os.remove(file_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {exc}") from exc
    _remove_metadata(safe_name)
    return {"status": "deleted", "filename": safe_name}


@app.post("/upload")
async def upload_file(files: List[UploadFile] = File(...)):
    results = []
    for file in files:
        storage_name = _sanitize_storage_name(file.filename or "")
        file_path = os.path.join(upload_dir, storage_name)
        try:
            with open(file_path, "wb") as output_file:
                shutil.copyfileobj(file.file, output_file)
            df = _read_dataframe(file_path)
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

        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        results.append({
            "status":           "Berhasil",
            "filename":         storage_name,
            "original_filename": file.filename,
            "rows":             int(len(df)),
            "columns":          df.columns.tolist(),
            "numeric_columns":  numeric_cols,
        })
    return {"results": results}


@app.get("/dataset/{filename}/info")
async def get_dataset_info(filename: str, sheet_name: str | int = 0):
    file_path = _resolve_file_path(filename)
    info, _ = _detect_file_info(file_path, sheet_name=sheet_name)
    return {"filename": os.path.basename(filename), "info": info}


@app.get("/dataset/{filename}/columns")
async def get_dataset_columns(filename: str, sheet_name: str | int = 0):
    file_path = _resolve_file_path(filename)
    df = _read_dataframe(file_path, sheet_name=sheet_name)
    return {
        "filename":             os.path.basename(filename),
        "columns":              df.columns.tolist(),
        "numeric_columns":      df.select_dtypes(include="number").columns.tolist(),
        "categorical_columns":  df.select_dtypes(include=["object", "category", "bool"]).columns.tolist(),
        "datetime_columns":     df.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns.tolist(),
    }


@app.post("/analyze")
async def analyze_file(request: AnalyzeRequest):
    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)
    stats = _summary_statistics(df)
    if stats is None:
        raise HTTPException(status_code=400, detail="Tidak ada Data Terdeteksi Dalam File Upload!")
    return {
        "filename":           os.path.basename(request.filename),
        "rows":               int(len(df)),
        "summary_statistics": stats,
    }


@app.post("/plot")
async def build_plot(request: PlotRequest):
    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)
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
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)
    if request.timestamp_column not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=f"Kolom '{request.timestamp_column}' tidak ada. "
                   f"Kolom tersedia: {df.columns.tolist()}",
        )

    parsed = _datetime_data(df, request.timestamp_column, request.date_column)
    if parsed.notna().sum() < 2:
        raise HTTPException(status_code=400, detail="Kolom Tidak Valid")

    invalid_count = int(parsed.isna().sum())
    parsed = parsed.dropna().sort_values()
    start_time = parsed.iloc[0]
    end_time   = parsed.iloc[-1]
    total_secs = (end_time - start_time).total_seconds()
    hours, remainder = divmod(int(total_secs), 3600)
    minutes, seconds = divmod(remainder, 60)
    return {
        "filename":             request.filename,
        "timestamp_column":     request.timestamp_column,
        "total_records":        int(len(parsed)),
        "invalid_rows_skipped": invalid_count,
        "start_time":           start_time.strftime("%d-%m-%Y %H:%M:%S"),
        "end_time":             end_time.strftime("%d-%m-%Y %H:%M:%S"),
        "total_duration": {
            "second":         total_secs,
            "minutes":        round(total_secs / 60, 2),
            "human_readable": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
        },
    }


@app.post("/counting")
async def counting_auto_mode(request: autoCounting):
    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)

    for col in [request.timestamp_column, request.state_column]:
        if col not in df.columns:
            raise HTTPException(
                status_code=400,
                detail=f"Kolom tidak ditemukan. Kolom tersedia: {df.columns.tolist()}",
            )

    df = df.copy()
    df["time"]  = _datetime_data(df, request.timestamp_column, request.date_column)
    df["state"] = pd.to_numeric(df[request.state_column], errors="coerce")
    df = df.dropna(subset=["time", "state"]).sort_values("time").reset_index(drop=True)

    if df.empty or len(df) < 2:
        raise HTTPException(status_code=400, detail="Data Auto/Manual belum lengkap.")

    df["_state_change"] = df["state"] != df["state"].shift(1)
    df["_group"]        = df["_state_change"].cumsum()

    segments: list[dict] = []
    for group_id, group_df in df.groupby("_group"):
        state_val  = int(group_df["state"].iloc[0])
        start_time = group_df["time"].iloc[0]
        last_idx   = group_df.index[-1]
        end_time   = df["time"].iloc[last_idx + 1] if last_idx + 1 < len(df) else group_df["time"].iloc[-1]
        dur_secs   = (end_time - start_time).total_seconds()
        h, rem = divmod(int(dur_secs), 3600)
        m, s   = divmod(rem, 60)
        segments.append({
            "segment":          int(group_id),
            "state":            state_val,
            "label":            "Auto" if state_val == 1 else "Manual",
            "start_time":       start_time.strftime("%d-%m-%Y %H:%M:%S"),
            "end_time":         end_time.strftime("%d-%m-%Y %H:%M:%S"),
            "duration_seconds": dur_secs,
            "duration_human":   f"{h:02d}:{m:02d}:{s:02d}",
            "record_count":     len(group_df),
        })

    total_auto    = sum(s["duration_seconds"] for s in segments if s["state"] == 1)
    total_manual  = sum(s["duration_seconds"] for s in segments if s["state"] == 0)
    total_process = total_auto + total_manual

    def fmt(secs: float) -> str:
        h, rem = divmod(int(secs), 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    return {
        "filename":       request.filename,
        "total_segments": len(segments),
        "segments":       segments,
        "summary": {
            "total_auto_counting": {
                "seconds":    total_auto,
                "human":      fmt(total_auto),
                "percentage": round(total_auto / total_process * 100, 2) if total_process else 0.0,
            },
            "total_manual_counting": {
                "seconds":    total_manual,
                "human":      fmt(total_manual),
                "percentage": round(total_manual / total_process * 100, 2) if total_process else 0.0,
            },
            "total_recorded": {
                "seconds": total_process,
                "human":   fmt(total_process),
            },
        },
    }


@app.post("/spv-analysis")
async def spv_analysis(request: SPVRequest):
    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)

    for col in [request.set_point_column, request.process_value_column]:
        if col not in df.columns:
            raise HTTPException(
                status_code=400,
                detail=f"Kolom '{col}' tidak ditemukan. Kolom tersedia: {df.columns.tolist()}",
            )

    df = df.copy()
    df["_sp"] = pd.to_numeric(df[request.set_point_column],     errors="coerce")
    df["_pv"] = pd.to_numeric(df[request.process_value_column], errors="coerce")
    df = df.dropna(subset=["_sp", "_pv"]).reset_index(drop=True)

    if df.empty:
        raise HTTPException(status_code=400, detail="Tidak ada nilai numerik valid pada kolom yang dipilih.")

    df["_deviation"] = df["_pv"] - df["_sp"]
    df["_status"]    = df["_deviation"].apply(
        lambda d: "normal" if d == 0 else ("lower" if d < 0 else "higher")
    )

    total        = len(df)
    normal_count = int((df["_status"] == "normal").sum())
    lower_count  = int((df["_status"] == "lower").sum())
    higher_count = int((df["_status"] == "higher").sum())

    def pct(n: int) -> float:
        return round((n / total) * 100, 2) if total else 0.0

    abs_devs = df["_deviation"].abs()
    return {
        "filename":              request.filename,
        "set_point_column":      request.set_point_column,
        "process_value_column":  request.process_value_column,
        "total_records":         total,
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


# ══════════════════════════════════════════════════════════════════════════════
#  GENERATE PDF REPORT
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/generate-report")
async def generate_report(request: ReportRequest):
    file_path    = _resolve_file_path(request.filename)
    df           = _read_dataframe(file_path)
    file_info, _ = _detect_file_info(file_path)
    metadata     = _load_metadata()
    display_name = metadata.get(request.filename, {}).get("original_filename", request.filename)
    numeric_cols = df.select_dtypes(include="number").columns
    stats        = _summary_statistics(df)   # ← fixed: was `= _summary_statistics` (no call)

    # ── PDF document setup ────────────────────────────────────────────────────
    buffer = io.BytesIO()
    MARGIN = 2 * cm

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f"Dashboard Report – {display_name}",
        author="Interactive Dashboard",
    )
    styles   = _build_report_styles()
    elements = []

    # ══ HEADER ════════════════════════════════════════════════════════════════
    elements.append(Paragraph("INTERACTIVE DASHBOARD", styles["title"]))
    elements.append(Paragraph("Automated Analysis Report", styles["subtitle"]))
    elements.append(HRFlowable(width="100%", thickness=2, color=_BLUE_MID, spaceAfter=10))

    # ══ DATASET INFORMATION ═══════════════════════════════════════════════════
    elements.append(Paragraph("Dataset Information", styles["section"]))

    info_pairs: list[tuple[str, str]] = [
        ("File Name",       display_name),
        ("Total Rows",      f"{len(df):,}"),
        ("Total Columns",   str(len(df.columns))),
        ("Numeric Columns", str(len(numeric_cols))),
        ("Generated At",    datetime.now().strftime("%d-%m-%Y %H:%M:%S")),
    ]
    # Append any embedded file metadata (e.g. EQUIPMENT_ID, PLANT, UNIT)
    for k, v in file_info.items():
        info_pairs.append((_col_label(k), v))

    elements.append(_info_table(info_pairs, styles))
    elements.append(Spacer(1, 10))

    # ══ SUMMARY STATISTICS ════════════════════════════════════════════════════
    if stats:
        elements.append(Paragraph("Summary Statistics", styles["section"]))
        elements.append(
            Paragraph(
                "Descriptive statistics (mean, median, min, max, std dev) "
                "for all numeric columns in the dataset.",
                styles["body"],
            )
        )
        elements.append(Spacer(1, 6))
        elements.append(KeepTogether([_stats_table(stats, styles)]))
        elements.append(Spacer(1, 10))

    # ══ DATA VISUALISATION ════════════════════════════════════════════════════
    chart_img = _make_chart_image(df, numeric_cols)
    if chart_img:
        n_plotted = min(4, len(numeric_cols))
        elements.append(Paragraph("Data Visualisation", styles["section"]))
        elements.append(
            Paragraph(
                f"Line chart of the first {n_plotted} numeric "
                f"column{'s' if n_plotted > 1 else ''} in the dataset.",
                styles["body"],
            )
        )
        elements.append(Spacer(1, 6))
        elements.append(chart_img)
        elements.append(Spacer(1, 10))

    # ══ COLUMN CATALOGUE ══════════════════════════════════════════════════════
    elements.append(Paragraph("Column Catalogue", styles["section"]))
    elements.append(
        Paragraph(
            "Full list of columns with data type, non-null count, and unique value count.",
            styles["body"],
        )
    )
    elements.append(Spacer(1, 6))
    elements.append(KeepTogether([_catalogue_table(df, styles)]))

    # ══ FOOTER ════════════════════════════════════════════════════════════════
    elements.append(Spacer(1, 18))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=_GREY_TEXT))
    elements.append(Spacer(1, 4))
    elements.append(
        Paragraph(
            f"Generated by Interactive Dashboard · Sinar Mas · "
            f"{datetime.now().strftime('%d %B %Y')}",
            styles["footer"],
        )
    )

    # ══ BUILD & STREAM ════════════════════════════════════════════════════════
    doc.build(elements)
    buffer.seek(0)

    safe_name = display_name.replace(" ", "_").replace("/", "-")
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            # fixed: was "Content=Disposition" (= instead of -)
            "Content-Disposition": f'attachment; filename="Report_{safe_name}.pdf"'
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
