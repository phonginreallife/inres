'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useAuth } from '../../../contexts/AuthContext';
import { apiClient } from '../../../lib/api';

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

const STEP_CONFIG = {
  jira_fetch:       { label: 'Fetch Jira Ticket',       phase: 'Gather Info',  icon: '1' },
  confluence_parse: { label: 'Parse Confluence',        phase: 'Gather Info',  icon: '2' },
  plan_changes:     { label: 'Generate Change Plan',    phase: 'Plan',         icon: '3' },
  approve_plan:     { label: 'Approve Plan',            phase: 'Plan',         icon: '4', approval: true },
  apply_yaml:       { label: 'Apply YAML Changes',      phase: 'Execute',      icon: '5' },
  sops_commands:    { label: 'SOPS Secret Updates',     phase: 'Execute',      icon: '6' },
  create_pr:        { label: 'Create GitHub PR',        phase: 'Execute',      icon: '7' },
  approve_pr:       { label: 'PR Review',               phase: 'Execute',      icon: '8', approval: true },
  approve_sync:     { label: 'Confirm ArgoCD Sync',     phase: 'Deploy',       icon: '9', approval: true },
  argocd_sync:      { label: 'ArgoCD Sync',             phase: 'Deploy',       icon: '10' },
  health_check:     { label: 'Health Check',            phase: 'Deploy',       icon: '11' },
  approve_deploy:   { label: 'Verify Deployment',       phase: 'Deploy',       icon: '12', approval: true },
};

const STEP_STATUS_ICONS = {
  pending:           { icon: 'circle', color: 'text-gray-300 dark:text-gray-600' },
  in_progress:       { icon: 'spinner', color: 'text-blue-500' },
  completed:         { icon: 'check', color: 'text-green-500' },
  failed:            { icon: 'x', color: 'text-red-500' },
  skipped:           { icon: 'skip', color: 'text-gray-400' },
  awaiting_approval: { icon: 'clock', color: 'text-purple-500' },
};

function StepStatusIcon({ status }) {
  const config = STEP_STATUS_ICONS[status] || STEP_STATUS_ICONS.pending;

  if (config.icon === 'check') {
    return (
      <svg className={`w-5 h-5 ${config.color}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
    );
  }
  if (config.icon === 'x') {
    return (
      <svg className={`w-5 h-5 ${config.color}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
      </svg>
    );
  }
  if (config.icon === 'spinner') {
    return (
      <svg className={`w-5 h-5 ${config.color} animate-spin`} fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth={4} />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
    );
  }
  if (config.icon === 'clock') {
    return (
      <svg className={`w-5 h-5 ${config.color} animate-pulse`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    );
  }
  if (config.icon === 'skip') {
    return (
      <svg className={`w-5 h-5 ${config.color}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
      </svg>
    );
  }
  return (
    <div className={`w-5 h-5 rounded-full border-2 border-current ${config.color}`} />
  );
}

function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.draft;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${config.color}`}>
      {config.label}
    </span>
  );
}

function Toast({ message, type, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 4000);
    return () => clearTimeout(timer);
  }, [onClose]);

  const bgColor = type === 'success' ? 'bg-green-600' : type === 'error' ? 'bg-red-600' : 'bg-gray-700';

  return (
    <div className={`fixed bottom-4 right-4 ${bgColor} text-white px-4 py-3 rounded-lg shadow-lg z-50 flex items-center gap-2 text-sm font-medium`}>
      {message}
    </div>
  );
}

export default function ReleaseDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { session } = useAuth();
  const [release, setRelease] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const [approvalComment, setApprovalComment] = useState('');
  const [approvingStep, setApprovingStep] = useState(null);
  const [expandedSteps, setExpandedSteps] = useState({});
  const [cancelling, setCancelling] = useState(false);

  const fetchRelease = useCallback(async () => {
    if (!session?.access_token || !params.id) return;

    try {
      apiClient.setToken(session.access_token);
      const data = await apiClient.getRelease(params.id);
      setRelease(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [session, params.id]);

  useEffect(() => {
    fetchRelease();
    const interval = setInterval(fetchRelease, 10000);
    return () => clearInterval(interval);
  }, [fetchRelease]);

  const handleApprove = async (stepId, decision) => {
    setApprovingStep(stepId);
    try {
      await apiClient.approveReleaseStep(params.id, stepId, decision, approvalComment);
      setToast({ message: `Step ${decision}`, type: decision === 'approved' ? 'success' : 'error' });
      setApprovalComment('');
      await fetchRelease();
    } catch (err) {
      setToast({ message: `Failed: ${err.message}`, type: 'error' });
    } finally {
      setApprovingStep(null);
    }
  };

  const handleCancel = async () => {
    if (!confirm('Are you sure you want to cancel this release?')) return;
    setCancelling(true);
    try {
      await apiClient.cancelRelease(params.id);
      setToast({ message: 'Release cancelled', type: 'success' });
      await fetchRelease();
    } catch (err) {
      setToast({ message: `Cancel failed: ${err.message}`, type: 'error' });
    } finally {
      setCancelling(false);
    }
  };

  const toggleStep = (stepId) => {
    setExpandedSteps(prev => ({ ...prev, [stepId]: !prev[stepId] }));
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600" />
      </div>
    );
  }

  if (error || !release) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-500 mb-4">{error || 'Release not found'}</p>
          <button onClick={() => router.push('/releases')} className="text-primary-600 hover:underline">
            Back to releases
          </button>
        </div>
      </div>
    );
  }

  const steps = release.steps || [];
  const approvals = release.approvals || [];
  const isTerminal = ['completed', 'failed', 'cancelled'].includes(release.status);
  const hasAwaitingApproval = steps.some((s) => s.status === 'awaiting_approval');
  const agentMayHaveWork =
    release.status === 'draft' ||
    steps.some((s) => s.status === 'pending' || s.status === 'in_progress');
  const showAgentCta = !isTerminal && !hasAwaitingApproval && agentMayHaveWork;

  // Group steps by phase
  const phases = {};
  steps.forEach((step) => {
    const config = STEP_CONFIG[step.step_type] || { phase: 'Other', label: step.step_type };
    if (!phases[config.phase]) phases[config.phase] = [];
    phases[config.phase].push({ ...step, config });
  });

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Back link */}
        <button
          onClick={() => router.push('/releases')}
          className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white mb-6"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Back to releases
        </button>

        {/* Header card */}
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6 mb-8">
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-3 mb-2">
                <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
                  {release.jira_ticket_id}
                </h1>
                <StatusBadge status={release.status} />
              </div>
              <div className="flex items-center gap-4 text-sm text-gray-500 dark:text-gray-400">
                <span className="font-mono bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded">
                  v{release.version}
                </span>
                <span>{release.region}</span>
                <span>Created {formatDate(release.created_at)}</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {release.pr_url && (
                <a
                  href={release.pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300"
                >
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16">
                    <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 01-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 010 8c0-4.42 3.58-8 8-8z" />
                  </svg>
                  PR #{release.pr_number || ''}
                </a>
              )}
              {!isTerminal && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="px-3 py-1.5 text-sm border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
                >
                  {cancelling ? 'Cancelling...' : 'Cancel Release'}
                </button>
              )}
            </div>
          </div>
        </div>

        {showAgentCta && (
          <div className="mb-8 rounded-xl border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950/40">
            <h2 className="text-sm font-semibold text-blue-900 dark:text-blue-200">
              Drive this workflow from the AI agent
            </h2>
            <p className="mt-1 text-sm text-blue-800/90 dark:text-blue-200/85">
              Steps such as fetching Jira and Confluence, generating plans, and applying changes are run by the
              InRes AI agent (release MCP tools), not from this page. This view updates as the agent progresses.
              When a step needs your sign-off, Approve and Reject buttons appear here.
            </p>
            <button
              type="button"
              onClick={() => router.push(`/ai-agent?release=${encodeURIComponent(release.id)}`)}
              className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
            >
              Open AI agent for this release
            </button>
            <p className="mt-2 font-mono text-xs text-blue-900/70 dark:text-blue-300/80">
              Release ID: {release.id}
            </p>
            <p className="mt-2 text-xs text-blue-800/85 dark:text-blue-200/80">
              Optional integrations (e.g. Coralogix MCP): configure under{' '}
              <button
                type="button"
                className="font-medium text-blue-900 underline hover:no-underline dark:text-blue-100"
                onClick={() => router.push('/agent-config')}
              >
                Integrations → MCP Servers
              </button>
              . Built-in release and incident tools are always available to the agent.
            </p>
          </div>
        )}

        {hasAwaitingApproval && !isTerminal && (
          <div className="mb-8 rounded-lg border border-purple-200 bg-purple-50 px-4 py-3 text-sm text-purple-900 dark:border-purple-800 dark:bg-purple-950/30 dark:text-purple-200">
            One or more steps need your decision — use Approve or Reject on the highlighted rows below.
          </div>
        )}

        {/* Workflow Steps Timeline */}
        <div className="space-y-8">
          {Object.entries(phases).map(([phaseName, phaseSteps]) => (
            <div key={phaseName}>
              <h2 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-4">
                {phaseName}
              </h2>
              <div className="space-y-1">
                {phaseSteps.map((step, idx) => {
                  const isExpanded = expandedSteps[step.id];
                  const isApprovalStep = step.config.approval;
                  const isAwaitingApproval = step.status === 'awaiting_approval';
                  const stepApprovals = approvals.filter(a => a.step_id === step.id);
                  const hasOutput = step.output && JSON.stringify(step.output) !== '{}' && JSON.stringify(step.output) !== 'null';
                  const isLast = idx === phaseSteps.length - 1;

                  return (
                    <div key={step.id} className="relative">
                      {/* Connector line */}
                      {!isLast && (
                        <div className="absolute left-[21px] top-[36px] bottom-0 w-px bg-gray-200 dark:bg-gray-700" />
                      )}

                      <div
                        className={`flex items-start gap-4 p-3 rounded-lg transition-colors ${
                          isAwaitingApproval
                            ? 'bg-purple-50 dark:bg-purple-900/10 border border-purple-200 dark:border-purple-800'
                            : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'
                        }`}
                      >
                        {/* Status icon */}
                        <div className="flex-shrink-0 mt-0.5 relative z-10 bg-gray-50 dark:bg-gray-900 rounded-full p-0.5">
                          <StepStatusIcon status={step.status} />
                        </div>

                        {/* Step content */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between">
                            <button
                              onClick={() => (hasOutput || step.error_message) && toggleStep(step.id)}
                              className="flex items-center gap-2 text-left"
                            >
                              <span className="text-sm font-medium text-gray-900 dark:text-white">
                                {step.config.label}
                              </span>
                              {(hasOutput || step.error_message) && (
                                <svg
                                  className={`w-4 h-4 text-gray-400 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                                >
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                                </svg>
                              )}
                            </button>
                            <div className="flex items-center gap-2 text-xs text-gray-400">
                              {step.started_at && <span>Started {formatDate(step.started_at)}</span>}
                              {step.completed_at && <span>Done {formatDate(step.completed_at)}</span>}
                            </div>
                          </div>

                          {/* Error message */}
                          {step.error_message && (
                            <p className="mt-1 text-sm text-red-600 dark:text-red-400">
                              {step.error_message}
                            </p>
                          )}

                          {/* Expanded output */}
                          {isExpanded && hasOutput && (
                            <pre className="mt-2 p-3 bg-gray-100 dark:bg-gray-900 rounded-lg text-xs text-gray-700 dark:text-gray-300 overflow-x-auto max-h-64 overflow-y-auto">
                              {typeof step.output === 'string'
                                ? step.output
                                : JSON.stringify(step.output, null, 2)}
                            </pre>
                          )}

                          {/* Approval actions */}
                          {isAwaitingApproval && isApprovalStep && (
                            <div className="mt-3 space-y-2">
                              <textarea
                                value={approvalComment}
                                onChange={(e) => setApprovalComment(e.target.value)}
                                placeholder="Optional comment..."
                                rows={2}
                                className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white placeholder-gray-400 focus:ring-2 focus:ring-primary-500"
                              />
                              <div className="flex gap-2">
                                <button
                                  onClick={() => handleApprove(step.id, 'approved')}
                                  disabled={approvingStep === step.id}
                                  className="inline-flex items-center gap-1.5 px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
                                >
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                  </svg>
                                  Approve
                                </button>
                                <button
                                  onClick={() => handleApprove(step.id, 'rejected')}
                                  disabled={approvingStep === step.id}
                                  className="inline-flex items-center gap-1.5 px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
                                >
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                  </svg>
                                  Reject
                                </button>
                              </div>
                            </div>
                          )}

                          {/* Previous approvals */}
                          {stepApprovals.length > 0 && (
                            <div className="mt-2 space-y-1">
                              {stepApprovals.map(a => (
                                <div key={a.id} className="flex items-center gap-2 text-xs">
                                  <span className={a.decision === 'approved' ? 'text-green-600' : 'text-red-600'}>
                                    {a.decision === 'approved' ? 'Approved' : 'Rejected'}
                                  </span>
                                  <span className="text-gray-400">
                                    {formatDate(a.created_at)}
                                  </span>
                                  {a.comment && (
                                    <span className="text-gray-500 dark:text-gray-400 italic">
                                      &quot;{a.comment}&quot;
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* Planned changes section */}
        {release.planned_changes && JSON.stringify(release.planned_changes) !== '{}' && (
          <div className="mt-8 bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Planned Changes</h3>
            <pre className="p-3 bg-gray-100 dark:bg-gray-900 rounded-lg text-xs text-gray-700 dark:text-gray-300 overflow-x-auto max-h-96 overflow-y-auto">
              {typeof release.planned_changes === 'string'
                ? release.planned_changes
                : JSON.stringify(release.planned_changes, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
