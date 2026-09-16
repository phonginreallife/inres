import { memo } from 'react';

/**
 * Landing view for the assistant, shown while a conversation has no messages.
 *
 * Every suggestion maps to a tool the agent actually has, so a card can never
 * lead somewhere it cannot follow:
 *
 *   Recent activity -> get_current_time + get_incidents_by_time
 *   Statistics      -> get_incident_stats
 *   Search          -> search_incidents
 *   Triage          -> get_incidents_by_time + get_incident_by_id
 *
 * Clicking sends immediately rather than filling the composer - the point is to
 * make the first question free, and the prompts are phrased to be useful as-is.
 */

// One accent for every card: the brand blue, matching the header mark.
const ACCENT = 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10';

const SUGGESTIONS = [
  {
    id: 'recent',
    title: 'Recent activity',
    caption: 'What fired overnight',
    prompt: 'What incidents fired in the last 24 hours? Group them by severity.',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
        d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
    ),
  },
  {
    id: 'stats',
    title: 'Statistics',
    caption: 'Trends and volume',
    prompt: 'Give me incident statistics for the past week. What stands out?',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
        d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    ),
  },
  {
    id: 'search',
    title: 'Search',
    caption: 'Find by keyword',
    prompt: 'Search incidents mentioning timeouts or elevated latency.',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
        d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
    ),
  },
  {
    id: 'triage',
    title: 'Triage',
    caption: 'Where to start',
    prompt: 'Summarise the most recent incident and suggest next steps for on-call.',
    icon: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
        d="M13 10V3L4 14h7v7l9-11h-7z" />
    ),
  },
];

const SuggestionCard = ({ suggestion, onSelect, disabled }) => (
  <button
    type="button"
    disabled={disabled}
    onClick={() => onSelect(suggestion.prompt)}
    title={suggestion.prompt}
    className="group flex flex-col gap-2 rounded-xl border border-gray-200 dark:border-gray-800
               bg-white dark:bg-gray-900 p-4 text-left
               hover:border-gray-300 dark:hover:border-gray-700
               hover:shadow-sm hover:-translate-y-0.5
               focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500
               disabled:opacity-50 disabled:pointer-events-none
               transition-all duration-150 motion-reduce:transform-none"
  >
    <span className="flex items-center gap-2.5">
      <span className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${ACCENT}`}>
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
          {suggestion.icon}
        </svg>
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-gray-900 dark:text-gray-100">
          {suggestion.title}
        </span>
        <span className="block text-xs text-gray-500 dark:text-gray-400">
          {suggestion.caption}
        </span>
      </span>
    </span>
    <span className="line-clamp-2 text-xs leading-relaxed text-gray-600 dark:text-gray-400
                     group-hover:text-gray-900 dark:group-hover:text-gray-200 transition-colors">
      {suggestion.prompt}
    </span>
  </button>
);

const EmptyState = memo(({ onSelectSuggestion, disabled = false, incidentId = null }) => {
  // Arriving from an incident link puts that incident first - it is almost
  // certainly why the user opened the assistant.
  const suggestions = incidentId
    ? [
        {
          id: 'context',
          title: 'This incident',
          caption: `#${String(incidentId).slice(0, 8)}`,
          prompt: `Analyze incident ${incidentId}: what happened, likely cause, and what to check next.`,
          icon: (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
              d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4c-.77-1.33-2.69-1.33-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z" />
          ),
        },
        ...SUGGESTIONS.slice(0, 3),
      ]
    : SUGGESTIONS;

  return (
    <div className="flex min-h-[55vh] flex-col items-center justify-center px-4 py-10">
      <div className="w-full max-w-2xl">
        <div className="mb-7 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl
                          bg-gradient-to-br from-blue-500 to-blue-600 shadow-sm">
            <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
            How can I help?
          </h2>
          <p className="mt-1.5 text-sm text-gray-500 dark:text-gray-400">
            I can query incidents, dig through logs and run read-only checks against your stack.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {suggestions.map((s) => (
            <SuggestionCard
              key={s.id}
              suggestion={s}
              onSelect={onSelectSuggestion}
              disabled={disabled}
            />
          ))}
        </div>

        <p className="mt-6 text-center text-xs text-gray-400 dark:text-gray-500">
          Pick one, or ask anything below. Actions that change state will always ask you first.
        </p>
      </div>
    </div>
  );
});

EmptyState.displayName = 'EmptyState';

export default EmptyState;
