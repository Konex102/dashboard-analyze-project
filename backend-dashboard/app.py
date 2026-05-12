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

from reportlab_builder import build_pdf_reportlab
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.utils import ImageReader
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

# Color Palette For Chart
BRAND_BLUE      = colors.HexColor("#2563EB")
BRAND_BLUE_DARK = colors.HexColor("#0F3FA6")
BRAND_LIGHT     = colors.HexColor("#F8FAFC")
ACCENT_GREEN    = colors.HexColor("#057A55")
ACCENT_NAVY    = colors.HexColor("#B45309")
ACCENT_RED      = colors.HexColor("#DC2626")
ROW_ALT         = colors.HexColor("#F8FAFF")
BORDER_COLOR    = colors.HexColor("#E5E7EB")
TEXT_DARK       = colors.HexColor("#111827")
TEXT_MID        = colors.HexColor("#374151")
TEXT_MUTED      = colors.HexColor("#6B7280")
WHITE           = colors.white

# PAGE SIZE
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

class PlotConfig(BaseModel):
    chart_type: Literal[
        "line", "bar", "area", "scatter"] = "line"
    x : str | None = None
    y : str | list[str] | None = None
    title : str | None = None
    date_column : str | None = None
class SPVPair(BaseModel):
    set_point_column: str
    process_value_column: str
    label: str | None = None

class SPVRequest(BaseModel):
    filename: str
    set_point_column: str
    process_value_column: str
    sheet_name: str | int = 0

class MultiSPVRequest(BaseModel):
    filename: str
    pairs: list[SPVPair]
    sheet_name: str | int = 0

class ReportRequest(BaseModel):
    filename: str
    sheet_name: str | int = 0
    timestamp_column: str | None = None
    date_column: str | None = None
    state_column: str | None = None
    set_point_column: str | None = None
    process_value_column: str | None = None
    spv_pairs: list[SPVPair] | None = None
    plot_configs : list[PlotConfig] | None = None
    stat_columns : list[str] | None = None

# File Helpers and Data Loading
META_KEY_RE  = re.compile(r"^[A-Z][A-Z0-9_]{0,29}$")
META_GROUP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _\-]{0,49}$")
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
        
        # EXISTING METADATA HELPER
        if len(non_empty) == 2 and META_KEY_RE.match(non_empty[0]):
            info[non_empty[0]] = non_empty[1]
            skip += 1
        
        # NEW METADATA HELPER
        elif len(non_empty) == 1 and META_GROUP_RE.match(non_empty[0]):
            info.setdefault("GROUP",non_empty[0])
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

def _detect_multi_header(content:str,delimiter:str,skip:int) -> int:
    lines = [l for l in content.split("\n") if l.strip()]
    data_lines = lines[skip:]
    if len(data_lines)<2:
        return 1
    
    first_count = len(data_lines[0].split(delimiter))

    # Checking Rows for new formatting
    multi = 0
    for row in data_lines[1:4]:
        parts =  row.split(delimiter)
        if parts[0].strip() == "":
            multi += 1
        else:
            break
    return 1 + multi

def _flatten_multi_headers(df:pd.DataFrame)->pd.DataFrame:
    if not isinstance(df.columns,pd.MultiIndex):
        return df
    
    new_cols=[]
    for col_tuple in df.columns:
        parts = [str(c).strip() for c in col_tuple if str(c).strip() not in ("","nan")]
        new_cols.append("-".join(parts) if parts else "col")

    # Deduplicate suffix
    seen : dict[str,int] = {}
    final = []
    for name in new_cols:
        if name in seen:
            seen[name]+=1
            final.append(f"{name}_{seen[name]}")
        else:
            seen[name]=0
            final.append(name)
    df.columns = final
    return df

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
        n_headers = _detect_multi_header(content,delimiter,skip)
        decimal   = _sniff_decimal(content, delimiter, skip + n_headers)

        header_arg = list(range(n_headers)) if n_headers > 1 else 0

        df = pd.read_csv(
            io.StringIO(content),
            sep=delimiter,
            decimal=decimal,
            skiprows=skip if skip else None,
            header=header_arg,
            engine="python",
        )

        df = _flatten_multi_headers(df)
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


# ─── DATETIME HELPER ─────────────────────────────────────────────────────────
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
        candidate    = pd.to_datetime(normalized[mask], format=fmt, errors="coerce")
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


# ─── ANALYSIS HELPERS ────────────────────────────────────────────────────────
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
        fig = px.line(chart_df, x=x_col, y=y_cols, color=request.color, title=request.title)
    elif ct == "bar":
        if not x_col or not y_cols:
            raise HTTPException(status_code=400, detail="Bar chart requires `x` and at least one `y`.")
        fig = px.bar(chart_df, x=x_col, y=y_cols if len(y_cols) > 1 else y_cols[0],
                     color=request.color, title=request.title or "Bar Chart", barmode="group")
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported chart type: {ct}")

    fig.update_layout(template="simple_white", font=dict(size=10, color="#111827"),
                      margin=dict(l=10, r=10, t=30, b=10))
    return fig


# ─── SHARED REPORT HELPERS ───────────────────────────────────────────────────
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

def _pick_meta(info: dict, keys: list[str]) -> str | None:
    if not info:
        return None
    normalized = {str(k).strip().upper(): k for k in info.keys()}
    for key in keys:
        lookup = normalized.get(key.upper())
        if lookup is None:
            continue
        value = info.get(lookup)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            return value
    return None

def _resolve_logo_path(base_dir: str) -> str | None:
    env_path = os.getenv("REPORT_LOGO_PATH", "").strip()
    candidates: list[str] = []
    if env_path:
        candidates.append(env_path if os.path.isabs(env_path) else os.path.join(base_dir, env_path))
    candidates += [
        os.path.join(base_dir, "Sinarmas_logo.png"),
        os.path.join(base_dir, "logo.png"),
        os.path.join(base_dir, "logo.jpg"),
        os.path.join(base_dir, "logo.jpeg"),
        os.path.join(base_dir, "assets", "logo.png"),
        os.path.join(base_dir, "assets", "logo.jpg"),
        os.path.join(base_dir, "assets", "logo.jpeg"),
        os.path.join(base_dir, "assets", "Sinarmas_logo.png"),
        os.path.join(os.path.dirname(base_dir), "frontend-dashboard", "src", "assets", "Sinarmas_logo.png"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


# ─── SPV ANALYSIS HELPER ─────────────────────────────────────────────────────
def _compute_spv(df: pd.DataFrame, sp_col: str, pv_col: str) -> dict | None:
    df_s = df.copy()
    df_s["_sp"] = pd.to_numeric(df_s[sp_col], errors="coerce")
    df_s["_pv"] = pd.to_numeric(df_s[pv_col], errors="coerce")
    df_s = df_s.dropna(subset=["_sp", "_pv"])
    if df_s.empty:
        return None
    df_s["_dev"]    = df_s["_pv"] - df_s["_sp"]
    df_s["_status"] = df_s["_dev"].apply(lambda d: "normal" if d == 0 else ("lower" if d < 0 else "higher"))
    total    = len(df_s)
    normal_c = int((df_s["_status"] == "normal").sum())
    lower_c  = int((df_s["_status"] == "lower").sum())
    higher_c = int((df_s["_status"] == "higher").sum())
    abs_dev  = df_s["_dev"].abs()
    pct      = lambda n: round(n / total * 100, 2) if total else 0.0  # noqa: E731
    return {
        "set_point_column":     sp_col,
        "process_value_column": pv_col,
        "total_records": total,
        "normal_count": normal_c, "normal_pct": pct(normal_c),
        "lower_count":  lower_c,  "lower_pct":  pct(lower_c),
        "higher_count": higher_c, "higher_pct": pct(higher_c),
        "avg_deviation": round(float(abs_dev.mean()), 4),
        "max_deviation": round(float(abs_dev.max()),  4),
        "min_deviation": round(float(abs_dev.min()),  4),
    }

def _build_pdf_reportlab(request: ReportRequest, out_path: str) -> str:
    from datetime import datetime as _dt

    file_path = _resolve_file_path(request.filename)
    df = _read_dataframe(file_path,sheet_name=request.sheet_name)
    file_info, _ = _detect_file_info(file_path, sheet_name=request.sheet_name)
    meta = _load_metadata()
    display_name = meta.get(request.filename,{}).get(
        "original_filename", request.filename
    )

    stat_columns = request.stat_columns
    if stat_columns:
        valid_cols = [c for c in stat_columns if c in df.columns]
        missing = set(stat_columns) - set(df.columns)
        if missing:
            print(f"DATA NOT FOUND:{missing}")
        stat_columns = valid_cols or None
        
    # Resolve SPV Pairs
    spv_pairs_dicts: list[dict] = []
    if request.spv_pairs:
        for p in request.spv_pairs:
            if p.set_point_column and p.process_value_column:
                spv_pairs_dicts.append({
                    "sp": p.set_point_column,
                    "pv": p.process_value_column,
                    "label": p.label or f"{p.set_point_column} vs {p.process_value_column}",
                })
    elif request.set_point_column and request.process_value_column:
        spv_pairs_dicts.append({
            "sp": request.set_point_column,
            "pv": request.process_value_column,
            "label": f"{request.set_point_column} vs {request.process_value_column}",
        })
    
    # Resolve Plot Configuration
    plot_configs_dicts: list[dict] = []
    if request.plot_configs:
        for pc in request.plot_configs:
            plot_configs_dicts.append({
                "chart_type": pc.chart_type,
                "x": pc.x,
                "y": pc.y or [],
                "title": pc.title,
                "date_column": pc.date_column or request.date_column,
            })

    # Calculation Duration 
    duration_result = None
    if request.timestamp_column and request.timestamp_column in df.columns:
        try:
            parsed = _datetime_data(df, request.timestamp_column, request.date_column)
            valid = parsed.dropna().sort_values()
            if len(valid) >= 2:
                total_secs = (valid.iloc[-1] - valid.iloc[0]).total_seconds()
                h, rem = divmod(int(total_secs), 3600)
                m, s = divmod(rem, 60)
                duration_result = {
                    "timestamp_column": request.timestamp_column,
                    "start_time": valid.iloc[0].strftime("%d-%m-%Y %H:%M:%S"),
                    "end_time": valid.iloc[-1].strftime("%d-%m-%Y %H:%M:%S"),
                    "total_records": len(valid),
                    "invalid_rows_skipped": int(parsed.isna().sum()),
                    "total_duration": {
                        "second": total_secs,
                        "minutes": round(total_secs / 60, 2),
                        "hours": round(total_secs / 3600, 4),
                        "human_readable": f"{h:02d}:{m:02d}:{s:02d}",
                    },
                }
        except Exception:
            pass
    
    # Calculation Auto/Manual Counting
    counting_result = None
    if (request.timestamp_column and request.timestamp_column in df.columns and request.state_column and request.state_column in df.columns):
        try:
            df_c = df.copy()
            df_c["_time"] = _datetime_data(
                df_c, request.timestamp_column, request.date_column
            )
            df_c["_state"] = pd.to_numeric(df_c[request.state_column], errors="coerce")
            df_c = (
                df_c.dropna(subset=["_time","_state"])
                .sort_values("_time")
                .reset_index(drop=True)
            )
            if len(df_c)>=2:
                df_c["_chg"] = df_c["_state"]!=df_c["_state"].shift(1)
                df_c["_group"] = df_c["_chg"].cumsum()
                segs = []
                for grid, gdf in df_c.groupby("_group"):
                    sv = int(gdf["_state"].iloc[0])
                    st = gdf["_time"].iloc[0]
                    li = gdf.index[-1]
                    et = (df_c["_time"].iloc[li+1] if li+1<len(df_c) else gdf["_time"].iloc[-1])
                    ds = (et - st).total_seconds()
                    h2, r2 = divmod(int(ds),3600)
                    m2, s2 = divmod(r2,60)
                    segs.append({
                        "segment": int(grid),
                        "state": sv,
                        "label": "Auto" if sv==1 else "Manual",
                        "start_time": st.strftime("%d-%m-%Y %H:%M:%S"),
                        "end_time": et.strftime("%d-%m-%Y %H:%M:%S"),
                        "duration_seconds": ds,
                        "duration_human": f"{h2:02d}:{m2:02d}:{s2:02d}",
                        "record_count": len(gdf),
                    })
                auto_s = sum(sg["duration_seconds"] for sg in segs if sg["state"]==1)
                manual_s = sum(sg["duration_seconds"] for sg in segs if sg["state"]==0)
                total_s = auto_s + manual_s
                counting_result = {
                    "total_segments": len(segs),
                    "segments": segs,
                    "summary": {
                        "total_auto_counting": {
                            "seconds": auto_s,
                            "human": _fmt_secs(auto_s),
                            "percentage": round(auto_s/total_s*100, 2) if total_s else 0,
                        },
                        "total_manual_counting":{
                            "seconds": manual_s,
                            "human": _fmt_secs(manual_s),
                            "percentage": round(manual_s/total_s*100,2) if total_s else 0,
                        },
                        "total_recorded": {
                            "seconds": total_s,
                            "human": _fmt_secs(total_s),
                        },
                    },
                }
        except Exception:
            pass
    
    # Resolve Logo
    _base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = _resolve_logo_path(_base_dir)

    # Call ReportLab builder
    pdf_bytes = build_pdf_reportlab(
        df,
        display_name          = display_name,
        dataset_info          = file_info or {},
        logo_path             = logo_path,
        timestamp_col         = request.timestamp_column,
        date_col              = request.date_column,
        state_col             = request.state_column,
        spv_pairs             = spv_pairs_dicts,
        plot_configs          = plot_configs_dicts,
        duration_result       = duration_result,
        counting_result       = counting_result,
        fn_datetime_data      = _datetime_data,
        fn_summary_statistics = _summary_statistics,
        fn_compute_spv        = _compute_spv,
        stat_columns          = stat_columns,
    )

    with open(out_path,"wb") as fh:
        fh.write(pdf_bytes)
    
    safe_stem = re.sub(r"[^\w\-]", "_", os.path.splitext(display_name)[0])
    return f"report_{safe_stem}_{_dt.now().strftime('%d%m%Y_%H%M')}.pdf"

# Reportlab job runner (legacy, can be removed after WeasyPrint is fully adopted)
def _run_reportlab_job(job_id: str, request: ReportRequest) -> None:
    out_path = os.path.join(report_dir, f"{job_id}.pdf")
    try:
        download_name = _build_pdf_reportlab(request, out_path)
        _set_job(job_id, status="done", filename=download_name)
    except Exception as exc:
        _set_job(job_id, status="error", detail=str(exc))

# ─── ROUTES ──────────────────────────────────────────────────────────────────
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
    sv = _compute_spv(df, request.set_point_column, request.process_value_column)
    if not sv:
        raise HTTPException(status_code=400, detail="Tidak ada nilai numerik valid pada kolom yang dipilih.")
    return {
        "filename":             request.filename,
        "set_point_column":     request.set_point_column,
        "process_value_column": request.process_value_column,
        "total_records":        sv["total_records"],
        "summary": {
            "normal":  {"count": sv["normal_count"],  "percentage": sv["normal_pct"]},
            "lower":   {"count": sv["lower_count"],   "percentage": sv["lower_pct"]},
            "higher":  {"count": sv["higher_count"],  "percentage": sv["higher_pct"]},
        },
        "statistics": {
            "avg_deviation": sv["avg_deviation"],
            "max_deviation": sv["max_deviation"],
            "min_deviation": sv["min_deviation"],
        },
    }

@app.post("/spv-analysis/multi")
async def spv_analysis_multi(request: MultiSPVRequest):
    file_path = _resolve_file_path(request.filename)
    df        = _read_dataframe(file_path, sheet_name=request.sheet_name)
    results   = []
    for pair in request.pairs:
        for col in [pair.set_point_column, pair.process_value_column]:
            if col not in df.columns:
                raise HTTPException(status_code=400,
                    detail=f"Kolom '{col}' tidak ditemukan. Tersedia: {df.columns.tolist()}")
        sv = _compute_spv(df, pair.set_point_column, pair.process_value_column)
        if not sv:
            results.append({
                "label": pair.label or f"{pair.set_point_column} vs {pair.process_value_column}",
                "error": "Tidak ada nilai numerik valid",
            })
            continue
        results.append({
            "label":                pair.label or f"{pair.set_point_column} vs {pair.process_value_column}",
            "set_point_column":     pair.set_point_column,
            "process_value_column": pair.process_value_column,
            "total_records":        sv["total_records"],
            "summary": {
                "normal":  {"count": sv["normal_count"],  "percentage": sv["normal_pct"]},
                "lower":   {"count": sv["lower_count"],   "percentage": sv["lower_pct"]},
                "higher":  {"count": sv["higher_count"],  "percentage": sv["higher_pct"]},
            },
            "statistics": {
                "avg_deviation": sv["avg_deviation"],
                "max_deviation": sv["max_deviation"],
                "min_deviation": sv["min_deviation"],
            },
        })
    return {"filename": request.filename, "pairs": results}

# ReportLab-based endpoints (legacy, can be removed after WeasyPrint is fully adopted)
@app.post("/generate-report/reportlab")
async def generate_report_reportlab_sync(request: ReportRequest):
    """
    Synchronous ReportLab PDF endpoint.
    Streams PDF directly; use /reportlab/async for non-blocking generation.
    """
    out_path = os.path.join(report_dir, f"rl_sync_{uuid.uuid4().hex[:8]}.pdf")
    try:
        download_name = _build_pdf_reportlab(request, out_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ReportLab error: {exc}") from exc

    def _iter():
        with open(out_path, "rb") as fh:
            yield from fh
        os.remove(out_path)

    return StreamingResponse(
        _iter(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )

@app.post("/generate-report/reportlab/async")
async def generate_report_reportlab_async(
    request: ReportRequest,
    background_tasks: BackgroundTasks,
):
    """
    Enqueue async ReportLab PDF job.
    Poll /generate-report/status/{job_id} → download via /generate-report/download/{job_id}
    """
    job_id = uuid.uuid4().hex
    _set_job(job_id, status="pending")
    background_tasks.add_task(_run_reportlab_job, job_id, request)
    return {"job_id": job_id, "status": "pending"}


# ─── REPORT ENDPOINTS (WeasyPrint — primary engine) ───────────────────────────
@app.post("/generate-report")
async def generate_report_sync(request: ReportRequest):
    """
    Generate PDF report (sync) using WeasyPrint.
    Replaces the old ReportLab-based endpoint with identical URL so existing
    clients require no changes.
    """
    out_path = os.path.join(report_dir, f"sync_{uuid.uuid4().hex[:8]}.pdf")
    try:
        download_name = _build_pdf_reportlab(request, out_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF generation error: {exc}") from exc

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
    """Enqueue an async PDF generation job (ReportLab)."""
    job_id = uuid.uuid4().hex
    _set_job(job_id, status="pending")
    background_tasks.add_task(_run_reportlab_job, job_id, request)
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

@app.post("/generate-report/v2")
async def generate_report_v2(request: ReportRequest):
    return await generate_report_sync(request)

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)
