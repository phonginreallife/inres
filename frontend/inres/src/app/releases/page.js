'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { useOrg } from '../../contexts/OrgContext';
import { apiClient } from '../../lib/api';

const STATUS_CONFIG = {
  draft:           { label: 'Draft',       color: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300' },
  planning:        { label: 'Planning',    color: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' },
  executing:       { label: 'Executing',   color: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300' },
  awaiting_review: { label: 'Awaiting Review', color: 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' },
  deploying:       { label: 'Deploying',   color: 'bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300' },
  verifying:       { label: 'Verifying',   color: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-300' },
  completed:       { label: 'Completed',   color: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' },
  failed:          { label: 'Failed',      color: 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300' },
  cancelled:       { label: 'Cancelled',   color: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400' },
};

function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.draft;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${config.color}`}>
      {config.label}
    </span>
  );
}

export default function ReleasesPage() {
  const { session } = useAuth();
  const { currentOrg } = useOrg();
  const router = useRouter();
  const [releases, setReleases] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [regionFilter, setRegionFilter] = useState('');

  const fetchReleases = useCallback(async () => {
    if (!session?.access_token || !currentOrg?.id) return;

    try {
      setLoading(true);
      apiClient.setToken(session.access_token);
      const data = await apiClient.getReleases({
        org_id: currentOrg.id,
        status: statusFilter,
        region: regionFilter,
        limit: 50,
      });
      setReleases(data.releases || []);
      setTotal(data.total || 0);
      setError(null);
    } catch (err) {
      console.error('Failed to fetch releases:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [session, currentOrg, statusFilter, regionFilter]);

  useEffect(() => {
    fetchReleases();
  }, [fetchReleases]);

  const formatDate = (dateStr) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Releases</h1>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              Manage deployment releases across regions. {total > 0 && `${total} release(s) total.`}
            </p>
          </div>
          <button
            onClick={() => router.push('/ai-agent')}
            className="inline-flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors text-sm font-medium"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Start New Release
          </button>
        </div>

        {/* Filters */}
        <div className="flex gap-4 mb-6">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-sm text-gray-700 dark:text-gray-300 focus:ring-2 focus:ring-primary-500"
          >
            <option value="">All Statuses</option>
            {Object.entries(STATUS_CONFIG).map(([key, cfg]) => (
              <option key={key} value={key}>{cfg.label}</option>
            ))}
          </select>
          <select
            value={regionFilter}
            onChange={(e) => setRegionFilter(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-sm text-gray-700 dark:text-gray-300 focus:ring-2 focus:ring-primary-500"
          >
            <option value="">All Regions</option>
            <option value="uswest2">us-west-2</option>
            <option value="useast2-ver2">us-east-2</option>
            <option value="eucentral1">eu-central-1</option>
            <option value="apsouth1">ap-south-1</option>
            <option value="apsoutheast1">ap-southeast-1</option>
          </select>
          <button
            onClick={fetchReleases}
            className="px-3 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white border border-gray-300 dark:border-gray-600 rounded-lg"
          >
            Refresh
          </button>
        </div>

        {/* Error state */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Loading state */}
        {loading && (
          <div className="flex items-center justify-center py-16">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600" />
          </div>
        )}

        {/* Empty state */}
        {!loading && releases.length === 0 && (
          <div className="text-center py-16">
            <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
            <h3 className="mt-4 text-lg font-medium text-gray-900 dark:text-white">No releases yet</h3>
            <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
              Start a new release by chatting with the AI Assistant.
            </p>
          </div>
        )}

        {/* Releases table */}
        {!loading && releases.length > 0 && (
          <div className="bg-white dark:bg-gray-800 shadow-sm rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900/50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Jira Ticket</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Version</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Region</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">PR</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {releases.map((release) => (
                  <tr
                    key={release.id}
                    onClick={() => router.push(`/releases/${release.id}`)}
                    className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer transition-colors"
                  >
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className="text-sm font-medium text-primary-600 dark:text-primary-400">
                        {release.jira_ticket_id}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-white font-mono">
                      {release.version}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600 dark:text-gray-400">
                      {release.region}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <StatusBadge status={release.status} />
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      {release.pr_url ? (
                        <a
                          href={release.pr_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="text-primary-600 dark:text-primary-400 hover:underline"
                        >
                          #{release.pr_number || 'PR'}
                        </a>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {formatDate(release.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
