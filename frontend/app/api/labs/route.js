import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

export const dynamic = "force-dynamic";

export async function GET() {
  const labsPath = path.resolve(process.cwd(), "..", "Lab");
  try {
    const entries = await readdir(labsPath, { withFileTypes: true });
    const labs = await Promise.all(entries
      .filter((entry) => entry.isDirectory() && /^Lab\d+/.test(entry.name))
      .map(async (entry) => {
        try {
          const filePath = path.join(labsPath, entry.name, "challenge.json");
          const config = JSON.parse(await readFile(filePath, "utf8"));
          const id = config.lab_id || entry.name.match(/^Lab\d+/)?.[0];
          if (!id || !/^Lab\d+$/.test(id)) return null;
          const labConfig = JSON.parse(await readFile(path.join(labsPath, entry.name, "lab.json"), "utf8"));
          if (labConfig.id !== id) return null;
          return {
            id,
            chat: labConfig.chat,
            title: config.title || entry.name,
            name: config.name || config.title?.split(/[：:]/)[0] || entry.name,
            category: config.category || "資安實驗",
            description: config.intro?.lead || "",
            objectives: (config.objectives || []).map(({ id: objectiveId, title, description }) => ({
              id: objectiveId,
              title,
              description,
            })),
          };
        } catch {
          return null;
        }
      }));
    return Response.json({ labs: labs.filter(Boolean) }, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ detail: "Lab 情境資料夾目前無法讀取" }, { status: 500 });
  }
}
