'use client';

/**
 * The inres mark.
 *
 * A shield carrying a pulse trace: the shield is the response, the trace is
 * the signal that triggered it. Both are drawn as strokes on a single
 * currentColor so the mark inherits whatever it sits on and needs no separate
 * light and dark variants.
 *
 * Replaces the previous Heroicons "bolt" glyph, which is the most heavily
 * reused icon in the set and identified the product as nothing in particular.
 *
 * Geometry is on a 32x32 grid with a 2px stroke. It stays legible down to
 * 16px; below that use BrandMarkCompact, which drops the pulse detail rather
 * than letting it collapse into noise.
 */
export const BrandMark = ({ className = 'w-8 h-8' }) => (
  <svg
    viewBox="0 0 32 32"
    fill="none"
    className={className}
    aria-hidden="true"
    focusable="false"
  >
    <path
      d="M16 2.75 27 7.4v8.05c0 6.55-4.72 11.85-11 13.8-6.28-1.95-11-7.25-11-13.8V7.4Z"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinejoin="round"
    />
    <path
      d="M9.8 16.4h3.05l2.05-4.5 2.9 8.3 1.75-3.8h2.65"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

/** Favicon-scale variant: shield only, heavier stroke, no interior detail. */
export const BrandMarkCompact = ({ className = 'w-4 h-4' }) => (
  <svg
    viewBox="0 0 32 32"
    fill="none"
    className={className}
    aria-hidden="true"
    focusable="false"
  >
    <path
      d="M16 2.75 27 7.4v8.05c0 6.55-4.72 11.85-11 13.8-6.28-1.95-11-7.25-11-13.8V7.4Z"
      stroke="currentColor"
      strokeWidth="2.75"
      strokeLinejoin="round"
    />
    <path
      d="M11 16.6h3.2l1.9-3.9 2.6 7"
      stroke="currentColor"
      strokeWidth="2.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

/** Mark plus wordmark. `stacked` puts the eyebrow under the name. */
export const BrandLockup = ({ size = 'md', stacked = true, className = '' }) => {
  const dims = {
    sm: { box: 'w-10 h-10 rounded-xl', mark: 'w-6 h-6', name: 'text-xl' },
    md: { box: 'w-12 h-12 rounded-xl', mark: 'w-7 h-7', name: 'text-2xl' },
    lg: { box: 'w-14 h-14 rounded-2xl', mark: 'w-8 h-8', name: 'text-3xl' },
  }[size];

  return (
    <div className={`flex items-center gap-3.5 ${className}`}>
      <div
        className={`${dims.box} bg-primary-500 text-white flex items-center justify-center
                    ring-1 ring-inset ring-white/15 shadow-lg shadow-primary-500/25`}
      >
        <BrandMark className={dims.mark} />
      </div>
      <div className={stacked ? '' : 'flex items-baseline gap-2'}>
        <span className={`block ${dims.name} font-semibold tracking-tight text-white`}>
          InRes
        </span>
        {stacked && (
          <span className="block text-[11px] font-medium uppercase tracking-[0.18em] text-primary-300/80">
            Incident Response
          </span>
        )}
      </div>
    </div>
  );
};

export default BrandMark;
