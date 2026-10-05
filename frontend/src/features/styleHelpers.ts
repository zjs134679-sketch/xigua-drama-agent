/** 项目画风解析：全站统一以 style_bible 为准。 */
import type { ArtStyle, Project } from "../api/client";

export function projectStyleId(project: Project | null | undefined): number | undefined {
  const id = project?.style_bible?.art_style_id;
  return typeof id === "number" ? id : undefined;
}

export function projectStyleName(project: Project | null | undefined): string {
  return (
    project?.style_bible?.visual_name?.trim()
    || project?.style?.trim()
    || ""
  );
}

/** 下拉第一项文案：跟随项目画风 */
export function followProjectStyleLabel(project: Project | null | undefined): string {
  const name = projectStyleName(project);
  return name ? `跟随项目：${name}` : "跟随项目（未设置，将用库内默认）";
}

export function resolveStyleName(
  styles: ArtStyle[],
  styleId: number | undefined,
  project: Project | null | undefined,
): string {
  if (styleId != null) {
    const hit = styles.find((s) => s.id === styleId);
    if (hit) return hit.name;
  }
  return projectStyleName(project) || "库内默认";
}
