import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

export async function findLab(id) {
  if (!/^Lab\d+$/.test(id)) return null;
  const root = path.resolve(process.cwd(), "..", "Lab");
  const entries = await readdir(root, { withFileTypes: true });
  const matches = entries.filter((entry) => entry.isDirectory() && (entry.name === id || entry.name.startsWith(`${id}-`)));
  if (matches.length !== 1) return null;
  const directory = path.join(root, matches[0].name);
  const config = JSON.parse(await readFile(path.join(directory, "lab.json"), "utf8"));
  return config.id === id ? { directory, config } : null;
}
