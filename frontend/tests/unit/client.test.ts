// frontend/tests/unit/client.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, upload } from '../../src/api/client';

afterEach(() => vi.restoreAllMocks());

describe('api()', () => {
  it('parses JSON on 200', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    ));
    expect(await api<{ ok: boolean }>('/healthz')).toEqual({ ok: true });
  });

  it('throws "<status> <statusText> — <body>" on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('boom', { status: 500, statusText: 'Internal Server Error' }),
    ));
    await expect(api('/jobs/missing')).rejects.toThrow(
      /^500 Internal Server Error — boom/,
    );
  });

  it('truncates long error bodies to 200 chars', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('x'.repeat(500), { status: 400, statusText: 'Bad Request' }),
    ));
    await expect(api('/x')).rejects.toThrow(/x{200}$/);
  });

  it('sends Accept: application/json by default', async () => {
    const fetchSpy = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchSpy);
    await api('/jobs');
    const [, callInit] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(callInit.headers).toMatchObject({
      Accept: 'application/json',
    });
  });
});

describe('upload()', () => {
  it('POSTs FormData to /upload', async () => {
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ job_id: 'j_1' }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);
    const file = new File(['# hello'], 'a.md', { type: 'text/markdown' });
    const result = await upload(file);
    expect(result.job_id).toBe('j_1');
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/upload');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
  });
});
