'use client';

import { useSidebar } from '../contexts/SidebarContext';
import { usePathname } from 'next/navigation';
import { useAuth } from '../contexts/AuthContext';
import NotificationBell from './ui/NotificationBell';

// Pages that should be full-bleed (no container padding)
const FULL_BLEED_PAGES = ['/ai-agent'];

export default function MainContent({ children }) {
  const { collapsed, isMobile } = useSidebar();
  const { isAuthenticated } = useAuth();
  const pathname = usePathname();

  // No sidebar margin for auth pages, onboarding, or unauthenticated users
  const isAuthPage = pathname === '/login' || pathname === '/signup';
  const isOnboardingPage = pathname === '/onboarding';
  const isLandingPage = pathname === '/';
  const isSharedPage = pathname.startsWith('/shared/');
  const isFullBleed = FULL_BLEED_PAGES.includes(pathname) || isOnboardingPage || isLandingPage || isSharedPage;
  const showSidebarMargin = !isMobile && isAuthenticated && !isAuthPage && !isOnboardingPage && !isLandingPage && !isSharedPage;
  const showNotificationBell = isAuthenticated && !isAuthPage && !isOnboardingPage && !isLandingPage && !isSharedPage;

  return (
    <main
      className={`
        transition-all duration-300 ease-in-out
        ${showSidebarMargin
          ? collapsed
            ? 'md:ml-[72px]'
            : 'md:ml-64'
          : ''
        }
        ${isMobile && !isAuthPage ? 'pt-14' : 'pt-0'}
        ${isFullBleed ? 'h-screen' : 'min-h-screen'}
      `}
      style={{ background: 'var(--background)' }}
    >
      {/* Floating Notification Bell - Bottom Right */}
      {showNotificationBell && !isMobile && (
        <div className="fixed bottom-6 right-6 z-50">
          <NotificationBell />
        </div>
      )}

      {isFullBleed ? (
        // Full-bleed layout for chat/AI pages - scrollbar at edge
        <div className={`h-full ${isMobile ? 'h-[calc(100vh-56px)]' : 'h-screen'}`}>
          {children}
        </div>
      ) : isAuthPage ? (
        // Auth pages are full-bleed: /login and /signup each supply their own
        // min-h-screen wrapper, background and padding. Wrapping them in the
        // app container inset them by 24px horizontally and 56px at the top,
        // which let the light-theme body show through around the edges of a
        // page that is always dark. min-h-screen rather than h-screen so a
        // form taller than the viewport still scrolls.
        children
      ) : (
        // Standard container layout with Brand-style padding
        <div className="mx-auto py-8 px-6 max-w-7xl">
          {/* Content */}
          <div className="relative">
            {children}
          </div>
        </div>
      )}
    </main>
  );
}
