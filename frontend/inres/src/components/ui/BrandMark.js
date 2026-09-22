'use client';

/**
 * The InRes mark: the original lightning glyph, unchanged.
 *
 * Only the surround is designed here - a flat primary badge with an inset
 * hairline instead of the previous gradient-plus-blur stack. Two reasons: a
 * blurred duplicate behind a 40px element is a paint cost for something nobody
 * consciously sees, and a gradient mark is the first thing to look dated.
 *
 * The earlier animated halo is gone. Motion on a logo is decoration, and on a
 * page whose job is to get someone signed in it competes with the form.
 */

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

const badgeSizes = {
  sm: { box: 'w-9 h-9 rounded-[10px]', mark: 'w-[18px] h-[18px]' },
  md: { box: 'w-10 h-10 rounded-[11px]', mark: 'w-5 h-5' },
  lg: { box: 'w-11 h-11 rounded-xl', mark: 'w-[22px] h-[22px]' },
};

export const BrandBadge = ({ size = 'md', className = '' }) => {
  const d = badgeSizes[size];
  return (
    <div
      className={`${d.box} flex-shrink-0 bg-primary-500 text-white flex items-center justify-center
                  ring-1 ring-inset ring-white/20 ${className}`}
    >
      <BrandMark className={d.mark} />
    </div>
  );
};

/**
 * Mark plus wordmark.
 *
 * The descriptor sits on its own line at a smaller size with wide tracking, so
 * "InRes" reads as the name and "Incident Response" as the category rather
 * than the two competing at similar weight.
 */
export const BrandLockup = ({ size = 'md', className = '' }) => {
  const nameSize = { sm: 'text-[15px]', md: 'text-[17px]', lg: 'text-lg' }[size];
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <BrandBadge size={size} />
      <div className="leading-none">
        <div className={`${nameSize} font-semibold tracking-tight text-white`}>InRes</div>
        <div className="mt-1 text-[10px] font-medium uppercase tracking-[0.2em] text-slate-500">
          Incident Response
        </div>
      </div>
    </div>
  );
};

/**
 * A waveform that draws itself on a loop.
 *
 * Pure SVG and CSS - no JS, no timers, nothing to leak on unmount. The trace is
 * one path drawn twice: a dim baseline so the shape is always legible, and a
 * bright copy animated via stroke-dashoffset. `motion-safe:` means anyone who
 * has asked their OS to reduce motion gets the fully drawn, static shape.
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

export default BrandMark;
