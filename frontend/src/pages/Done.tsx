import { useEffect, useRef, useState } from 'react';
import { api, type JobState } from '../api/client';
import type { components } from '../api/types.gen';
import GithubRepoStatus from '../components/GithubRepoStatus';

type EmbedManifestEntry = components['schemas']['EmbedManifestEntry'];

interface ManifestResponse {
  job_id: string;
  ready: boolean;
  manifest: EmbedManifestEntry[];
  token_usage: {
    total_tokens: number;
    estimated_cost_usd: string | number;
  };
}

type Props = {
  jobId: string;
  onError: (message: string) => void;
  onRestart: () => void;
};

export function Done({ jobId, onError, onRestart }: Props) {
  const [data, setData] = useState<ManifestResponse | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function refresh() {
    try {
      const [m, j] = await Promise.all([
        api<ManifestResponse>(`/jobs/${jobId}/manifest`),
        api<JobState>(`/jobs/${jobId}`),
      ]);
      setData(m);
      setJob(j);
      return j;
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      const j = await refresh();
      if (cancelled || !j) return;
      const builds = Object.values(j.builds || {});
      const anyBuilding = builds.some(
        (b) => !['completed', 'failed'].includes(b.phase),
      );
      const anyPublishing = builds.some((b) => b.github_status === 'publishing');
      if (anyBuilding || anyPublishing) {
        timerRef.current = setTimeout(tick, 2000);
      }
    }
    tick();
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  if (!data || !job) {
    return (
      <div className="panel">
        <span className="spinner" /> Loading manifest…
      </div>
    );
  }

  const json = JSON.stringify(data, null, 2);

  function download() {
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `manifest_${jobId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function copy() {
    navigator.clipboard?.writeText(json);
  }

  return (
    <div>
      <div className="kicker">Step 05 — manifest</div>
      <h1>
        Done.{' '}
        <span style={{ color: 'var(--accent)' }}>{data.manifest.length}</span>{' '}
        viz{data.manifest.length === 1 ? '' : 's'} pushed to GitHub.
      </h1>
      <p className="lead" style={{ marginTop: 8 }}>
        Each viz has its own standalone repo. Clone, deploy, and embed at your leisure.
      </p>

      <div style={{ marginTop: 36, display: 'flex', flexDirection: 'column', gap: 18 }}>
        {data.manifest.map((m, i) => {
          // Find the matching build to surface its GitHub publish status
          const build = Object.values(job.builds || {}).find(
            (b) => b.project_dir === m.project_dir,
          );

          return (
            <div key={i} className={'panel ' + (m.status === 'ok' ? 'accent' : '')}>
              <div className="kicker">{m.section}</div>
              <h2>{m.topic}</h2>
              <p style={{ marginTop: 8, color: 'var(--ink-2)' }}>{m.why_visual_helps}</p>

              {m.screenshot_path ? (
                <div className="shot-thumb" style={{ marginTop: 18 }}>
                  <img
                    src={`/preview?path=${encodeURIComponent(m.screenshot_path)}`}
                    alt={m.topic}
                  />
                </div>
              ) : (
                <div className="shot-thumb" style={{ marginTop: 18 }}>
                  <div className="placeholder">no screenshot captured</div>
                </div>
              )}

              {/* ── GitHub publish status ── */}
              <div style={{ marginTop: 18 }}>
                <div className="kicker">Repo</div>
                {build && build.github_status && build.github_status !== 'not_started' ? (
                  <GithubRepoStatus
                    status={build.github_status}
                    repoUrl={build.github_repo_url}
                    repoName={build.github_repo_name}
                    error={build.github_error || ''}
                  />
                ) : (
                  <div className="tiny" style={{ marginTop: 6, opacity: 0.7 }}>
                    Build completed, but no GitHub publish status yet.
                  </div>
                )}
              </div>

              <div style={{ marginTop: 18 }}>
                <div className="kicker">Embed after sentence</div>
                <div
                  className="quote"
                  style={{
                    borderLeft: '2px solid var(--accent)',
                    paddingLeft: 14,
                    fontFamily: 'Fraunces, serif',
                    fontStyle: 'italic',
                    color: 'var(--ink-1)',
                    marginTop: 6,
                  }}
                >
                  {m.embed_after_sentence}
                </div>
              </div>

              <div style={{ marginTop: 18 }}>
                <div className="kicker">Project directory</div>
                <code
                  style={{
                    display: 'block',
                    marginTop: 6,
                    padding: '10px 14px',
                    background: 'var(--bg-0)',
                    border: '1px solid var(--line)',
                    wordBreak: 'break-all',
                  }}
                >
                  {m.project_dir}
                </code>
              </div>
            </div>
          );
        })}
      </div>

      <hr className="divider" />

      <div className="panel">
        <h3>Full manifest JSON</h3>
        <p className="tiny" style={{ marginTop: 6 }}>
          Token usage: {data.token_usage.total_tokens.toLocaleString()} tokens · $
          {data.token_usage.estimated_cost_usd}
        </p>
        <div className="row-actions" style={{ marginTop: 14, marginBottom: 18 }}>
          <button onClick={copy}>Copy JSON</button>
          <button onClick={download}>Download manifest.json</button>
          <button className="ghost" onClick={onRestart}>
            ↻ Start a new job
          </button>
        </div>
        <pre className="manifest-block">{json}</pre>
      </div>
    </div>
  );
}
