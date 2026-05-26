import type { JobState } from '../api/client';
import type { components } from '../api/types.gen';

type BuildTask = NonNullable<JobState['builds']>[string];
type ExtractedTopic = components['schemas']['ExtractedTopic'];

const PHASE_LABELS: Record<string, string> = {
  queued:    'Queued',
  draft:     'Generating draft',
  validate:  'Validating',
  polish:    'Polishing design',
  publish:   'Publishing to GitHub',
  done:      'Done',
  failed:    'Failed',
};

const PHASE_ORDER = [
  'queued',
  'draft',
  'validate',
  'polish',
  'publish',
  'done',
];

interface BuildCardProps {
  build: BuildTask;
  topic?: ExtractedTopic | null;
}

export default function BuildCard({ build, topic }: BuildCardProps) {
  const phaseIdx = PHASE_ORDER.indexOf(build.phase);
  const tail = (build.progress_log || []).slice(-12);

  return (
    <div className={'panel ' + (build.phase === 'failed' ? '' : 'accent')}>
      <div className="kicker">{topic ? topic.section : ''}</div>
      <h2>{topic ? topic.topic : build.topic_id}</h2>
      <div className="tiny" style={{ marginTop: 6 }}>
        {build.id} · {PHASE_LABELS[build.phase] || build.phase}
      </div>

      <div className="progress-track">
        {PHASE_ORDER.slice(1).map((p, i) => {
          const here = PHASE_ORDER.indexOf(p);
          let cls = 'seg';
          if (build.phase === 'failed') cls = 'seg' + (i < phaseIdx ? ' done' : '');
          else if (here < phaseIdx) cls = 'seg done';
          else if (here === phaseIdx) cls = 'seg active';
          return <div key={p} className={cls} title={PHASE_LABELS[p]} />;
        })}
      </div>

      {build.error && <div className="error">{build.error}</div>}

      {tail.length > 0 && (
        <div className="log-tail">
          {tail.map((l, i) => (
            <div key={i} className="line">
              {l}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
