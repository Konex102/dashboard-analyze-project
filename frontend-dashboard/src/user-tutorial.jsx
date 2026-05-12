import { useCallback, useEffect, useRef, useState } from "react";

/* ─── Step Definitions ─────────────────────────────────────────────────────
   Each step targets an element by ID, shows a title + description,
   and specifies where the tooltip card should appear relative to the element.
   ───────────────────────────────────────────────────────────────────────── */
const STEPS = [
  {
    targetId:    "tut-data-upload",
    title:       "Upload Data",
    description: "Start here — browse and select a CSV or XLSX file, then click Upload. You can upload multiple files at once.",
    position:    "right",
    icon:        "⬆",
  },
  {
    targetId:    "tut-dataset-select",
    title:       "Select Dataset",
    description: "After uploading, pick your dataset from this dropdown. The dashboard will auto-detect all columns and numeric fields instantly.",
    position:    "right",
    icon:        "📂",
  },
  {
    targetId:    "tut-chart-setup",
    title:       "Chart Setup",
    description: "Choose a chart type (Line, Bar, Area, Scatter), set your X and Y axes, then click Generate Plot. Use '+ Add Series' for multi-line charts.",
    position:    "right",
    icon:        "📊",
  },
  {
    targetId:    "tut-data-viz",
    title:       "Data Visualisation",
    description: "Your chart renders here. Use the range slider at the bottom to zoom into time windows, or the buttons (1m / 6m / YTD) for quick navigation.",
    position:    "left",
    icon:        "📈",
  },
  {
    targetId:    "tut-hour-record",
    title:       "Hour Record",
    description: "Select a timestamp column and click Total Hour Record to calculate total elapsed machine time — start time, end time, and total HH:MM:SS.",
    position:    "top",
    icon:        "⏱",
  },
  {
    targetId:    "tut-auto-record",
    title:       "Auto / Manual Record",
    description: "Select the state column (1 = Auto, 0 = Manual) to break down total time into Auto and Manual segments, with a donut chart summary.",
    position:    "top",
    icon:        "🔄",
  },
  {
    targetId:    "tut-range-analysis",
    title:       "Range Analysis (SP vs PV)",
    description: "Pick a Set Point and a Process Value column to analyse how often the process stays within target — broken into Within SP, Below, and Above.",
    position:    "top",
    icon:        "🎯",
  },
  {
    targetId:    "tut-pdf-report",
    title:       "PDF Report",
    description: "All your analysis is compiled into a professional PDF. The report auto-queues whenever you change columns. Click Download PDF when ready.",
    position:    "top",
    icon:        "📄",
  },
];

/* ─── Tooltip Positioning ──────────────────────────────────────────────── */
function getTooltipStyle(rect, position, tooltipW = 300, tooltipH = 180) {
  if (!rect) return { top: "50%", left: "50%", transform: "translate(-50%,-50%)" };

  const GAP  = 18;
  const vw   = window.innerWidth;
  const vh   = window.innerHeight;

  let top, left;

  switch (position) {
    case "right":
      top  = rect.top + rect.height / 2 - tooltipH / 2;
      left = rect.right + GAP;
      if (left + tooltipW > vw - 10) { left = rect.left - tooltipW - GAP; }
      break;
    case "left":
      top  = rect.top + rect.height / 2 - tooltipH / 2;
      left = rect.left - tooltipW - GAP;
      if (left < 10) { left = rect.right + GAP; }
      break;
    case "top":
      top  = rect.top - tooltipH - GAP;
      left = rect.left + rect.width / 2 - tooltipW / 2;
      if (top < 10) { top = rect.bottom + GAP; }
      break;
    case "bottom":
    default:
      top  = rect.bottom + GAP;
      left = rect.left + rect.width / 2 - tooltipW / 2;
      break;
  }

  // Clamp within viewport
  top  = Math.max(10, Math.min(top,  vh - tooltipH - 10));
  left = Math.max(10, Math.min(left, vw - tooltipW - 10));

  return { top, left };
}

/* ─── Spotlight rect (the highlighted region) ──────────────────────────── */
function getSpotlightClip(rect) {
  if (!rect) return "none";
  const PAD = 8;
  const t   = Math.max(0, rect.top    - PAD);
  const l   = Math.max(0, rect.left   - PAD);
  const b   = rect.bottom + PAD;
  const r   = rect.right  + PAD;
  return `polygon(
    0% 0%, 100% 0%, 100% 100%, 0% 100%,
    0% ${t}px, ${l}px ${t}px, ${l}px ${b}px, ${r}px ${b}px,
    ${r}px ${t}px, 0% ${t}px, 0% 100%
  )`;
}

/* ─── Tutorial Component ────────────────────────────────────────────────── */
export default function Tutorial({ onClose }) {
  const [step,       setStep]       = useState(0);
  const [rect,       setRect]       = useState(null);
  const [visible,    setVisible]    = useState(false);
  const tooltipRef                  = useRef(null);

  const current = STEPS[step];

  /* Measure target element and scroll it into view */
  const measureTarget = useCallback((stepIndex) => {
    const id  = STEPS[stepIndex].targetId;
    const el  = document.getElementById(id);
    if (!el) { setRect(null); return; }

    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    // Wait for scroll to settle before measuring
    setTimeout(() => {
      const r = el.getBoundingClientRect();
      setRect(r);
      setVisible(true);
    }, 380);
  }, []);

  useEffect(() => {
    setVisible(false);
    measureTarget(step);
  }, [step, measureTarget]);

  /* Recalculate on resize */
  useEffect(() => {
    const onResize = () => measureTarget(step);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [step, measureTarget]);

  const goNext  = () => { if (step < STEPS.length - 1) { setVisible(false); setTimeout(() => setStep((s) => s + 1), 180); } };
  const goPrev  = () => { if (step > 0)                { setVisible(false); setTimeout(() => setStep((s) => s - 1), 180); } };
  const goClose = () => { setVisible(false); setTimeout(onClose, 200); };

  const tooltipStyle  = getTooltipStyle(rect, current.position);
  const spotlightClip = getSpotlightClip(rect);

  return (
    <>
      {/* ── Dark backdrop with spotlight cutout ── */}
      <div
        className="tut-backdrop"
        style={{ clipPath: spotlightClip }}
        onClick={goClose}
      />

      {/* ── Highlight ring around target element ── */}
      {rect && (
        <div
          className="tut-highlight-ring"
          style={{
            top:    rect.top    - 8,
            left:   rect.left   - 8,
            width:  rect.width  + 16,
            height: rect.height + 16,
          }}
        />
      )}

      {/* ── Tooltip card ── */}
      <div
        ref={tooltipRef}
        className={`tut-card${visible ? " tut-card--visible" : ""}`}
        style={{ top: tooltipStyle.top, left: tooltipStyle.left }}
      >
        {/* Progress dots */}
        <div className="tut-dots">
          {STEPS.map((_, i) => (
            <button
              key={i}
              className={`tut-dot${i === step ? " tut-dot--active" : ""}`}
              onClick={() => { setVisible(false); setTimeout(() => setStep(i), 180); }}
              aria-label={`Go to step ${i + 1}`}
            />
          ))}
        </div>

        {/* Step counter badge */}
        <div className="tut-badge">
          <span className="tut-badge-icon">{current.icon}</span>
          <span className="tut-badge-count">STEP {step + 1} / {STEPS.length}</span>
        </div>

        <h3 className="tut-title">{current.title}</h3>
        <p  className="tut-desc">{current.description}</p>

        {/* Navigation */}
        <div className="tut-nav">
          <button
            className="tut-btn tut-btn--ghost"
            onClick={goPrev}
            disabled={step === 0}
          >
            ← Back
          </button>

          <button className="tut-btn tut-btn--ghost tut-btn--close" onClick={goClose}>
            ✕ Close
          </button>

          {step < STEPS.length - 1 ? (
            <button className="tut-btn tut-btn--primary" onClick={goNext}>
              Next →
            </button>
          ) : (
            <button className="tut-btn tut-btn--primary tut-btn--finish" onClick={goClose}>
              ✓ Finish
            </button>
          )}
        </div>
      </div>
    </>
  );
}
