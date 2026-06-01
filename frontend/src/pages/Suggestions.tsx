import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { JobState } from '../api/client';

type Suggestion = {
  id: string;
  title: string;
  approach: string;
  complexity: string;
  beginner_benefit: string;
  intermediate_benefit: string;
};

type Props = {
  job: JobState;
  topic: { id: string; title: string };
  onBuildQueued: (jobId: string) => void;
  onBack: () => void;
  onError: (message: string) => void;
};

export function Suggestions({ job, topic, onBuildQueued, onBack, onError }: Props) {
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const r = await api<{ suggestions: Suggestion[] }>(
          `/jobs/${job.job_id}/topics/${topic.id}/suggestions`,
          { method: 'POST' },
        );
        if (!cancelled) setSuggestions(r.suggestions);
      } catch (e) {
        if (!cancelled) onError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [job.job_id, topic.id]);

  async function build() {
    if (!picked && !notes.trim()) {
      onError('Pick a suggestion or write custom notes.');
      return;
    }
    setBusy(true);
    try {
      await api(`/jobs/${job.job_id}/topics/${topic.id}/build`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          suggestion_id: picked || null,
          custom_notes: notes,
        }),
      });
      onBuildQueued(job.job_id);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="kicker">Step 03 — pick an approach</div>
      <h1 style={{ marginBottom: 8 }}>{topic.title}</h1>

      {loading && (
        <div className="panel">
          <span className="spinner" />
          {' '}Generating five distinct viz approaches…
        </div>
      )}

      {!loading && suggestions && (
        <div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 24 }}>
            {suggestions.map((s, i) => (
              <div
                key={s.id}
                className={'sug-card ' + (picked === s.id ? 'selected' : '')}
                onClick={() => setPicked(s.id)}
              >
                <span className="num">0{i + 1}</span>
                <h3>
                  {s.title}
                  <span className="complexity-badge">complexity · {s.complexity}</span>
                </h3>
                <p style={{ marginTop: 6, color: 'var(--ink-1)' }}>{s.approach}</p>
                <div className="benefit-row">
                  <div>
                    <span className="who">For beginners</span>
                    <span className="what">{s.beginner_benefit}</span>
                  </div>
                  <div>
                    <span className="who">For intermediate</span>
                    <span className="what">{s.intermediate_benefit}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="panel">
            <label>Custom notes (optional)</label>
            <textarea
              placeholder="e.g. Use a 5x5 input matrix with a 3x3 kernel — match the example from chapter 9 of Goodfellow's book."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={2000}
            />
          </div>

          <div className="tiny" style={{ marginTop: 16, opacity: 0.75 }}>
            On success, the viz is published to your program's repo
            (set <code>PROGRAM_REPOS</code> + <code>GITHUB_TOKEN</code> on the
            server) and served on Vercel at{' '}
            <code>https://&lt;program&gt;.vercel.app/&lt;module&gt;/&lt;viz&gt;/</code>.
          </div>

          <div className="row-actions" style={{ marginTop: 16 }}>
            <button className="ghost" onClick={onBack}>← Back to topics</button>
            <button
              className="primary"
              onClick={build}
              disabled={busy || (!picked && !notes.trim())}
            >
              {busy ? (
                <>
                  <span className="spinner" />
                  {' '}Queuing build…
                </>
              ) : (
                'Build this viz →'
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
