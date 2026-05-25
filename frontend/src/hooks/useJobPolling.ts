// frontend/src/hooks/useJobPolling.ts
import { useEffect, useState } from 'react';
import { api, type JobState } from '../api/client';

export function useJobPolling(jobId: string | null, intervalMs = 1000, stop = false) {
  const [job, setJob] = useState<JobState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId || stop) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const next = await api<JobState>(`/jobs/${jobId}`);
        if (cancelled) return;
        setJob(next);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) timer = setTimeout(tick, intervalMs);
      }
    };

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, intervalMs, stop]);

  return { job, error };
}
