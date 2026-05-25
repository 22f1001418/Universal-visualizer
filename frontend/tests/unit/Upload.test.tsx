import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Upload } from '../../src/pages/Upload';

afterEach(() => vi.restoreAllMocks());

describe('Upload', () => {
  it('rejects non-.md file with onError', async () => {
    const onError = vi.fn();
    const onJobReady = vi.fn();
    render(<Upload onJobReady={onJobReady} onError={onError} />);
    const file = new File(['hi'], 'a.txt', { type: 'text/plain' });
    const input = screen.getByLabelText(/upload/i) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: /upload/i }));
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(onJobReady).not.toHaveBeenCalled();
  });

  it('uploads .md and calls onJobReady with hydrated job', async () => {
    const onJobReady = vi.fn();
    const fetchSpy = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url === '/upload') {
        return new Response(JSON.stringify({ job_id: 'j_1' }), { status: 200 });
      }
      if (url === '/jobs/j_1') {
        return new Response(
          JSON.stringify({ job_id: 'j_1', status: 'topics_extracted', topics: [] }),
          { status: 200 },
        );
      }
      return new Response('?', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchSpy);
    render(<Upload onJobReady={onJobReady} onError={vi.fn()} />);
    const input = screen.getByLabelText(/upload/i) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['# hi'], 'a.md', { type: 'text/markdown' })] },
    });
    fireEvent.click(screen.getByRole('button', { name: /upload/i }));
    await waitFor(() => expect(onJobReady).toHaveBeenCalledTimes(1));
    expect(onJobReady.mock.calls[0][0].job_id).toBe('j_1');
  });
});
