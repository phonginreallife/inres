'use client';

/**
 * Live-monitoring visualisation for the sign-in page.
 *
 * A glowing waveform with a pulse riding its leading edge.
 *
 * Everything is SVG and CSS. No JS, no timers, no state - so there is nothing
 * to leak on unmount and nothing recalculating on the main thread while
 * someone types their password. The glow is a real SVG blur rather than a
 * stack of translucent copies, which keeps it to one filter pass.
 *
 * The waveform is a fixed path, not sampled from anything. It reads as motion
 * rather than as a reading, which is the point: it carries no service name, no
 * figure and no status, so there is nothing here that could be mistaken for
 * telemetry and nothing to disclaim.
 *
 * All motion sits behind `motion-safe:`. With reduced motion the waveform
 * renders fully drawn and static and the pulse is absent - a still image
 * rather than a degraded animation.
 */

const WAVE =
  'M0 30 H44 l9-17 11 34 10-25 9 8 h46 l8-13 10 26 9-18 8 5 h48 l9-21 11 39 10-28 9 10 h40 l8-12 10 24 9-14 h36';



export const LiveMonitor = ({ className = '' }) => (
  <div className={className}>
    {/* Header */}
    <div className="mb-3 flex items-center gap-2.5">
      <span className="relative flex h-2 w-2" aria-hidden="true">
        <span className="absolute inline-flex h-full w-full rounded-full bg-accent-400 opacity-75 motion-safe:animate-ping" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-accent-400 shadow-[0_0_10px_rgba(0,229,255,0.95)]" />
      </span>
      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
        Signals monitored continuously
      </span>
    </div>

    {/* Waveform */}
    <svg
      viewBox="0 0 520 64"
      fill="none"
      className="h-[92px] w-full"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id="lm-stroke" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#1a75ff" />
          <stop offset="55%" stopColor="#4d94ff" />
          <stop offset="100%" stopColor="#00e5ff" />
        </linearGradient>
        <filter id="lm-glow" x="-10%" y="-60%" width="120%" height="220%">
          <feGaussianBlur stdDeviation="3.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Baseline: always visible, so the shape reads even before the sweep. */}
      <path d={WAVE} stroke="#1a75ff" strokeOpacity="0.14" strokeWidth="1.5"
            strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />

      {/* The drawn trace. */}
      <path
        d={WAVE}
        stroke="url(#lm-stroke)"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
        filter="url(#lm-glow)"
        strokeDasharray="520"
        strokeDashoffset="520"
        className="motion-safe:animate-trace motion-reduce:[stroke-dashoffset:0]"
      />

      {/* Leading pulse: a short bright dash on the same path. */}
      <path
        d={WAVE}
        stroke="#7cc4ff"
        strokeWidth="2.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
        filter="url(#lm-glow)"
        strokeDasharray="14 506"
        strokeDashoffset="520"
        className="opacity-0 motion-safe:animate-comet"
      />
    </svg>

  </div>
);

export default LiveMonitor;
