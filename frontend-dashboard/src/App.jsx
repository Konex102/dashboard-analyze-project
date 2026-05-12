import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import logo from "./assets/Sinarmas_logo.png";
import "./App.css";

const CHART_OPTIONS = [
  { label: "Line",    value: "line"    },
  { label: "Bar",     value: "bar"     },
];

const RANGE_SELECTOR_OPTIONS = {
  buttons: [
    { step: "month", stepmode: "backward", count: 1,  label: "1m"  },
    { step: "month", stepmode: "backward", count: 6,  label: "6m"  },
    { step: "year",  stepmode: "todate",   count: 1,  label: "YTD" },
    { step: "year",  stepmode: "backward", count: 1,  label: "1y"  },
    { step: "all" },
  ],
};

// Main App Function
function App() {
  const backendUrl = "http://localhost:8000";

  // File/Dataset State
  const [uploadFile,      setUploadFile]      = useState([]);
  const [files,           setFiles]           = useState([]);
  const [selectedFile,    setSelectedFile]    = useState("");
  const [columns,         setColumns]         = useState([]);
  const [numericColumns,  setNumericColumns]  = useState([]);
  const [datasetInfo,     setDatasetInfo]     = useState({});

  // Chart Setup State
  const [chartType,    setChartType]    = useState("");
  const [xColumn,      setXColumn]      = useState("");
  const [yColumn,      setYColumn]      = useState([""]);
  const [sizeColumn,   setSizeColumn]   = useState("");
  const [namesColumn,  setNamesColumn]  = useState("");
  const [valuesColumn, setValuesColumn] = useState("");
  const [zColumn,      setZColumn]      = useState("");
  const [plotFigure,   setPlotFigure]   = useState(null);
  const [plotConfig,   setPlotConfig]   = useState({ responsive: true, displaylogo: false });

  // Analytics Data State
  const [summaryStatistics,  setSummaryStatistics]  = useState(null);
  const [dayfirst,           setDayFirst]            = useState(true);
  const [timestampColumn,    setTimestampColumn]     = useState("");
  const [dateTime,           setDateTime]            = useState("");
  const [durationResult,     setDurationResult]      = useState(null);
  const [stateColumn,        setStateColumn]         = useState("");
  const [autoCountingResult, setAutoCountingResult]  = useState(null);
  const [spValue,            setSpValue]             = useState("");
  const [pvValue,            setPvValue]             = useState("");
  const [rangeValue,         setRangeValue]          = useState(null);
  const [selectionData,      setSelectionData]       = useState([]);
  const [showFilter,         setShowFilter]          = useState(false);

  // Loading State
  const [isUploading,           setIsUploading]           = useState(false);
  const [isAnalyzing,           setIsAnalyzing]           = useState(false);
  const [isPlotting,            setIsPlotting]            = useState(false);
  const [isCalculatingDuration, setIsCalculatingDuration] = useState(false);
  const [isAutoCounting,        setIsAutoCounting]        = useState(false);
  const [isDeleting,            setIsDeleting]            = useState(false);
  const [rangeAnalyzing,        setRangeAnalyzing]        = useState(false);

  // ── status messages ───────────────────────────────────────────────────────
  const [message, setMessage] = useState("");
  const [error,   setError]   = useState("");

  // ── background PDF report ─────────────────────────────────────────────────
  const [plotConfigs, setPlotConfigs] = useState([]);
  const [spvPairs, setSpvPairs]       = useState([]); 
  const [autoReport,  setAutoReport]  = useState({
    status:       "idle",
    jobId:        null,
    downloadName: "",
    errorMsg:     "",
  });
  const autoReportRef    = useRef(null);
  const reportRequestRef = useRef(0);

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const plotRef         = useRef(null);
  const pieRef          = useRef(null);
  const rangeRef        = useRef(null);
  const selectedFileRef = useRef("");

  // ── derived ───────────────────────────────────────────────────────────────
  const hasDataset  = Boolean(selectedFile);
  const showXY      = Boolean(chartType);
  const showHeatmap = chartType === "heatmap";

  // Summary Function
  const summaryRows = useMemo(() => {
    if (!summaryStatistics?.mean) return [];
    const metrics = ["mean", "median", "min", "max", "std"];
    return Object.keys(summaryStatistics.mean).filter((col) => selectionData.includes(col)).map((col) => {
      const row = { column: col };
      metrics.forEach((m) => { row[m] = summaryStatistics[m]?.[col]; });
      return row;
    });
  }, [summaryStatistics, selectionData]);

  const stateColumnOptions = useMemo(() => columns, [columns]);

  const autoCountingSummary = useMemo(() => {
    if (!autoCountingResult) return null;
    const totalSegments =
      autoCountingResult.total_segments ??
      autoCountingResult.segments?.length ?? 0;
    const totalRecords = Array.isArray(autoCountingResult.segments)
      ? autoCountingResult.segments.reduce((s, r) => s + (r.record_count ?? 0), 0)
      : 0;
    return {
      totalSegments,
      totalRecords,
      totalAuto:     autoCountingResult.summary?.total_auto_counting   ?? null,
      totalManual:   autoCountingResult.summary?.total_manual_counting ?? null,
      totalRecorded: autoCountingResult.summary?.total_recorded        ?? null,
    };
  }, [autoCountingResult]);

  const selectedFileLabel = useMemo(() => {
    return files.find((file) => file.filename === selectedFile)?.displayName ?? selectedFile;
  }, [files, selectedFile]);

  const reportSections = useMemo(() => ([
    {
      label: "Dataset Overview",
      detail: hasDataset
        ? `${columns.length} columns detected`
        : "Select a dataset to start building the PDF",
      included: hasDataset,
    },
    {
      label: "Summary Statistics",
      detail: hasDataset
        ? `${numericColumns.length} numeric columns will be summarized`
        : "Waiting for dataset",
      included: hasDataset,
    },
    {
      label: "Hour Record",
      detail: timestampColumn
        ? `Uses timestamp column "${timestampColumn}"`
        : "Choose a timestamp column",
      included: Boolean(timestampColumn),
    },
    {
      label: "Auto / Manual Totals",
      detail: timestampColumn && stateColumn
        ? `Combines "${timestampColumn}" with "${stateColumn}"`
        : "Choose timestamp and state columns",
      included: Boolean(timestampColumn && stateColumn),
    },
    {
      label: "SP vs PV Analysis",
      detail: spvPairs.length
        ? `${spvPairs.length} pair(s) dalam report`
        : spValue && pvValue
        ? `Compares "${spValue}" with "${pvValue}"`
        : "Choose set point and process value",
      included: spvPairs.length > 0,
    },
    {
      label: "Custom Charts",
      detail: plotConfigs.length
        ? `${plotConfigs.length} chart${plotConfigs.length > 1 ? "s" : ""} queued for the PDF`
        : "Add charts from Chart Setup",
      included: plotConfigs.length > 0,
    },
  ]), [
    columns.length,
    hasDataset,
    numericColumns.length,
    plotConfigs.length,
    pvValue,
    selectedFile,
    spValue,
    spvPairs,
    stateColumn,
    timestampColumn,
  ]);

  // API helpers function
  const parseApiError = useCallback(async (response) => {
    try {
      const p = await response.json();
      return p.detail || p.error || JSON.stringify(p);
    } catch {
      return `${response.status} ${response.statusText}`;
    }
  }, []);

  const callApi = useCallback(async (path, options) => {
    const res = await fetch(`${backendUrl}${path}`, options);
    if (!res.ok) throw new Error(await parseApiError(res));
    return res.json();
  }, [backendUrl, parseApiError]);

  const normalizeFiles = useCallback((payload) => {
    if (Array.isArray(payload?.file_details)) {
      return payload.file_details.map((item) => ({
        filename:    item.filename,
        displayName: item.display_name ?? item.original_filename ?? item.filename,
      }));
    }
    if (Array.isArray(payload?.files)) {
      return payload.files.map((n) => ({ filename: n, displayName: n }));
    }
    return [];
  }, []);

  // ══════════════════════════════════════════════════════════════════════════
  //  BACKGROUND REPORT — poll + enqueue
  // ══════════════════════════════════════════════════════════════════════════
  const _pollJob = useCallback((jobId, requestId) => {
    if (autoReportRef.current) clearInterval(autoReportRef.current);

    autoReportRef.current = setInterval(async () => {
      if (requestId !== reportRequestRef.current) {
        clearInterval(autoReportRef.current);
        return;
      }
      try {
        const res = await callApi(`/generate-report/status/${jobId}`);
        if (requestId !== reportRequestRef.current) {
          clearInterval(autoReportRef.current);
          return;
        }
        if (res.status === "done") {
          clearInterval(autoReportRef.current);
          setAutoReport({
            status: "done", jobId,
            downloadName: res.filename || "report.pdf",
            errorMsg: "",
          });
        } else if (res.status === "error") {
          clearInterval(autoReportRef.current);
          setAutoReport((prev) => ({
            ...prev, status: "error",
            errorMsg: res.detail || "Generation failed",
          }));
        }
      } catch {
        clearInterval(autoReportRef.current);
      }
    }, 2500);
  }, [callApi]);

  const _enqueueReportJob = useCallback(async (
    filename, tsCol, dtCol, stateCol, spCol, pvCol, configs, statCols, spvPairsArg,
  ) => {
    if (!filename) return;
    if (autoReportRef.current) clearInterval(autoReportRef.current);
    const requestId = ++reportRequestRef.current;
    setAutoReport({ status: "pending", jobId: null, downloadName: "", errorMsg: "" });

    const body = {
      filename,
      timestamp_column:     tsCol    || null,
      date_column:          dtCol    || null,
      state_column:         stateCol || null,
      set_point_column:     spCol    || null,
      process_value_column: pvCol    || null,
      spv_pairs : spvPairsArg?.length
        ? spvPairsArg.map((p)=>({
            set_point_column : p.sp,
            process_value_column : p.pv,
            label : p.label || `${p.sp} vs ${p.pv}`,
        }))
        : null,
      plot_configs:         configs.length ? configs : null,
      stat_columns:         statCols?.length ? statCols : null,
    };

    try {
      const res = await callApi("/generate-report/reportlab/async", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });
      if (requestId !== reportRequestRef.current) return;
      setAutoReport((prev) => ({ ...prev, jobId: res.job_id }));
      _pollJob(res.job_id, requestId);
    } catch (err) {
      if (requestId !== reportRequestRef.current) return;
      setAutoReport({ status: "error", jobId: null, downloadName: "", errorMsg: err.message });
      setError(`Report job failed: ${err.message}`);
    }
  }, [callApi, _pollJob]);

  const requestReportRefresh = useCallback((overrides = {}) => {
    const filename = overrides.filename ?? selectedFile;
    if (!filename) return;
    _enqueueReportJob(
      filename,
      overrides.timestampColumn ?? timestampColumn,
      overrides.dateColumn ?? dateTime,
      overrides.stateColumn ?? stateColumn,
      overrides.spColumn ?? spValue,
      overrides.pvColumn ?? pvValue,
      overrides.plotConfigs ?? plotConfigs,
      overrides.statColumns ?? selectionData,
      overrides.spvPairs ?? spvPairs,
    );
  }, [
    _enqueueReportJob,
    dateTime,
    plotConfigs,
    pvValue,
    selectedFile,
    selectionData,
    spValue,
    spvPairs,
    stateColumn,
    timestampColumn,
  ]);

  const downloadReport = useCallback(async (jobId, downloadName) => {
    if (!jobId) return;
    try {
      const response = await fetch(`${backendUrl}/generate-report/download/${jobId}`);
      if (!response.ok) throw new Error("Download failed");
      const blob = await response.blob();
      const url  = window.URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = downloadName || "report.pdf";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(`Download error: ${err.message}`);
    }
  }, [backendUrl]);

  // ══════════════════════════════════════════════════════════════════════════
  //  SELECT FILE
  // ══════════════════════════════════════════════════════════════════════════
  const handleSelectFile = useCallback(async (filename) => {
    if (!filename) {
      if (autoReportRef.current) clearInterval(autoReportRef.current);
      reportRequestRef.current += 1;
      setAutoReport({ status: "idle", jobId: null, downloadName: "", errorMsg: "" });
      setPlotConfigs([]);
      setSpvPairs([]);
      setSelectedFile(""); setColumns([]); setNumericColumns([]);
      setXColumn(""); setYColumn([""]); setSizeColumn("");
      setNamesColumn(""); setValuesColumn(""); setZColumn("");
      setTimestampColumn(""); setStateColumn("");
      setSummaryStatistics(null); setPlotFigure(null);
      setDurationResult(null); setAutoCountingResult(null);
      setRangeValue(null); setDateTime(""); setDatasetInfo({});
      setMessage(""); setError("");
      return;
    }

    setSelectedFile(filename);
    setPlotConfigs([]);
    setSpvPairs([]);
    setSummaryStatistics(null); setPlotFigure(null);
    setDurationResult(null);    setAutoCountingResult(null);
    setRangeValue(null);        setDatasetInfo({});
    setMessage("");             setError("");

    try {
      const [colPayload, infoPayload] = await Promise.all([
        callApi(`/dataset/${encodeURIComponent(filename)}/columns`),
        callApi(`/dataset/${encodeURIComponent(filename)}/info`).catch(() => ({ info: {} })),
      ]);

      const allColumns = colPayload.columns         ?? [];
      const numeric    = colPayload.numeric_columns ?? [];

      setColumns(allColumns);
      setNumericColumns(numeric);
      setSelectionData(numeric);
      setDatasetInfo(infoPayload.info ?? {});

      setXColumn((p)      => allColumns.includes(p) ? p : (allColumns[0] ?? ""));
      setYColumn((p)      => {
        const valid = p.filter((c) => numeric.includes(c));
        return valid.length ? valid : (numeric.length ? [numeric[0]] : [""]);
      });
      setSizeColumn((p)   => numeric.includes(p)   ? p : "");
      setNamesColumn((p)  => allColumns.includes(p) ? p : (allColumns[0] ?? ""));
      setValuesColumn((p) => numeric.includes(p)   ? p : (numeric[0]    ?? ""));
      setZColumn((p)      => numeric.includes(p)   ? p : (numeric[0]    ?? ""));

      const timeKeywords  = ["time", "timestamp"];
      const dateKeywords  = ["date", "tanggal"];
      const stateKeywords = ["auto", "manual", "mode", "status"];
      const spKeywords    = ["set_point", "setpoint", "sp"];
      const pvKeywords    = ["process_value", "pv", "process"];

      const autoDetectedTime  = allColumns.find((c) => timeKeywords .some((k) => c.toLowerCase().includes(k))) ?? allColumns[0] ?? "";
      const detectedDate      = allColumns.find((c) => dateKeywords .some((k) => c.toLowerCase().includes(k))) ?? "";
      const autoDetectedState = allColumns.find((c) => stateKeywords.some((k) => c.toLowerCase().includes(k))) ?? numeric[0] ?? allColumns[0] ?? "";
      const autoSp            = numeric.find((c) => spKeywords.some((k) => c.toLowerCase().includes(k))) ?? numeric[0] ?? "";
      const autoPv            = numeric.find((c) => pvKeywords.some((k) => c.toLowerCase().includes(k))) ?? numeric[1] ?? numeric[0] ?? "";

      setTimestampColumn(autoDetectedTime);
      setDateTime(detectedDate);
      setStateColumn(autoDetectedState);
      setSpValue(autoSp);
      setPvValue(autoPv);

      setTimeout(() => {
        _enqueueReportJob(
          filename,
          autoDetectedTime,
          detectedDate,
          autoDetectedState,
          autoSp,
          autoPv,
          [],
          numeric,
          [],
        );
      }, 0);

    } catch (err) {
      setError(`Failed to read columns: ${err.message}`);
    }
  }, [callApi, _enqueueReportJob]);

  // ══════════════════════════════════════════════════════════════════════════
  //  EFFECTS
  // ══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    const plotly_pie = globalThis.Plotly;
    if (!plotly_pie || !pieRef.current) return;
    if (!autoCountingResult) { plotly_pie.purge(pieRef.current); return; }

    const autoSecs    = autoCountingSummary.totalAuto?.seconds   ?? 0;
    const manualSecs  = autoCountingSummary.totalManual?.seconds ?? 0;
    const autoHuman   = autoCountingSummary.totalAuto?.human     ?? "00:00:00";
    const manualHuman = autoCountingSummary.totalManual?.human   ?? "00:00:00";

    const pieslices = [
      { label: "Auto",   value: autoSecs,   text: autoHuman,   color: "#1a9fd4" },
      { label: "Manual", value: manualSecs, text: manualHuman, color: "#e63946" },
    ].filter((s) => s.value > 0);

    plotly_pie.react(
      pieRef.current,
      [{
        type: "pie", hole: "0.45",
        values: pieslices.map((s) => s.value),
        labels: pieslices.map((s) => s.label),
        text:   pieslices.map((s) => s.text),
        textinfo: "label+percent",
        hovertemplate: "<b>%{label}</b><br>%{text}<br>%{percent}<extra></extra>",
        marker: { colors: pieslices.map((s) => s.color) },
      }],
      {
        margin: { t: 20, b: 40, l: 40, r: 30 },
        showlegend: true,
        legend: { orientation: "h", y: -0.15, font: { color: "#8a9db0" } },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        height: 220,
        font: { family: "IBM Plex Sans, sans-serif", color: "#8a9db0" },
      },
      { responsive: true, displaylogo: false, displayModeBar: false },
    );
  }, [autoCountingSummary, autoCountingResult]);

  useEffect(() => {
    const plotlyLib = globalThis.Plotly;
    if (!plotlyLib || !plotRef.current) return;
    if (!plotFigure) { plotlyLib.purge(plotRef.current); return; }

    const isDateAxis = plotFigure.layout?.xaxis?.type === "date";
    const xaxis = {
      ...(plotFigure.layout?.xaxis ?? {}),
      rangeslider: {
        visible: true, thickness: 0.065,
        bgcolor: "rgba(246,162,26,0.06)",
        bordercolor: "rgba(246,162,26,0.22)", borderwidth: 1,
      },
    };
    if (isDateAxis) {
      xaxis.rangeselector = {
        ...RANGE_SELECTOR_OPTIONS,
        bgcolor: "#0e1420",
        activecolor: "rgba(246,162,26,0.25)",
        bordercolor: "rgba(246,162,26,0.3)",
        borderwidth: 1,
        font: { family: "Barlow Condensed, sans-serif", size: 11, color: "#c8d6e5" },
      };
    }

    const layout = {
      ...plotFigure.layout,
      xaxis,
      paper_bgcolor: "transparent",
      plot_bgcolor:  "transparent",
      font: { family: "IBM Plex Sans, sans-serif", color: "#8a9db0", size: 11 },
    };

    plotlyLib.react(plotRef.current, plotFigure.data, layout, plotConfig);
  }, [plotFigure, plotConfig]);

  useEffect(() => {
    const plotly_pie = globalThis.Plotly;
    if (!plotly_pie || !rangeRef.current) return;
    if (!rangeValue) { plotly_pie.purge(rangeRef.current); return; }

    const normal = rangeValue.summary?.normal?.count  ?? 0;
    const lower  = rangeValue.summary?.lower?.count   ?? 0;
    const higher = rangeValue.summary?.higher?.count  ?? 0;

    const allSlices = [
      { label: "Dalam SP",      value: normal, color: "#1a9fd4" },
      { label: "Lebih Rendah",  value: lower,  color: "#f6a21a" },
      { label: "Lebih Tinggi",  value: higher, color: "#e63946" },
    ].filter((s) => s.value > 0);

    if (!allSlices.length) { plotly_pie.purge(rangeRef.current); return; }

    plotly_pie.react(
      rangeRef.current,
      [{
        type: "pie", hole: 0.45,
        values: allSlices.map((s) => s.value),
        labels: allSlices.map((s) => s.label),
        textinfo: "label+percent",
        hovertemplate: "<b>%{label}</b><br>%{value} records<br>%{percent}<extra></extra>",
        marker: { colors: allSlices.map((s) => s.color) },
      }],
      {
        margin: { t: 20, b: 40, l: 40, r: 30 },
        showlegend: true,
        legend: { orientation: "h", y: -0.15, font: { color: "#8a9db0" } },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        height: 220,
        font: { family: "IBM Plex Sans, sans-serif", color: "#8a9db0" },
      },
      { responsive: true, displaylogo: false, displayModeBar: false },
    );
  }, [rangeValue]);

  useEffect(() => { selectedFileRef.current = selectedFile; }, [selectedFile]);

  useEffect(() => {
    if (!message) return undefined;
    const t = setTimeout(() => setMessage(""), 3000);
    return () => clearTimeout(t);
  }, [message]);

  useEffect(() => {
    return () => {
      if (autoReportRef.current) clearInterval(autoReportRef.current);
    };
  }, []);

  // Initial file list load
  useEffect(() => {
    let isMounted = true;
    (async () => {
      try {
        const payload = await callApi("/files");
        if (!isMounted) return;
        const list = normalizeFiles(payload);
        setFiles(list);
        if (list.length && !selectedFileRef.current) {
          await handleSelectFile(list[0].filename);
        }
      } catch (err) {
        if (isMounted) setError(`Failed to load files: ${err.message}`);
      }
    })();
    return () => { isMounted = false; };
  }, [callApi, handleSelectFile, normalizeFiles]);

  // ══════════════════════════════════════════════════════════════════════════
  //  HANDLERS
  // ══════════════════════════════════════════════════════════════════════════
  const handleUpload = async () => {
    if (!uploadFile.length) { setError("Pilih File Dengan Format CSV atau XLSX!"); return; }
    const formData = new FormData();
    for (const file of uploadFile) formData.append("files", file);
    setIsUploading(true); setError(""); setMessage("");
    try {
      const payload   = await callApi("/upload", { method: "POST", body: formData });
      const results   = payload.results ?? [];
      const succeeded = results.filter((r) => r.status === "Berhasil");
      const failed    = results.filter((r) => r.status === "Gagal");
      if (failed.length)    setError(failed.map((r) => `${r.original_filename}: ${r.error}`).join(" | "));
      if (succeeded.length) setMessage(`Uploaded: ${succeeded.map((r) => r.original_filename).join(", ")}`);
      setUploadFile([]);
      const newFiles = succeeded.map((r) => ({ filename: r.filename, displayName: r.original_filename ?? r.filename }));
      setFiles((prev) => {
        const existing = prev.filter((item) => !newFiles.some((a) => a.filename === item.filename));
        return [...newFiles, ...existing];
      });
      if (newFiles.length) await handleSelectFile(newFiles[0].filename);
    } catch (err) {
      setError(`Upload failed: ${err.message}`);
    } finally { setIsUploading(false); }
  };

  const handleDeleteFile = async () => {
    if (!selectedFile) { setError("Select a dataset first."); return; }
    const targetFile = selectedFile;
    const confirmed  = globalThis.confirm?.(`Delete ${targetFile}?`) ?? true;
    if (!confirmed) return;
    setIsDeleting(true); setError(""); setMessage("");
    try {
      await callApi(`/files/${encodeURIComponent(targetFile)}`, { method: "DELETE" });
      setMessage(`Deleted: ${targetFile}`);
    } catch (err) {
      if (String(err.message).toLowerCase().includes("not found")) {
        setMessage(`Already deleted: ${targetFile}`);
      } else { setError(`Delete failed: ${err.message}`); return; }
    } finally { setIsDeleting(false); }
    const remaining = files.filter((item) => item.filename !== targetFile);
    setFiles(remaining);
    await handleSelectFile(remaining[0]?.filename ?? "");
  };

  const handleAnalyze = async () => {
    if (!hasDataset) { setError("Select a dataset first."); return; }
    setIsAnalyzing(true); setError("");
    try {
      const payload = await callApi("/analyze", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: selectedFile }),
      });
      setSummaryStatistics(payload.summary_statistics ?? null);
      setMessage("Summary statistics generated.");
    } catch (err) { setError(`Analyze failed: ${err.message}`); }
    finally { setIsAnalyzing(false); }
  };

  const handleRangeAnalysis = async () => {
    if (!hasDataset) { setError("Pilih Dataset terlebih dahulu."); return; }
    if (!spValue)    { setError("Pilih kolom Set Point.");          return; }
    if (!pvValue)    { setError("Pilih kolom Process Value.");      return; }
    setRangeAnalyzing(true); setError("");
    try {
      const payload = await callApi("/spv-analysis", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: selectedFile, set_point_column: spValue, process_value_column: pvValue }),
      });
      setRangeValue(payload);
      setMessage("Range Analysis Complete.");
    } catch (err) { setError(`Analisis Range Gagal: ${err.message}`); }
    finally { setRangeAnalyzing(false); }
  };

  const handleAddYColumn    = () => setYColumn((p) => [...p, ""]);
  const handleRemoveYColumn = (i) => setYColumn((p) => p.filter((_, idx) => idx !== i));
  const handleChangeYColumn = (i, v) => setYColumn((p) => p.map((c, idx) => (idx === i ? v : c)));

  const handlePlot = async () => {
    if (!hasDataset) { setError("Select a dataset first."); return; }
    if (!chartType)  { setError("Choose a chart type first."); return; }
    const yFiltered = yColumn.filter(Boolean);
    setIsPlotting(true); setError("");
    try {
      const response = await callApi("/plot", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename:    selectedFile,
          chart_type:  chartType,
          x:           xColumn      || null,
          y:           yFiltered.length ? yFiltered : null,
          size:        sizeColumn   || null,
          names:       namesColumn  || null,
          values:      valuesColumn || null,
          z:           zColumn      || null,
          date_column: dateTime     || null,
        }),
      });
      setPlotFigure(response.figure);
      setPlotConfig(response.config ?? { responsive: true, displaylogo: false });
      setMessage(`Rendered ${response.chart_type} chart.`);
    } catch (err) { setError(`Plot failed: ${err.message}`); }
    finally { setIsPlotting(false); }
  };

  const handleDuration = async () => {
    if (!hasDataset)       { setError("Select a dataset first.");          return; }
    if (!timestampColumn)  { setError("Choose a timestamp column first."); return; }
    setIsCalculatingDuration(true); setError("");
    try {
      const payload = await callApi("/duration", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: selectedFile, timestamp_column: timestampColumn, date_column: dateTime || null, dayfirst }),
      });
      setDurationResult(payload);
      setMessage("Total hour record calculated.");
    } catch (err) { setError(`Duration failed: ${err.message}`); }
    finally { setIsCalculatingDuration(false); }
  };

  const handleAutoCounting = async () => {
    if (!hasDataset)      { setError("Select a dataset first.");          return; }
    if (!timestampColumn) { setError("Choose a timestamp column first."); return; }
    if (!stateColumn)     { setError("Choose a state column first.");     return; }
    setIsAutoCounting(true); setError("");
    try {
      const payload = await callApi("/counting", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: selectedFile, timestamp_column: timestampColumn, state_column: stateColumn, date_column: dateTime || null, dayfirst }),
      });
      setAutoCountingResult(payload);
      setMessage("Auto/Manual totals calculated.");
    } catch (err) { setError(`Auto/Manual counting failed: ${err.message}`); }
    finally { setIsAutoCounting(false); }
  };

  const handleAddChartToReport = () => {
    const cfg = {
      chart_type:  chartType,
      x:           xColumn    || null,
      y:           yColumn.filter(Boolean),
      title:       null,
      date_column: dateTime   || null,
    };
    const next = [...plotConfigs, cfg];
    setPlotConfigs(next);
    setMessage(`Chart #${next.length} ditambahkan ke report.`);
    requestReportRefresh({ plotConfigs: next });
  };

  const handleAddPairToReport = () => {
    if(!spValue || !pvValue) {
      setError("Choose Set Point and Process Value First!");
      return;
    }
    const pair = { sp: spValue, pv:pvValue, label : `${spValue} vs ${pvValue}`};
    const next = [...spvPairs, pair];
    setSpvPairs(next);
    setMessage(`SP/PV Pair #${next.length} added to Report`);
    requestReportRefresh({spvPairs:next});
  };
  
  const formatNumber = (value) => {
    if (typeof value !== "number" || Number.isNaN(value)) return "—";
    return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
  };

  const datasetInfoEntries = Object.entries(datasetInfo);

  //  RENDER
  return (
    <div className="dashboard-app">

      {/* ── HERO ─────────────────────────────────────────────────────────── */}
      <header className="hero">
        <div className="hero-title">
          <img className="hero-logo" src={logo} alt="Sinarmas Logo" />
          <h1>Interactive Dashboard</h1>
        </div>
        {datasetInfoEntries.length > 0 && (
          <div className="dataset-info-bar">
            {datasetInfoEntries.map(([key, value]) => (
              <div className="dataset-info-badge" key={key}>
                <span className="dataset-info-key">{key}</span>
                <span className="dataset-info-value">{value}</span>
              </div>
            ))}
          </div>
        )}
      </header>

      <main className="content-grid">

        {/* ── DATA UPLOAD ──────────────────────────────────────────────── */}
        <section className="panel data-source">
          <h2>Data Upload</h2>
          <div className="inline-actions">
            <input
              type="file" multiple accept=".csv,.xlsx"
              onChange={(e) => setUploadFile(Array.from(e.target.files ?? []))}
            />
          </div>
          <div className="inline-actions file-actions">
            <button type="button" onClick={handleUpload}
              disabled={isUploading || !uploadFile.length}>
              {isUploading ? "Uploading…" : "Upload"}
            </button>
            <button type="button" className="action-button ghost"
              onClick={handleDeleteFile} disabled={!hasDataset || isDeleting}>
              {isDeleting ? "Deleting…" : "Delete"}
            </button>
          </div>
          <label className="field">
            <select value={selectedFile}
              onChange={(e) => handleSelectFile(e.target.value)}
              disabled={!files.length}>
              <option value="">(Select dataset)</option>
              {files.map((f) => (
                <option key={f.filename} value={f.filename}>{f.displayName}</option>
              ))}
            </select>
          </label>
          {error && <p className="status error" style={{ marginTop: "0.75rem" }}>{error}</p>}
          {message && !error && <p className="status ok" style={{ marginTop: "0.75rem" }}>{message}</p>}
        </section>

        {/* ── SUMMARY SHEET ────────────────────────────────────────────── */}
        <section className="panel summary">
          <div className = "summary-header">
            <h2>Data Summary</h2>
            {numericColumns.length > 0 && (
              <button
                type = "button"
                className = {showFilter ? "red stat-mini-btn" : "ghost stat-mini-btn"}
                onClick={() => setShowFilter((p) => !p)}
              >
                {showFilter?"Hide":`▼ Columns (${selectionData.length}/${numericColumns.length})`}
              </button>
            )}
          </div>

          {showFilter && numericColumns.length > 0 && (
            <div className = "stat-filter-panel">
              <div className = "stat-filter-actions">
                <button type = "button"
                 className = "y-add"
                 onClick = {() => {
                    setSelectionData(numericColumns);
                    requestReportRefresh({statColumns:numericColumns});
                 }}
                 >
                  Select All
                 </button>
                 <button type = "button"
                    className = "y-remove"
                    style = {{fontSize:"0.6rem",padding:"2px 8px"}}
                    onClick = {() => {
                      setSelectionData([]);
                      requestReportRefresh({statColumns:[]});
                    }}>
                      Clear All
                    </button>
              </div>
              <div className = "stat-filter-grid">
                {numericColumns.map((col) => (
                  <label key = {col} className = "stat-col-checkbox">
                    <input
                      type = "checkbox"
                      checked = {selectionData.includes(col)}
                      onChange = {(e) => {
                        const next = e.target.checked
                        ? [...selectionData,col]
                        : selectionData.filter((c)=>c !== col);
                      setSelectionData(next);
                      requestReportRefresh({statColumns:next});
                      }}
                    />
                    <span title = {col}>{col}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {!summaryRows.length && (
            <p className = "muted">
              {summaryStatistics
                ? ""
                : ""}
            </p>
          )}
          {!!summaryRows.length && (
            <div className = "table-scroll">
              <table>
                <thead>
                  <tr>
                    {["Column","Mean","Median","Min","Max","Std"].map((h)=>(
                      <th key = {h}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {summaryRows.map((row) => (
                    <tr key = {row.column}>
                      <td>{row.column}</td>
                      <td>{formatNumber(row.mean)}</td>
                      <td>{formatNumber(row.median)}</td>
                      <td>{formatNumber(row.min)}</td>
                      <td>{formatNumber(row.max)}</td>
                      <td>{formatNumber(row.std)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </section>

        {/* ── CHART SETUP ──────────────────────────────────────────────── */}
        <section className="panel chart-setup">
          <h2>Chart Setup</h2>
          <div className="form-grid">
            <label className="field chart-field">
              Chart type
              <select className="input-compact compact-select" value={chartType}
                onChange={(e) => setChartType(e.target.value)}>
                <option value="">(None)</option>
                {CHART_OPTIONS.map((c) => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>
            </label>

            {showXY && (
              <label className="field x-field">
                X Axis
                <select className="input-compact" value={xColumn}
                  onChange={(e) => setXColumn(e.target.value)}>
                  <option value="">(None)</option>
                  {columns.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
            )}

            {showXY && (
              <div className="field y-field">
                <span>Y Axis</span>
                <div className="y-list">
                  {yColumn.map((col, idx) => (
                    <div key={`y-${idx}`} className="y-row">
                      <select className="input-compact y-select" value={col}
                        onChange={(e) => handleChangeYColumn(idx, e.target.value)}>
                        <option value="">(None)</option>
                        {numericColumns.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                      {yColumn.length > 1 && (
                        <button type="button" className="y-remove"
                          onClick={() => handleRemoveYColumn(idx)}>Remove</button>
                      )}
                    </div>
                  ))}
                </div>
                <button type="button" className="y-add" onClick={handleAddYColumn}>
                  + Add Series
                </button>
              </div>
            )}
          </div>

          <div className="inline-actions" style={{ marginTop: "0.75rem" }}>
            <button type="button" onClick={handleAnalyze}
              disabled={!hasDataset || isAnalyzing}>
              {isAnalyzing ? "Analyzing…" : "Summary Statistics"}
            </button>
            <button type="button" onClick={handlePlot}
              disabled={!hasDataset || isPlotting}>
              {isPlotting ? "Rendering…" : "Generate Plot"}
            </button>
            <button type="button" className="action-button ghost"
              disabled={!chartType || !hasDataset}
              onClick={handleAddChartToReport}>
              {`+ Add to Report${plotConfigs.length ? ` (${plotConfigs.length})` : ""}`}
            </button>
            <button
              type="button"
              className="action-button"
              onClick={() => {
                if(autoReport.status === "done"){
                  downloadReport(autoReport.jobId,autoReport.downloadName);
                } else if (autoReport.status === "idle" || autoReport.status === "error"){
                  requestReportRefresh();
                }
              }}
              disabled={!hasDataset || autoReport.status === "pending"}>
                {autoReport.status === "pending" ? "Generating...":
                 autoReport.status === "done" ? "⬇Download PDF":
                 autoReport.status === "error" ? "Retry": "Generate Report"}
              </button>
              {autoReport.status === "pending" && (
                <div className = "pdf-progress">
                  <div className = "pdf-progress-bar">
                    <div className = "pdf-progress-fill"/>
                  </div>
                  <p className = "pdf-progress-label"> Generating PDF Report </p>
                </div>
              )}
              {autoReport.status === "error" && (
                <p className = "status error" style={{fontSize:"0.72rem"}}>
                  {autoReport.errorMsg || "Generating Report Failed"}
                </p>
              )}
          </div>

          {plotConfigs.length > 0 && (
            <div className="duration-card" style={{ marginTop: "0.85rem" }}>
              <div className="duration-row" style={{ borderBottom: "none", paddingBottom: 0 }}>
                <span className="duration-label" style={{ fontWeight: 700 }}>Charts in Report</span>
                <button type="button" className="y-remove"
                  style={{ fontSize: "0.62rem", height: "22px", padding: "0 8px" }}
                  onClick={() => {
                    const next = [];
                    setPlotConfigs(next);
                    setMessage("All charts removed from report.");
                    requestReportRefresh({ plotConfigs: next });
                  }}>
                  Clear All
                </button>
              </div>
              {plotConfigs.map((c, i) => (
                <div key={i} className="duration-row">
                  <span className="duration-label">#{i + 1} {c.chart_type.toUpperCase()}</span>
                  <span className="duration-value" style={{ fontSize: "0.72rem" }}>
                    {c.x} × {(c.y || []).join(", ")}
                  </span>
                  <button type="button" className="y-remove"
                    style={{ fontSize: "0.58rem", height: "20px", padding: "0 6px", marginLeft: "6px" }}
                    onClick={() => {
                      const next = plotConfigs.filter((_, idx) => idx !== i);
                      setPlotConfigs(next);
                      requestReportRefresh({ plotConfigs: next });
                    }}>×
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── VISUALISASI ──────────────────────────────────────────────── */}
        <section className="panel output">
          <h2>Data Visualisation</h2>
          <div className="plot-wrapper">
            {plotFigure && message && (
              <p className="status ok plot-message">{message}</p>
            )}
            <div ref={plotRef}
              className={plotFigure ? "plot-canvas ready" : "plot-canvas"} />
            {!plotFigure && (
              <div className="empty-state">
                <p>No chart rendered.</p>
                <p>Upload data, choose columns, then click Generate Plot.</p>
              </div>
            )}
          </div>
        </section>

        {/* ── BOTTOM ROW ───────────────────────────────────────────────── */}
        <div className="bottom-row">

          {/* HOUR RECORD */}
          <section className="panel hour-record">
            <h2>Hour Record</h2>
            <label className="field">
              Timestamp Column
              <select value={timestampColumn}
                onChange={(e) => {
                  const next = e.target.value;
                  setTimestampColumn(next);
                  setDurationResult(null); setAutoCountingResult(null);
                  requestReportRefresh({ timestampColumn: next });
                }}
                disabled={!columns.length}>
                <option value="">(None)</option>
                {columns.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <button type="button" className="action-button"
              onClick={handleDuration}
              disabled={!hasDataset || isCalculatingDuration}>
              {isCalculatingDuration ? "Calculating…" : "Total Hour Record"}
            </button>
            {durationResult && (
              <div className="duration-card">
                {[
                  ["Start",             durationResult.start_time],
                  ["End",               durationResult.end_time],
                  ["Total (HH:MM:SS)",  durationResult.total_duration?.human_readable ?? "—"],
                  ["Minutes",           formatNumber(durationResult.total_duration?.minutes)],
                  ["Valid Records",     formatNumber(durationResult.total_records)],
                ].map(([label, value]) => (
                  <div className="duration-row" key={label}>
                    <span className="duration-label">{label}</span>
                    <span className="duration-value">{value}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* AUTO RECORD */}
          <section className="panel auto-record">
            <h2>Auto Record</h2>
            <label className="field">
              State Column
              <select value={stateColumn}
                onChange={(e) => {
                  const next = e.target.value;
                  setStateColumn(next);
                  setAutoCountingResult(null);
                  requestReportRefresh({ stateColumn: next });
                }}
                disabled={!stateColumnOptions.length}>
                <option value="">(None)</option>
                {stateColumnOptions.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <div className="action-stack">
              <button type="button" className="action-button"
                onClick={handleAutoCounting}
                disabled={!hasDataset || isAutoCounting}>
                {isAutoCounting ? "Calculating…" : "Total Auto / Manual"}
              </button>
            </div>
            {autoCountingSummary && (
              <div className="duration-card">
                {[
                  ["Total Recorded",          autoCountingSummary.totalRecorded?.human     ?? "—",  null],
                  ["Auto Total (HH:MM:SS)",   autoCountingSummary.totalAuto?.human         ?? "—",
                    autoCountingSummary.totalAuto?.percentage],
                  ["Manual Total (HH:MM:SS)", autoCountingSummary.totalManual?.human       ?? "—",
                    autoCountingSummary.totalManual?.percentage],
                  ["Segments",  formatNumber(autoCountingSummary.totalSegments), null],
                  ["Records",   formatNumber(autoCountingSummary.totalRecords),  null],
                ].map(([label, value, pct]) => (
                  <div className="duration-row" key={label}>
                    <span className="duration-label">{label}</span>
                    <span className="duration-value">
                      {value}
                      {pct != null && <span className="duration-pct"> ({pct}%)</span>}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div ref={pieRef}
             style={{ width: "100%", minHeight: autoCountingResult ? 220 : 0 }} />
          </section>

          {/* RANGE VALUE ANALYSIS */}
          <section className="panel spv-record">
            <h2>Range Analysis</h2>
            <label className="field">
              Set Point
              <select value={spValue}
                onChange={(e) => {
                  const next = e.target.value;
                  setSpValue(next);
                  setRangeValue(null);
                  requestReportRefresh({ spColumn: next });
                }}
                disabled={!numericColumns.length}>
                <option value="">(None)</option>
                {numericColumns.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <label className="field">
              Process Value
              <select value={pvValue}
                onChange={(e) => {
                  const next = e.target.value;
                  setPvValue(next);
                  setRangeValue(null);
                  requestReportRefresh({ pvColumn: next });
                }}
                disabled={!numericColumns.length}>
                <option value="">(None)</option>
                {numericColumns.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <button type="button" className="action-button"
              onClick={handleRangeAnalysis}
              disabled={!hasDataset || rangeAnalyzing}>
              {rangeAnalyzing ? "Analyzing…" : "Analyse SP & PV"}
            </button>
            <button
              type = "button"
              className = "action-button ghost"
              disabled = {!spValue || !pvValue}
              onClick = {handleAddPairToReport}
              style = {{marginTop:"0.45rem"}}
            >
              {`+ Add to Report${spvPairs.length? `(${spvPairs.length})`:""}`}
            </button>
            {spvPairs.length > 0 && (
              <div className = "duration-card" style = {{marginTop : "0.8rem"}}>
                <div className = "duration-row" style = {{borderBottom : "none", paddingBottom : 0}}>
                  <span className = "duration-label" style = {{fontWeigth:700}}>
                    Range Value Report
                  </span>
                  <button
                    type = "button"
                    className = "y-remove"
                    style = {{fontSize:"0.6rem",height:"22px",padding:"0 8px"}}
                    onClick = {() => {
                      setSpvPairs([]);
                      setMessage("Data Deleted");
                      requestReportRefresh({spvPairs:[]});
                    }}
                    >
                      CLEAR
                    </button>
                </div>
                {spvPairs.map((p,i) => (
                  <div className = "duration-row" key = {i}>
                    <span className = "duration-label">#{i+1}</span>
                    <span className = "duration-value" style = {{fontSize : "0.7rem"}}>
                      {p.sp} x {p.pv}
                    </span>
                    <button
                      type = "button"
                      className = "y-remove"
                      style = {{fontSize : "0.5rem",height:"20px",padding:"0 6px",marginLeft : "6px"}}
                      onClick={() => {
                        const next = spvPairs.filter((_,idx) => idx !==i);
                        setSpvPairs(next);
                        requestReportRefresh({spvPairs:next}); 
                      }}
                    >
                      X
                    </button>
                    </div>
                ))}
              </div>
            )}
            {rangeValue && (
              <div className="duration-card">
                <div className="duration-row">
                  <span className="duration-label">Total Records</span>
                  <span className="duration-value">{formatNumber(rangeValue.total_records)}</span>
                </div>
                {[
                  ["Within Set Point",   rangeValue.summary.normal],
                  ["Below SP",           rangeValue.summary.lower],
                  ["Above SP",           rangeValue.summary.higher],
                ].map(([label, s]) => (
                  <div className="duration-row" key={label}>
                    <span className="duration-label">{label}</span>
                    <span className="duration-value">
                      {formatNumber(s.count)}
                      <span className="duration-pct"> ({s.percentage}%)</span>
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div ref={rangeRef}
              style={{ width: "100%", minHeight: rangeValue ? 220 : 0 }} />
          </section>

        </div>{/* end bottom-row */}
      </main>
    </div>
  );
}


export default App;
