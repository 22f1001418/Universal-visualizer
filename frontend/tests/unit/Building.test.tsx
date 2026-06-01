import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Building } from '../../src/pages/Building';

afterEach(() => vi.restoreAllMocks());

/** Minimal fixture that satisfies the polled JobState shape */
function makeJob(overrides: Record<string, unknown> = {}) {
  return {
    job_id: 'j_1',
    script_name: 'test.md',
    track: 'Academy DSA',
    status: 'building',
    error: '',
    topics: [
      { id: 't_1', topic: 'Binary search', section: 'Searching', difficulty: 'beginner', why: '', quote: '' },
      { id: 't_2', topic: 'Hash maps',     section: 'Data Structures', difficulty: 'intermediate', why: '', quote: '' },
    ],
    builds: {
      t_1: { id: 'b_1', topic_id: 't_1', phase: 'draft',    progress_log: [], error: null },
      t_2: { id: 'b_2', topic_id: 't_2', phase: 'validate', progress_log: [], error: null },
    },
    logs: ['log line 1', 'log line 2', 'log line 3'],
    ...overrides,
  };
}

describe('Building', () => {
  it('renders a BuildCard for each build in the polled job', async () => {
    const job = makeJob();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify(job), { status: 200 }),
      ),
    );

    render(
      <Building jobId="j_1" onAllDone={vi.fn()} onBackToTopics={vi.fn()} pollIntervalMs={50} />,
    );

    // wait until polling resolves and BuildCards appear
    await waitFor(() =>
      expect(screen.getByText('Binary search')).toBeInTheDocument(),
    );
    expect(screen.getByText('Hash maps')).toBeInTheDocument();
  });

  it('renders log lines from job.logs', async () => {
    const job = makeJob();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify(job), { status: 200 }),
      ),
    );

    render(
      <Building jobId="j_1" onAllDone={vi.fn()} onBackToTopics={vi.fn()} pollIntervalMs={50} />,
    );

    await waitFor(() =>
      expect(screen.getByText('log line 1')).toBeInTheDocument(),
    );
    expect(screen.getByText('log line 2')).toBeInTheDocument();
    expect(screen.getByText('log line 3')).toBeInTheDocument();
  });

  it('calls onAllDone(jobId) exactly once when every build reaches a terminal phase', async () => {
    const onAllDone = vi.fn();

    // First response: still building
    // Second and subsequent: all done
    let callCount = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        callCount++;
        const phase = callCount === 1 ? 'draft' : 'done';
        const job = makeJob({
          builds: {
            t_1: { id: 'b_1', topic_id: 't_1', phase, progress_log: [], error: null },
            t_2: { id: 'b_2', topic_id: 't_2', phase: 'failed',    progress_log: [], error: null },
          },
        });
        return new Response(JSON.stringify(job), { status: 200 });
      }),
    );

    render(
      <Building jobId="j_1" onAllDone={onAllDone} onBackToTopics={vi.fn()} pollIntervalMs={50} />,
    );

    // wait until onAllDone fires (allow up to 2s for two poll cycles)
    await waitFor(() => expect(onAllDone).toHaveBeenCalledTimes(1), { timeout: 2000 });

    // extra polling rounds must NOT re-fire it
    await new Promise((r) => setTimeout(r, 120));
    expect(onAllDone).toHaveBeenCalledTimes(1);
    expect(onAllDone).toHaveBeenCalledWith('j_1');
  });
});
