// frontend/tests/unit/useJobPolling.test.tsx
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useJobPolling } from '../../src/hooks/useJobPolling';

afterEach(() => vi.restoreAllMocks());

describe('useJobPolling', () => {
  it('returns null initially then polled job', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ job_id: 'j', status: 'building' }), { status: 200 }),
    ));
    const { result } = renderHook(() => useJobPolling('j', 50));
    expect(result.current.job).toBeNull();
    await waitFor(() => expect(result.current.job?.status).toBe('building'));
  });

  it('stops polling when stop=true', async () => {
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ job_id: 'j', status: 'done' }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);
    const { rerender } = renderHook(({ stop }) => useJobPolling('j', 30, stop), {
      initialProps: { stop: false },
    });
    await new Promise((r) => setTimeout(r, 100));
    const callsBefore = fetchSpy.mock.calls.length;
    rerender({ stop: true });
    await new Promise((r) => setTimeout(r, 100));
    expect(fetchSpy.mock.calls.length - callsBefore).toBeLessThanOrEqual(1);
  });

  it('surfaces fetch errors', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('nope', { status: 500, statusText: 'Internal Server Error' }),
    ));
    const { result } = renderHook(() => useJobPolling('j', 30));
    await waitFor(() => expect(result.current.error).toMatch(/500/));
  });
});
