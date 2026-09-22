'use client';

import { useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { BrandLockup, BrandMark } from '../../components/ui/BrandMark';

/**
 * Feature icons.
 *
 * These were emoji. Emoji render from the viewer's system font, so the same
 * four glyphs looked different on every platform and carried a colour palette
 * nobody chose - which is most of what made this page read as unfinished.
 * Stroked SVG on a 24 grid inherits the type colour and stays consistent.
 */
const featureIcons = {
  analysis: (
    <>
      <path d="M12 3v2.2M12 18.8V21M21 12h-2.2M5.2 12H3M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6M18.4 18.4l-1.6-1.6M7.2 7.2 5.6 5.6" />
      <circle cx="12" cy="12" r="4.2" />
    </>
  ),
  monitoring: (
    <>
      <path d="M3 12h3.5l2.2-5.4 3.4 10.2 2.3-6 1.6 3.2H21" />
    </>
  ),
  scheduling: (
    <>
      <rect x="3.2" y="5" width="17.6" height="16" rx="2.4" />
      <path d="M3.2 10h17.6M8.5 3v4M15.5 3v4" />
    </>
  ),
  integrations: (
    <>
      <path d="M10.5 13.5a4 4 0 0 0 5.7 0l2.6-2.6a4 4 0 1 0-5.7-5.7l-1.3 1.3" />
      <path d="M13.5 10.5a4 4 0 0 0-5.7 0l-2.6 2.6a4 4 0 1 0 5.7 5.7l1.3-1.3" />
    </>
  ),
};

const features = [
  { key: 'analysis', title: 'AI-powered incident analysis' },
  { key: 'monitoring', title: 'Real-time monitoring & alerts' },
  { key: 'scheduling', title: 'Smart on-call scheduling' },
  { key: 'integrations', title: 'Integrations with 50+ tools' },
];

const FeatureIcon = ({ name }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="w-5 h-5"
    aria-hidden="true"
  >
    {featureIcons[name]}
  </svg>
);

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showPassword, setShowPassword] = useState(false);

  const { signIn } = useAuth();
  const router = useRouter();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    const { error } = await signIn(email, password);

    if (error) {
      setError(error.message);
      setLoading(false);
    } else {
      router.push('/dashboard');
    }
  };

  return (
    <div className="min-h-screen flex bg-navy-950 relative overflow-hidden">
      {/*
        Background. Two soft pools of light and a grid, all within the blue
        range - the previous version leaned on a second hue that fought the
        brand. Kept low-contrast on purpose: this is the backdrop to a form,
        not the subject.
      */}
      <div className="absolute inset-0 overflow-hidden" aria-hidden="true">
        <div className="absolute -top-48 -left-32 w-[32rem] h-[32rem] bg-primary-500/15 rounded-full blur-[140px]" />
        <div className="absolute -bottom-52 right-[-8rem] w-[34rem] h-[34rem] bg-accent-500/10 rounded-full blur-[150px]" />
        <div
          className="absolute inset-0 opacity-[0.12]"
          style={{
            backgroundImage:
              'linear-gradient(rgba(96,165,250,0.35) 1px, transparent 1px), linear-gradient(90deg, rgba(96,165,250,0.35) 1px, transparent 1px)',
            backgroundSize: '64px 64px',
            maskImage: 'radial-gradient(ellipse 80% 60% at 50% 40%, #000 40%, transparent 100%)',
            WebkitMaskImage: 'radial-gradient(ellipse 80% 60% at 50% 40%, #000 40%, transparent 100%)',
          }}
        />
      </div>

      {/* Left - brand and value proposition */}
      <div className="hidden lg:flex lg:w-[52%] relative z-10 flex-col justify-center px-16 xl:px-24">
        <div className="max-w-lg">
          <BrandLockup size="lg" className="mb-14" />

          <h2 className="text-[2.75rem] leading-[1.1] font-semibold tracking-tight text-white mb-5">
            Intelligent incident
            <span className="block text-primary-400">response</span>
          </h2>
          <p className="text-lg leading-relaxed text-slate-400 mb-12 max-w-md">
            AI-powered on-call management, incident response, and monitoring -
            all in one platform.
          </p>

          <ul className="space-y-3">
            {features.map((feature) => (
              <li key={feature.key} className="flex items-center gap-4">
                <span
                  className="flex-shrink-0 w-10 h-10 rounded-lg bg-primary-500/10 text-primary-300
                             ring-1 ring-inset ring-primary-400/20 flex items-center justify-center"
                >
                  <FeatureIcon name={feature.key} />
                </span>
                <span className="text-[15px] text-slate-300">{feature.title}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Right - sign-in */}
      <div className="flex-1 flex items-center justify-center px-6 py-12 lg:px-12 relative z-10">
        <div className="w-full max-w-[26rem]">
          <div className="lg:hidden flex justify-center mb-10">
            <BrandLockup size="md" />
          </div>

          <div className="bg-navy-900/70 backdrop-blur-xl rounded-2xl border border-white/[0.07] p-8 sm:p-10 shadow-2xl shadow-navy-950/60">
            <div className="mb-8">
              <h1 className="text-[1.4rem] font-semibold tracking-tight text-white mb-1.5">
                Sign in
              </h1>
              <p className="text-sm text-slate-400">
                Use your work account to continue.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-5" noValidate>
              {/*
                role="alert" so screen readers announce the failure. Without it
                a sighted user sees the message and everyone else just sees the
                form fail to submit.
              */}
              {error && (
                <div
                  role="alert"
                  className="bg-danger-500/10 border border-danger-500/25 rounded-xl px-4 py-3 flex items-start gap-3"
                >
                  <svg
                    className="w-[18px] h-[18px] text-danger-500 flex-shrink-0 mt-0.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    viewBox="0 0 24 24"
                    aria-hidden="true"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <p className="text-sm text-danger-500 leading-snug">{error}</p>
                </div>
              )}

              <div>
                <label htmlFor="email" className="block text-[13px] font-medium text-slate-300 mb-2">
                  Email address
                </label>
                <div className="relative">
                  <span className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-500">
                    <svg className="w-[18px] h-[18px]" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24" aria-hidden="true">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4 5h16a1 1 0 011 1v12a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1z" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="m3.5 6.5 8.5 6 8.5-6" />
                    </svg>
                  </span>
                  <input
                    id="email"
                    name="email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="input-brand pl-11"
                    placeholder="you@company.com"
                  />
                </div>
              </div>

              <div>
                <div className="flex items-baseline justify-between mb-2">
                  <label htmlFor="password" className="block text-[13px] font-medium text-slate-300">
                    Password
                  </label>
                  <Link
                    href="/forgot-password"
                    className="text-[13px] text-primary-400 hover:text-primary-300 transition-colors"
                  >
                    Forgot password?
                  </Link>
                </div>
                <div className="relative">
                  <span className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-500">
                    <svg className="w-[18px] h-[18px]" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24" aria-hidden="true">
                      <rect x="4" y="10.5" width="16" height="10" rx="2" />
                      <path strokeLinecap="round" d="M8 10.5V7a4 4 0 118 0v3.5" />
                    </svg>
                  </span>
                  <input
                    id="password"
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="input-brand pl-11 pr-11"
                    placeholder="Enter your password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    aria-pressed={showPassword}
                    className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-slate-500 hover:text-slate-300 transition-colors rounded-r-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/60"
                  >
                    {showPassword ? (
                      <svg className="w-[18px] h-[18px]" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24" aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.878 9.878L3 3m6.878 6.878L21 21" />
                      </svg>
                    ) : (
                      <svg className="w-[18px] h-[18px]" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24" aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.543 7-1.275 4.057-5.065 7-9.543 7-4.477 0-8.268-2.943-9.542-7z" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              {/* id/htmlFor so the label is actually bound to the control. */}
              <label htmlFor="remember" className="flex items-center gap-2.5 cursor-pointer w-fit">
                <input
                  id="remember"
                  name="remember"
                  type="checkbox"
                  className="w-4 h-4 rounded border-navy-600 bg-navy-950 text-primary-500 focus:ring-2 focus:ring-primary-500/50 focus:ring-offset-0"
                />
                <span className="text-sm text-slate-400">Keep me signed in</span>
              </label>

              <button
                type="submit"
                disabled={loading}
                className="btn-brand w-full flex items-center justify-center gap-2 py-3 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <>
                    <svg className="animate-spin w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    <span>Signing in...</span>
                  </>
                ) : (
                  <span>Sign in</span>
                )}
              </button>
            </form>

            <p className="mt-7 text-center text-sm text-slate-400">
              Don&apos;t have an account?{' '}
              <Link href="/signup" className="text-primary-400 hover:text-primary-300 font-medium transition-colors">
                Create one
              </Link>
            </p>
          </div>

          <p className="mt-8 text-center text-xs leading-relaxed text-slate-600">
            By signing in, you agree to our{' '}
            <Link href="/terms" className="text-slate-500 hover:text-slate-400 underline underline-offset-2">
              Terms of Service
            </Link>{' '}
            and{' '}
            <Link href="/privacy" className="text-slate-500 hover:text-slate-400 underline underline-offset-2">
              Privacy Policy
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
