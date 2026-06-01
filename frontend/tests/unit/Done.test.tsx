import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Done } from '../../src/pages/Done';

afterEach(() => vi.restoreAllMocks());

const MANIFEST_RESPONSE = {
  job_id: 'j1',
  ready: true,
  manifest: [
    {
      section: '01 — intro',
      topic: 'Binary search',
      why_visual_helps: 'Animations clarify the pointer movement.',
      viz_title: 'Binary Search Visualizer',
      viz_brief: 'Step-through visualization of binary search.',
      embed_after_sentence: 'This algorithm runs in O(log n).',
      project_dir: '/tmp/viz/binary_search',
      screenshot_path: '/abs/path/to/screenshot.png',
      github_repo_url: 'https://github.com/org/binary_search',
      status: 'ok',
    },
  ],
  token_usage: {
    total_tokens: 4200,
    estimated_cost_usd: '0.0042',
  },
};

const JOB_RESPONSE = {
  job_id: 'j1',
  script_name: 'test.md',
  track: 'Academy DSA',
  status: 'done',
  error: '',
  builds: {
    topic_1: {
      id: 'build_1',
      topic_id: 'topic_1',
      short_topic: 'Binary search',
      final_viz_brief: '',
      custom_notes: '',
      phase: 'done',
      project_dir: '/tmp/viz/binary_search',
      screenshot_path: '/abs/path/to/screenshot.png',
      error: '',
      github_status: 'published',
      github_repo_url: 'https://github.com/org/binary_search',
      github_clone_url: '',
      github_repo_name: 'binary_search',
      github_commit_sha: 'abc123',
      github_error: '',
    },
  },
};

function makeFetch(manifestResp: unknown, jobResp: unknown) {
  return vi.fn(async (url: string) => {
    if (String(url).includes('/manifest')) {
      return new Response(JSON.stringify(manifestResp), { status: 200 });
    }
    return new Response(JSON.stringify(jobResp), { status: 200 });
  });
}

describe('Done', () => {
  it('parallel-loads manifest + job and renders one card per manifest entry with correct preview img src', async () => {
    vi.stubGlobal('fetch', makeFetch(MANIFEST_RESPONSE, JOB_RESPONSE));

    render(<Done jobId="j1" onError={vi.fn()} onRestart={vi.fn()} />);

    // Initially shows loading state
    expect(screen.getByText(/loading manifest/i)).toBeInTheDocument();

    // After data loads, renders the entry
    await waitFor(() =>
      expect(screen.getByText('Binary search')).toBeInTheDocument(),
    );

    // Check the img src uses encodeURIComponent
    const img = screen.getByRole('img', { name: 'Binary search' }) as HTMLImageElement;
    expect(img.src).toContain(
      `/preview?path=${encodeURIComponent('/abs/path/to/screenshot.png')}`,
    );

    // Check manifest entry details are rendered
    expect(screen.getByText('01 — intro')).toBeInTheDocument();
    expect(screen.getByText('Animations clarify the pointer movement.')).toBeInTheDocument();
  });

  it('"Start a new job" button calls onRestart', async () => {
    vi.stubGlobal('fetch', makeFetch(MANIFEST_RESPONSE, JOB_RESPONSE));
    const onRestart = vi.fn();

    render(<Done jobId="j1" onError={vi.fn()} onRestart={onRestart} />);

    await waitFor(() =>
      expect(screen.getByText('Binary search')).toBeInTheDocument(),
    );

    const btn = screen.getByRole('button', { name: /start a new job/i });
    await userEvent.click(btn);
    expect(onRestart).toHaveBeenCalledTimes(1);
  });

  it('calls onError when a fetch fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('boom', { status: 500, statusText: 'Internal Server Error' })),
    );
    const onError = vi.fn();

    render(<Done jobId="j1" onError={onError} onRestart={vi.fn()} />);

    await waitFor(() => expect(onError).toHaveBeenCalled());
  });
});
