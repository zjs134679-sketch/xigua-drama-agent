import { useCallback, useRef, useState } from "react";

export type BatchProgress = { done: number; total: number; failed: number; currentId: number | null };
export type BatchOutcome = { ok: boolean; message?: string; banned?: boolean };
export type BatchSummary = {
  total: number;
  done: number;
  failed: number;
  banned: boolean;
  stopped: boolean;
  lastMessage?: string;
};

/**
 * 顺序批量执行（本地 ComfyUI 单卡，串行即最优；并发只会在 ComfyUI 排队）。
 * 单项失败不会中断整批；命中封号则立即停止；用户可随时「停止」。
 */
export function useBatchRun() {
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<BatchProgress | null>(null);
  const stopRef = useRef(false);

  const stop = useCallback(() => {
    stopRef.current = true;
  }, []);

  const run = useCallback(
    async (
      ids: number[],
      generateOne: (id: number) => Promise<BatchOutcome>,
    ): Promise<BatchSummary | null> => {
      if (!ids.length) return null;
      stopRef.current = false;
      setRunning(true);
      const total = ids.length;
      let done = 0;
      let failed = 0;
      let banned = false;
      let stopped = false;
      let lastMessage: string | undefined;
      setProgress({ done, total, failed, currentId: null });
      try {
        for (const id of ids) {
          if (stopRef.current) {
            stopped = true;
            break;
          }
          setProgress({ done, total, failed, currentId: id });
          let outcome: BatchOutcome;
          try {
            outcome = await generateOne(id);
          } catch (e) {
            outcome = { ok: false, message: e instanceof Error ? e.message : "生成失败" };
          }
          if (!outcome.ok) {
            failed += 1;
            if (outcome.message) lastMessage = outcome.message;
          }
          done += 1;
          setProgress({ done, total, failed, currentId: null });
          if (outcome.banned) {
            banned = true;
            break;
          }
        }
      } finally {
        setRunning(false);
        setProgress(null);
      }
      return { total, done, failed, banned, stopped, lastMessage };
    },
    [],
  );

  return { running, progress, run, stop };
}
