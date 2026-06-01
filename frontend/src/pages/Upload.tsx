import { useState } from 'react';
import { api } from '../api/client';
import type { JobState } from '../api/client';

const TRACKS = [
  'Academy DSA', 'Academy Fullstack', 'Academy Backend',
  'DSML DA', 'DSML DS', 'AIML', 'DevOps',
];

type Props = {
  onJobReady: (job: JobState) => void;
  onError: (message: string) => void;
};

export function Upload({ onJobReady, onError }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [track, setTrack] = useState('AIML');
  const [module, setModule] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!file) {
      onError('Pick a HackMD .md file first.');
      return;
    }
    const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
    if (ext !== 'md' && ext !== 'markdown') {
      onError('Only .md / .markdown files are accepted.');
      return;
    }
    if (!module.trim()) {
      onError('Enter a module name.');
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('track', track);
      fd.append('module', module.trim());
      fd.append('file', file);
      const res = await fetch('/upload', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(await res.text());
      const uploadRes = (await res.json()) as { job_id: string };
      const job = await api<JobState>(`/jobs/${uploadRes.job_id}`);
      onJobReady(job);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="kicker">Step 01 — input</div>
      <h1>Find the visual moments<br />in a lecture script.</h1>
      <p className="lead">
        Upload a HackMD markdown file. A topic-extraction agent reads it and surfaces
        the 3–7 sections where an interactive visualization would actually help a
        beginner learner — anchored to the exact sentence after which the viz should
        embed.
      </p>

      <div className="panel accent" style={{ marginTop: 36 }}>
        <div className="grid-2">
          <div>
            <label htmlFor="upload-track">Track</label>
            <select
              id="upload-track"
              value={track}
              onChange={(e) => setTrack(e.target.value)}
            >
              {TRACKS.map((t) => <option key={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="upload-module">Module</label>
            <input
              id="upload-module"
              type="text"
              placeholder="e.g. convolutional-neural-nets"
              value={module}
              onChange={(e) => setModule(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="upload-file">HackMD file</label>
            <input
              id="upload-file"
              type="file"
              accept=".md,.markdown"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            {file && (
              <div className="tiny" style={{ marginTop: 6 }}>
                {file.name} · {Math.round(file.size / 1024)} KB
              </div>
            )}
          </div>
        </div>
        <div className="row-actions">
          <button className="primary" onClick={submit} disabled={busy || !file}>
            {busy ? <><span className="spinner" /> Extracting topics…</> : 'Analyze script'}
          </button>
        </div>
      </div>
    </div>
  );
}
