import { useEffect, useState } from "react";
import { getJob } from "../api/client";
import type { JobOut } from "../api/types";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
export function useJobPolling(jobId: number | null) {
  const [job, setJob] = useState<JobOut | null>(null);
  useEffect(() => {
    if (jobId == null) return;
    let stop = false;
    const tick = async () => { const j = await getJob(jobId); if (!stop) setJob(j); return j; };
    tick();
    const h = setInterval(async () => { const j = await tick(); if (j && TERMINAL.has(j.status)) clearInterval(h); }, 2000);
    return () => { stop = true; clearInterval(h); };
  }, [jobId]);
  return job;
}
