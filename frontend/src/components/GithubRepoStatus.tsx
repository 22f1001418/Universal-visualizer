interface GithubRepoStatusProps {
  status: string;
  repoUrl?: string;
  repoName?: string;
  error?: string;
}

export default function GithubRepoStatus({
  status,
  repoUrl,
  error,
}: GithubRepoStatusProps) {
  if (status === 'published' && repoUrl) {
    return (
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
        <a href={repoUrl} target="_blank" rel="noopener noreferrer" style={{ textDecoration: 'none' }}>
          <button className="primary">View on GitHub ↗</button>
        </a>
        <code
          style={{
            padding: '4px 10px',
            background: 'var(--bg-0)',
            border: '1px solid var(--line)',
            fontSize: '0.78rem',
            wordBreak: 'break-all',
          }}
        >
          {repoUrl}
        </code>
      </div>
    );
  }

  if (status === 'publishing') {
    return (
      <div className="tiny" style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="spinner" />
        Publishing to GitHub…
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div style={{ marginTop: 8 }}>
        <div className="error" style={{ margin: 0 }}>
          GitHub publish failed{error ? `: ${error.slice(0, 240)}` : ''}
        </div>
      </div>
    );
  }

  if (status === 'skipped') {
    return (
      <div className="tiny" style={{ marginTop: 8, opacity: 0.7 }}>
        GitHub publish skipped{error ? ` — ${error}` : ''}
      </div>
    );
  }

  return null;
}
