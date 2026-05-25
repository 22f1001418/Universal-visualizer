import { useState } from 'react';
import type { JobState } from './api/client';
import Crumbs from './components/Crumbs';
import { Upload } from './pages/Upload';
import { Topics } from './pages/Topics';
import { Suggestions } from './pages/Suggestions';
import { Building } from './pages/Building';
import { Done } from './pages/Done';

export type Screen = 'upload' | 'topics' | 'suggestions' | 'building' | 'done';

export default function App() {
  const [screen, setScreen] = useState<Screen>('upload');
  const [job, setJob] = useState<JobState | null>(null);
  const [topic, setTopic] = useState<{ id: string; title: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const showError = (msg: string) => setError(msg);

  return (
    <div className="shell">
      <div className="brand">
        <div className="name">HackMD <em>·</em> Visualization Orchestrator</div>
        <div className="meta">v1 · multi-agent</div>
      </div>

      <Crumbs screen={screen} />

      {error !== null && (
        <div className="error">
          {error}{' '}
          <button className="ghost" style={{ marginLeft: 12, padding: '4px 10px' }} onClick={() => setError(null)}>
            dismiss
          </button>
        </div>
      )}

      {screen === 'upload' && (
        <Upload
          onJobReady={(j) => { setJob(j); setScreen('topics'); }}
          onError={showError}
        />
      )}

      {screen === 'topics' && job && (
        <Topics
          job={job}
          onPickTopic={(t) => { setTopic(t); setScreen('suggestions'); }}
          onError={showError}
        />
      )}

      {screen === 'suggestions' && job && topic && (
        <Suggestions
          job={job}
          topic={topic}
          onBuildQueued={() => setScreen('building')}
          onBack={() => setScreen('topics')}
          onError={showError}
        />
      )}

      {screen === 'building' && job && (
        <Building
          jobId={job.job_id}
          onAllDone={() => setScreen('done')}
          onBackToTopics={() => setScreen('topics')}
        />
      )}

      {screen === 'done' && job && (
        <Done
          jobId={job.job_id}
          onError={showError}
          onRestart={() => { setJob(null); setTopic(null); setError(null); setScreen('upload'); }}
        />
      )}
    </div>
  );
}
