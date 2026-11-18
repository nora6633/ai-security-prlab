import { findLab } from "../../../lib/labs";

export const dynamic = "force-dynamic";

export async function POST(request) {
  try {
    const params = new URL(request.url).searchParams;
    const lab = await findLab(params.get("lab") || "Lab01");
    if (!lab || lab.config.controller_profile !== "elastic_chatbot") return Response.json({ detail: "找不到實驗。" }, { status: 404 });
    if (params.get("mode") !== "unguarded") return Response.json({ detail: "此模式不支援 AI Check。" }, { status: 400 });
    const port = lab.config.chat.unguarded_port;
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid port");
    const response = await fetch(`http://127.0.0.1:${port}/evaluate`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(await request.json()),
      cache: "no-store", signal: AbortSignal.timeout(120_000),
    });
    return new Response(await response.arrayBuffer(), { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ detail: "無法連線 AI Check，請確認 Lab 已啟動。" }, { status: 502 });
  }
}
