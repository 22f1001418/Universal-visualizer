import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Topics } from '../../src/pages/Topics';

afterEach(() => vi.restoreAllMocks());

describe('Topics', () => {
  it('renders one card per topic', () => {
    const job = {
      job_id: 'j',
      status: 'topics_extracted',
      topics: [
        { id: 't1', topic: 'Binary search', section: 'Algorithms', embed_after_sentence: 'foo', why_visual_helps: 'bar', audience_difficulty: 'beginner', surrounding_context: '' },
        { id: 't2', topic: 'Hash maps', section: 'Data Structures', embed_after_sentence: 'baz', why_visual_helps: 'qux', audience_difficulty: 'intermediate', surrounding_context: '' },
      ],
    } as any;
    render(<Topics job={job} onPickTopic={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByText('Binary search')).toBeInTheDocument();
    expect(screen.getByText('Hash maps')).toBeInTheDocument();
  });

  it('fires onPickTopic with the full topic object when a card is clicked', () => {
    const onPick = vi.fn();
    const topic = { id: 't1', topic: 'Binary search', section: 'Algorithms', embed_after_sentence: 'foo', why_visual_helps: 'bar', audience_difficulty: 'beginner', surrounding_context: '' };
    const job = {
      job_id: 'j',
      status: 'topics_extracted',
      topics: [topic],
    } as any;
    render(<Topics job={job} onPickTopic={onPick} onError={vi.fn()} />);
    fireEvent.click(screen.getByText('Binary search'));
    // onPickTopic is called with { id, title } — title is mapped from t.topic
    expect(onPick).toHaveBeenCalledWith({ id: 't1', title: 'Binary search' });
  });

  it('shows a loading state while topics are not yet extracted', () => {
    // Stub fetch so polling doesn't blow up
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ job_id: 'j', status: 'uploaded', topics: [] }), { status: 200 }),
    ));
    const job = { job_id: 'j', status: 'uploaded', topics: [] } as any;
    render(<Topics job={job} onPickTopic={vi.fn()} onError={vi.fn()} />);
    // Loading text: "Extracting topics…" — matches the legacy upload button busy text
    expect(screen.getByText(/extracting topics/i)).toBeInTheDocument();
  });

  it('renders polled topics when polling completes', async () => {
    const onPick = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(
        JSON.stringify({
          job_id: 'j',
          status: 'topics_extracted',
          topics: [
            { id: 't1', topic: 'Binary search', section: 'Algorithms', embed_after_sentence: 'foo', why_visual_helps: 'bar', audience_difficulty: 'beginner', surrounding_context: '' },
          ],
        }),
        { status: 200 },
      ),
    ));
    const job = { job_id: 'j', status: 'uploaded', topics: [] } as any;
    render(<Topics job={job} onPickTopic={onPick} onError={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Binary search')).toBeInTheDocument());
  });
});
