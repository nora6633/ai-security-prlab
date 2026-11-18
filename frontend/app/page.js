"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

function Brand() {
  return <div className="brand"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 40 40"><path d="M20 3 34 11v16L20 37 6 27V11L20 3Z"/><path d="M13 24V15l7-4 7 4v9l-7 4-7-4Z"/><path d="m13 15 7 4 7-4M20 19v9"/></svg></span><span className="brand-copy"><strong>TWISC <i>@</i> NTUST</strong><small>資通安全研究與教學中心</small></span></div>;
}

export default function Home() {
  const router = useRouter();
  const [labs, setLabs] = useState([]);

  useEffect(() => {
    fetch("/api/labs", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : { labs: [] })
      .then((data) => setLabs(data.labs || []))
      .catch(() => setLabs([]));
  }, []);

  return (
    <main className="shell">
      <nav className="topbar" id="top"><Brand /><span className="home-nav-label">實驗情境目錄</span></nav>

      <section className="home-intro">
        <div><div className="section-kicker">TAIWAN TECH · INFORMATION SECURITY</div><h1>資安實驗場域</h1><p>選擇實驗情境，進入專屬操作頁面啟動實驗機器。</p></div>
        <div className="home-index"><span>開放情境</span><strong>{String(labs.length).padStart(2, "0")}</strong></div>
      </section>

      <section className="labs-section" id="labs" aria-label="實驗情境列表">
        <div className="section-heading"><div><div className="section-kicker">LAB CATALOGUE</div><h2>所有實驗情境</h2></div><span>點選情境卡片進入專屬實驗頁</span></div>
        <div className="lab-grid">{labs.map((lab) => <button className="lab-card" type="button" onClick={() => router.push(`/labs/${lab.id}`)} key={lab.id}>
          <div className="lab-card-head"><span>{lab.id.toUpperCase()}</span><span className="lab-category">{lab.category}</span></div>
          <div className="lab-card-body"><h3>{lab.name}</h3><p>{lab.description}</p></div>
          <div className="lab-card-foot"><span className="objective-label">通關目標</span><strong>{lab.objectives?.[0]?.title || "查看實驗情境"}</strong><span className="lab-card-link">進入實驗 <i>→</i></span></div>
        </button>)}</div>
      </section>

      <footer><span>TWISC @ NTUST · 資通安全研究與教學中心</span><span>實驗情境目錄</span></footer>
    </main>
  );
}
