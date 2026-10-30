import type { ReactNode } from 'react';
import type { User } from '../lib/types';

export type ViewId =
  | 'briefing'
  | 'chat'
  | 'tasks'
  | 'approvals'
  | 'workflows'
  | 'agents'
  | 'memory'
  | 'research'
  | 'finance'
  | 'compliance'
  | 'security';

export const VIEW_META: Record<ViewId, { label: string; title: string; subtitle: string; group: 'core' | 'work' | 'system' }> = {
  briefing: { label: 'Briefing', title: 'Daily briefing', subtitle: 'What matters now, from real stored data.', group: 'core' },
  chat: { label: 'Conversation', title: 'Conversation', subtitle: 'Talk or type. Every turn is routed, logged and validated.', group: 'core' },
  tasks: { label: 'Tasks', title: 'Tasks', subtitle: 'Priorities, deadlines and dependencies.', group: 'work' },
  approvals: { label: 'Approvals', title: 'Approval centre', subtitle: 'Nothing external happens without your approval here.', group: 'work' },
  workflows: { label: 'Workflows', title: 'Workflow monitor', subtitle: 'Live execution events from the orchestrator.', group: 'work' },
  agents: { label: 'Agents', title: 'Agent management', subtitle: 'Specialists, their permissions and honest status.', group: 'work' },
  memory: { label: 'Memory', title: 'Memory', subtitle: 'What J.A.R.V.I.S. remembers, and who stated it.', group: 'work' },
  research: { label: 'Research', title: 'Research library', subtitle: 'Sources with dates. No fabricated findings.', group: 'work' },
  finance: { label: 'Finance', title: 'Finance', subtitle: 'Records and forecasts. Records only — money never moves.', group: 'work' },
  compliance: { label: 'Compliance', title: 'Rules & compliance', subtitle: 'Jurisdiction-aware register. Not legal advice.', group: 'work' },
  security: { label: 'Security', title: 'Settings, security & permissions', subtitle: 'Provider, autonomy, voice, audit trail and cost.', group: 'system' },
};

const ICONS: Record<ViewId, ReactNode> = {
  briefing: <path d="M4 12h4l2-6 4 12 2-6h4" />,
  chat: <path d="M4 5h16v10H9l-5 4V5Z" />,
  tasks: <path d="M4 6h3v3H4V6Zm0 6h3v3H4v-3Zm7-3h9M11 15h9M11 6h9" />,
  approvals: <path d="M12 3l8 4v5c0 4.2-3.2 7.6-8 9-4.8-1.4-8-4.8-8-9V7l8-4Zm-3 9 2.5 2.5L16 10" />,
  workflows: <path d="M4 6h6v4H4V6Zm10 8h6v4h-6v-4ZM7 10v4h10" />,
  agents: <path d="M12 3a3 3 0 0 1 3 3v1h2a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3h2V6a3 3 0 0 1 3-3Zm-3 9h.01M15 12h.01M9 17h6" />,
  memory: <path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0 0 6v1a3 3 0 0 0 6 0V7a3 3 0 0 0-3-3Zm6 0a3 3 0 0 1 3 3v1a3 3 0 0 1 0 6v1a3 3 0 0 1-6 0" />,
  research: <path d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm5 12 4 4" />,
  finance: <path d="M4 19V5m0 14h16M8 16V9m4 7V6m4 10v-4" />,
  compliance: <path d="M6 3h9l4 4v14H6V3Zm8 0v5h5M9 13h6M9 17h6" />,
  security: <path d="M12 3l8 4v5c0 4.2-3.2 7.6-8 9-4.8-1.4-8-4.8-8-9V7l8-4Zm0 5v5m0 3h.01" />,
};

function NavIcon({ view }: { view: ViewId }) {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[view]}
    </svg>
  );
}

export function Shell({
  user,
  view,
  onNavigate,
  badge,
  children,
  headerMeta,
  onLogout,
}: {
  user: User;
  view: ViewId;
  onNavigate: (view: ViewId) => void;
  badge?: number;
  children: ReactNode;
  headerMeta?: ReactNode;
  onLogout: () => void;
}) {
  const groups: Array<{ key: 'core' | 'work' | 'system'; label: string }> = [
    { key: 'core', label: 'Command' },
    { key: 'work', label: 'Operations' },
    { key: 'system', label: 'Control' },
  ];
  const meta = VIEW_META[view];
  const initials = user.name
    .split(/\s+/)
    .map((p) => p[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand__mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="12" r="3.2" />
              <circle cx="12" cy="12" r="8" opacity="0.45" />
              <path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3" opacity="0.7" />
            </svg>
          </div>
          <div className="brand__text">
            <span className="brand__title">J.A.R.V.I.S.</span>
            <span className="brand__sub">AI operating system</span>
          </div>
        </div>

        <nav className="nav" aria-label="Primary">
          {groups.map((group) => (
            <div key={group.key} style={{ display: 'contents' }}>
              <span className="nav__label">{group.label}</span>
              {(Object.keys(VIEW_META) as ViewId[])
                .filter((id) => VIEW_META[id].group === group.key)
                .map((id) => (
                  <button
                    key={id}
                    className={`nav__item ${view === id ? 'nav__item--active' : ''}`}
                    onClick={() => onNavigate(id)}
                    aria-current={view === id ? 'page' : undefined}
                  >
                    <span className="nav__icon">
                      <NavIcon view={id} />
                    </span>
                    {VIEW_META[id].label}
                    {id === 'approvals' && badge ? <span className="nav__count">{badge}</span> : null}
                  </button>
                ))}
            </div>
          ))}
        </nav>

        <div className="sidebar__footer">
          <div className="user-chip">
            <span className="user-chip__avatar">{initials || 'O'}</span>
            <div style={{ minWidth: 0 }}>
              <div className="user-chip__name">{user.name}</div>
              <div className="user-chip__mail">{user.email}</div>
            </div>
          </div>
          <button className="btn btn--subtle btn--sm" onClick={onLogout}>
            Sign out
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar__title">
            <h1>{meta.title}</h1>
            <span>{meta.subtitle}</span>
          </div>
          <div className="topbar__spacer" />
          <div className="topbar__meta">{headerMeta}</div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
