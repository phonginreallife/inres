'use client';

import { useAuth } from '../../contexts/AuthContext';
import { useRouter, usePathname } from 'next/navigation';
import { useEffect, useState, useRef } from 'react';
import { apiClient } from '../../lib/api';

const PUBLIC_ROUTES = ['/login', '/signup', '/auth/callback', '/', '/onboarding', '/shared'];

export default function AuthWrapper({ children }) {
  const { user, session, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [checkingOnboarding, setCheckingOnboarding] = useState(false);
  const [onboardingChecked, setOnboardingChecked] = useState(false);

  // Refs to prevent redundant checks
  const lastCheckedUserIdRef = useRef(null);
  const isCheckingRef = useRef(false);

  const isPublicRoute = PUBLIC_ROUTES.includes(pathname) || pathname.startsWith('/shared/');
  const isOnboardingPage = pathname === '/onboarding';

  useEffect(() => {
    const checkOnboardingStatus = async () => {
      // Skip if already checked for this user, on onboarding page, or no session
      if (isOnboardingPage || !session?.access_token || !user?.id) {
        return;
      }

      // Skip if already checked for this user (prevents re-check on session refresh)
      if (lastCheckedUserIdRef.current === user.id) {
        return;
      }

      // Prevent concurrent checks
      if (isCheckingRef.current) {
        return;
      }

      try {
        isCheckingRef.current = true;
        setCheckingOnboarding(true);
        apiClient.setToken(session.access_token);
        
        // Add timeout to prevent hanging
        const timeoutPromise = new Promise((_, reject) => 
          setTimeout(() => reject(new Error('Timeout')), 5000)
        );
        const data = await Promise.race([
          apiClient.getOrganizations(),
          timeoutPromise
        ]);
        const orgs = Array.isArray(data) ? data : (data?.organizations || []);

        // Mark as checked for this user
        lastCheckedUserIdRef.current = user.id;
        setOnboardingChecked(true);

        if (orgs.length === 0) {
          // No organizations - redirect to onboarding
          router.push('/onboarding');
        }
      } catch (err) {
        console.log('Onboarding check failed:', err.message);
        
        // Check if it's an auth error (401) - redirect to login
        if (err.message?.includes('401') || err.message?.includes('Unauthorized') || err.message?.includes('token')) {
          console.log('Auth error detected, redirecting to login');
          localStorage.removeItem('inres-auth-token');
          router.push('/login');
        } else if (err.message === 'Timeout') {
          // Timeout - mark as checked and let user continue (API might be down)
          console.log('API timeout, allowing user to proceed');
          lastCheckedUserIdRef.current = user.id;
          setOnboardingChecked(true);
        } else {
          // Other errors - assume no orgs, redirect to onboarding
          router.push('/onboarding');
        }
      } finally {
        isCheckingRef.current = false;
        setCheckingOnboarding(false);
      }
    };

    if (!loading) {
      if (!user && !isPublicRoute) {
        // Redirect to login if not authenticated and not on public route
        router.push('/login');
      } else if (user && (pathname === '/login' || pathname === '/signup')) {
        // Redirect to dashboard if authenticated and on auth pages
        router.push('/dashboard');
      } else if (user && !isOnboardingPage && lastCheckedUserIdRef.current !== user.id) {
        // Check if user needs onboarding (no organizations)
        // Only check if we haven't checked for this specific user yet
        checkOnboardingStatus();
      }
    }
  }, [user?.id, session?.access_token, loading, pathname, router, isPublicRoute, isOnboardingPage]);

  // Show loading spinner while checking authentication or onboarding status.
  //
  // Public routes are excluded deliberately. AuthContext.signIn flips this same
  // global `loading` flag for the duration of the request, so on /login this
  // gate swapped the whole tree for a spinner mid-submit. That unmounted the
  // sign-in form, destroyed its local error state, and remounted it blank - so
  // a failed sign-in showed the user nothing at all. Public routes own their
  // own loading UI; the sign-in button already has one.
  if ((loading || checkingOnboarding) && !isPublicRoute) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="text-center">
          <div className="w-16 h-16 border-4 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-gray-600 dark:text-gray-400">
            {checkingOnboarding ? 'Setting up your workspace...' : 'Loading...'}
          </p>
        </div>
      </div>
    );
  }

  // Show content if on public route or authenticated
  if (isPublicRoute || user) {
    return children;
  }

  // This shouldn't be reached due to the redirect in useEffect
  return null;
}
