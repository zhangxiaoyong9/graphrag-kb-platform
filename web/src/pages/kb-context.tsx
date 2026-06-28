import { createContext, useContext } from "react";
import type { KbDetail } from "../api/types";

/** KB workspace context: the loaded KB + a reload trigger, shared across tabs. */
export interface KbCtx {
  kbId: number;
  kb: KbDetail | null;
  reload: () => void;
}

export const KbContext = createContext<KbCtx | null>(null);

export function useKb(): KbCtx {
  const ctx = useContext(KbContext);
  if (!ctx) throw new Error("useKb must be used inside a KbLayout");
  return ctx;
}
