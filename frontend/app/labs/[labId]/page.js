"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";

export default function LabPage() {
  const { labId = "Lab01" } = useParams();
  const [status, setStatus] = useState({ loading: true, running: false, starting: false, services: [], chatbot_configured: false, elastic_credentials: null });
  const [markdown, setMarkdown] = useState("");
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [now, setNow] = useState(Date.now());

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`/api/control/status?lab=${encodeURIComponent(labId)}`, { cache: "no-store" });
      if (response.status === 404) {
        setStatus({ loading: false, running: false, services: [], unsupported: true });
        return;
      }
      if (!response.ok) throw new Error("status unavailable");
      setStatus({ loading: false, ...(await response.json()) });
    } catch {
      setStatus((current) => ({ ...current, loading: false, controllerError: true }));
    }
  }, [labId]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    fetch(`/api/labs/${encodeURIComponent(labId)}/content`, { cache: "no-store" })
      .then((response) => response.ok ? response.text() : "# 實驗內容暫時無法讀取")
      .then(setMarkdown)
      .catch(() => setMarkdown("# 實驗內容暫時無法讀取"));
  }, [labId]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!job?.id || job.done) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch(`/api/control/progress?id=${encodeURIComponent(job.id)}`, { cache: "no-store" });
        const next = await response.json();
        if (cancelled) return;
        setJob({ ...next, action: job.action });
        if (next.done) {
          setBusy(false);
          setMessage(next.ok ? (job.action === "stop" ? "實驗機器已關閉，實驗資料已清除。" : "Lab 已啟動，服務連線資訊如下。") : (next.error || "操作失敗，請查看控制台狀態。"));
          refresh();
          return;
        }
      } catch {
        if (!cancelled) setMessage("讀取啟動進度時，控制台連線中斷。");
      }
      if (!cancelled) setTimeout(poll, 700);
    };
    const timer = setTimeout(poll, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job, refresh]);

  async function startLab() {
    setBusy(true);
    setMessage("");
    setJob({ current: "正在建立啟動工作…", percent: 0, logs: [], services: [], done: false });
    try {
      const response = await fetch(`/api/control/start?lab=${encodeURIComponent(labId)}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "無法開始啟動");
      setJob({ id: data.id, action: "start", current: "準備啟動", percent: 0, logs: [], services: [], done: false });
    } catch (error) {
      setBusy(false);
      setMessage(error.message);
      setJob(null);
    }
  }

  async function stopLab() {
    setBusy(true);
    setMessage("");
    setJob({ action: "stop", current: "正在關閉實驗機器…", percent: 0, logs: [], services: [], done: false });
    try {
      const response = await fetch(`/api/control/stop?lab=${encodeURIComponent(labId)}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "無法開始停止");
      setJob({ id: data.id, action: "stop", current: "正在停止…", percent: 0, logs: [], services: [], done: false });
    } catch (error) {
      setBusy(false);
      setMessage(error.message);
    }
  }

  const starting = status.starting || status.services.length > 0;
  const stateLabel = status.loading ? "正在讀取狀態…" : status.controllerError ? "控制台離線" : status.running ? "服務已就緒" : starting ? "服務啟動中" : "尚未啟動";
  const startedMs = status.started_at ? Date.parse(status.started_at) : null;
  const elapsed = startedMs ? Math.max(0, Math.floor((now - startedMs) / 1000)) : 0;
  const uptime = `${Math.floor(elapsed / 3600)} 小時`;
  const startedLabel = startedMs ? new Date(startedMs).toLocaleString("zh-TW", { hour12: false }) : "";

  return (
    <main className="shell">
      <nav className="topbar" id="top"><Link className="brand" href="/"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 40 40"><path d="M20 3 34 11v16L20 37 6 27V11L20 3Z"/><path d="M13 24V15l7-4 7 4v9l-7 4-7-4Z"/><path d="m13 15 7 4 7-4M20 19v9"/></svg></span><span className="brand-copy"><strong>TWISC <i>@</i> NTUST</strong><small>資通安全研究與教學中心</small></span></Link><div className="topbar-links"><Link href="/">所有實驗情境</Link><Link href={`/chat?lab=${encodeURIComponent(labId)}`}>聊天室</Link></div></nav>

      <div className="lab-breadcrumb"><Link href="/">所有實驗情境</Link><span>/</span><span>{labId.toUpperCase()}</span></div>
      <section className="panel lab-markdown">
        <ReactMarkdown>{markdown || "內容載入中…"}</ReactMarkdown>
      </section>

      <section className="panel lab-machine-panel" aria-label="實驗機器控制與資訊">
        <div className="machine-heading">
          <div><h2>實驗機器</h2><p>{status.running ? "啟動於 " + startedLabel + "　·　已運行 " + uptime : "啟動後可使用本關服務。關閉機器會清除實驗資料。"}</p></div>
          <span className={"machine-state " + (status.running ? "is-running" : "")}>{stateLabel}</span>
        </div>
        <div className="machine-action">
          <button className={"primary-button " + (status.running ? "shutdown-button" : "")} onClick={status.running ? stopLab : startLab} disabled={status.unsupported || busy || status.loading || status.controllerError}>
            <span>{busy ? (job?.action === "stop" ? "正在關閉…" : "正在啟動…") : status.running ? "關閉機器" : "啟動 " + labId}</span>
            <span className="button-arrow">{status.running ? "■" : "→"}</span>
          </button>
          {status.unsupported && <span>此 Lab 的實驗機器尚未設定。</span>}
        </div>
        {job && <div className="machine-progress"><strong>{job.percent || 0}%</strong><div className="progress-track"><div className={"progress-fill " + (!job.done ? "moving" : "")} style={{ width: (job.percent || 0) + "%" }} /></div></div>}
        {message && <div className={"notice " + (job?.done && !job.ok ? "notice-error" : "")}>{message}</div>}
        {status.running && status.elastic_credentials && <div className="credential-card" aria-label="Elasticsearch 登入資訊">
          <div><strong>Elasticsearch / Kibana 登入資訊</strong><p>關閉 Lab 後會清除。</p></div>
          <dl><div><dt>網址</dt><dd><a href="http://127.0.0.1:5601" target="_blank" rel="noreferrer">http://127.0.0.1:5601</a></dd></div><div><dt>帳號</dt><dd>{status.elastic_credentials.username}</dd></div><div><dt>密碼</dt><dd><code>{status.elastic_credentials.password}</code></dd></div></dl>
          <button type="button" className="copy-credential" onClick={() => navigator.clipboard.writeText("帳號：" + status.elastic_credentials.username + "\n密碼：" + status.elastic_credentials.password)}>複製帳密</button>
        </div>}
        <div className="service-heading"><h3>服務連線資訊</h3></div>
        <div className="table-wrap"><table><thead><tr><th>服務名稱</th><th>服務 IP</th><th>服務 Port</th><th>本機 IP</th><th>本機 Port</th></tr></thead><tbody>{status.services.length ? status.services.map((service, index) => <tr key={service.name + "-" + service.service_port + "-" + index}><td>{service.name === "chatbot" ? "Chatbot（練習模式）" : service.name === "chatbot-unguarded" ? "Chatbot（考試模式）" : service.name}</td><td>{service.cluster_ip}</td><td>{service.service_port}</td><td>{service.local_ip}</td><td>{service.local_port}</td></tr>) : <tr><td className="empty-row" colSpan="5">機器啟動後會顯示服務連線資訊。</td></tr>}</tbody></table></div>
      </section>
      <footer><span>TWISC @ NTUST · 資通安全研究與教學中心</span><span>{labId.toUpperCase()}</span></footer>
    </main>
  );
}

