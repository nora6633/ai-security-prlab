export const dynamic = "force-dynamic";

export async function POST(request) {
  const params = new URL(request.url).searchParams;
  const mode = params.get("mode");
  const labId = params.get("lab") || "Lab01";
  if (mode && mode !== "guarded" && mode !== "unguarded") {
    return Response.json({ detail: "無效的 chatbot 模式。" }, { status: 400 });
  }
  let payload;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ detail: "請求內容不是有效的 JSON。" }, { status: 400 });
  }

  try {
    const lab = await findLab(labId);
    if (!lab || lab.config.controller_profile !== "elastic_chatbot") return Response.json({ detail: "此 Lab 的聊天服務尚未設定。" }, { status: 404 });
    const config = lab.config;
    const port = mode === "unguarded" ? config.chat.unguarded_port : config.chat.guarded_port;
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid chatbot port");
    const response = await fetch(`http://127.0.0.1:${port}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.timeout(240_000),
    });
    const contentType = response.headers.get("Content-Type") || "application/json";
    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: { "Content-Type": contentType, "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json(
      { detail: "無法連到 Lab chatbot。請確認 Lab 已啟動，並稍後再試。" },
      { status: 502 },
    );
  }
}
import { findLab } from "../../../lib/labs";

