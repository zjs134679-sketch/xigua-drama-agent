import {
  type CSSProperties,
  type ReactNode,
  type RefObject,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  forwardRef,
} from "react";

export type CanvasPoint = { x: number; y: number };

export type InfiniteCanvasHandle = {
  fitView: (bounds?: { minX: number; minY: number; maxX: number; maxY: number }, padding?: number) => void;
  setZoom: (zoom: number) => void;
  zoomBy: (factor: number) => void;
  resetView: () => void;
  getTransform: () => { x: number; y: number; zoom: number };
  screenToWorld: (clientX: number, clientY: number) => CanvasPoint;
  /** 不改缩放，把世界坐标点平移到视口中心 */
  panToWorld: (worldX: number, worldY: number) => void;
  /**
   * 不改缩放：仅当矩形在视口外时做最小平移，让其进入可视区。
   * 已在可视区内则完全不动 —— 用于点选镜头，避免 fitView 乱跳。
   */
  ensureVisible: (
    bounds: { minX: number; minY: number; maxX: number; maxY: number },
    margin?: number,
  ) => void;
};

type Props = {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  minZoom?: number;
  maxZoom?: number;
  /** Called when pan/zoom changes (throttled by rAF internally via state). */
  onTransformChange?: (t: { x: number; y: number; zoom: number }) => void;
  /** When true, space is held externally for pan mode. */
  spacePanning?: boolean;
};

const DEFAULT_ZOOM = 0.85;

export const InfiniteCanvas = forwardRef<InfiniteCanvasHandle, Props>(function InfiniteCanvas(
  {
    children,
    className,
    style,
    minZoom = 0.25,
    maxZoom = 2.5,
    onTransformChange,
    spacePanning = false,
  },
  ref,
) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [tx, setTx] = useState(40);
  const [ty, setTy] = useState(40);
  const [zoom, setZoomState] = useState(DEFAULT_ZOOM);
  const dragRef = useRef<{
    mode: "pan" | null;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const spaceRef = useRef(false);
  const transformRef = useRef({ x: 40, y: 40, zoom: DEFAULT_ZOOM });

  useEffect(() => {
    transformRef.current = { x: tx, y: ty, zoom };
    onTransformChange?.(transformRef.current);
  }, [tx, ty, zoom, onTransformChange]);

  useEffect(() => {
    spaceRef.current = spacePanning;
  }, [spacePanning]);

  const clampZoom = useCallback((z: number) => Math.min(maxZoom, Math.max(minZoom, z)), [minZoom, maxZoom]);

  const screenToWorld = useCallback((clientX: number, clientY: number): CanvasPoint => {
    const el = viewportRef.current;
    if (!el) return { x: 0, y: 0 };
    const rect = el.getBoundingClientRect();
    const { x, y, zoom: z } = transformRef.current;
    return {
      x: (clientX - rect.left - x) / z,
      y: (clientY - rect.top - y) / z,
    };
  }, []);

  const fitView = useCallback(
    (bounds?: { minX: number; minY: number; maxX: number; maxY: number }, padding = 64) => {
      const el = viewportRef.current;
      if (!el || !bounds) {
        setTx(40);
        setTy(40);
        setZoomState(DEFAULT_ZOOM);
        return;
      }
      const rect = el.getBoundingClientRect();
      const w = Math.max(bounds.maxX - bounds.minX, 1);
      const h = Math.max(bounds.maxY - bounds.minY, 1);
      const z = clampZoom(Math.min((rect.width - padding * 2) / w, (rect.height - padding * 2) / h));
      const cx = (bounds.minX + bounds.maxX) / 2;
      const cy = (bounds.minY + bounds.maxY) / 2;
      setZoomState(z);
      setTx(rect.width / 2 - cx * z);
      setTy(rect.height / 2 - cy * z);
    },
    [clampZoom],
  );

  const panToWorld = useCallback((worldX: number, worldY: number) => {
    const el = viewportRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const z = transformRef.current.zoom;
    setTx(rect.width / 2 - worldX * z);
    setTy(rect.height / 2 - worldY * z);
  }, []);

  const ensureVisible = useCallback(
    (bounds: { minX: number; minY: number; maxX: number; maxY: number }, margin = 48) => {
      const el = viewportRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const { x: tx0, y: ty0, zoom: z } = transformRef.current;
      // world → screen
      const left = bounds.minX * z + tx0;
      const right = bounds.maxX * z + tx0;
      const top = bounds.minY * z + ty0;
      const bottom = bounds.maxY * z + ty0;
      let dx = 0;
      let dy = 0;
      if (left < margin) dx = margin - left;
      else if (right > rect.width - margin) dx = rect.width - margin - right;
      if (top < margin) dy = margin - top;
      else if (bottom > rect.height - margin) dy = rect.height - margin - bottom;
      // 目标过大时居中（少见）
      if (right - left > rect.width - margin * 2) {
        const cx = ((bounds.minX + bounds.maxX) / 2) * z + tx0;
        dx = rect.width / 2 - cx;
      }
      if (bottom - top > rect.height - margin * 2) {
        const cy = ((bounds.minY + bounds.maxY) / 2) * z + ty0;
        dy = rect.height / 2 - cy;
      }
      if (dx !== 0 || dy !== 0) {
        setTx(tx0 + dx);
        setTy(ty0 + dy);
      }
    },
    [],
  );

  useImperativeHandle(
    ref,
    () => ({
      fitView,
      setZoom: (z: number) => setZoomState(clampZoom(z)),
      zoomBy: (factor: number) => {
        const el = viewportRef.current;
        if (!el) {
          setZoomState((z) => clampZoom(z * factor));
          return;
        }
        const rect = el.getBoundingClientRect();
        const cx = rect.width / 2;
        const cy = rect.height / 2;
        setZoomState((prev) => {
          const next = clampZoom(prev * factor);
          const ratio = next / prev;
          setTx((x) => cx - (cx - x) * ratio);
          setTy((y) => cy - (cy - y) * ratio);
          return next;
        });
      },
      resetView: () => {
        setTx(40);
        setTy(40);
        setZoomState(DEFAULT_ZOOM);
      },
      getTransform: () => transformRef.current,
      screenToWorld,
      panToWorld,
      ensureVisible,
    }),
    [clampZoom, fitView, screenToWorld, panToWorld, ensureVisible],
  );

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const factor = e.deltaY > 0 ? 0.92 : 1.08;
      setZoomState((prev) => {
        const next = clampZoom(prev * factor);
        const ratio = next / prev;
        setTx((x) => mx - (mx - x) * ratio);
        setTy((y) => my - (my - y) * ratio);
        return next;
      });
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [clampZoom]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat) {
        const tag = (e.target as HTMLElement)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || (e.target as HTMLElement)?.isContentEditable) return;
        e.preventDefault();
        spaceRef.current = true;
        if (viewportRef.current) viewportRef.current.dataset.panning = "1";
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        spaceRef.current = false;
        if (viewportRef.current) delete viewportRef.current.dataset.panning;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    const panWithMiddle = e.button === 1;
    const panWithSpace = e.button === 0 && (spaceRef.current || spacePanning);
    const panWithBlank = e.button === 0 && (e.target as HTMLElement).dataset?.canvasBg === "1";
    if (!panWithMiddle && !panWithSpace && !panWithBlank) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = {
      mode: "pan",
      startX: e.clientX,
      startY: e.clientY,
      originX: transformRef.current.x,
      originY: transformRef.current.y,
    };
    if (viewportRef.current) viewportRef.current.dataset.panning = "1";
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag || drag.mode !== "pan") return;
    setTx(drag.originX + (e.clientX - drag.startX));
    setTy(drag.originY + (e.clientY - drag.startY));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (dragRef.current) {
      dragRef.current = null;
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
    }
    if (!spaceRef.current && viewportRef.current) delete viewportRef.current.dataset.panning;
  };

  const gridSize = 24 * zoom;
  const gridStyle: CSSProperties = {
    backgroundImage: `radial-gradient(circle, rgba(255,255,255,0.09) 1px, transparent 1px)`,
    backgroundSize: `${gridSize}px ${gridSize}px`,
    backgroundPosition: `${tx % gridSize}px ${ty % gridSize}px`,
  };

  return (
    <div
      ref={viewportRef}
      className={`infinite-canvas${className ? ` ${className}` : ""}`}
      style={style}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="infinite-canvas-grid" data-canvas-bg="1" style={gridStyle} />
      <div
        className="infinite-canvas-world"
        style={{
          transform: `translate(${tx}px, ${ty}px) scale(${zoom})`,
          transformOrigin: "0 0",
        }}
      >
        {children}
      </div>
    </div>
  );
});

export type NodeDragHandlers = {
  onPointerDown: (e: React.PointerEvent) => void;
};

/** Attach to a canvas node to enable drag in world space. */
export function useCanvasNodeDrag(
  id: string | number,
  position: CanvasPoint,
  zoomRef: RefObject<number>,
  onMove: (id: string | number, next: CanvasPoint) => void,
  onMoveEnd?: (id: string | number, next: CanvasPoint) => void,
): NodeDragHandlers {
  const drag = useRef<{
    id: string | number;
    startClientX: number;
    startClientY: number;
    originX: number;
    originY: number;
  } | null>(null);

  return {
    onPointerDown: (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      const target = e.target as HTMLElement;
      if (target.closest("button, input, textarea, select, a, label, [data-no-drag]")) return;
      e.stopPropagation();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      drag.current = {
        id,
        startClientX: e.clientX,
        startClientY: e.clientY,
        originX: position.x,
        originY: position.y,
      };

      const onMoveWin = (ev: PointerEvent) => {
        if (!drag.current) return;
        const z = zoomRef.current || 1;
        const nx = drag.current.originX + (ev.clientX - drag.current.startClientX) / z;
        const ny = drag.current.originY + (ev.clientY - drag.current.startClientY) / z;
        onMove(drag.current.id, { x: nx, y: ny });
      };
      const onUpWin = (ev: PointerEvent) => {
        window.removeEventListener("pointermove", onMoveWin);
        window.removeEventListener("pointerup", onUpWin);
        if (!drag.current) return;
        const z = zoomRef.current || 1;
        const nx = drag.current.originX + (ev.clientX - drag.current.startClientX) / z;
        const ny = drag.current.originY + (ev.clientY - drag.current.startClientY) / z;
        onMoveEnd?.(drag.current.id, { x: nx, y: ny });
        drag.current = null;
      };
      window.addEventListener("pointermove", onMoveWin);
      window.addEventListener("pointerup", onUpWin);
    },
  };
}
