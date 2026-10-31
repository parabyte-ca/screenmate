import { useLayoutEffect, useMemo, useRef, useState } from "react";

// Small, dependency-free charts for Insights. One series each, so no legend box:
// the card title names what's plotted. Hover (or keyboard focus) shows a tooltip;
// every chart can flip to a table.

export interface Datum {
  key: string;
  label: string;
  value: number;
  detail?: string;
}

/** Round axis maximum and 4 ticks (0 included). */
function niceTicks(max: number): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  return Array.from({ length: Math.ceil(max / step) + 1 }, (_, i) => i * step);
}

function Tooltip({ x, y, width, children }: { x: number; y: number; width: number; children: React.ReactNode }) {
  // Keep it inside the card: anchor left/right near the edges instead of centring.
  const shift = x < width * 0.2 ? "0%" : x > width * 0.8 ? "-100%" : "-50%";
  return (
    <div role="tooltip" style={{ left: x, top: y, transform: `translate(${shift}, -100%)` }}
         className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs text-gray-900 dark:text-gray-100 shadow-md">
      {children}
    </div>
  );
}

export function TableView({ data, valueLabel, format }: { data: Datum[]; valueLabel: string; format: (n: number) => string }) {
  return (
    <div className="max-h-64 overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-gray-500 dark:text-gray-400">
          <tr><th className="py-1 font-medium">Item</th><th className="py-1 text-right font-medium">{valueLabel}</th></tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-800 tabular-nums text-gray-700 dark:text-gray-200">
          {data.map((d) => (
            <tr key={d.key}><td className="py-1">{d.label}</td><td className="py-1 text-right">{format(d.value)}{d.detail ? ` · ${d.detail}` : ""}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Vertical columns over time (one per day). */
export function ColumnChart({ data, format, axisFormat, height = 180, ariaLabel }: {
  data: Datum[]; format: (n: number) => string; axisFormat?: (n: number) => string; height?: number; ariaLabel: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [W, setW] = useState(0);  // drawn at the real width so text stays 10px on phones; 0 until measured
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const max = Math.max(0, ...data.map((d) => d.value));
  const ticks = useMemo(() => niceTicks(max), [max]);
  const top = ticks[ticks.length - 1] || 1;
  const padL = 48, padB = 22, padT = 8;
  const plotW = Math.max(1, W - padL), plotH = height - padB - padT;
  const band = plotW / Math.max(1, data.length);
  const barW = Math.min(24, Math.max(2, band - 2));  // ≤24px, 2px surface gap between neighbours
  const y = (v: number) => padT + plotH - (v / top) * plotH;
  const labelEvery = Math.ceil(data.length / Math.max(2, Math.min(7, Math.floor(plotW / 56))));
  const fmtAxis = axisFormat ?? format;

  return (
    <div ref={wrap} className="relative min-w-0" style={{ height }}>
      {W > 0 && <svg width={W} height={height} className="block" role="img" aria-label={ariaLabel}
           onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W} y1={y(t)} y2={y(t)} stroke="var(--viz-grid)" strokeWidth={1} />
            <text x={padL - 6} y={y(t)} dy="0.32em" textAnchor="end" className="fill-gray-500 dark:fill-gray-400 text-[10px] tabular-nums">{fmtAxis(t)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = padL + i * band + (band - barW) / 2;
          const h = Math.max(0, padT + plotH - y(d.value));
          const r = Math.min(4, h, barW / 2);
          return (
            <g key={d.key}>
              {hover === i && <rect x={padL + i * band} y={padT} width={band} height={plotH} fill="var(--viz-hover)" />}
              {h > 0 && (
                <path fill="var(--viz-series-1)"
                      d={`M${x},${padT + plotH} v${-(h - r)} q0,${-r} ${r},${-r} h${barW - 2 * r} q${r},0 ${r},${r} v${h - r} z`} />
              )}
              {i % labelEvery === 0 && (
                <text x={padL + i * band + band / 2} y={height - 6} textAnchor="middle" className="fill-gray-500 dark:fill-gray-400 text-[10px]">{d.label}</text>
              )}
              {/* hit target: the whole band, taller than the mark */}
              <rect x={padL + i * band} y={padT} width={band} height={plotH + padB} fill="transparent" tabIndex={0}
                    aria-label={`${d.label}: ${format(d.value)}${d.detail ? `, ${d.detail}` : ""}`}
                    onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
            </g>
          );
        })}
      </svg>}
      {hover !== null && W > 0 && (() => {
        const d = data[hover];
        return (
          <Tooltip x={padL + hover * band + band / 2} y={y(d.value) - 6} width={W}>
            <b>{format(d.value)}</b> · {d.label}{d.detail ? ` · ${d.detail}` : ""}
          </Tooltip>
        );
      })()}
    </div>
  );
}

/** Horizontal bars for categories, value at the tip. */
export function BarList({ data, format, ariaLabel }: { data: Datum[]; format: (n: number) => string; ariaLabel: string }) {
  const [hover, setHover] = useState<string | null>(null);
  const max = Math.max(0, ...data.map((d) => d.value)) || 1;
  return (
    <ul className="space-y-2.5" aria-label={ariaLabel}>
      {data.map((d) => (
        <li key={d.key} tabIndex={0} title={`${d.label}: ${format(d.value)}${d.detail ? ` · ${d.detail}` : ""}`}
            onMouseEnter={() => setHover(d.key)} onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(d.key)} onBlur={() => setHover(null)}
            className={`grid grid-cols-[minmax(5rem,9rem)_1fr] items-center gap-3 rounded px-1 ${hover === d.key ? "bg-[var(--viz-hover)]" : ""}`}>
          <span className="truncate text-sm text-gray-700 dark:text-gray-200">{d.label}</span>
          <span className="flex items-center gap-2">
            <span className="h-4 rounded-r" style={{ width: `${Math.max(d.value > 0 ? 1 : 0, (d.value / max) * 85)}%`, background: "var(--viz-series-1)" }} />
            <span className="shrink-0 text-xs text-gray-600 dark:text-gray-300 tabular-nums">
              {format(d.value)}{hover === d.key && d.detail ? <span className="text-gray-400"> · {d.detail}</span> : null}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}
