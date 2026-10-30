import { Notice, Panel, Pill, Spinner, StatusBadge } from '../components/ui';
import { api } from '../lib/api';
import { useAsync } from '../lib/hooks';
import type { Agent, Capabilities } from '../lib/types';

export default function AgentsView({ capabilities }: { capabilities: Capabilities | null }) {
  const agents = useAsync<{ agents: Agent[] }>(() => api.get('/system/agents'), []);
  const list = agents.data?.agents ?? [];
  const active = list.filter((a) => a.status === 'active');
  const limited = list.filter((a) => a.status === 'limited');
  const planned = list.filter((a) => a.status === 'planned');

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Agents</h2>
          <p>
            Every specialist declares a role, the tools it may call, the actions it may propose and an
            honest implementation status. Adding a new specialist means adding one registry entry.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Pill tone="green">{active.length} active</Pill>
        <Pill tone="amber">{limited.length} limited</Pill>
        <Pill tone="violet">{planned.length} planned</Pill>
      </div>

      {agents.loading && !agents.data && <Spinner label="Loading agents…" />}
      {agents.error && <Notice tone="red" title="Could not load agents">{agents.error}</Notice>}

      <Panel title="Active specialists" subtitle="Their workflows run today">
        <div className="grid grid--3">
          {active.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      </Panel>

      <Panel title="Working with a named gap" subtitle="Usable, but one capability is missing">
        <div className="grid grid--2">
          {limited.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      </Panel>

      <Panel title="Not implemented" subtitle="Shown honestly instead of pretending">
        <div className="grid grid--2">
          {planned.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      </Panel>

      {capabilities && (
        <div className="grid grid--2">
          <Panel title="Capability matrix" subtitle="What actually works right now">
            <div className="list">
              {capabilities.features.map((feature) => (
                <div key={feature.id} className="feature-row">
                  <div className="feature-row__main">
                    <div>{feature.name}</div>
                    <div className="feature-row__note">{feature.note}</div>
                  </div>
                  <StatusBadge status={feature.status} />
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Communication integrations" subtitle="None are connected">
            <div className="list">
              {capabilities.integrations.map((integration) => (
                <div key={integration.platform} className="list__row">
                  <div className="list__main">
                    <div className="list__title" style={{ textTransform: 'capitalize' }}>
                      {integration.platform}
                    </div>
                    <div className="list__sub">{integration.officialRoute}</div>
                    <div className="list__sub" style={{ color: 'var(--amber)' }}>{integration.limitation}</div>
                  </div>
                  <Pill tone="muted">{integration.status.replace(/_/g, ' ')}</Pill>
                </div>
              ))}
            </div>
            <p className="tiny muted-2" style={{ marginTop: 12 }}>
              Connecting any of these requires official API credentials, a verified business account
              and a separate review of platform policy. No automation of unsupported actions will be
              added.
            </p>
          </Panel>
        </div>
      )}
    </>
  );
}

function AgentCard({ agent }: { agent: Agent }) {
  return (
    <article className="agent-card">
      <div className="agent-card__head">
        <span className="agent-card__badge" style={{ background: agent.accent, color: agent.accent }} aria-hidden="true" />
        <div style={{ minWidth: 0 }}>
          <div className="agent-card__name">{agent.name}</div>
          <div className="agent-card__role">{agent.role}</div>
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <StatusBadge status={agent.status} />
        </div>
      </div>
      <p className="agent-card__mission">{agent.mission}</p>
      <div className="agent-card__meta">
        {agent.tools.length > 0 ? (
          agent.tools.map((tool) => (
            <Pill key={tool} tone="cyan">
              {tool}
            </Pill>
          ))
        ) : (
          <Pill tone="muted">no tools</Pill>
        )}
        {agent.permissions.map((permission) => (
          <Pill key={permission} tone="violet">
            can propose: {permission}
          </Pill>
        ))}
      </div>
      <p className="small muted-2">
        <strong style={{ color: 'var(--muted)' }}>Output: </strong>
        {agent.outputFormat}
      </p>
      <p className="agent-card__note">{agent.statusNote}</p>
    </article>
  );
}
