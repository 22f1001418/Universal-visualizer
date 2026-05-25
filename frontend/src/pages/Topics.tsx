import { useEffect, useRef } from 'react';
import type { JobState } from '../api/client';
import { useJobPolling } from '../hooks/useJobPolling';

type ExtractedTopic = NonNullable<JobState['topics']>[number];

type Props = {
  job: JobState;
  onPickTopic: (topic: { id: string; title: string }) => void;
  onError: (message: string) => void;
};

export function Topics({ job, onPickTopic, onError }: Props) {
  // Track whether we have already seen topics_extracted so we can stop polling.
  // A ref is mutable across renders without causing extra re-renders; it lets us
  // pass the correct stop flag to useJobPolling without a circular dependency
  // on the hook's own return value within the same render cycle.
  const doneRef = useRef(job.status === 'topics_extracted');

  const { job: polledJob, error } = useJobPolling(job.job_id, 1000, doneRef.current);

  // Use polled job once available, otherwise fall back to the prop job.
  const activeJob: JobState = polledJob ?? job;

  // Update the ref synchronously so the next render (triggered by polledJob
  // state change) will pass stop=true to the hook.
  if (activeJob.status === 'topics_extracted') {
    doneRef.current = true;
  }

  // Lift polling error into the App error banner via a side-effect (not at
  // render time) so dismissing the banner doesn't immediately re-trigger it.
  useEffect(() => {
    if (error) onError(error);
  }, [error, onError]);

  const isExtracted = activeJob.status === 'topics_extracted';
  const topics: ExtractedTopic[] = activeJob.topics ?? [];

  if (!isExtracted) {
    return (
      <div>
        <div className="kicker">Step 02 — topics</div>
        <h1>Extracting topics…</h1>
        <p className="lead">
          <span className="spinner" />
          We're pulling viz opportunities from your script. This usually takes a
          few seconds.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="kicker">Step 02 — topics</div>
      <h1>Extracted{' '}
        <span style={{ color: 'var(--accent)' }}>{topics.length}</span>{' '}
        viz opportunities.
      </h1>
      <p className="lead">
        Pick one to see five distinct approaches a viz agent could build for it.
        Each topic shows the section it lives under and the exact sentence the viz
        will be embedded after.
      </p>

      <div className="grid-2" style={{ marginTop: 36 }}>
        {topics.map((t, i) => (
          <div
            key={t.id}
            className="topic-card"
            onClick={() => onPickTopic({ id: t.id, title: t.topic })}
          >
            <span className="num">{String(i + 1).padStart(2, '0')}</span>
            <div className="section-tag">{t.section}</div>
            <h3>{t.topic}</h3>
            <div className="quote">{t.embed_after_sentence || '(no anchor sentence)'}</div>
            <p className="why">{t.why_visual_helps}</p>
            <div className={'diff-pill ' + t.audience_difficulty}>{t.audience_difficulty}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
