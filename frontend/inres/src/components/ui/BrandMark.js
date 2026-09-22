'use client';

/**
 * The inres mark: the bolt glyph, inside a badge that is visibly alive.
 *
 * The glyph itself is unchanged from the original. What changed is the
 * surround - a waveform that redraws on a loop and a single slow halo - so the
 * logo reads as a system that is currently watching rather than a static
 * sticker. Incident response is a live posture; the mark now says so.
 *
 * Motion is wrapped in `motion-safe:`, so anyone who has asked their OS to
 * reduce motion gets the static badge. That matters more than usual here: a
 * looping animation with no off switch is a common accessibility complaint,
 * and vestibular triggers are not hypothetical.
 */

/** The bolt. Unchanged geometry, inherits currentColor. */
export const BrandMark = ({ className = 'w-8 h-8' }) => (
  <svg
    className={className}
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    aria-hidden="true"
    focusable="false"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M13 10V3L4 14h7v7l9-11h-7z"
    />
  </svg>
);

/**
 * The badge, with the live surround.
 *
 * `live` exists so the sidebar can opt out: an animation that loops forever in
 * persistent chrome is noise, whereas on a sign-in page seen for ten seconds
 * it is a signal.
 */
export const BrandBadge = ({ size = 'md', live = false, className = '' }) => {
  const dims = {
    sm: { box: 'w-10 h-10 rounded-xl', mark: 'w-6 h-6' },
    md: { box: 'w-12 h-12 rounded-xl', mark: 'w-7 h-7' },
    lg: { box: 'w-14 h-14 rounded-2xl', mark: 'w-8 h-8' },
  }[size];

  return (
    <div className={`relative flex-shrink-0 ${className}`}>
      {live && (
        <span
          aria-hidden="true"
          className={`absolute inset-0 ${dims.box} bg-primary-500/40 motion-safe:animate-halo`}
        />
      )}
      <div
        className={`relative ${dims.box} bg-primary-500 text-white flex items-center justify-center
                    ring-1 ring-inset ring-white/15 shadow-lg shadow-primary-500/25`}
      >
        <BrandMark className={dims.mark} />
      </div>
    </div>
  );
};

/**
 * A waveform that draws itself on a loop, with a dot riding the leading edge.
 *
 * Pure SVG and CSS - no JS, no timers, nothing to leak on unmount. The trace
 * is one path drawn twice: a dim baseline so the shape is always legible, and
 * a bright copy animated via stroke-dashoffset.
 */
export const LiveSignal = ({ className = '' }) => (
  <svg
    viewBox="0 0 260 44"
    fill="none"
    className={className}
    aria-hidden="true"
    focusable="false"
    preserveAspectRatio="none"
  >
    <path
      d="M0 24h38l7-13 9 26 8-19 7 6h34l6-10 8 20 7-14 6 4h36l7-16 9 30 8-22 7 8h30l6-9 8 18 7-11h28"
      stroke="currentColor"
      strokeOpacity="0.16"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <path
      d="M0 24h38l7-13 9 26 8-19 7 6h34l6-10 8 20 7-14 6 4h36l7-16 9 30 8-22 7 8h30l6-9 8 18 7-11h28"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeDasharray="260"
      strokeDashoffset="260"
      className="motion-safe:animate-trace motion-reduce:[stroke-dashoffset:0] motion-reduce:[stroke-opacity:0.5]"
    />
  </svg>
);

/** Mark plus wordmark. `live` turns on the halo behind the badge. */
export const BrandLockup = ({ size = 'md', live = false, className = '' }) => {
  const nameSize = { sm: 'text-xl', md: 'text-2xl', lg: 'text-3xl' }[size];
  return (
    <div className={`flex items-center gap-3.5 ${className}`}>
      <BrandBadge size={size} live={live} />
      <div>
        <span className={`block ${nameSize} font-semibold tracking-tight text-white`}>
          InRes
        </span>
        <span className="block text-[11px] font-medium uppercase tracking-[0.18em] text-primary-300/80">
          Incident Response
        </span>
      </div>
    </div>
  );
};

export default BrandMark;
