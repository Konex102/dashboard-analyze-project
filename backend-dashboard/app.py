import csv as csv_module
import json
import os
import re
import shutil
import uuid
from typing import Literal, List

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

class timestampRequest(BaseModel):
    filename: str
    timestamp_column: str
    date_column: str | None = None
    sheet_name: str | int = 0

class autoCounting(BaseModel):
    filename: str
    timestamp_column: str
    state_column: str
    date_column : str | None = None
    sheet_name: str | int = 0

class SPVRequest(BaseModel):
    filename: str
    set_point_column: str
    process_value_column: str
    sheet_name: str | int = 0


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
            # Resolve sheet
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

def _datetime_data(df:pd.DataFrame, time_col:str, date_col:str|None)->pd.Series:
    raw = df[time_col].astype(str).str.strip()
    if date_col and date_col in df.columns:
        date_raw = df[date_col].astype(str).str.strip()
        combined = date_raw + "" + raw
        parsed = pd.to_datetime(combined,firstDay=True,errors="coerce")
        if parsed.notna().sum()>=2:
            return parsed
        
    parsed = pd.to_datetime(raw,format="%H:%M:%S",erros="coerce")
    if parsed.notna().sum()<2:
        parsed = pd.to_datetime(raw,errors="coerce")
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

def _maybe_sort_datetime(df: pd.DataFrame, x_col: str | None) -> pd.DataFrame:
    if not x_col:
        return df
    parsed = pd.to_datetime(df[x_col], errors="coerce")
    if parsed.notna().sum() == 0:
        return df
    sorted_df = df.copy()
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
        chart_df = _maybe_sort_datetime(chart_df, x_col)

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
    """
    Returns key-value metadata rows found at the top of the file
    before the actual data header (e.g. MILL, NAME).
    Supports both CSV and Excel (.xlsx).
    """
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
    raw_series = df[request.timestamp_column].astype(str).str.strip()
    parsed = _datetime_data(df,request.timestamp_column,request.date_column)
    if parsed.notna().sum() < 2:
        parsed = _datetime_data(df,request.timestamp_column,request.date_column)
    invalid_count = int(parsed.isna().sum())
    if parsed.notna().sum() < 2:
        raise HTTPException(status_code=400,
            detail=f"Kolom '{request.timestamp_column}' tidak valid.")
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
        "start_time": start_time.strftime("%H:%M:%S"),
        "end_time":   end_time.strftime("%H:%M:%S"),
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
    raw_series = df[request.timestamp_column].astype(str).str.strip()
    parsed = _datetime_data(df, request.timestamp_column,request.date_column)
    if parsed.notna().sum() < 2:
        parsed = _datetime_data(df,request.timestamp_column,request.date_column)
    df = df.copy()
    df["time"]  = parsed
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
            "start_time": start_time.strftime("%H:%M:%S"),
            "end_time":   end_time.strftime("%H:%M:%S"),
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

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)