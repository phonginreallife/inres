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
  md: { box: 'w-11 h-11 rounded-xl', mark: 'w-[22px] h-[22px]' },
  lg: { box: 'w-[52px] h-[52px] rounded-[14px]', mark: 'w-7 h-7' },
};

/**
 * The badge.
 *
 * `glow` adds a blurred colour field behind it. That layer animates opacity
 * only - scaling or blurring on a keyframe forces a repaint every frame, and
 * at this size nobody would see the difference anyway.
 */
export const BrandBadge = ({ size = 'md', glow = false, className = '' }) => {
  const d = badgeSizes[size];
  return (
    <div className={`relative flex-shrink-0 ${className}`}>
      {glow && (
        <div
          aria-hidden="true"
          className={`absolute -inset-2.5 ${d.box} bg-primary-500/45 blur-xl motion-safe:animate-breathe`}
        />
      )}
      <div
        className={`relative ${d.box} flex items-center justify-center text-white
                    bg-gradient-to-br from-primary-400 via-primary-500 to-primary-600
                    ring-1 ring-inset ring-white/25
                    shadow-[0_0_0_1px_rgba(26,117,255,0.25),0_8px_24px_-6px_rgba(26,117,255,0.65)]`}
      >
        <BrandMark className={d.mark} />
      </div>
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
export const BrandLockup = ({ size = 'md', glow = false, className = '' }) => {
  const nameSize = { sm: 'text-[16px]', md: 'text-[19px]', lg: 'text-[22px]' }[size];
  return (
    <div className={`flex items-center gap-3.5 ${className}`}>
      <BrandBadge size={size} glow={glow} />
      <div className="leading-none">
        <div
          className={`${nameSize} font-semibold tracking-tight text-white
                      [text-shadow:0_0_18px_rgba(26,117,255,0.45)]`}
        >
          InRes
        </div>
        <div className="mt-1.5 text-[10px] font-semibold uppercase tracking-[0.22em] text-accent-400">
          Incident Response
        </div>
      </div>
    </div>
  );
};

export default BrandMark;
