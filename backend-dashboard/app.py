import csv as csv_module
import json
import os
import re
import shutil
import uuid
import warnings
from typing import Literal, List
import io

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import plotly.io as pio

from reportlab.platypus import(
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
os.makedirs(upload_dir, exist_ok=True)
metadata_path = os.path.join(upload_dir, "_metadata.json")

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

# Color Brand
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

PAGE_W, PAGE_H  = A4
MARGIN          = 1.8 * cm

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
    date_column: str | None = None  # ← for midnight rollover fix

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
    filename:str
    sheet_name : str | int = 0
    # Hour Record
    timestamp_column : str | None = None
    date_column : str | None = None
    # Auto/Manual
    state_column : str | None = None
    # Range Value Analysis
    set_point_column : str | None = None
    process_value_column : str | None = None
    
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

def _parse_format_time(raw:pd.Series,default_dayfirst:bool=True)->pd.Series:
    cleaned_time = raw.astype("string").str.strip().replace(r"^\s*$",pd.NA,regex=True)
    dayfirst = _prefer_dayfirst(cleaned_time,default=default_dayfirst)

    # Normalize Format
    normalized = (
        cleaned_time.fillna("")
        .astype(str)
        .str.replace(r"[./]","-",regex=True)
        .str.replace("T","",regex=False)
        .str.strip()
    )

    parsed = pd.Series(pd.NaT,index=raw.index)
    if dayfirst:
        parsed = _parse_format_time(normalized,_DATE_FORMATS)

    inferred = pd.to_datetime(cleaned_time,dayfirst=dayfirst,errors="coerce",format="mixed")
    alternate = pd.to_datetime(cleaned_time,dayfirst=not dayfirst,errors="coerce",format="mixed")

    parsed = parsed.fillna(inferred)
    return alternate if alternate.notna().sum() > parsed.notna().sum() else parsed

    parsed = parsed.fillna(candidate)
    return parsed

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
    """
    Detect and fix midnight rollover for time-only parsed series.
    When time goes backwards (23:59 → 00:00), increment the date offset.
    """
    time_only = parsed.dt.hour * 3600 + parsed.dt.minute * 60 + parsed.dt.second
    rollover = (time_only < time_only.shift(1)).fillna(False).cumsum()
    return parsed + pd.to_timedelta(rollover, unit="D")


def _datetime_data(df: pd.DataFrame, time_col: str, date_col: str | None) -> pd.Series:
    raw = df[time_col].astype(str).str.strip()

    # --- Case 1: Separate date column provided ---
    if date_col and date_col in df.columns:
        date_raw = df[date_col].astype(str).str.strip()
        date_raw = date_raw.replace(r"^\s*$", pd.NA, regex=True)

        # Forward-fill sparse dates (data loggers often only write date on change)
        date_series = _parse_datetime_series(date_raw)
        date_series = date_series.ffill()

        combined = date_series.dt.strftime("%Y-%m-%d") + " " + raw.astype(str)
        parsed = _parse_datetime_series(combined,default_dayfirst=False)
        return parsed

    # --- Case 2: Timestamp column already has date+time ---
    parsed = _parse_datetime_series(raw)
    if parsed.notna().sum() >= 2:
        # Check if it's truly datetime (has non-default date variation)
        if parsed.dt.date.nunique() > 1:
            return parsed
        # Only one unique date — might be time-only parsed with default date, apply rollover fix
        return _fix_midnight_rollover(parsed)

    # --- Case 3: Time-only column (HH:MM:SS) → detect and fix midnight rollover ---
    parsed = pd.to_datetime(raw, format="%H:%M:%S", errors="coerce")
    if parsed.notna().sum() >= 2:
        return _fix_midnight_rollover(parsed)

    return parsed


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
        raise HTTPException(status_code=400,
            detail=f"Unknown {label} column(s): {', '.join(missing)}")

def _maybe_sort_datetime(df: pd.DataFrame, x_col: str | None, date_col: str | None = None) -> pd.DataFrame:
    if not x_col:
        return df

    sorted_df = df.copy()

    # Use _datetime_data so date_column + midnight rollover are both handled
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
            figure = px.density_heatmap(chart_df, x=x_col, y=y_cols[0], z=request.z,
                                        title=request.title or "Heatmap",
                                        color_continuous_scale="Viridis")
        else:
            numeric_df = chart_df.select_dtypes(include="number")
            if numeric_df.shape[1] < 2:
                raise HTTPException(status_code=400,
                    detail="Heatmap without `x`/`y` requires at least two numeric columns.")
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

def _fmt_secs(secs:float)->str:
    h, rem = divmod(int(secs),3600)
    m, s = divmod(rem,60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def _fmt_num(value)->str:
    try:
        f = float(value)
        if f!=f:
            return "-"
        if abs(f)>=1000:
            return f"{f:.,2f}"
        return f"{f:.4g}"
    except (TypeError,ValueError):
        return str(value) if value is not None else "-"
    
def _plotly_to_image(fig:go.figure,width:700,height:300)->bytes|None:
    try:
        return pio.to_image(fig,format="png",width=width,height=height,scale=2)
    except Exception:
        return None
def _build_donut_figure(
        labels:list[str],
        values:list[float],
        color_map:dict[str,str],
) -> go.Figure:
    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.48,
        marker_colors=[color_map.get(l,"#999") for l in labels],
        textinfo="label+percent",
        hoverinfo="label+value+percent",
    ))
    fig.update_layout(
        margin=dict(t=20,b=20,l=20,r=20),
        showlegend=True,
        legend=dict(orientation="h", y=-0.15),
        paper_bgcolor="white",
        plot_bgcolor="white",
        height=height,
    ) if False else fig.update_layout(
        margin=dict(t=20,b=40,l=20,r=20),
        showLegend = True,
        legend=dict(orientation="h",y=-0.12,font=dict(size=10)),
        paper_bgcolor="white",
        plot_bgcolor="white",
        height=300,
    )
    return fig

def _styles_report(page_width:float)->dict:
    base = getSampleStyleSheet()

    def _s(name,parent="Normal",**kw)->ParagraphStyle:
        s = ParagraphStyle(name,parent=base[parent])
        for k, v in kw.items():
            setattr(s,k,v)
        return s
    
    usable = page_width - 2 * MARGIN

    return {
        "report_title" : _s(
            "report_title","Title",
            fontSize=20,texcolor=WHITE,
            alignment=TA_CENTER, spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "report_subtitle":_s(
            "report_subtitle",
            fontSize=10,textColor=colors.HexColor("#FFFFFF"),
            alignment=TA_CENTER, spaceAfter=0,
            fontName="Helvetica",
        ),
        "section-title":_s(
            "section_title",
            fontSize=16,textColor=WHITE,
            fontName="Helvetica-Bold",spaceAfter=0,
            leftIndent=6,
        ),
        "body":_s(
            "body",
            fontSize=11,textColor=TEXT_DARK,
            leading=14, spaceAfter=4,
        ),
        "label":_s(
            "label",
            fontSize=8,textColor=TEXT_MUTED,
            fontName="Helvetica",spaceAfter=0,
        ),
        "value_large":_s(
            "value_large",
            fontSize=16,textColor=BRAND_BLUE_DARK,
            fontName="Helvetica",spaceAfter=0,
        ),
        "kv_label":_s(
            "kv_label",
            fontSize=9,textColor=TEXT_MUTED,
            fontName="Helvetica",
        ),
        "kv_value":_s(
            "kv_value",
            fontSize=9,textColor=TEXT_DARK,
            fontName="Helvetica",
        ),
        "table_header":_s(
            "table_header",
            fontSize=8,textColor=WHITE,
            fontName="Helvetica", alignment=TA_CENTER,
        ),
        "table_cell":_s(
            "table_cell",
            fontSize=8,textColor=TEXT_DARK,
            alignment=TA_CENTER,leading=11,
        ),
        "table_cell_left":_s(
            "table_cell_left",
            fontSize=8,textColor=TEXT_DARK,
            alignment=TA_LEFT,leading=11,
        ),
        "footer":_s(
            "footer",
            fontSize=8,textColor=TEXT_MUTED,
            alignment=TA_CENTER,
        ),
        "no_data":_s(
            "no_data",
            fontSize=9, textColor=TEXT_MUTED,
            alignment=TA_CENTER,spaceAfter=6,
            fontName="Helvetica",
        ),
    }

def _section_header(title:str,styles:dict)->list:
    table_data=Table(
        [[Paragraph(title,styles["section-title"])]],
        colWidths=[PAGE_W-2*MARGIN],
    )
    table_data.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), BRAND_BLUE),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [BRAND_BLUE]),
        ("TOPPADDING",  (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [4]),
    ]))
    return [table_data,Spacer(1,6)]

def _kv_table(rows:list[tuple[str,str]],styles:dict,col_widths=None)->Table:
    usable = PAGE_W-2*MARGIN
    col_widths = col_widths or [usable*0.42, usable*0.58]
    data = [
        [Paragraph(k,styles["kv_label"]), Paragraph(str(v),styles["kv_value"])]
        for k, v in rows
    ]
    table_data = Table(data,colWidths = col_widths)
    table_data.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, ROW_ALT]),
        ("GRID",           (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 8),
    ]))
    return table_data

def _stat_table(stats:dict,styles:dict)->Table:
    usable = PAGE_W-2*MARGIN
    headers = ["Column","Mean","Median","Min","Max","Std Dev"]
    col_w = [usable*0.28]+[usable*0.144]*5

    header_row=[Paragraph(h,styles["table_header"]) for h in headers]
    data = [header_row]

    for col in stats["mean"]:
        row=[
            Paragraph(col, styles["table_cell_left"]),
            Paragraph(_fmt_num(stats["mean"].get(col)),   styles["table_cell"]),
            Paragraph(_fmt_num(stats["median"].get(col)), styles["table_cell"]),
            Paragraph(_fmt_num(stats["min"].get(col)),    styles["table_cell"]),
            Paragraph(_fmt_num(stats["max"].get(col)),    styles["table_cell"]),
            Paragraph(_fmt_num(stats["std"].get(col)),    styles["table_cell"]),
        ]
        data.append(row)

    n = len(data)
    table_data = Table(data, colWidths=col_w,repeatRows=1)
    table_data.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), BRAND_BLUE_DARK),
        ("TOPPADDING",    (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        # Alternating rows
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table_data

def _segment_table(segments:list[dict],styles:dict)->Table:
    usable = PAGE_W-2*MARGIN
    headers = ["Seg","State","Label","Start Time","End Time","Duration","Records"]
    col_w = [
        usable * 0.06,
        usable * 0.07,
        usable * 0.10,
        usable * 0.20,
        usable * 0.20,
        usable * 0.18,
        usable * 0.09,
    ]
    header_row = [Paragraph(h,styles["table_header"]) for h in headers]
    data = [header_row]

    for seg in segments:
        label = seg.get("label","")
        label_color = BRAND_BLUE if label == "Auto" else ACCENT_RED
        label_style = ParagraphStyle(
            "seg_label",
            fontSize=8,
            textColor=label_color,
            fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        )
        row = [
            Paragraph(str(seg.get("segment", "")), styles["table_cell"]),
            Paragraph(str(seg.get("state", "")),   styles["table_cell"]),
            Paragraph(label,                        label_style),
            Paragraph(seg.get("start_time", ""),   styles["table_cell"]),
            Paragraph(seg.get("end_time", ""),     styles["table_cell"]),
            Paragraph(seg.get("duration_human", ""), styles["table_cell"]),
            Paragraph(str(seg.get("record_count", "")), styles["table_cell"]),
        ]
        data.append(row)
    table_data = Table(data, colWidths=col_w,repeatRows=1)
    table_data.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), BRAND_BLUE_DARK),
        ("TOPPADDING",    (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ("TOPPADDING",    (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table_data

def _metric_cards(cards:list[dict],styles:dict)->Table:
    usable = PAGE_W-2*MARGIN
    cell_w = usable/len(cards)

    def _card_cell(card:dict):
        accent = colors.HexColor(card.get("color","#1A56DB"))
        label_s = ParagraphStyle(
            "mc_label", fontSize = 7.5,
            textColor=TEXT_MUTED,fontName="Arial",
        )
        value_s = ParagraphStyle(
            "mc_value",fontSize=14,
            textColor=TEXT_MUTED, fontName="Arial",
        )
        sub_s = ParagraphStyle(
            "mc_sub",fontSize=7.5,
            textColor=TEXT_MUTED,fontName="Arial",
        )
        inner = [
            [Paragraph(card["label"], label_s)],
            [Paragraph(card["value"], value_s)],
        ]
        if card.get("sub"):
            inner.append([Paragraph(card["sub"],sub_s)])
        t = Table(inner,colWidths=[cell_w-0.6*cm])
        t.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))
        return t
    
    row = [[_card_cell(c) for c in cards]]
    outer = Table(row,colWidths=[cell_w]*len(cards))
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
            "filename": name,
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
            "status": "Berhasil",
            "filename": storage_name,
            "original_filename": file.filename,
            "rows": int(len(df)),
            "columns": df.columns.tolist(),
            "numeric_columns": numeric_cols,
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
        "filename": os.path.basename(filename),
        "columns": df.columns.tolist(),
        "numeric_columns": df.select_dtypes(include="number").columns.tolist(),
        "categorical_columns": df.select_dtypes(include=["object", "category", "bool"]).columns.tolist(),
        "datetime_columns": df.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns.tolist(),
    }

@app.post("/analyze")
async def analyze_file(request: AnalyzeRequest):
    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)
    stats = _summary_statistics(df)
    if stats is None:
        raise HTTPException(status_code=400, detail="Tidak ada Data Terdeteksi Dalam File Upload!")
    return {
        "filename": os.path.basename(request.filename),
        "rows": int(len(df)),
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
        "filename": os.path.basename(request.filename),
        "chart_type": request.chart_type,
        "figure": json.loads(figure.to_json()),
        "config": {"responsive": True, "displaylogo": False},
    }

@app.post("/duration")
async def timestamp_calculation(request: timestampRequest):
    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)
    if request.timestamp_column not in df.columns:
        raise HTTPException(status_code=400,
            detail=f"Kolom '{request.timestamp_column}' tidak ada. "
                   f"Kolom tersedia: {df.columns.tolist()}")

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
        "filename": request.filename,
        "timestamp_column": request.timestamp_column,
        "total_records": int(len(parsed)),
        "invalid_rows_skipped": invalid_count,
        "start_time": start_time.strftime("%d-%m-%Y %H:%M:%S"),
        "end_time":   end_time.strftime("%d-%m-%Y %H:%M:%S"),
        "total_duration": {
            "second": total_secs,
            "minutes": round(total_secs / 60, 2),
            "human_readable": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
        },
    }

@app.post("/counting")
async def counting_auto_mode(request: autoCounting):
    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)
    for col in [request.timestamp_column, request.state_column]:
        if col not in df.columns:
            raise HTTPException(status_code=400,
                detail=f"Kolom tidak ditemukan. Kolom tersedia: {df.columns.tolist()}")
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
            "segment": int(group_id),
            "state": state_val,
            "label": "Auto" if state_val == 1 else "Manual",
            "start_time": start_time.strftime("%d-%m-%Y %H:%M:%S"),
            "end_time":   end_time.strftime("%d-%m-%Y %H:%M:%S"),
            "duration_seconds": dur_secs,
            "duration_human": f"{h:02d}:{m:02d}:{s:02d}",
            "record_count": len(group_df),
        })
    total_auto    = sum(s["duration_seconds"] for s in segments if s["state"] == 1)
    total_manual  = sum(s["duration_seconds"] for s in segments if s["state"] == 0)
    total_process = total_auto + total_manual

    def fmt(secs: float) -> str:
        h, rem = divmod(int(secs), 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    return {
        "filename": request.filename,
        "total_segments": len(segments),
        "segments": segments,
        "summary": {
            "total_auto_counting":   {"seconds": total_auto,    "human": fmt(total_auto),    "percentage": round(total_auto   / total_process * 100, 2) if total_process else 0.0},
            "total_manual_counting": {"seconds": total_manual,  "human": fmt(total_manual),  "percentage": round(total_manual / total_process * 100, 2) if total_process else 0.0},
            "total_recorded":        {"seconds": total_process, "human": fmt(total_process)},
        },
    }

@app.post("/spv-analysis")
async def spv_analysis(request: SPVRequest):
    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path, sheet_name=request.sheet_name)
    for col in [request.set_point_column, request.process_value_column]:
        if col not in df.columns:
            raise HTTPException(status_code=400,
                detail=f"Kolom '{col}' tidak ditemukan. Kolom tersedia: {df.columns.tolist()}")
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
        "filename":             request.filename,
        "set_point_column":     request.set_point_column,
        "process_value_column": request.process_value_column,
        "total_records":        total,
        "summary": {
            "normal":       {"count": normal_count,  "percentage": pct(normal_count)},
            "lower":        {"count": lower_count,   "percentage": pct(lower_count)},
            "higher":       {"count": higher_count,  "percentage": pct(higher_count)},
        },
        "statistics": {
            "avg_deviation": round(float(abs_devs.mean()), 4),
            "max_deviation": round(float(abs_devs.max()),  4),
            "min_deviation": round(float(abs_devs.min()),  4),
        },
    }

@app.post("/generate-report")
async def generate_report(request: ReportRequest):
    from datetime import datetime as _dt

    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path,sheet_name=request.sheet_name)
    file_info,_ = _detect_file_info(file_path,sheet_name=request.sheet_name)
    metadata = _load_metadata()
    display_name = metadata.get(request.filename,{}).get("original_filename",request.filename)
    stats = _summary_statistics(df)

    styles = _styles_report(PAGE_W)
    story = []
    usable = PAGE_W-2*MARGIN

    # Cover & Header
    cover_data = [[
        Paragraph("INTERACTIVE DASHBOARD", styles["report_title"]),
        Paragraph("Analysis Report", styles["report_subtitle"]),
        Paragraph(
            f'Generated: {_dt.now().strftime("%d %M %Y  %H:%M")}  |  File: {display_name}',
            styles["report_subtitle"],
        ),
    ]]
    cover_table = Table(cover_data,colWidths=[usable])
    cover_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), BRAND_BLUE_DARK),
        ("TOPPADDING",    (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
        ("LEFTPADDING",   (0, 0), (-1, -1), 14),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 14),
        ("ROUNDEDCORNERS", [6]),
    ]))
    story.append(cover_table)
    story.append(Spacer(1,14))

    # Dataset Info
    if file_info:
        story += _section_header("Informasi Dataset",styles)
        info_rows = [(k,v) for k, v in file_info.items()]

        info_rows += [
            ("Total Rows", f"{len(df):,}"),
            ("Total Columns", str(len(df.columns))),
        ]
        story.append(_kv_table(info_rows,styles))
        story.append(Spacer(1,14))
    
    story += _section_header("SUMMARY",styles)
    if stats:
        story.append(_stat_table(stats,styles))
    else:
        story.append(Paragraph("Kolom Angka tidak ada di Dataset",styles["no_data"]))
    story.append(Spacer(1,18))

    # Hour Record
    story += _section_header("HOUR RECORD",styles)

    duration_result = None
    if request.timestamp_column and request.timestamp_column in df.columns:
        try:
            parsed = _datetime_data(df,request.timestamp_column,request.date_column)
            valid = parsed.dropna().sort_values()
            if len(valid)>=2:
                start_t = valid.iloc[0]
                end_t = valid.iloc[-1]
                total_secs = (end_t-start_t).total_seconds()
                duration_result={
                    "timestamp_column":request.timestamp_column,
                    "start_time":start_t.strftime("%d-%m-%Y %H:%M:%S"),
                    "end_time":end_t.strftime("%d-%m-%Y %H:%M:%S"),
                    "total_records": len(valid),
                    "invalid_skipped":int(parsed.isna().sum()),
                    "total_seconds": total_secs,
                    "human_readable": _fmt_secs(total_secs),
                    "minutes": round(total_secs/60,2),
                    "hours": round(total_secs/3600,4),
                }
        except Exception:
            pass
    
    if duration_result:
        dr = duration_result
        cards = [
            {"label": "Total Duration (HH:MM:SS)", "value": dr["human_readable"],
             "sub": f"{dr['hours']} hours", "color": "#1A56DB"},
            {"label": "Total Minutes",  "value": f"{dr['minutes']:,.1f}",
             "sub": "minutes",           "color": "#0369A1"},
            {"label": "Valid Records",  "value": f"{dr['total_records']:,}",
             "sub": f"skipped: {dr['invalid_skipped']}", "color": "#047857"},
        ]
        story.append(_metric_cards(cards,styles))
        story.append(Spacer(1,6))
        story.append(_kv_table([
            ("Timestamp Column", dr["timestamp_column"]),
            ("Start Time",       dr["start_time"]),
            ("End Time",         dr["end_time"]),
        ], styles))
    else:
        story.append(Paragraph(
            "Tidak Waktu Tercatat di Dataset",
            styles["no_data"],
        ))
    
    story.append(Spacer(1,18))

    # Auto/Manual Record
    story += _section_header("AUTO/MANUAL RECORD",styles)

    counting_result = None
    if(request.timestamp_column and request.timestamp_column in df.columns and
       request.state_column and request.state_column in df.columns):
        try:
            df_c = df.copy()
            df_c["_time"]=_datetime_data(df_c,request.timestamp_column,request.date_column)
            df_c["_state"]= pd.to_numeric(df_c[request.state_column],errors="coerce")
            df_c = df_c.dropna(subset=["_time","_state"]).sort_values("_time").reset_index(drop=True)
            if len(df_c)>=2:
                df_c["_chg"] = df_c["_state"]!=df_c["_state"].shift(1)
                df_c["_group"] = df_c["_chg"].cumsum()
                segs = []
                for gid, gdf in df_c.groupby("_group"):
                    sv = int(gdf["_state"].iloc[0])
                    st = gdf["_time"].iloc[0]
                    li = gdf.index[-1]
                    et = df_c["_time"].iloc[li+1] if li + 1 < len(df_c) else gdf["_time"].iloc[-1]
                    ds = (et-st).total_seconds()
                    segs.append({
                        "segment":int(gid),
                        "state": sv,
                        "label":"Auto" if sv==1 else "Manual",
                        "start_time":st.strftime("%d-%m-%Y %H:%M:%S"),
                        "end_time":et.strftime("%d-%m-%Y %H:%M:%S"),
                        "duration_seconds":ds,
                        "duration_human": _fmt_secs(ds),
                        "record_count":len(gdf),
                    })
                auto_s = sum(s["duration_seconds"] for s in segs if s["state"]==1)
                manual_s = sum(s["duration_seconds"] for s in segs if s["state"]==0)
                total_s = auto_s + manual_s
                counting_result={
                    "segments": segs,
                    "auto_seconds":   auto_s,
                    "manual_seconds": manual_s,
                    "total_seconds":  total_s,
                    "auto_human":     _fmt_secs(auto_s),
                    "manual_human":   _fmt_secs(manual_s),
                    "total_human":    _fmt_secs(total_s),
                    "auto_pct":       round(auto_s   / total_s * 100, 2) if total_s else 0.0,
                    "manual_pct":     round(manual_s / total_s * 100, 2) if total_s else 0.0,
                }
        except Exception:
            pass
    
    if counting_result:
        cr = counting_result
        cards = [
            {"label": "Total Recorded",          "value": cr["total_human"],  "sub": "HH:MM:SS",            "color": "#374151"},
            {"label": "Auto Mode Total",          "value": cr["auto_human"],   "sub": f"{cr['auto_pct']}%",  "color": "#1A56DB"},
            {"label": "Manual Mode Total",        "value": cr["manual_human"], "sub": f"{cr['manual_pct']}%","color": "#DC2626"},
            {"label": "Total Segments",           "value": str(len(cr["segments"])), "sub": "transitions",   "color": "#B45309"},
        ]
        story.append(_metric_cards(cards,styles))
        story.append(Spacer(1,8))

        # Pie Chart Visualization
        if cr["auto_seconds"]>0 or cr["manual_seconds"]>0:
            donut_labels=[]
            donut_values=[]
            donut_colors={}
            if cr["auto_seconds"]>0:
                donut_labels.append("Auto")
                donut_values.append(cr["auto_seconds"])
                donut_colors["Auto"] = "#1A56DB"
            if cr["manual_seconds"]>0:
                donut_labels.append("Manual")
                donut_values.append(cr["manual_seconds"])
                donut_colors["Manual"] = "#DC2626"
            
            fig_donut = _build_donut_figure(donut_labels,donut_values,donut_colors)
            img_bytes = _plotly_to_image(fig_donut,width=500,height=260)
            if img_bytes:
                img = Image(io.BytesIO(img_bytes),width=12*cm,height=6.5*cm)
                img.hAlign = "CENTER"
                story.append(img)
                story.append(Spacer(1,6))
        
        story.append(Paragraph(
            f"Segment Details (showing{min(50, len(cr['segments']))} of {len(cr['segments'])} segments)",
            styles["label"],
        ))
        story.append(Spacer(1,4))
        story.append(_segment_table(cr["segments"][:50],styles))
    else:
        story.append(Paragraph(
            "Tidak Timestamp Yang Terdeteksi utnuk Auto/Manual",
            styles["no_data"],
        ))

    story.append(Spacer(1,18))

    # Range Analysis
    story += _section_header("Range Value Analysis",styles)

    spv_result = None
    if(request.set_point_column and request.set_point_column in df.columns
       and request.process_value_column and request.process_value_column in df.columns):
        try:
            df_s = df.copy()
            df_s["_sp"] = pd.to_numeric(df_s[request.set_point_column],     errors="coerce")
            df_s["_pv"] = pd.to_numeric(df_s[request.process_value_column], errors="coerce")
            df_s = df_s.dropna(subset=["_sp", "_pv"])
            if not df_s.empty:
                df_s["_dev"] = df_s["_pv"] - df_s["_sp"]
                df_s["_status"] = df_s["_dev"].apply(
                    lambda d: "normal" if d == 0 else ("lower" if d < 0 else "higher")
                )
                total     = len(df_s)
                normal_c  = int((df_s["_status"] == "normal").sum())
                lower_c   = int((df_s["_status"] == "lower").sum())
                higher_c  = int((df_s["_status"] == "higher").sum())
                abs_dev   = df_s["_dev"].abs()

                def pct(n):
                    return round(n/total*100,2) if total else 0.0
                
                spv_result = {
                    "set_point_column":     request.set_point_column,
                    "process_value_column": request.process_value_column,
                    "total_records": total,
                    "normal_count":  normal_c,  "normal_pct":  pct(normal_c),
                    "lower_count":   lower_c,   "lower_pct":   pct(lower_c),
                    "higher_count":  higher_c,  "higher_pct":  pct(higher_c),
                    "avg_deviation": round(float(abs_dev.mean()), 4),
                    "max_deviation": round(float(abs_dev.max()),  4),
                    "min_deviation": round(float(abs_dev.min()),  4),
                }
        except Exception:
            pass
    if spv_result:
        sv = spv_result
        cards = [
            {"label": "Total Records",         "value": f"{sv['total_records']:,}",  "sub": "data points",            "color": "#374151"},
            {"label": "Within Set Point",      "value": f"{sv['normal_count']:,}",   "sub": f"{sv['normal_pct']}%",   "color": "#1A56DB"},
            {"label": "Below Set Point",       "value": f"{sv['lower_count']:,}",    "sub": f"{sv['lower_pct']}%",    "color": "#B45309"},
            {"label": "Above Set Point",       "value": f"{sv['higher_count']:,}",   "sub": f"{sv['higher_pct']}%",   "color": "#DC2626"},
        ]
        story.append(_metric_cards(cards,styles))
        story.append(Spacer(1,8))

        # Info & Deviation Stat
        left_rows = [
            ("Set Point Column",     sv["set_point_column"]),
            ("Process Value Column", sv["process_value_column"]),
        ]
        right_rows = [
            ("Avg Absolute Deviation", _fmt_num(sv["avg_deviation"])),
            ("Max Absolute Deviation", _fmt_num(sv["max_deviation"])),
            ("Min Absolute Deviation", _fmt_num(sv["min_deviation"])),
        ]
        half = usable / 2 - 0.2 * cm
        left_tbl  = _kv_table(left_rows,  styles, col_widths=[half * 0.45, half * 0.55])
        right_tbl = _kv_table(right_rows, styles, col_widths=[half * 0.55, half * 0.45])

        two_col = Table([[left_tbl, right_tbl]], colWidths=[half + 0.2 * cm, half])
        two_col.setStyle(TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ]))
        story.append(two_col)
        story.append(Spacer(1, 8))

        # Pie Chart For Counting Auto/Manual
        donut_items = [
            ("Dalam SP",    sv["normal_count"],  "#1A56DB"),
            ("Lebih Rendah",sv["lower_count"],   "#F59E0B"),
            ("Lebih Tinggi",sv["higher_count"],  "#EF4444"),
        ]
        donut_labels  = [x[0] for x in donut_items if x[1] > 0]
        donut_values  = [x[1] for x in donut_items if x[1] > 0]
        donut_colors  = {x[0]: x[2] for x in donut_items if x[1] > 0}

        if donut_labels:
            fig_spv = _build_donut_figure(donut_labels, donut_values, donut_colors)
            img_bytes_spv = _plotly_to_image(fig_spv, width=500, height=260)
            if img_bytes_spv:
                img_spv = Image(io.BytesIO(img_bytes_spv), width=12 * cm, height=6.5 * cm)
                img_spv.hAlign = "CENTER"
                story.append(img_spv)
    else:
        story.append(Paragraph(
            "No Set Point / Process Value columns selected or insufficient numeric data.",
            styles["no_data"],
        ))

    story.append(Spacer(1, 20))

    # Footer
    story.append(HRFlowable(width=usable, thickness=0.5, color=BORDER_COLOR))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Generated by Interactive Dashboard  •  {_dt.now().strftime('%d %B %Y %H:%M')}  •  {display_name}",
        styles["footer"],
    ))
    
    # Generate PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pageSize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f"Report Analisis – {display_name}",
        author="USERS"
    )
    doc.build(story)
    buffer.seek(0)

    safe_stem = os.path.splitext(display_name)[0]
    safe_stem = re.sub(r"[^\w\-]", "_", safe_stem)
    from datetime import datetime as _dt2
    filename_out = f"report_{safe_stem}_{_dt2.now().strftime('%d%m%Y_%H%M')}.pdf"

    return Response(
        content=buffer.read(),
        media_type="application/pdf",
        headers={"Content-Disposition":f'attachment;filename="{filename_out}"'},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)