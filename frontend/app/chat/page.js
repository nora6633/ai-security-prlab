"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./chat.module.css";

export default function ChatPage() {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [turnIds, setTurnIds] = useState([]);
  const [status, setStatus] = useState({ loading: true, running: false });
  const [error, setError] = useState("");
  const [challenge, setChallenge] = useState(null);
  const [objectiveResults, setObjectiveResults] = useState({});
  const [challengeError, setChallengeError] = useState("");
  const [checkFeedback, setCheckFeedback] = useState(null);
  const [chatMode, setChatMode] = useState("guarded");
  const [labId, setLabId] = useState("Lab01");
  const [policy, setPolicy] = useState(null);
  const [detectionRules, setDetectionRules] = useState(null);
  const [rulesError, setRulesError] = useState("");
  const [policyMessage, setPolicyMessage] = useState("");
  const [policyBusy, setPolicyBusy] = useState(false);
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
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy, checking, checkFeedback, error]);

  async function sendMessage(text = draft) {
    const content = text.trim();
    if (!content || busy || !status.running) return;

    const nextMessages = [...messages, { role: "user", content }];
    setMessages(nextMessages);
    setDraft("");
    setBusy(true);
    setError("");
    setCheckFeedback(null);

    try {
      const response = await fetch(`/api/chat?lab=${encodeURIComponent(labId)}&mode=${chatMode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: nextMessages }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "聊天請求失敗。");
      if (data.turn_id) setTurnIds((current) => [...current, data.turn_id]);
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
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  async function checkAnswer() {
    if (!turnIds.length || checking || busy) return;
    setChecking(true);
    setChallengeError("");
    setCheckFeedback(null);
    try {
      const response = await fetch(`/api/evaluate?lab=${encodeURIComponent(labId)}&mode=${chatMode}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ turn_ids: turnIds }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "AI Check 失敗。");
      if (!Array.isArray(data.objectives) || data.objectives.length === 0 || data.objectives.some((item) => typeof item.passed !== "boolean")) throw new Error("AI Check 沒有回傳有效的判定結果。");
      setCheckFeedback({ ok: data.objectives.every((item) => item.passed), text: data.objectives.every((item) => item.passed) ? "AI Check：已通過。" : "AI Check：未通過。AI Judge 判定目前回答尚未符合完整取得的條件。" });
      setObjectiveResults((current) => Object.fromEntries([
        ...Object.entries(current),
        ...data.objectives.map((objective) => [objective.id, { passed: Boolean(current[objective.id]?.passed || objective.passed) }]),
      ]));
    } catch (error) { setChallengeError(error.message || "AI Check 失敗。"); setCheckFeedback({ error: true, text: error.message || "AI Check 失敗。" }); }
    finally { setChecking(false); }
  }

  function updatePolicy(section, field, value) {
    setPolicy((current) => ({ ...current, [section]: { ...current[section], [field]: value } }));
    setPolicyMessage("");
  }

  async function savePolicy(event) {
    event.preventDefault();
    setPolicyBusy(true);
    setPolicyMessage("");
    try {
      const response = await fetch(`/api/control/guardrail?lab=${encodeURIComponent(labId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(policy),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "儲存失敗");
      setPolicyMessage(data.message);
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
    setTurnIds([]);
    setObjectiveResults({});
    setError("");
    setChallengeError("");
    setCheckFeedback(null);
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
      <section className={styles.chatCard}>
        <header className={styles.chatHeader}>
          <div className={styles.avatar}>AI</div>
          <div className={styles.headerText}>
            <div className={styles.kicker}>TWISC CYBER RANGE · {labId.toUpperCase()}</div>
            <h1>測試聊天室</h1>
            <p>直接輸入訊息，測試 Lab chatbot。</p>
          </div>
          <button className={styles.clearButton} type="button" onClick={() => { setMessages([]); setTurnIds([]); setObjectiveResults({}); setChallengeError(""); setCheckFeedback(null); setError(""); }} disabled={busy || checking || !messages.length}>
            新對話
          </button>
        </header>

        <div className={styles.modeSwitch} role="group" aria-label="選擇 chatbot 服務">
          <span>選擇模式</span>
          <button type="button" disabled={busy || checking} className={chatMode === "guarded" ? styles.modeSelected : ""} onClick={() => switchMode("guarded")} aria-pressed={chatMode === "guarded"}>練習模式</button>
          <button type="button" disabled={busy || checking} className={chatMode === "unguarded" ? styles.modeSelected : ""} onClick={() => switchMode("unguarded")} aria-pressed={chatMode === "unguarded"}>考試模式</button>
        </div>



        {challenge?.objectives?.length > 0 && <section className={styles.challengePanel} aria-label="過關條件進度">
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
          {chatMode === "unguarded" && <button type="button" className={styles.checkButton} onClick={checkAnswer} disabled={!turnIds.length || busy || checking || !status.running}>{checking ? "正在檢查…" : "AI Check"}</button>}
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
          <div aria-live="polite" role="status">
            {checking && <div className={styles.checkFeedback}>AI Judge 正在檢查本次對話的回答…</div>}
            {!checking && checkFeedback && <div className={`${styles.checkFeedback} ${checkFeedback.error ? styles.checkFeedbackError : checkFeedback.ok ? styles.checkFeedbackPassed : ""}`}>{checkFeedback.text}</div>}
          </div>
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
            disabled={!status.running || busy || checking}
            aria-label="輸入聊天訊息"
          />
          <button type="submit" disabled={!draft.trim() || busy || checking || !status.running} aria-label="傳送訊息">↑</button>
        </form>
        <div className={styles.chatFoot}><span>重新整理頁面會清除本次對話。</span><span>{messages.length ? `${messages.length} 則訊息` : `${labId} 測試`}</span></div>
      </section>
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
          <summary>護欄設定</summary>
          {policy ? <form onSubmit={savePolicy}>
            <fieldset><legend>輸入護欄</legend>
              <label><input type="checkbox" checked={policy.input.enabled} onChange={(event) => updatePolicy("input", "enabled", event.target.checked)} /> 命中偵測規則關鍵字時拒答</label>
            </fieldset>
            <fieldset><legend>輸出護欄</legend>
              <label><input type="checkbox" checked={policy.output.enabled} onChange={(event) => updatePolicy("output", "enabled", event.target.checked)} /> 回覆含資料庫帳號或密碼時拒答</label>
            </fieldset>
            <button className={styles.policySave} type="submit" disabled={policyBusy}>{policyBusy ? "儲存中…" : "儲存護欄設定"}</button>
          </form> : <p>正在讀取護欄設定…</p>}
          {policyMessage && <p role="status">{policyMessage}</p>}
          <p>此設定套用於練習模式。</p>
        </details>
        </aside>}
      </div>
      <footer className={styles.footer}><span>TWISC @ NTUST · AI SECURITY LAB</span><span>CHAT TEST · PROMPT INJECTION</span></footer>
    </main>
  );
}
