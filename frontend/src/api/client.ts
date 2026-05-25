import type { components } from './types.gen';

export type JobState = components['schemas']['JobState'];
export type JobSummary = components['schemas']['JobSummary'];
export type UploadResponse = components['schemas']['UploadResponse'];

export async function api<T = unknown>(
  path: string,
  opts: RequestInit = {},
): Promise<T> {
  const r = await fetch(path, {
    ...opts,
    headers: { Accept: 'application/json', ...(opts.headers || {}) },
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${r.statusText} — ${body.slice(0, 200)}`);
  }
  return (await r.json()) as T;
}

export async function upload(file: File): Promise<UploadResponse> {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch('/upload', { method: 'POST', body: fd });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${r.statusText} — ${body.slice(0, 200)}`);
  }
  return (await r.json()) as UploadResponse;
}
