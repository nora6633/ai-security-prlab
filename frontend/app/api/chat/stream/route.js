import { findLab } from "../../../../lib/labs";

export const dynamic = "force-dynamic";

// Streams each step of a practice-mode turn so the chat page can draw it live.
export async function POST(request) {
  const labId = new URL(request.url).searchParams.get("lab") || "Lab01";
  let payload;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ detail: "請求內容不是有效的 JSON。" }, { status: 400 });
  }

  try {
    const lab = await findLab(labId);
    if (!lab || lab.config.controller_profile !== "elastic_chatbot") return Response.json({ detail: "此 Lab 的聊天服務尚未設定。" }, { status: 404 });
    // Only the practice-mode chatbot is streamed; exam mode keeps the plain /api/chat route.
    const port = lab.config.chat.guarded_port;
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid chatbot port");
    const response = await fetch(`http://127.0.0.1:${port}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: request.signal,
    });
    if (!response.ok || !response.body) {
      return new Response(await response.arrayBuffer(), {
        status: response.status,
        headers: { "Content-Type": response.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" },
      });
    }
    return new Response(response.body, {
      headers: { "Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store", "X-Accel-Buffering": "no" },
    });
  } catch {
    return Response.json(
      { detail: "無法連到 Lab chatbot。請確認 Lab 已啟動，並稍後再試。" },
      { status: 502 },
    );
  }
}
