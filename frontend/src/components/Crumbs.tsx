// Temporary home for the Screen type.
// Task 11 will move the canonical definition into App.tsx and import from there.
export type Screen = 'upload' | 'topics' | 'suggestions' | 'building' | 'done';

const STEPS: Screen[] = ['upload', 'topics', 'suggestions', 'building', 'done'];

const LABELS: Record<Screen, string> = {
  upload:      '01 · UPLOAD',
  topics:      '02 · TOPICS',
  suggestions: '03 · APPROACH',
  building:    '04 · BUILDING',
  done:        '05 · MANIFEST',
};

interface CrumbsProps {
  screen: Screen;
}

export default function Crumbs({ screen }: CrumbsProps) {
  return (
    <div className="crumbs">
      {STEPS.map((s) => (
        <span key={s} className={s === screen ? 'now' : ''}>
          {LABELS[s]}
        </span>
      ))}
    </div>
  );
}
