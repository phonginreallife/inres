'use client';

import { useState, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  SparklesIcon,
  SignalIcon,
  CalendarDaysIcon,
  PuzzlePieceIcon,
  EnvelopeIcon,
  LockClosedIcon,
  EyeIcon,
  EyeSlashIcon,
  ExclamationCircleIcon,
  ArrowPathIcon,
} from '@heroicons/react/24/outline';
import { useAuth } from '../../contexts/AuthContext';
import { BrandLockup, LiveSignal } from '../../components/ui/BrandMark';

const FEATURES = [
  { Icon: SparklesIcon, label: 'AI-powered incident analysis' },
  { Icon: SignalIcon, label: 'Real-time monitoring and alerting' },
  { Icon: CalendarDaysIcon, label: 'Intelligent on-call scheduling' },
  { Icon: PuzzlePieceIcon, label: 'Integrations with observability tools' },
];

/** Deliberately loose. Real validation is the server's job; this only catches typos. */
const looksLikeEmail = (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [formError, setFormError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const [touched, setTouched] = useState({});

  // Guards against a double submit that state alone cannot: two rapid Enter
  // presses can both read `loading === false` before React re-renders.
  const inFlight = useRef(false);

  const { signIn } = useAuth();
  const router = useRouter();

  const validate = () => {
    const errors = {};
    if (!email.trim()) errors.email = 'Enter your email address.';
    else if (!looksLikeEmail(email)) errors.email = 'Enter a valid email address.';
    if (!password) errors.password = 'Enter your password.';
    return errors;
  };

  const handleBlur = (field) => {
    setTouched((t) => ({ ...t, [field]: true }));
    setFieldErrors(validate());
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (inFlight.current) return;

    const errors = validate();
    setFieldErrors(errors);
    setTouched({ email: true, password: true });
    if (Object.keys(errors).length > 0) return;

    inFlight.current = true;
    setLoading(true);
    setFormError('');

    const { error } = await signIn(email, password);

    if (error) {
      // Deliberately generic. Distinguishing "no such account" from "wrong
      // password" tells an attacker which addresses are registered.
      setFormError('That email and password combination is not recognised.');
      setLoading(false);
      inFlight.current = false;
    } else {
      router.push('/dashboard');
    }
  };

  const showError = (field) => touched[field] && fieldErrors[field];

  return (
    <div className="relative min-h-screen min-h-dvh bg-navy-950 text-slate-200 lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] xl:grid-cols-[1.05fr_1fr]">
      {/*
        The app supports a light theme and a script sets html.light at runtime,
        but authentication is always dark. Without this layer the light body
        shows through wherever the page's own background stops - below the fold
        on short viewports, and around the edges while the theme script runs.
        Fixed rather than absolute so it covers the viewport, not the document.
      */}
      <div aria-hidden="true" className="fixed inset-0 -z-10 bg-navy-950" />
      {/* ---------------------------------------------------------------- */}
      {/* Left: product. Hidden below lg - on a phone the form is the page. */}
      {/* ---------------------------------------------------------------- */}
      <section className="relative hidden lg:flex flex-col justify-between overflow-hidden border-r border-white/[0.06] px-12 py-14 xl:px-16">
        {/* One ambient wash and a masked grid. Low contrast on purpose. */}
        <div aria-hidden="true" className="pointer-events-none absolute inset-0">
          <div className="absolute -top-40 -left-24 h-[28rem] w-[28rem] rounded-full bg-primary-500/10 blur-[120px]" />
          <div
            className="absolute inset-0 opacity-[0.07]"
            style={{
              backgroundImage:
                'linear-gradient(rgba(148,163,184,.5) 1px,transparent 1px),linear-gradient(90deg,rgba(148,163,184,.5) 1px,transparent 1px)',
              backgroundSize: '56px 56px',
              maskImage: 'radial-gradient(ellipse 70% 55% at 35% 40%,#000 30%,transparent 100%)',
              WebkitMaskImage: 'radial-gradient(ellipse 70% 55% at 35% 40%,#000 30%,transparent 100%)',
            }}
          />
        </div>

        <div className="relative">
          <BrandLockup size="lg" />
        </div>

        <div className="relative max-w-[30rem]">
          <h2 className="text-[2.5rem] xl:text-[2.75rem] font-semibold leading-[1.12] tracking-[-0.02em] text-white">
            Resolve incidents.
            <span className="block text-primary-400">Restore confidence.</span>
          </h2>
          <p className="mt-5 text-[15px] leading-relaxed text-slate-400">
            AI-powered incident analysis, real-time observability, and intelligent
            on-call management &mdash; unified in one platform.
          </p>

          {/*
            A live trace rather than a mock product view. A miniature
            service-health card invites the reader to evaluate it as a product
            screenshot - names, states, whether the numbers are real - which is
            a lot of attention spent on the half of the page that is not the
            form. This carries the same idea with far less to read.
          */}
          <div className="mt-10 max-w-md" aria-hidden="true">
            <div className="mb-2 flex items-center gap-2.5">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full rounded-full bg-accent-400 opacity-70 motion-safe:animate-ping" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent-400" />
              </span>
              <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                Signals monitored continuously
              </span>
            </div>
            <LiveSignal className="h-11 w-full text-primary-400" />
          </div>
        </div>

        <ul className="relative space-y-3.5">
          {FEATURES.map(({ Icon, label }) => (
            <li key={label} className="flex items-center gap-3 text-[14px] text-slate-400">
              <Icon className="h-[18px] w-[18px] flex-shrink-0 text-primary-400/80" strokeWidth={1.5} />
              <span>{label}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Right: the form. The only thing on this page with a job to do.   */}
      {/* ---------------------------------------------------------------- */}
      <section className="flex min-h-screen flex-col justify-center px-5 py-12 sm:px-8 lg:px-12 xl:px-16">
        <div className="mx-auto w-full max-w-[25rem]">
          {/*
            Compact hero for everything below lg. Without it, tablet widths are
            a narrow form floating in a large empty field - the split-screen
            content is gone but nothing takes its place. The supporting line is
            held back to sm so the phone layout stays form-first.
          */}
          <div className="mb-9 lg:hidden">
            <BrandLockup size="md" />
            <h2 className="mt-7 text-[1.65rem] sm:text-[1.875rem] font-semibold leading-[1.15] tracking-[-0.02em] text-white">
              Resolve incidents.
              <span className="block text-primary-400">Restore confidence.</span>
            </h2>
            <p className="mt-3 hidden text-[14px] leading-relaxed text-slate-400 sm:block">
              AI-powered incident analysis, real-time observability, and
              intelligent on-call management &mdash; unified in one platform.
            </p>
          </div>

          <header className="mb-7 border-t border-white/[0.06] pt-7 lg:border-0 lg:pt-0">
            <h1 className="text-[1.5rem] font-semibold tracking-[-0.01em] text-white">
              Sign in
            </h1>
            <p className="mt-1.5 text-[14px] text-slate-400">
              Welcome back. Enter your details to continue.
            </p>
          </header>

          <form onSubmit={handleSubmit} noValidate className="space-y-5">
            {formError && (
              <div
                role="alert"
                className="flex items-start gap-2.5 rounded-lg border border-danger-500/25 bg-danger-500/[0.07] px-3.5 py-3"
              >
                <ExclamationCircleIcon className="mt-px h-[18px] w-[18px] flex-shrink-0 text-danger-500" />
                <p className="text-[13px] leading-snug text-danger-500">{formError}</p>
              </div>
            )}

            {/* Email */}
            <div>
              <label htmlFor="email" className="mb-1.5 block text-[13px] font-medium text-slate-300">
                Email address
              </label>
              <div className="relative">
                <EnvelopeIcon
                  className="pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-slate-500"
                  strokeWidth={1.5}
                  aria-hidden="true"
                />
                <input
                  id="email"
                  name="email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  autoCapitalize="none"
                  spellCheck="false"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onBlur={() => handleBlur('email')}
                  aria-invalid={showError('email') ? 'true' : undefined}
                  aria-describedby={showError('email') ? 'email-error' : undefined}
                  placeholder="you@company.com"
                  className={`h-11 w-full rounded-lg border bg-navy-950/60 pl-11 pr-3.5 text-[14px] text-white
                              placeholder:text-slate-600 transition-colors duration-200
                              focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40
                              ${showError('email')
                                ? 'border-danger-500/60 focus-visible:border-danger-500'
                                : 'border-white/[0.09] hover:border-white/20 focus-visible:border-primary-500'}`}
                />
              </div>
              {showError('email') && (
                <p id="email-error" className="mt-1.5 text-[12.5px] text-danger-500">
                  {fieldErrors.email}
                </p>
              )}
            </div>

            {/* Password */}
            <div>
              <div className="mb-1.5 flex items-baseline justify-between gap-3">
                <label htmlFor="password" className="text-[13px] font-medium text-slate-300">
                  Password
                </label>
                <Link
                  href="/forgot-password"
                  className="rounded text-[13px] text-primary-400 transition-colors duration-200 hover:text-primary-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40"
                >
                  Forgot password?
                </Link>
              </div>
              <div className="relative">
                <LockClosedIcon
                  className="pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-slate-500"
                  strokeWidth={1.5}
                  aria-hidden="true"
                />
                <input
                  id="password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onBlur={() => handleBlur('password')}
                  aria-invalid={showError('password') ? 'true' : undefined}
                  aria-describedby={showError('password') ? 'password-error' : undefined}
                  placeholder="Enter your password"
                  className={`h-11 w-full rounded-lg border bg-navy-950/60 pl-11 pr-11 text-[14px] text-white
                              placeholder:text-slate-600 transition-colors duration-200
                              focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40
                              ${showError('password')
                                ? 'border-danger-500/60 focus-visible:border-danger-500'
                                : 'border-white/[0.09] hover:border-white/20 focus-visible:border-primary-500'}`}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  aria-pressed={showPassword}
                  className="absolute right-1.5 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 transition-colors duration-200 hover:text-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40"
                >
                  {showPassword ? (
                    <EyeSlashIcon className="h-[18px] w-[18px]" strokeWidth={1.5} />
                  ) : (
                    <EyeIcon className="h-[18px] w-[18px]" strokeWidth={1.5} />
                  )}
                </button>
              </div>
              {showError('password') && (
                <p id="password-error" className="mt-1.5 text-[12.5px] text-danger-500">
                  {fieldErrors.password}
                </p>
              )}
            </div>

            <label htmlFor="remember" className="flex w-fit cursor-pointer items-center gap-2.5">
              <input
                id="remember"
                name="remember"
                type="checkbox"
                className="h-4 w-4 rounded border-white/20 bg-navy-950 text-primary-500 transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40"
              />
              <span className="text-[13.5px] text-slate-400">Keep me signed in</span>
            </label>

            <button
              type="submit"
              disabled={loading}
              className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-primary-500 text-[14px] font-medium text-white
                         transition-colors duration-200 hover:bg-primary-400
                         focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/50 focus-visible:ring-offset-2 focus-visible:ring-offset-navy-950
                         disabled:cursor-not-allowed disabled:bg-primary-500/50 disabled:text-white/70"
            >
              {loading ? (
                <>
                  <ArrowPathIcon className="h-[18px] w-[18px] motion-safe:animate-spin" strokeWidth={2} />
                  <span>Signing in&hellip;</span>
                </>
              ) : (
                <span>Sign in</span>
              )}
            </button>
          </form>

          <p className="mt-6 text-center text-[13.5px] text-slate-400">
            Don&apos;t have an account?{' '}
            <Link
              href="/signup"
              className="rounded font-medium text-primary-400 transition-colors duration-200 hover:text-primary-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40"
            >
              Create one
            </Link>
          </p>

          <p className="mt-10 text-center text-[12px] leading-relaxed text-slate-600">
            By signing in you agree to our{' '}
            <Link href="/terms" className="rounded text-slate-500 underline underline-offset-2 transition-colors duration-200 hover:text-slate-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40">
              Terms of Service
            </Link>{' '}
            and{' '}
            <Link href="/privacy" className="rounded text-slate-500 underline underline-offset-2 transition-colors duration-200 hover:text-slate-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40">
              Privacy Policy
            </Link>
            .
          </p>
        </div>
      </section>
    </div>
  );
}
