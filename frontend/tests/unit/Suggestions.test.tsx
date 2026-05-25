import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Suggestions } from '../../src/pages/Suggestions';

afterEach(() => vi.restoreAllMocks());

const job = { job_id: 'j_1' } as any;
const topic = { id: 't_1', title: 'Binary search' };

const mockSuggestions = [
  {
    id: 's_1',
    title: 'Animated Array Walk',
    approach: 'Step through an array one element at a time',
    complexity: 'low',
    beginner_benefit: 'See each comparison visually',
    intermediate_benefit: 'Trace the exact algorithm steps',
  },
  {
    id: 's_2',
    title: 'Bisection Highlight',
    approach: 'Highlight the narrowing search window',
    complexity: 'medium',
    beginner_benefit: 'Understand divide and conquer',
    intermediate_benefit: 'Grasp O(log n) intuitively',
  },
];

describe('Suggestions', () => {
  it('on mount POSTs /suggestions and renders returned items', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/jobs/j_1/topics/t_1/suggestions') {
          return new Response(
            JSON.stringify({ suggestions: mockSuggestions }),
            { status: 200 },
          );
        }
        return new Response('not found', { status: 404 });
      }),
    );

    render(
      <Suggestions
        job={job}
        topic={topic}
        onBuildQueued={vi.fn()}
        onBack={vi.fn()}
        onError={vi.fn()}
      />,
    );

    // Loading spinner initially
    expect(screen.getByText(/generating five distinct viz approaches/i)).toBeInTheDocument();

    // After fetch resolves, suggestion titles should appear
    await waitFor(() =>
      expect(screen.getByText('Animated Array Walk')).toBeInTheDocument(),
    );
    expect(screen.getByText('Bisection Highlight')).toBeInTheDocument();

    // fetch should have been called with POST to the suggestions endpoint
    const fetchSpy = vi.mocked(fetch);
    const sugCall = fetchSpy.mock.calls.find(
      ([url]) => url === '/jobs/j_1/topics/t_1/suggestions',
    );
    expect(sugCall).toBeDefined();
    expect(sugCall![1]).toMatchObject({ method: 'POST' });
  });

  it('selecting a suggestion enables the Build button', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({ suggestions: mockSuggestions }),
          { status: 200 },
        ),
      ),
    );

    render(
      <Suggestions
        job={job}
        topic={topic}
        onBuildQueued={vi.fn()}
        onBack={vi.fn()}
        onError={vi.fn()}
      />,
    );

    // Wait for suggestions to render
    await waitFor(() =>
      expect(screen.getByText('Animated Array Walk')).toBeInTheDocument(),
    );

    // Build button is disabled before a selection
    const buildBtn = screen.getByRole('button', { name: /build this viz/i });
    expect(buildBtn).toBeDisabled();

    // Click the first suggestion card
    fireEvent.click(screen.getByText('Animated Array Walk'));

    // Build button should now be enabled
    expect(buildBtn).not.toBeDisabled();
  });

  it('clicking Build POSTs /build with { suggestion_id, custom_notes } and calls onBuildQueued', async () => {
    const onBuildQueued = vi.fn();
    let callCount = 0;

    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, _init?: RequestInit) => {
        if (url === '/jobs/j_1/topics/t_1/suggestions') {
          return new Response(
            JSON.stringify({ suggestions: mockSuggestions }),
            { status: 200 },
          );
        }
        if (url === '/jobs/j_1/topics/t_1/build') {
          callCount++;
          return new Response(
            JSON.stringify({ job_id: 'j_1', build_id: 'b_1', phase: 'queued' }),
            { status: 200 },
          );
        }
        return new Response('not found', { status: 404 });
      }),
    );

    render(
      <Suggestions
        job={job}
        topic={topic}
        onBuildQueued={onBuildQueued}
        onBack={vi.fn()}
        onError={vi.fn()}
      />,
    );

    // Wait for suggestions
    await waitFor(() =>
      expect(screen.getByText('Animated Array Walk')).toBeInTheDocument(),
    );

    // Select first suggestion
    fireEvent.click(screen.getByText('Animated Array Walk'));

    // Click Build
    fireEvent.click(screen.getByRole('button', { name: /build this viz/i }));

    // Wait for onBuildQueued to be called
    await waitFor(() => expect(onBuildQueued).toHaveBeenCalledTimes(1));

    // Verify onBuildQueued was called with job.job_id
    expect(onBuildQueued).toHaveBeenCalledWith('j_1');

    // Verify the build POST body
    const fetchSpy = vi.mocked(fetch);
    const buildCall = fetchSpy.mock.calls.find(
      ([url]) => url === '/jobs/j_1/topics/t_1/build',
    );
    expect(buildCall).toBeDefined();
    const body = JSON.parse(buildCall![1]?.body as string);
    expect(body).toEqual({ suggestion_id: 's_1', custom_notes: '' });
  });
});
