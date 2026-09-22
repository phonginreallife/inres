'use client';

/**
 * A miniature, deliberately illustrative view of the InRes incident surface.
 *
 * Every value here is fabricated and must stay that way. It carries no uptime
 * percentage, no latency figure, no customer name and no count - anything that
 * could be mistaken for a measurement. Service names are generic, states are
 * qualitative words rather than numbers, and the sparklines are drawn from
 * fixed arrays rather than anything sampled.
 *
 * The "Illustrative" chip is load-bearing, not decoration: a product preview
 * that looks like live telemetry is a claim, and this one is not entitled to
 * make it. If anyone later wires this to real data, that chip has to change in
 * the same commit.
 */

const services = [
  { name: 'api-gateway', state: 'healthy', series: [3, 4, 3, 5, 4, 3, 4, 3, 4, 5, 4, 3] },
  { name: 'checkout-service', state: 'degraded', series: [3, 4, 6, 9, 12, 14, 11, 13, 15, 12, 14, 13] },
  { name: 'auth-service', state: 'healthy', series: [2, 3, 2, 3, 4, 3, 2, 3, 3, 2, 3, 3] },
];

const stateStyles = {
  healthy: { dot: 'bg-emerald-400', label: 'text-slate-500', text: 'Healthy' },
  degraded: { dot: 'bg-amber-400', label: 'text-amber-300/90', text: 'Degraded' },
};

/** Fixed-array sparkline. No axes or figures, so it reads as shape, not data. */
const Sparkline = ({ series, className = '' }) => {
  const max = Math.max(...series);
  const step = 56 / (series.length - 1);
  const points = series
    .map((v, i) => `${(i * step).toFixed(1)},${(18 - (v / max) * 15).toFixed(1)}`)
    .join(' ');

  return (
    <svg viewBox="0 0 56 20" fill="none" className={className} aria-hidden="true" preserveAspectRatio="none">
      <polyline
        points={points}
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
};

export const ProductPreview = ({ className = '' }) => (
  <div
    className={`rounded-xl border border-white/[0.08] bg-navy-900/50 overflow-hidden ${className}`}
    role="img"
    aria-label="Illustrative preview of the InRes service health view. Example data, not live."
  >
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.06]">
      <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-slate-500">
        Service health
      </span>
      <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-slate-500 px-1.5 py-0.5 rounded border border-white/10">
        Illustrative
      </span>
    </div>

    <ul className="divide-y divide-white/[0.04]">
      {services.map((svc) => {
        const s = stateStyles[svc.state];
        return (
          <li key={svc.name} className="flex items-center gap-3 px-4 py-2.5">
            <span className={`h-1.5 w-1.5 rounded-full flex-shrink-0 ${s.dot}`} />
            <span className="font-mono text-[12px] text-slate-300 truncate flex-1 min-w-0">
              {svc.name}
            </span>
            <Sparkline
              series={svc.series}
              className={`w-14 h-5 flex-shrink-0 ${svc.state === 'degraded' ? 'text-amber-400/70' : 'text-primary-400/50'}`}
            />
            <span className={`text-[11px] w-16 text-right flex-shrink-0 ${s.label}`}>{s.text}</span>
          </li>
        );
      })}
    </ul>

    <div className="flex items-start gap-3 px-4 py-3 border-t border-white/[0.06] bg-primary-500/[0.04]">
      <span className="mt-1 h-1.5 w-1.5 rounded-full bg-primary-400 flex-shrink-0 motion-safe:animate-pulse" />
      <div className="min-w-0">
        <p className="text-[12px] text-slate-300 leading-snug">
          Elevated error rate on{' '}
          <span className="font-mono text-slate-200">checkout-service</span>
        </p>
        <p className="text-[11px] text-slate-500 mt-0.5">
          Analysis in progress &middot; on-call engineer paged
        </p>
      </div>
    </div>
  </div>
);

export default ProductPreview;
