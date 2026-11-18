import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { parse } from "yaml";

export const dynamic = "force-dynamic";

export async function GET(request) {
  const labId = new URL(request.url).searchParams.get("lab") || "Lab01";
  if (!/^Lab\d{2,}$/.test(labId)) {
    return Response.json({ detail: "Invalid lab id" }, { status: 400 });
  }

  try {
    const labsPath = path.resolve(process.cwd(), "..", "Lab");
    const labFolders = await readdir(labsPath, { withFileTypes: true });
    const labFolder = labFolders.find((entry) => entry.isDirectory() && (entry.name === labId || entry.name.startsWith(`${labId}-`)));
    if (!labFolder) throw new Error("Lab folder not found");
    const filePath = path.join(labsPath, labFolder.name, "challenge.yaml");
    const config = parse(await readFile(filePath, "utf8"));
    return Response.json({
      lab_id: config.lab_id,
      title: config.title,
      intro: config.intro,
      examples: (config.examples || []).map(({ type, title, prompt, explanation }) => ({ type, title, prompt, explanation })),
      objectives: (config.objectives || []).map(({ id, title, description }) => ({ id, title, description })),
    }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ detail: `Challenge config for ${labId} is unavailable` }, { status: 404 });
  }
}
