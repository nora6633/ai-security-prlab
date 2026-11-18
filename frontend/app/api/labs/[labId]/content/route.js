import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

export const dynamic = "force-dynamic";

export async function GET(_request, { params }) {
  const { labId } = await params;
  if (!/^Lab\d+$/.test(labId)) {
    return Response.json({ detail: "Invalid lab id" }, { status: 400 });
  }

  try {
    const labsPath = path.resolve(process.cwd(), "..", "Lab");
    const entries = await readdir(labsPath, { withFileTypes: true });
    const folder = entries.find((entry) => entry.isDirectory() && (entry.name === labId || entry.name.startsWith(`${labId}-`)));
    if (!folder) return Response.json({ detail: "Lab not found" }, { status: 404 });
    const markdown = await readFile(path.join(labsPath, folder.name, "introduction.md"), "utf8");
    return new Response(markdown, {
      headers: { "Content-Type": "text/markdown; charset=utf-8", "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ detail: "Lab Markdown 內容目前無法讀取" }, { status: 404 });
  }
}
