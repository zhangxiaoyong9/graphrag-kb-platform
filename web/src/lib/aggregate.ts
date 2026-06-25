/** Cross-KB aggregation helpers — use ONLY existing per-KB endpoints (no backend change). */
import { listKbs, listJobsByKb, getKbCost, listDocuments } from "../api/client";
import type { DocumentOut } from "../api/types";

export interface AllJobRow {
  kbId: number;
  kbName: string;
  id: number;
  status: string;
}

/** Every job across every KB, newest first. Survives per-KB fetch failures. */
export async function loadAllJobs(): Promise<AllJobRow[]> {
  const kbs = await listKbs();
  const perKb = await Promise.all(
    kbs.map(async (k) => {
      try {
        const jobs = await listJobsByKb(k.id);
        return jobs.map((j) => ({ kbId: k.id, kbName: k.name, id: j.id, status: j.status }));
      } catch {
        return [];
      }
    }),
  );
  return perKb.flat().sort((a, b) => b.id - a.id);
}

export interface KbCostRow {
  id: number;
  name: string;
  totalUsd: number | null;
}

export interface AllCost {
  totalUsd: number | null;
  kbs: KbCostRow[];
}

/** Sum cumulative cost across KBs. Unknown (null) latches the total to null. */
export async function loadAllCost(): Promise<AllCost> {
  const kbs = await listKbs();
  const rows: KbCostRow[] = [];
  let total: number | null = 0;
  let anyNull = false;
  for (const k of kbs) {
    try {
      const c = await getKbCost(k.id);
      rows.push({ id: k.id, name: k.name, totalUsd: c.total_usd });
      if (c.total_usd == null) anyNull = true;
      else total = (total ?? 0) + c.total_usd;
    } catch {
      rows.push({ id: k.id, name: k.name, totalUsd: null });
    }
  }
  return { totalUsd: anyNull ? null : total, kbs: rows.sort((a, b) => (b.totalUsd ?? 0) - (a.totalUsd ?? 0)) };
}

export interface KbDocsRow {
  id: number;
  name: string;
  method: string;
  docs: DocumentOut[];
}

export interface AllDocuments {
  totalDocs: number;
  totalChunks: number;
  totalBytes: number;
  kbs: KbDocsRow[];
}

/** Every document across every KB. Survives per-KB fetch failures. */
export async function loadAllDocuments(): Promise<AllDocuments> {
  const kbs = await listKbs();
  const rows: KbDocsRow[] = [];
  let totalDocs = 0;
  let totalChunks = 0;
  let totalBytes = 0;
  for (const k of kbs) {
    try {
      const docs = await listDocuments(k.id);
      rows.push({ id: k.id, name: k.name, method: k.method, docs });
      totalDocs += docs.length;
      totalChunks += docs.reduce((s, d) => s + d.chunk_count, 0);
      totalBytes += docs.reduce((s, d) => s + d.bytes, 0);
    } catch {
      rows.push({ id: k.id, name: k.name, method: k.method, docs: [] });
    }
  }
  return { totalDocs, totalChunks, totalBytes, kbs: rows };
}
