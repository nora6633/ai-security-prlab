import { readFile } from "node:fs/promises";
import path from "node:path";
import { findLab } from "../../../../../lib/labs";

export const dynamic = "force-dynamic";

export async function GET(_request, { params }) {
  const { labId } = await params;
  try {
    const lab = await findLab(labId);
    if (!lab) return Response.json({ detail: "找不到 Lab" }, { status: 404 });
    const ids = lab.config.detection_rule_ids || [];
    if (!Array.isArray(ids) || ids.some((id) => typeof id !== "string")) throw new Error("無效的規則設定");
    if (!ids.length) return Response.json({ rules: [] });
    const contents = await readFile(path.join(lab.directory, "alerts", "rules_export.ndjson"), "utf8");
    const rows = contents.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
    const rules = ids.map((id) => {
      const rule = rows.find((item) => item.rule_id === id);
      if (!rule || rule.language !== "kuery" || typeof rule.query !== "string") throw new Error(`找不到 KQL 規則：${id}`);
      const phrases = [...rule.query.matchAll(/\b[\w.]+\s*:\s*"((?:\\.|[^"\\])*)"/g)]
        .map((match) => match[1].replace(/\\(["\\])/g, "$1"));
      return { id, name: rule.name, description: rule.description, keywords: [...new Set(phrases)], index: rule.index };
    });
    return Response.json({ rules }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ detail: "無法讀取本關偵測規則。" }, { status: 503 });
  }
}
