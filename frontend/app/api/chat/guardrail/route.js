import { findLab } from "../../../../lib/labs";

export const dynamic = "force-dynamic";

// Reports which guardrails the running practice-mode chatbot has actually loaded.
export async function GET(request) {
  const labId = new URL(request.url).searchParams.get("lab") || "Lab01";
  try {
    const lab = await findLab(labId);
    if (!lab || lab.config.controller_profile !== "elastic_chatbot") return Response.json({ detail: "此 Lab 的聊天服務尚未設定。" }, { status: 404 });
    const port = lab.config.chat.guarded_port;
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid chatbot port");
    const response = await fetch(`http://127.0.0.1:${port}/guardrail`, { cache: "no-store", signal: AbortSignal.timeout(5_000) });
    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ detail: "無法連到 Lab chatbot。" }, { status: 502 });
  }
}
