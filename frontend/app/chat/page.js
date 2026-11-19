"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import SequenceDiagram from "./SequenceDiagram";
import styles from "./chat.module.css";

const APPLY_TIMEOUT_SECONDS = 180;
const onOff = (enabled) => (enabled ? "開啟" : "關閉");

export default function ChatPage() {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState({ loading: true, running: false });
  const [error, setError] = useState("");
  const [challenge, setChallenge] = useState(null);
  const [objectiveResults, setObjectiveResults] = useState({});
  const [challengeError, setChallengeError] = useState("");
  const [chatMode, setChatMode] = useState("guarded");
  const [trace, setTrace] = useState({ steps: [], error: "" });
  const [labId, setLabId] = useState("Lab01");
  const [policy, setPolicy] = useState(null);
  const [detectionRules, setDetectionRules] = useState(null);
  const [rulesError, setRulesError] = useState("");
  const [policyMessage, setPolicyMessage] = useState("");
  const [policyBusy, setPolicyBusy] = useState(false);
  const [effective, setEffective] = useState(null);
  const [applying, setApplying] = useState(null);
  const [applyStatus, setApplyStatus] = useState(null);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  const refreshStatus = useCallback(async () => {
    try {
      const response = await fetch(`/api/control/status?lab=${encodeURIComponent(labId)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("controller unavailable");
      const data = await response.json();
      setStatus({ loading: false, running: Boolean(data.running) });
    } catch {
      setStatus({ loading: false, running: false, controllerError: true });
    }
  }, [labId]);

  useEffect(() => { setLabId(new URLSearchParams(window.location.search).get("lab") || "Lab01"); }, []);
  useEffect(() => {
    fetch(`/api/control/guardrail?lab=${encodeURIComponent(labId)}`, { cache: "no-store" })
      .then(async (response) => { const data = await response.json(); if (!response.ok) throw new Error(data.error); setPolicy(data); })
      .catch((error) => setPolicyMessage(error.message || "無法讀取護欄設定"));
  }, [labId]);
  useEffect(() => {
    setDetectionRules(null);
    setRulesError("");
    fetch(`/api/labs/${encodeURIComponent(labId)}/rules`, { cache: "no-store" })
      .then(async (response) => { const data = await response.json(); if (!response.ok) throw new Error(data.detail); setDetectionRules(data.rules); })
      .catch((error) => setRulesError(error.message || "無法讀取偵測規則"));
  }, [labId]);
  useEffect(() => { refreshStatus(); }, [refreshStatus]);
  useEffect(() => {
    fetch(`/api/challenge?lab=${encodeURIComponent(labId)}`, { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then(setChallenge)
      .catch(() => setChallenge(null));
  }, [labId]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy, error]);

  // The chatbot reads the guardrail ConfigMap only after Kubernetes syncs it, so ask the
  // chatbot which switches it is really using instead of trusting the save response.
  const fetchEffective = useCallback(async () => {
    const response = await fetch(`/api/chat/guardrail?lab=${encodeURIComponent(labId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error("chatbot unavailable");
    const data = await response.json();
    setEffective(data);
    return data;
  }, [labId]);
  useEffect(() => {
    if (status.running && chatMode === "guarded") fetchEffective().catch(() => setEffective(null));
  }, [status.running, chatMode, fetchEffective]);
  useEffect(() => {
    if (!applying) return undefined;
    let stopped = false;
    async function check() {
      const current = await fetchEffective().catch(() => null);
      if (stopped) return;
      const seconds = Math.round((Date.now() - applying.since) / 1000);
      if (current && current.input === applying.target.input && current.output === applying.target.output && Boolean(current.mask) === applying.target.mask) {
        setApplying(null);
        setApplyStatus({ kind: "applied", seconds });
      } else if (seconds >= APPLY_TIMEOUT_SECONDS) {
        setApplying(null);
        setApplyStatus({ kind: "timeout", seconds });
      } else {
        setApplyStatus({ kind: "pending", seconds });
      }
    }
    check();
    const timer = setInterval(check, 2000);
    return () => { stopped = true; clearInterval(timer); };
  }, [applying, fetchEffective]);

  // Practice mode streams each step so the sequence diagram can follow the turn live.
  async function streamChat(nextMessages) {
    const response = await fetch(`/api/chat/stream?lab=${encodeURIComponent(labId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: nextMessages }),
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "聊天請求失敗。");
    }
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines.filter(Boolean)) {
        const event = JSON.parse(line);
        if (event.type === "step") setTrace((current) => ({ ...current, steps: [...current.steps, event] }));
        else if (event.type === "result") return event.data;
        else if (event.type === "error") throw new Error(event.detail || "聊天請求失敗。");
      }
    }
    throw new Error("chatbot 連線中斷，沒有收到回覆。");
  }

  async function postChat(nextMessages) {
    const response = await fetch(`/api/chat?lab=${encodeURIComponent(labId)}&mode=${chatMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: nextMessages }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "聊天請求失敗。");
    return data;
  }

  async function sendMessage(text = draft) {
    const content = text.trim();
    if (!content || busy || !status.running) return;

    const nextMessages = [...messages, { role: "user", content }];
    setMessages(nextMessages);
    setDraft("");
    setBusy(true);
    setError("");
    setTrace({ steps: [], error: "" });

    try {
      const data = chatMode === "guarded" ? await streamChat(nextMessages) : await postChat(nextMessages);
      if (data.challenge?.objectives) {
        setObjectiveResults((current) => Object.fromEntries([
          ...Object.entries(current),
          ...data.challenge.objectives.map((objective) => [objective.id, {
            passed: Boolean(current[objective.id]?.passed || objective.passed),
          }]),
        ]));
      }
      setChallengeError(data.challenge?.error || "");
      setMessages([...nextMessages, { role: "assistant", content: data.reply }]);
    } catch (requestError) {
      setError(requestError.message || "無法連到 chatbot。");
      setTrace((current) => ({ ...current, error: requestError.message || "無法連到 chatbot。" }));
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function updatePolicy(section, field, value) {
    setPolicy((current) => ({ ...current, [section]: { ...current[section], [field]: value } }));
    setPolicyMessage("");
  }

  async function savePolicy(event) {
    event.preventDefault();
    setPolicyBusy(true);
    setPolicyMessage("");
    setApplyStatus(null);
    try {
      const response = await fetch(`/api/control/guardrail?lab=${encodeURIComponent(labId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(policy),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "儲存失敗");
      if (data.applied) {
        setApplying({ target: { input: policy.input.enabled, output: policy.output.enabled, mask: Boolean(policy.output.mask) }, since: Date.now() });
        setApplyStatus({ kind: "pending", seconds: 0 });
      } else {
        setPolicyMessage(data.message);
      }
    } catch (error) { setPolicyMessage(error.message || "儲存失敗"); }
    finally { setPolicyBusy(false); }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      sendMessage();
    }
  }

  function switchMode(mode) {
    if (mode === chatMode) return;
    setChatMode(mode);
    setMessages([]);
    setObjectiveResults({});
    setError("");
    setChallengeError("");
    setTrace({ steps: [], error: "" });
  }

  const stateText = status.loading
    ? "正在連線…"
    : status.controllerError
      ? "控制台未啟動"
      : status.running
        ? "Lab 已啟動"
        : "請先啟動 Lab";

  return (
    <main className={styles.page}>
      <nav className={styles.nav}>
        <Link className={styles.brand} href="/"><span className={styles.brandMark} aria-hidden="true"><svg viewBox="0 0 40 40"><path d="M20 3 34 11v16L20 37 6 27V11L20 3Z"/><path d="M13 24V15l7-4 7 4v9l-7 4-7-4Z"/><path d="m13 15 7 4 7-4M20 19v9"/></svg></span><span className={styles.brandCopy}><strong>TWISC <i>@</i> NTUST</strong><small>AI SECURITY LAB</small></span></Link>
        <div className={styles.navRight}>
          <span className={styles.connection}><i className={status.running ? styles.online : ""} />{stateText}</span>
          <Link className={styles.backLink} href="/">實驗控制台</Link>
        </div>
      </nav>

      <div className={styles.workspace}>
      <div className={styles.chatColumn}>
      <section className={styles.chatCard}>
        <header className={styles.chatHeader}>
          <div className={styles.avatar}>AI</div>
          <div className={styles.headerText}>
            <div className={styles.kicker}>TWISC CYBER RANGE · {labId.toUpperCase()}</div>
            <h1>測試聊天室</h1>
            <p>直接輸入訊息，測試 Lab chatbot。</p>
          </div>
          <button className={styles.clearButton} type="button" onClick={() => { setMessages([]); setObjectiveResults({}); setChallengeError(""); setError(""); setTrace({ steps: [], error: "" }); }} disabled={busy || !messages.length}>
            新對話
          </button>
        </header>

        <div className={styles.modeSwitch} role="group" aria-label="選擇 chatbot 服務">
          <span>選擇模式</span>
          <button type="button" disabled={busy} className={chatMode === "guarded" ? styles.modeSelected : ""} onClick={() => switchMode("guarded")} aria-pressed={chatMode === "guarded"}>練習模式</button>
          <button type="button" disabled={busy} className={chatMode === "unguarded" ? styles.modeSelected : ""} onClick={() => switchMode("unguarded")} aria-pressed={chatMode === "unguarded"}>考試模式</button>
        </div>



        {chatMode === "unguarded" && challenge?.objectives?.length > 0 && <section className={styles.challengePanel} aria-label="過關條件進度">
          <div className={styles.challengeHeading}><strong>過關進度</strong><b>{challenge.objectives.filter((objective) => objectiveResults[objective.id]?.passed).length} / {challenge.objectives.length}</b></div>
          <div className={styles.objectiveList}>
            {challenge.objectives.map((objective) => {
              const result = objectiveResults[objective.id];
              const passed = Boolean(result?.passed);
              return <div className={`${styles.objectiveRow} ${passed ? styles.objectivePassed : ""}`} key={objective.id}>
                <span className={styles.objectiveMark}>{passed ? "✓" : "○"}</span>
                <div><strong>{objective.title}</strong><span>{objective.description}</span></div>
                <small>{result ? (passed ? "是・已通關" : "否・未通過") : "尚未判定"}</small>
              </div>;
            })}
          </div>
          {challengeError && <div className={styles.challengeError}>{challengeError}</div>}
        </section>}

        <div className={styles.transcript} aria-live="polite">
          {!messages.length && !busy && (
            <div className={styles.emptyState}>
              <div className={styles.emptyIcon}>✳</div>
              <strong>開始測試對話</strong>
              <span>訊息會送到本機 Lab chatbot。</span>
              <div className={styles.suggestions}>
                {["你好，請簡短介紹你自己。", "請只回覆 OK。"].map((suggestion) => (
                  <button key={suggestion} type="button" onClick={() => sendMessage(suggestion)} disabled={!status.running || busy}>
                    {suggestion}<span>↗</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message, index) => (
            <article className={`${styles.messageRow} ${message.role === "user" ? styles.userRow : ""}`} key={`${index}-${message.role}`}>
              {message.role === "assistant" && <div className={styles.messageAvatar}>AI</div>}
              <div className={styles.messageBody}>
                <span className={styles.messageLabel}>{message.role === "user" ? "你" : "CHATBOT"}</span>
                <div className={`${styles.bubble} ${message.role === "user" ? styles.userBubble : styles.assistantBubble}`}>{message.content}</div>
              </div>
              {message.role === "user" && <div className={styles.userAvatar}>你</div>}
            </article>
          ))}

          {busy && <div className={styles.typing}><span /><span /><span /> chatbot 正在回覆</div>}
          {error && <div className={styles.errorBox}>{error}</div>}
          <div ref={bottomRef} />
        </div>

        <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); sendMessage(); }}>
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={status.running ? "輸入訊息…（Enter 傳送，Shift + Enter 換行）" : "先到實驗控制台啟動 Lab"}
            rows={2}
            disabled={!status.running || busy}
            aria-label="輸入聊天訊息"
          />
          <button type="submit" disabled={!draft.trim() || busy || !status.running} aria-label="傳送訊息">↑</button>
        </form>
        <div className={styles.chatFoot}><span>重新整理頁面會清除本次對話。</span><span>{messages.length ? `${messages.length} 則訊息` : `${labId} 測試`}</span></div>
      </section>
      {chatMode === "guarded" && <SequenceDiagram steps={trace.steps} running={busy} error={trace.error} />}
      </div>
        {chatMode === "guarded" && <aside className={styles.guardrailSidebar}>
        <section className={styles.detectionPanel} aria-label="本關防禦規則">
          <h2>本關偵測規則</h2>
          {rulesError ? <p role="alert">{rulesError}</p> : detectionRules ? (detectionRules.length ? detectionRules.map((rule) => <article className={styles.detectionRule} key={rule.id}>
            <h3>{rule.name}</h3>
            <p>{rule.description}</p>
            <p>資料來源：{rule.index?.join(", ") || "未指定"}</p>
            <strong>查詢關鍵字</strong>
            {rule.keywords?.length ? <ul className={styles.keywordList}>{rule.keywords.map((keyword) => <li key={keyword}>{keyword}</li>)}</ul> : <p>此規則沒有可顯示的引號關鍵字。</p>}
          </article>) : <p>本關尚未設定顯示的偵測規則。</p>) : <p>正在讀取規則…</p>}
        </section>
        <details className={styles.policyPanel}>
          <summary>護欄設定<em className={styles.policyEffective}>{effective ? `目前生效：輸入 ${onOff(effective.input)} · 輸出 ${onOff(effective.output)}${effective.mask ? " · 敏感資料遮罩 開啟" : ""}` : "目前生效：讀取中…"}</em></summary>
          {policy ? <form onSubmit={savePolicy}>
            <fieldset><legend>輸入護欄</legend>
              <label><input type="checkbox" checked={policy.input.enabled} onChange={(event) => updatePolicy("input", "enabled", event.target.checked)} /> 命中偵測規則關鍵字時拒答</label>
            </fieldset>
            <fieldset><legend>輸出護欄</legend>
              <label><input type="checkbox" checked={policy.output.enabled} onChange={(event) => updatePolicy("output", "enabled", event.target.checked)} /> 回覆含資料庫帳號或密碼時拒答</label>
              <div className={styles.advancedOption}>
                <strong>進階設定（非本課程內容）</strong>
                <label><input type="checkbox" checked={Boolean(policy.output.mask)} onChange={(event) => updatePolicy("output", "mask", event.target.checked)} /> 遮罩回覆中的敏感資料（帳號密碼、金鑰、個資等）</label>
              </div>
            </fieldset>
            <button className={styles.policySave} type="submit" disabled={policyBusy || Boolean(applying)}>{policyBusy ? "儲存中…" : applying ? "套用中…" : "儲存護欄設定"}</button>
          </form> : <p>正在讀取護欄設定…</p>}
          {applyStatus && <div role="status" className={`${styles.applyStatus} ${styles[`apply_${applyStatus.kind}`]}`}>
            {applyStatus.kind === "pending" && <>已儲存，等待 chatbot 載入新設定…（{applyStatus.seconds} 秒）<small>Kubernetes 同步設定檔通常需要數十秒到一兩分鐘，套用前送出的訊息仍使用舊設定。</small></>}
            {applyStatus.kind === "applied" && <>✓ 已套用（{applyStatus.seconds} 秒）：從下一則訊息開始，輸入護欄{onOff(effective?.input)}、輸出護欄{onOff(effective?.output)}、敏感資料遮罩{onOff(effective?.mask)}。</>}
            {applyStatus.kind === "timeout" && <>超過 {APPLY_TIMEOUT_SECONDS / 60} 分鐘 chatbot 仍未載入新設定，請到實驗控制台重新啟動 Lab。</>}
          </div>}
          {policyMessage && <p role="status">{policyMessage}</p>}
          <p>此設定套用於練習模式。</p>
        </details>
        </aside>}
      </div>
      <footer className={styles.footer}><span>TWISC @ NTUST · AI SECURITY LAB</span><span>CHAT TEST · PROMPT INJECTION</span></footer>
    </main>
  );
}
