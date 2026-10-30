import type { ReactNode } from 'react';

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = '',
  glow = false,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  glow?: boolean;
}) {
  return (
    <section className={`panel ${glow ? 'panel--glow' : ''} ${className}`}>
      {(title || actions) && (
        <header className="panel__head">
          <div>
            {title && <h2 className="panel__title">{title}</h2>}
            {subtitle && <p className="panel__subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="panel__actions">{actions}</div>}
        </header>
      )}
      <div className="panel__body">{children}</div>
    </section>
  );
}

export type Tone = 'cyan' | 'green' | 'amber' | 'red' | 'violet' | 'muted';

export function Pill({ tone = 'muted', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`pill pill--${tone}`} title={title}>
      {children}
    </span>
  );
}

const STATUS_TONES: Record<string, Tone> = {
  completed: 'green',
  verified: 'green',
  approved: 'green',
  auto_executed: 'green',
  done: 'green',
  available: 'green',
  active: 'green',
  running: 'cyan',
  pending: 'amber',
  needs_approval: 'amber',
  waiting: 'amber',
  to_review: 'amber',
  in_progress: 'cyan',
  planned: 'violet',
  limited: 'amber',
  needs_config: 'amber',
  blocked: 'red',
  failed: 'red',
  rejected: 'red',
  cancelled: 'muted',
  not_connected: 'muted',
  not_applicable: 'muted',
};

export function statusTone(status: string): Tone {
  return STATUS_TONES[status] ?? 'muted';
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <Pill tone={statusTone(status)}>
      <span className="dot" /> {label ?? status.replace(/_/g, ' ')}
    </Pill>
  );
}

export function Button({
  children,
  variant = 'ghost',
  size = 'md',
  type = 'button',
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'danger' | 'subtle';
  size?: 'sm' | 'md';
}) {
  return (
    <button type={type} className={`btn btn--${variant} btn--${size}`} {...rest}>
      {children}
    </button>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <span className="stat__label">{label}</span>
      <span className="stat__value">{value}</span>
      {hint && <span className="stat__hint">{hint}</span>}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <label className="field" htmlFor={htmlFor}>
      <span className="field__label">{label}</span>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </label>
  );
}

export function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty__ring" aria-hidden="true" />
      <h3>{title}</h3>
      <p>{body}</p>
      {action}
    </div>
  );
}

export function Notice({
  tone = 'cyan',
  title,
  children,
  onDismiss,
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div className={`notice notice--${tone}`} role={tone === 'red' ? 'alert' : 'status'}>
      <div>
        {title && <strong>{title}</strong>}
        <div className="notice__body">{children}</div>
      </div>
      {onDismiss && (
        <button className="notice__close" onClick={onDismiss} aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <div className={`toggle ${disabled ? 'toggle--disabled' : ''}`}>
      <div>
        <span className="toggle__label">{label}</span>
        {hint && <span className="toggle__hint">{hint}</span>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        className={`switch ${checked ? 'switch--on' : ''}`}
        onClick={() => onChange(!checked)}
        disabled={disabled}
      >
        <span className="switch__knob" />
      </button>
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap" role="status">
      <span className="spinner" aria-hidden="true" />
      {label && <span className="spinner__label">{label}</span>}
    </span>
  );
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function formatDay(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value.length <= 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
}

/**
 * Tiny safe markdown renderer: everything is rendered as React text nodes, so
 * model output can never inject HTML. Supports headings, lists, bold, code,
 * blockquotes and links.
 */
export function RichText({ text }: { text: string }) {
  const lines = text.split('\n');
  const blocks: ReactNode[] = [];
  let list: string[] = [];

  const flushList = () => {
    if (list.length === 0) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="rich__list">
        {list.map((item, i) => (
          <li key={i}>{inline(item)}</li>
        ))}
      </ul>,
    );
    list = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? '';
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
    if (bullet) {
      list.push(bullet[1] ?? '');
      continue;
    }
    if (numbered) {
      list.push(numbered[2] ?? '');
      continue;
    }
    flushList();
    if (line.trim() === '') continue;
    if (/^###\s/.test(line)) blocks.push(<h4 key={i}>{inline(line.replace(/^###\s/, ''))}</h4>);
    else if (/^##\s/.test(line)) blocks.push(<h3 key={i}>{inline(line.replace(/^##\s/, ''))}</h3>);
    else if (/^#\s/.test(line)) blocks.push(<h2 key={i}>{inline(line.replace(/^#\s/, ''))}</h2>);
    else if (/^>\s?/.test(line)) blocks.push(<blockquote key={i}>{inline(line.replace(/^>\s?/, ''))}</blockquote>);
    else blocks.push(<p key={i}>{inline(line)}</p>);
  }
  flushList();
  return <div className="rich">{blocks}</div>;
}

function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    const token = match[0];
    if (token.startsWith('**')) nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith('`')) nodes.push(<code key={key++}>{token.slice(1, -1)}</code>);
    else {
      const linkMatch = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/.exec(token);
      if (linkMatch) {
        nodes.push(
          <a key={key++} href={linkMatch[2]} target="_blank" rel="noreferrer noopener">
            {linkMatch[1]}
          </a>,
        );
      }
    }
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}
