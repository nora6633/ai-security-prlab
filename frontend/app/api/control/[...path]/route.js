export const dynamic = "force-dynamic";

async function forward(request, context) {
  const { path = [] } = await context.params;
  const incoming = new URL(request.url);
  const target = new URL(`/api/${path.join("/")}${incoming.search}`, "http://127.0.0.1:8081");
  const method = request.method;
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  try {
    const response = await fetch(target, {
      method,
      body,
      cache: "no-store",
      headers: body ? { "Content-Type": request.headers.get("Content-Type") || "application/json" } : {},
      signal: AbortSignal.timeout(15_000),
    });
    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ error: "Lab controller 尚未啟動，請重新開啟控制台。" }, { status: 503 });
  }
}

export const GET = forward;
export const POST = forward;
