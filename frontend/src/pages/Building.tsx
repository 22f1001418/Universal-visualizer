import { useEffect, useRef } from 'react';
import { useJobPolling } from '../hooks/useJobPolling';
import BuildCard from '../components/BuildCard';

type Props = {
  jobId: string;
  onAllDone: (jobId: string) => void;
  onBackToTopics: () => void;
  /** Polling interval in ms — defaults to 1000. Exposed for testing. */
  pollIntervalMs?: number;
};

export function Building({ jobId, onAllDone, onBackToTopics, pollIntervalMs = 1000 }: Props) {
  const { job } = useJobPolling(jobId, pollIntervalMs);
  const firedRef = useRef(false);

  useEffect(() => {
    if (!job || firedRef.current) return;
    const builds = Object.values(job.builds || {});
    const allDone =
      builds.length > 0 &&
      builds.every(
        (b) => b.phase === 'done' || b.phase === 'failed',
      );
    if (allDone) {
      firedRef.current = true;
      onAllDone(jobId);
    }
  }, [job, jobId, onAllDone]);

  if (!job) {
    return (
      <div className="panel">
        <span className="spinner" /> Loading job state…
      </div>
    );
  }

  const builds = Object.values(job.builds || {});
  const logs = job.logs || [];

  return (
    <div>
      <div className="kicker">Step 04 — building</div>
      <h1>
        Generating{' '}
        <span style={{ color: 'var(--accent)' }}>{builds.length}</span>{' '}
        visualization{builds.length === 1 ? '' : 's'}.
      </h1>
      <p className="lead">
        Each viz spawns the vanilla generator subprocess: it drafts a
        self-contained HTML page, runs a single Playwright validation pass,
        polishes the design, and screenshots the result.
      </p>

      <div style={{ marginTop: 36, display: 'flex', flexDirection: 'column', gap: 18 }}>
        {builds.map((b) => {
          const topic = (job.topics || []).find((t) => t.id === b.topic_id);
          return <BuildCard key={b.topic_id} build={b} topic={topic} />;
        })}
      </div>

      {builds.length === 0 && (
        <div className="panel">
          No builds yet.{' '}
          <button className="ghost" onClick={onBackToTopics}>
            back
          </button>
        </div>
      )}

      {logs.length > 0 && (
        <div className="log-tail" style={{ marginTop: 24 }}>
          {logs.slice(-20).map((l, i) => (
            <div key={i} className="line">
              {l}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
