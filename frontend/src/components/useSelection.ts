import { useCallback, useState } from "react";

/** 多选：维护一组被选中的素材 id（全选 / 未生成 / 反选 / 取消）。 */
export function useSelection() {
  const [selected, setSelected] = useState<Set<number>>(() => new Set());

  const toggle = useCallback((id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const replace = useCallback((ids: number[]) => setSelected(new Set(ids)), []);
  const clear = useCallback(() => setSelected(new Set()), []);
  const invert = useCallback(
    (allIds: number[]) => setSelected((prev) => new Set(allIds.filter((id) => !prev.has(id)))),
    [],
  );

  return { selected, toggle, replace, clear, invert };
}
