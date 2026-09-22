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

export default BrandMark;
