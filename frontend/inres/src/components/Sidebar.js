'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '../contexts/AuthContext';
import { useSidebar } from '../contexts/SidebarContext';
import OrgSwitcher from './OrgSwitcher';
import ProjectSwitcher from './ProjectSwitcher';
import ThemeToggle from './ThemeToggle';

const NAV_ITEMS = [
  {
    href: '/dashboard',
    label: 'Dashboard',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
      </svg>
    ),
  },
  {
    href: '/ai-agent',
    label: 'AI Assistant',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    ),
    badge: 'AI',
  },
  {
    href: '/incidents',
    label: 'Incidents',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    ),
  },
  {
    href: '/monitors',
    label: 'Monitors',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
    ),
  },
  {
    href: '/groups',
    label: 'On-Call',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    href: '/releases',
    label: 'Releases',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
      </svg>
    ),
  },
  {
    href: '/integrations',
    label: 'Integrations',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z" />
      </svg>
    ),
    children: [
      {
        href: '/integrations/webhooks',
        label: 'Webhooks',
        icon: (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        ),
      },
      {
        href: '/agent-config',
        label: 'Plugins',
        icon: (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
          </svg>
        ),
      },
    ],
  },
];

const ADMIN_NAV_ITEMS = [
  {
    href: '/organizations',
    label: 'Organizations',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
      </svg>
    ),
  },
  {
    href: '/projects',
    label: 'Projects',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
      </svg>
    ),
  },
  {
    href: '/audit',
    label: 'Audit Logs',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
      </svg>
    ),
  },
];

export default function Sidebar() {
  const { collapsed, setCollapsed, isMobile } = useSidebar();
  const pathname = usePathname();
  const { user, signOut, isAuthenticated } = useAuth();
  const [expandedMenus, setExpandedMenus] = useState({});

  const toggleSubmenu = (label) => {
    setExpandedMenus(prev => ({
      ...prev,
      [label]: !prev[label]
    }));
  };

  const toggleCollapsed = () => {
    setCollapsed(!collapsed);
  };

  // Don't show on auth pages or onboarding
  if (pathname === '/login' || pathname === '/signup' || pathname === '/onboarding' || pathname === '/' || pathname.startsWith('/shared/')) {
    return null;
  }

  // Mobile: don't render sidebar (use MobileNav instead)  
  if (isMobile) {
    return null;
  }

  if (!isAuthenticated) {
    return null;
  }

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-30 flex flex-col transition-all duration-300 ease-in-out
        ${collapsed ? 'w-[72px]' : 'w-64'}
        bg-gradient-to-b from-navy-800 to-navy-900
        border-r border-navy-700/50
      `}
    >
      {/* Logo Header */}
      <div className={`flex items-center h-16 px-4 border-b border-navy-700/50 ${collapsed ? 'justify-center' : 'justify-between'}`}>
        <Link href="/dashboard" className="flex items-center gap-3">
          {/* Brand-style logo */}
          <div className="relative">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center shadow-glow">
              <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            {/* Glow effect */}
            <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 blur-lg opacity-30" />
          </div>
          {!collapsed && (
            <div className="flex flex-col">
              <span className="text-lg font-bold text-white tracking-tight">InRes</span>
              <span className="text-[10px] text-accent-400 uppercase tracking-widest">Incident Response</span>
            </div>
          )}
        </Link>

        {/* Collapse Toggle */}
        {!collapsed && (
          <button
            onClick={toggleCollapsed}
            className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-navy-700/50 transition-all"
            title="Collapse sidebar"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          </button>
        )}
      </div>

      {/* Organization & Project Switchers */}
      <div className="border-b border-navy-700/50">
        <OrgSwitcher collapsed={collapsed} />
        <ProjectSwitcher collapsed={collapsed} />
      </div>

      {/* Main Navigation */}
      <nav className="flex-1 overflow-y-auto py-4 px-3">
        {/* Main Items */}
        <div className="space-y-1">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
            const hasChildren = item.children && item.children.length > 0;
            const isExpanded = expandedMenus[item.label] || isActive;
            
            // If item has children, render as expandable menu
            if (hasChildren) {
              return (
                <div key={item.href}>
                  <button
                    onClick={() => toggleSubmenu(item.label)}
                    className={`
                      w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                      transition-all duration-200 group relative
                      ${isActive
                        ? 'bg-gradient-to-r from-primary-500/20 to-transparent text-white'
                        : 'text-gray-400 hover:text-white hover:bg-navy-700/50'
                      }
                      ${collapsed ? 'justify-center px-2' : ''}
                    `}
                    title={collapsed ? item.label : undefined}
                  >
                    <span className={`flex-shrink-0 transition-colors ${isActive ? 'text-primary-400' : 'text-gray-500 group-hover:text-primary-400'}`}>
                      {item.icon}
                    </span>
                    {!collapsed && (
                      <>
                        <span className="flex-1 text-left">{item.label}</span>
                        <svg 
                          className={`w-4 h-4 transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`} 
                          fill="none" 
                          stroke="currentColor" 
                          viewBox="0 0 24 24"
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                      </>
                    )}
                  </button>
                  
                  {/* Submenu */}
                  {!collapsed && isExpanded && (
                    <div className="mt-1 ml-4 pl-4 border-l border-navy-700/50 space-y-1">
                      {item.children.map((child) => {
                        const isChildActive = pathname === child.href || pathname.startsWith(child.href + '/');
                        return (
                          <Link
                            key={child.href}
                            href={child.href}
                            className={`
                              flex items-center gap-2 px-3 py-2 rounded-lg text-sm
                              transition-all duration-200 group
                              ${isChildActive
                                ? 'bg-primary-500/20 text-white'
                                : 'text-gray-400 hover:text-white hover:bg-navy-700/50'
                              }
                            `}
                          >
                            <span className={`flex-shrink-0 ${isChildActive ? 'text-primary-400' : 'text-gray-500 group-hover:text-primary-400'}`}>
                              {child.icon}
                            </span>
                            <span>{child.label}</span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                  
                  {/* Collapsed tooltip submenu */}
                  {collapsed && (
                    <div className="absolute left-full ml-2 hidden group-hover:block z-50">
                      <div className="bg-navy-800 border border-navy-700 rounded-lg shadow-xl py-2 min-w-[160px]">
                        <div className="px-3 py-1 text-xs font-semibold text-gray-400 uppercase">{item.label}</div>
                        {item.children.map((child) => (
                          <Link
                            key={child.href}
                            href={child.href}
                            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-300 hover:bg-navy-700 hover:text-white"
                          >
                            {child.icon}
                            <span>{child.label}</span>
                          </Link>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            }
            
            // Regular nav item without children
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`
                  flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                  transition-all duration-200 group relative
                  ${isActive
                    ? 'bg-gradient-to-r from-primary-500/20 to-transparent text-white border-l-2 border-primary-500'
                    : 'text-gray-400 hover:text-white hover:bg-navy-700/50'
                  }
                  ${collapsed ? 'justify-center px-2' : ''}
                `}
                title={collapsed ? item.label : undefined}
              >
                <span className={`flex-shrink-0 transition-colors ${isActive ? 'text-primary-400' : 'text-gray-500 group-hover:text-primary-400'}`}>
                  {item.icon}
                </span>
                {!collapsed && (
                  <>
                    <span className="flex-1">{item.label}</span>
                    {item.badge && (
                      <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded bg-accent-500/20 text-accent-400 border border-accent-500/30">
                        {item.badge}
                      </span>
                    )}
                  </>
                )}
                {/* Active indicator glow */}
                {isActive && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-6 bg-primary-500 rounded-r shadow-[0_0_10px_rgba(0,102,204,0.5)]" />
                )}
              </Link>
            );
          })}
        </div>

        {/* Admin Section */}
        {!collapsed && (
          <div className="mt-6 pt-4 border-t border-navy-700/50">
            <h3 className="px-3 text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-2">
              Administration
            </h3>
          </div>
        )}
        <div className={`space-y-1 ${collapsed ? 'mt-4 pt-4 border-t border-navy-700/50' : ''}`}>
          {ADMIN_NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`
                  flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                  transition-all duration-200 group
                  ${isActive
                    ? 'bg-gradient-to-r from-primary-500/20 to-transparent text-white border-l-2 border-primary-500'
                    : 'text-gray-400 hover:text-white hover:bg-navy-700/50'
                  }
                  ${collapsed ? 'justify-center px-2' : ''}
                `}
                title={collapsed ? item.label : undefined}
              >
                <span className={`flex-shrink-0 transition-colors ${isActive ? 'text-primary-400' : 'text-gray-500 group-hover:text-primary-400'}`}>
                  {item.icon}
                </span>
                {!collapsed && <span>{item.label}</span>}
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Bottom Section - User */}
      <div className="border-t border-navy-700/50 p-3">
        {collapsed ? (
          <div className="flex flex-col items-center gap-3">
            <ThemeToggle />
            <button
              onClick={toggleCollapsed}
              className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-navy-700/50 transition-all"
              title="Expand sidebar"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
              </svg>
            </button>
            <button
              onClick={signOut}
              className="w-10 h-10 bg-gradient-to-br from-primary-500 to-accent-500 rounded-xl flex items-center justify-center text-white text-sm font-semibold shadow-glow hover:shadow-glow-lg transition-all"
              title={user?.email}
            >
              {user?.email?.charAt(0).toUpperCase() || 'U'}
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-3 p-3 rounded-xl bg-navy-700/30 border border-navy-600/50">
            <div className="w-10 h-10 bg-gradient-to-br from-primary-500 to-accent-500 rounded-xl flex items-center justify-center text-white text-sm font-semibold shadow-glow flex-shrink-0">
              {user?.email?.charAt(0).toUpperCase() || 'U'}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-white truncate">
                {user?.user_metadata?.full_name || 'User'}
              </div>
              <div className="text-xs text-gray-400 truncate">
                {user?.email}
              </div>
            </div>
            <div className="flex items-center gap-1">
              <ThemeToggle />
              <Link
                href="/profile"
                className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-navy-600/50 transition-all"
                title="Settings"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </Link>
              <button
                onClick={signOut}
                className="p-2 rounded-lg text-gray-400 hover:text-danger-500 hover:bg-danger-500/10 transition-all"
                title="Sign Out"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                </svg>
              </button>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
