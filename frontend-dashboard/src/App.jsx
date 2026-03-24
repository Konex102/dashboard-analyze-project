// New Update
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import logo from "./assets/Sinarmas_logo.png";
import "./App.css";

const CHART_OPTIONS = [
  { label: "Line", value: "line" },
  { label: "Bar", value: "bar" },
  { label: "Area", value: "area" },
  { label: "Scatter", value: "scatter" },
];

function App() {
  const backendUrl = "http://localhost:8000";
  const [uploadFile, setUploadFile] = useState([]);
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState("");
  const [columns, setColumns] = useState([]);
  const [numericColumns, setNumericColumns] = useState([]);
  const [chartType, setChartType] = useState("");
  const [xColumn, setXColumn] = useState("");
  const [yColumn, setYColumn] = useState([""]);
  const [sizeColumn, setSizeColumn] = useState("");
  const [namesColumn, setNamesColumn] = useState("");
  const [valuesColumn, setValuesColumn] = useState("");
  const [zColumn, setZColumn] = useState("");
  const [summaryStatistics, setSummaryStatistics] = useState(null);
  const [plotFigure, setPlotFigure] = useState(null);
  const [timestampColumn, setTimestampColumn] = useState("");
  const [durationResult, setDurationResult] = useState(null);
  const [stateColumn, setStateColumn] = useState("");
  const [autoCountingResult, setAutoCountingResult] = useState(null);
  const [plotConfig, setPlotConfig] = useState({
    responsive: true,
    displaylogo: false,
  });
  const [dateTime, setDateTime] = useState("");
  const [spValue, setSpValue] = useState("");
  const [pvValue, setPvValue] = useState("");
  const [rangeValue, setRangeValue] = useState(null);
  const [rangeAnalyzing, setRangeAnalyzing] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isPlotting, setIsPlotting] = useState(false);
  const [isCalculatingDuration, setIsCalculatingDuration] = useState(false);
  const [isAutoCounting, setIsAutoCounting] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [datasetInfo, setDatasetInfo] = useState({});

  const plotRef = useRef(null);
  const pieRef = useRef(null);
  const rangeRef = useRef(null);
  const selectedFileRef = useRef("");
  const hasDataset = Boolean(selectedFile);
  const showXY = Boolean(chartType);
  const showHeatmap = chartType === "heatmap";

  const summaryRows = useMemo(() => {
    if (!summaryStatistics?.mean) return [];
    const metrics = ["mean", "median", "min", "max", "std"];
    return Object.keys(summaryStatistics.mean).map((columnName) => {
      const row = { column: columnName };
      metrics.forEach((metric) => {
        row[metric] = summaryStatistics[metric]?.[columnName];
      });
      return row;
    });
  }, [summaryStatistics]);

  const stateColumnOptions = useMemo(() => columns, [columns]);

  const autoCountingSummary = useMemo(() => {
    if (!autoCountingResult) return null;
    const totalSegments =
      autoCountingResult.total_segments ??
      autoCountingResult.segments?.length ??
      0;
    const totalRecords = Array.isArray(autoCountingResult.segments)
      ? autoCountingResult.segments.reduce(
          (sum, s) => sum + (s.record_count ?? 0),
          0,
        )
      : 0;
    return {
      totalSegments,
      totalRecords,
      totalAuto: autoCountingResult.summary?.total_auto_counting ?? null,
      totalManual: autoCountingResult.summary?.total_manual_counting ?? null,
      totalRecorded: autoCountingResult.summary?.total_recorded ?? null,
    };
  }, [autoCountingResult]);

  useEffect(() => {
    const plotlyLib = globalThis.Plotly;
    if (!plotlyLib || !plotRef.current) return;
    if (!plotFigure) {
      plotlyLib.purge(plotRef.current);
      return;
    }
    plotlyLib.react(
      plotRef.current,
      plotFigure.data,
      plotFigure.layout,
      plotConfig,
    );
  }, [plotFigure, plotConfig]);

  useEffect(() => {
    const plotly_pie = globalThis.Plotly;
    if (!plotly_pie || !pieRef.current) return;

    if (!autoCountingResult) {
      plotly_pie.purge(pieRef.current);
      return;
    }

    const autoSecs = autoCountingSummary.totalAuto?.seconds ?? 0;
    const manualSecs = autoCountingSummary.totalManual?.seconds ?? 0;
    const autoHuman = autoCountingSummary.totalAuto?.human ?? "00:00:00";
    const manualHuman = autoCountingSummary.totalManual?.human ?? "00:00:00";

    const pieslices = [
      { label: "Auto", value: autoSecs, text: autoHuman, color: "#0062ff" },
      {
        label: "Manual",
        value: manualSecs,
        text: manualHuman,
        color: "#ff0000",
      },
    ].filter((s) => s.value > 0);

    const data = [
      {
        type: "pie",
        hole: "0.45",
        values: pieslices.map((s) => s.value),
        labels: pieslices.map((s) => s.label),
        text: pieslices.map((s) => s.text),
        textinfo: "label+percent",
        hovertemplate:
          "<b>%{label}</b><br>%{text}<br>%{percent}<extra></extra>",
        marker: { colors: pieslices.map((s) => s.color) },
      },
    ];

    const layout = {
      margin: { t: 20, b: 40, l: 40, r: 30 },
      showlegend: true,
      legend: { orientation: "h", y: -0.15 },
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      height: 220,
    };

    plotly_pie.react(pieRef.current, data, layout, {
      responsive: true,
      displaylogo: false,
      displayModeBar: false,
    });
  }, [autoCountingSummary, autoCountingResult]);

  useEffect(() => {
    const plotly_pie = globalThis.Plotly;
    if (!plotly_pie || !rangeRef.current) return;
    if (!rangeValue) {
      plotly_pie.purge(rangeRef.current);
      return;
    }

    const normal = rangeValue.summary?.normal?.count ?? 0;
    const lower = rangeValue.summary?.lower?.count ?? 0;
    const higher = rangeValue.summary?.higher?.count ?? 0;

    const allSlices = [
      { label: "Dalam SP", value: normal, color: "#3b82f6" },
      { label: "Lebih Rendah", value: lower, color: "#f59e0b" },
      { label: "Lebih Tinggi", value: higher, color: "#ef4444" },
    ].filter((s) => s.value > 0);

    if (!allSlices.length) {
      plotly_pie.purge(rangeRef.current);
      return;
    }

    const data = [
      {
        type: "pie",
        hole: 0.45,
        values: allSlices.map((s) => s.value),
        labels: allSlices.map((s) => s.label),
        textinfo: "label+percent",
        hovertemplate:
          "<b>%{label}</b><br>%{value} records<br>%{percent}<extra></extra>",
        marker: { colors: allSlices.map((s) => s.color) },
      },
    ];

    const layout = {
      margin: { t: 20, b: 40, l: 40, r: 30 },
      showlegend: true,
      legend: { orientation: "h", y: -0.15 },
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      height: 220,
    };

    plotly_pie.react(rangeRef.current, data, layout, {
      responsive: true,
      displaylogo: false,
      displayModeBar: false,
    });
  }, [rangeValue]);

  useEffect(() => {
    selectedFileRef.current = selectedFile;
  }, [selectedFile]);

  useEffect(() => {
    if (!message) return undefined;
    const timer = setTimeout(() => setMessage(""), 3000);
    return () => clearTimeout(timer);
  }, [message]);

  const parseApiError = useCallback(async (response) => {
    try {
      const payload = await response.json();
      return payload.detail || payload.error || JSON.stringify(payload);
    } catch {
      return `${response.status} ${response.statusText}`;
    }
  }, []);

  const callApi = useCallback(
    async (path, options) => {
      const response = await fetch(`${backendUrl}${path}`, options);
      if (!response.ok) throw new Error(await parseApiError(response));
      return response.json();
    },
    [backendUrl, parseApiError],
  );

  const normalizeFiles = useCallback((payload) => {
    if (Array.isArray(payload?.file_details)) {
      return payload.file_details.map((item) => ({
        filename: item.filename,
        displayName:
          item.display_name ?? item.original_filename ?? item.filename,
      }));
    }
    if (Array.isArray(payload?.files)) {
      return payload.files.map((name) => ({
        filename: name,
        displayName: name,
      }));
    }
    return [];
  }, []);

  const handleSelectFile = useCallback(
    async (filename) => {
      if (!filename) {
        setSelectedFile("");
        setColumns([]);
        setNumericColumns([]);
        setXColumn("");
        setYColumn([""]);
        setSizeColumn("");
        setNamesColumn("");
        setValuesColumn("");
        setZColumn("");
        setTimestampColumn("");
        setStateColumn("");
        setSummaryStatistics(null);
        setPlotFigure(null);
        setDurationResult(null);
        setAutoCountingResult(null);
        setRangeValue(null);
        setDateTime(detectionDate);
        setDatasetInfo({}); // ← reset info
        setMessage("");
        setError("");
        return;
      }

      setSelectedFile(filename);
      setSummaryStatistics(null);
      setPlotFigure(null);
      setDurationResult(null);
      setAutoCountingResult(null);
      setRangeValue(null);
      setDatasetInfo({}); // ← reset info while loading
      setMessage("");
      setError("");

      try {
        // ── Fetch columns AND metadata in parallel ──────────────────────────
        const [colPayload, infoPayload] = await Promise.all([
          callApi(`/dataset/${encodeURIComponent(filename)}/columns`),
          callApi(`/dataset/${encodeURIComponent(filename)}/info`).catch(
            () => ({ info: {} }),
          ),
        ]);
        // ───────────────────────────────────────────────────────────────────

        const allColumns = colPayload.columns ?? [];
        const numeric = colPayload.numeric_columns ?? [];

        setColumns(allColumns);
        setNumericColumns(numeric);
        setDatasetInfo(infoPayload.info ?? {}); // ← store MILL / NAME / etc.

        setXColumn((prev) =>
          allColumns.includes(prev) ? prev : (allColumns[0] ?? ""),
        );
        setYColumn((prev) => {
          const valid = prev.filter((col) => numeric.includes(col));
          if (valid.length) return valid;
          return numeric.length ? [numeric[0]] : [""];
        });
        setSizeColumn((prev) => (numeric.includes(prev) ? prev : ""));
        setNamesColumn((prev) =>
          allColumns.includes(prev) ? prev : (allColumns[0] ?? ""),
        );
        setValuesColumn((prev) =>
          numeric.includes(prev) ? prev : (numeric[0] ?? ""),
        );
        setZColumn((prev) =>
          numeric.includes(prev) ? prev : (numeric[0] ?? ""),
        );

        const timeKeywords = ["time", "timestamp"];
        const autoDetectedTime =
          allColumns.find((col) =>
            timeKeywords.some((kw) => col.toLowerCase().includes(kw)),
          ) ??
          allColumns[0] ??
          "";
        setTimestampColumn(autoDetectedTime);

        const dateKeywords = ["date", "DATE", "tanggal"];
        const detectionDate =
          allColumns.find((col) =>
            dateKeywords.some((kw) => col.toLowerCase().includes(kw)),
          ) ?? "";
        setDateTime(detectionDate);

        const stateKeywords = ["auto", "manual", "mode", "status"];
        const autoDetectedState =
          allColumns.find((col) =>
            stateKeywords.some((kw) => col.toLowerCase().includes(kw)),
          ) ??
          numeric[0] ??
          allColumns[0] ??
          "";
        setStateColumn(autoDetectedState);

        const spKeywords = ["set_point", "setpoint", "sp"];
        const pvKeywords = ["process_value", "pv", "process"];
        const autoSp =
          numeric.find((col) =>
            spKeywords.some((kw) => col.toLowerCase().includes(kw)),
          ) ??
          numeric[0] ??
          "";
        const autoPv =
          numeric.find((col) =>
            pvKeywords.some((kw) => col.toLowerCase().includes(kw)),
          ) ??
          numeric[1] ??
          numeric[0] ??
          "";
        setSpValue(autoSp);
        setPvValue(autoPv);
      } catch (err) {
        setError(`Failed to read columns: ${err.message}`);
      }
    },
    [callApi],
  );

  useEffect(() => {
    let isMounted = true;
    const loadFiles = async () => {
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
    };
    loadFiles();
    return () => {
      isMounted = false;
    };
  }, [callApi, handleSelectFile, normalizeFiles]);

  const handleUpload = async () => {
    if (!uploadFile) {
      setError("Pilih File Dengan Format CSV atau XLSX!");
      return;
    }
    const formData = new FormData();
    for (const file of uploadFile) formData.append("files", file);
    setIsUploading(true);
    setError("");
    setMessage("");
    try {
      const payload = await callApi("/upload", {
        method: "POST",
        body: formData,
      });
      const results = payload.results ?? [];
      const succeeded = results.filter((r) => r.status === "Berhasil");
      const failed = results.filter((r) => r.status === "Gagal");
      if (failed.length)
        setError(
          failed.map((r) => `${r.original_filename}: ${r.error}`).join(" | "),
        );
      if (succeeded.length)
        setMessage(
          `Uploaded: ${succeeded.map((r) => r.original_filename).join(", ")}`,
        );
      setUploadFile([]);
      const newFiles = succeeded.map((r) => ({
        filename: r.filename,
        displayName: r.original_filename ?? r.filename,
      }));
      setFiles((prev) => {
        const existing = prev.filter(
          (item) => !newFiles.some((added) => added.filename === item.filename),
        );
        return [...newFiles, ...existing];
      });
      if (newFiles.length) await handleSelectFile(newFiles[0].filename);
    } catch (err) {
      setError(`Upload failed: ${err.message}`);
    } finally {
      setIsUploading(false);
    }
  };

  const handleDeleteFile = async () => {
    if (!selectedFile) {
      setError("Select a dataset first.");
      return;
    }
    const targetFile = selectedFile;
    const confirmed = globalThis.confirm?.(`Delete ${targetFile}?`) ?? true;
    if (!confirmed) return;
    setIsDeleting(true);
    setError("");
    setMessage("");
    try {
      await callApi(`/files/${encodeURIComponent(targetFile)}`, {
        method: "DELETE",
      });
      setMessage(`Deleted: ${targetFile}`);
    } catch (err) {
      if (String(err.message).toLowerCase().includes("not found")) {
        setMessage(`Already deleted: ${targetFile}`);
      } else {
        setError(`Delete failed: ${err.message}`);
        return;
      }
    } finally {
      setIsDeleting(false);
    }
    const remaining = files.filter((item) => item.filename !== targetFile);
    setFiles(remaining);
    await handleSelectFile(remaining[0]?.filename ?? "");
  };

  const handleAnalyze = async () => {
    if (!hasDataset) {
      setError("Select a dataset first.");
      return;
    }
    setIsAnalyzing(true);
    setError("");
    try {
      const payload = await callApi("/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: selectedFile }),
      });
      setSummaryStatistics(payload.summary_statistics ?? null);
      setMessage("Summary statistics generated.");
    } catch (err) {
      setError(`Analyze failed: ${err.message}`);
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleRangeAnalysis = async () => {
    if (!hasDataset) {
      setError("Pilih Dataset terlebih dahulu.");
      return;
    }
    if (!spValue) {
      setError("Pilih kolom Set Point.");
      return;
    }
    if (!pvValue) {
      setError("Pilih kolom Process Value.");
      return;
    }
    setRangeAnalyzing(true);
    setError("");
    try {
      const payload = await callApi("/spv-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: selectedFile,
          set_point_column: spValue,
          process_value_column: pvValue,
        }),
      });
      setRangeValue(payload);
      setMessage("Range Analysis Complete.");
    } catch (err) {
      setError(`Analisis Range Gagal: ${err.message}`);
    } finally {
      setRangeAnalyzing(false);
    }
  };

  const handleAddYColumn = () => setYColumn((prev) => [...prev, ""]);
  const handleRemoveYColumn = (index) =>
    setYColumn((prev) => prev.filter((_, i) => i !== index));
  const handleChangeYColumn = (index, value) =>
    setYColumn((prev) => prev.map((col, i) => (i === index ? value : col)));

  const handlePlot = async () => {
    if (!hasDataset) {
      setError("Select a dataset first.");
      return;
    }
    if (!chartType) {
      setError("Choose a chart type first.");
      return;
    }
    const yColumnFiltered = yColumn.filter(Boolean);
    const payload = {
      filename: selectedFile,
      chart_type: chartType,
      x: xColumn || null,
      y: yColumnFiltered.length ? yColumnFiltered : null,
      size: sizeColumn || null,
      names: namesColumn || null,
      values: valuesColumn || null,
      z: zColumn || null,
    };
    setIsPlotting(true);
    setError("");
    try {
      const response = await callApi("/plot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setPlotFigure(response.figure);
      setPlotConfig(
        response.config ?? { responsive: true, displaylogo: false },
      );
      setMessage(`Rendered ${response.chart_type} chart.`);
    } catch (err) {
      setError(`Plot failed: ${err.message}`);
    } finally {
      setIsPlotting(false);
    }
  };

  const handleDuration = async () => {
    if (!hasDataset) {
      setError("Select a dataset first.");
      return;
    }
    if (!timestampColumn) {
      setError("Choose a timestamp column first.");
      return;
    }
    setIsCalculatingDuration(true);
    setError("");
    try {
      const payload = await callApi("/duration", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: selectedFile,
          timestamp_column: timestampColumn,
          date_column: dateTime || null,
        }),
      });
      setDurationResult(payload);
      setMessage("Total hour record calculated.");
    } catch (err) {
      setError(`Duration failed: ${err.message}`);
    } finally {
      setIsCalculatingDuration(false);
    }
  };

  const handleAutoCounting = async () => {
    if (!hasDataset) {
      setError("Select a dataset first.");
      return;
    }
    if (!timestampColumn) {
      setError("Choose a timestamp column first.");
      return;
    }
    if (!stateColumn) {
      setError("Choose a state column first.");
      return;
    }
    setIsAutoCounting(true);
    setError("");
    try {
      const payload = await callApi("/counting", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: selectedFile,
          timestamp_column: timestampColumn,
          state_column: stateColumn,
          date_column: dateTime || null,
        }),
      });
      setAutoCountingResult(payload);
      setMessage("Auto/Manual totals calculated.");
    } catch (err) {
      setError(`Auto/Manual counting failed: ${err.message}`);
    } finally {
      setIsAutoCounting(false);
    }
  };

  const formatNumber = (value) => {
    if (typeof value !== "number" || Number.isNaN(value)) return "-";
    return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
  };

  const datasetInfoEntries = Object.entries(datasetInfo);

  return (
    <div className="dashboard-app">
      <header className="hero">
        <div className="hero-title">
          <img className="hero-logo" src={logo} alt="Logo" />
          <h1>INTERACTIVE DASHBOARD</h1>
        </div>

        {/* ── DATASET INFO BADGES (MILL / NAME / …) ──────────────────── */}
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
        {/* ─────────────────────────────────────────────────────────────── */}
      </header>
      <main className="content-grid">
        <section className="panel data-source">
          <h2>DATA UPLOAD</h2>
          <div className="inline-actions">
            <input
              type="file"
              multiple
              accept=".csv,.xlsx"
              onChange={(event) =>
                setUploadFile(Array.from(event.target.files ?? []))
              }
            />
          </div>
          <div className="inline-actions file-actions">
            <button
              type="button"
              onClick={handleUpload}
              disabled={isUploading || !uploadFile.length}
            >
              {isUploading ? "Uploading..." : "Upload"}
            </button>
            <button
              type="button"
              className="action-button ghost"
              onClick={handleDeleteFile}
              disabled={!hasDataset || isDeleting}
            >
              {isDeleting ? "Deleting..." : "Delete"}
            </button>
          </div>
          <label className="field">
            <select
              value={selectedFile}
              onChange={(e) => handleSelectFile(e.target.value)}
              disabled={!files.length}
            >
              <option value="">(Select dataset)</option>
              {files.map((file) => (
                <option key={file.filename} value={file.filename}>
                  {file.displayName}
                </option>
              ))}
            </select>
          </label>
        </section>

        <section className="panel summary">
          <h2>SUMMARY SHEET</h2>
          {!summaryRows.length && <p className="muted"></p>}
          {!!summaryRows.length && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Mean</th>
                    <th>Median</th>
                    <th>Min</th>
                    <th>Max</th>
                    <th>Std</th>
                  </tr>
                </thead>
                <tbody>
                  {summaryRows.map((row) => (
                    <tr key={row.column}>
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

        {/* CHART SETUP */}
        <section className="panel chart-setup">
          <h2>CHART SETUP</h2>
          <div className="form-grid">
            <label className="field chart-field">
              Chart type
              <select
                className="input-compact compact-select"
                value={chartType}
                onChange={(e) => setChartType(e.target.value)}
              >
                <option value="">(None)</option>
                {CHART_OPTIONS.map((chart) => (
                  <option key={chart.value} value={chart.value}>
                    {chart.label}
                  </option>
                ))}
              </select>
            </label>

            {showXY && (
              <label className="field x-field">
                Nilai X
                <select
                  className="input-compact"
                  value={xColumn}
                  onChange={(e) => setXColumn(e.target.value)}
                >
                  <option value="">(None)</option>
                  {columns.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}

            {showXY && (
              <div className="field y-field">
                <span>Nilai Y</span>
                <div className="y-list">
                  {yColumn.map((col, index) => (
                    <div key={`y-${index}`} className="y-row">
                      <select
                        className="input-compact y-select"
                        value={col}
                        onChange={(e) =>
                          handleChangeYColumn(index, e.target.value)
                        }
                      >
                        <option value="">(None)</option>
                        {numericColumns.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </select>
                      {yColumn.length > 1 && (
                        <button
                          type="button"
                          className="y-remove"
                          onClick={() => handleRemoveYColumn(index)}
                        >
                          Hapus
                        </button>
                      )}
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className="y-add"
                  onClick={handleAddYColumn}
                >
                  Tambah Input
                </button>
              </div>
            )}

            {showHeatmap && (
              <label className="field z-field">
                Nilai Z
                <select
                  value={zColumn}
                  onChange={(e) => setZColumn(e.target.value)}
                >
                  <option value="">(None)</option>
                  {numericColumns.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          <div className="inline-actions">
            <button
              type="button"
              onClick={handleAnalyze}
              disabled={!hasDataset || isAnalyzing}
            >
              {isAnalyzing ? "Analyzing..." : "Rangkuman Analisis Data"}
            </button>
            <button
              type="button"
              onClick={handlePlot}
              disabled={!hasDataset || isPlotting}
            >
              {isPlotting ? "Rendering..." : "Buat Plot Analisis"}
            </button>
          </div>
        </section>

        {/* VISUALISASI */}
        <section className="panel output">
          {error && <p className="status error">{error}</p>}
          <h2>VISUALISASI DATA</h2>
          <div className="plot-wrapper">
            {plotFigure && message && (
              <p className="status ok plot-message">{message}</p>
            )}
            <div
              ref={plotRef}
              className={plotFigure ? "plot-canvas ready" : "plot-canvas"}
            />
            {!plotFigure && (
              <div className="empty-state">
                <p>No chart yet.</p>
                <p>Upload data, choose columns, then click Generate Plot.</p>
              </div>
            )}
          </div>
        </section>

        <div className="bottom-row">
          {/* HOUR RECORD */}
          <section className="panel hour-record">
            <h2>HOUR RECORD</h2>
            <label className="field">
              <select
                value={timestampColumn}
                onChange={(e) => {
                  setTimestampColumn(e.target.value);
                  setDurationResult(null);
                  setAutoCountingResult(null);
                }}
                disabled={!columns.length}
              >
                <option value="">(None)</option>
                {columns.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="action-button"
              onClick={handleDuration}
              disabled={!hasDataset || isCalculatingDuration}
            >
              {isCalculatingDuration ? "Calculating..." : "TOTAL HOUR RECORD"}
            </button>
            {durationResult && (
              <div className="duration-card">
                <div className="duration-row">
                  <span className="duration-label">Start</span>
                  <span className="duration-value">
                    {durationResult.start_time}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">End</span>
                  <span className="duration-value">
                    {durationResult.end_time}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Total (HH:MM:SS)</span>
                  <span className="duration-value">
                    {durationResult.total_duration?.human_readable ?? "-"}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Minutes</span>
                  <span className="duration-value">
                    {formatNumber(durationResult.total_duration?.minutes)}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Valid Records</span>
                  <span className="duration-value">
                    {formatNumber(durationResult.total_records)}
                  </span>
                </div>
              </div>
            )}
          </section>

          {/* AUTO RECORD */}
          <section className="panel auto-record">
            <h2>AUTO RECORD</h2>
            <label className="field">
              <select
                value={stateColumn}
                onChange={(e) => {
                  setStateColumn(e.target.value);
                  setAutoCountingResult(null);
                }}
                disabled={!stateColumnOptions.length}
              >
                <option value="">(None)</option>
                {stateColumnOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <div className="action-stack">
              <button
                type="button"
                className="action-button"
                onClick={handleAutoCounting}
                disabled={!hasDataset || isAutoCounting}
              >
                {isAutoCounting ? "Calculating..." : "TOTAL AUTO / MANUAL"}
              </button>
            </div>
            {autoCountingSummary && (
              <div className="duration-card">
                <div className="duration-row">
                  <span className="duration-label">Total Recorded</span>
                  <span className="duration-value">
                    {autoCountingSummary.totalRecorded?.human ?? "-"}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Auto Total (HH:MM:SS)</span>
                  <span className="duration-value">
                    {autoCountingSummary.totalAuto?.human ?? "-"}
                    {autoCountingSummary.totalAuto?.percentage != null && (
                      <span className="duration-pct">
                        {" "}
                        ({autoCountingSummary.totalAuto.percentage}%)
                      </span>
                    )}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">
                    Manual Total (HH:MM:SS)
                  </span>
                  <span className="duration-value">
                    {autoCountingSummary.totalManual?.human ?? "-"}
                    {autoCountingSummary.totalManual?.percentage != null && (
                      <span className="duration-pct">
                        {" "}
                        ({autoCountingSummary.totalManual.percentage}%)
                      </span>
                    )}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Segments</span>
                  <span className="duration-value">
                    {formatNumber(autoCountingSummary.totalSegments)}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Records</span>
                  <span className="duration-value">
                    {formatNumber(autoCountingSummary.totalRecords)}
                  </span>
                </div>
                <div
                  ref={pieRef}
                  style={{
                    width: "100%",
                    minHeight: autoCountingResult ? 220 : 0,
                  }}
                />
              </div>
            )}
          </section>

          {/* RANGE VALUE ANALYSIS */}
          <section className="panel spv-record">
            <h2>RANGE VALUE ANALYSIS</h2>
            <label className="field">
              Set Point
              <select
                value={spValue}
                onChange={(e) => {
                  setSpValue(e.target.value);
                  setRangeValue(null);
                }}
                disabled={!numericColumns.length}
              >
                <option value="">(None)</option>
                {numericColumns.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Process Value
              <select
                value={pvValue}
                onChange={(e) => {
                  setPvValue(e.target.value);
                  setRangeValue(null);
                }}
                disabled={!numericColumns.length}
              >
                <option value="">(None)</option>
                {numericColumns.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="action-button"
              onClick={handleRangeAnalysis}
              disabled={!hasDataset || rangeAnalyzing}
            >
              {rangeAnalyzing ? "Analyzing..." : "ANALISIS SP & PV"}
            </button>
            {rangeValue && (
              <div className="duration-card">
                <div className="duration-row">
                  <span className="duration-label">Total Records</span>
                  <span className="duration-value">
                    {formatNumber(rangeValue.total_records)}
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Dalam Set Point</span>
                  <span className="duration-value">
                    {formatNumber(rangeValue.summary.normal.count)}
                    <span className="duration-pct">
                      {" "}
                      ({rangeValue.summary.normal.percentage}%)
                    </span>
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Lebih Rendah dari SP</span>
                  <span className="duration-value">
                    {formatNumber(rangeValue.summary.lower.count)}
                    <span className="duration-pct">
                      {" "}
                      ({rangeValue.summary.lower.percentage}%)
                    </span>
                  </span>
                </div>
                <div className="duration-row">
                  <span className="duration-label">Lebih Tinggi dari SP</span>
                  <span className="duration-value">
                    {formatNumber(rangeValue.summary.higher.count)}
                    <span className="duration-pct">
                      {" "}
                      ({rangeValue.summary.higher.percentage}%)
                    </span>
                  </span>
                </div>
              </div>
            )}
            <div
              ref={rangeRef}
              style={{ width: "100", minHeight: rangeValue ? 220 : 0 }}
            />
          </section>
          {/* <section className="panel panel-empty" aria-hidden="false" /> */}
        </div>
      </main>
    </div>
  );
}

export default App;
