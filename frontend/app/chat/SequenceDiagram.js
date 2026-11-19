"use client";

import { useEffect, useRef } from "react";
import styles from "./chat.module.css";

const NODES = [
  { id: "user", label: "使用者" },
  { id: "input", label: "輸入護欄" },
  { id: "chatbot", label: "Chatbot" },
  { id: "llm", label: "LLM" },
  { id: "tool", label: "內部工具" },
  { id: "output", label: "輸出護欄" },
];
const COLUMN = Object.fromEntries(NODES.map((node, index) => [node.id, index]));
const WAITING = { input: "輸入護欄檢查中…", chatbot: "Chatbot 處理中…", llm: "LLM 思考中…", tool: "工具執行中…", output: "輸出護欄檢查中…" };

// Tool results arrive as JSON; show file contents with their real line breaks.
function formatDetail(detail) {
  try {
    const value = JSON.parse(detail);
    if (value && typeof value === "object") {
      if (typeof value.content === "string") return `${value.path}${value.truncated ? "（已截斷）" : ""}\n\n${value.content}`;
      return JSON.stringify(value, null, 2);
    }
  } catch {}
  return detail;
}

function Lifelines() {
  return NODES.map((node) => <i key={node.id} className={styles.lifeline} style={{ left: `${(COLUMN[node.id] + 0.5) / NODES.length * 100}%` }} />);
}

export default function SequenceDiagram({ steps, running, error }) {
  const listRef = useRef(null);
  const last = steps.at(-1);
  const active = running ? (last?.to ?? "user") : null;
  const visited = new Set(steps.flatMap((step) => [step.from, step.to]));

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [steps.length, running]);

  return (
    <section className={styles.sequencePanel} aria-label="執行流程">
      <div className={styles.sequenceHeading}>
        <h2>執行流程</h2>
        <div className={styles.sequenceState}>{running ? "執行中" : steps.length ? (error ? "發生錯誤" : "已完成") : "等待訊息"}</div>
      </div>
      <div className={styles.sequenceNodes}>
        {NODES.map((node) => (
          <div key={node.id} className={`${styles.sequenceNode} ${node.id === active ? styles.sequenceNodeActive : visited.has(node.id) ? styles.sequenceNodeVisited : ""}`}>{node.label}</div>
        ))}
      </div>
      <ol ref={listRef} className={styles.sequenceSteps}>
        {!steps.length && !running && <li className={styles.sequenceEmpty}>送出訊息後，這裡會即時顯示訊息經過哪些節點。</li>}
        {steps.map((step, index) => {
          const from = COLUMN[step.from];
          const to = COLUMN[step.to];
          const left = (Math.min(from, to) + 0.5) / NODES.length * 100;
          const width = Math.abs(to - from) / NODES.length * 100;
          return (
            <li key={index} className={`${styles.sequenceStep} ${styles[`sequence_${step.status}`] || ""}`}>
              <div className={styles.sequenceLabel}><b>{index + 1}</b>{step.label}</div>
              <div className={styles.sequenceArrowRow}>
                <Lifelines />
                <i className={`${styles.sequenceArrow} ${to < from ? styles.sequenceArrowLeft : ""}`} style={{ left: `${left}%`, width: `${width}%` }} />
              </div>
              {step.detail && <pre className={styles.sequenceDetail}>{formatDetail(step.detail)}</pre>}
            </li>
          );
        })}
        {running && (
          <li className={`${styles.sequenceStep} ${styles.sequenceWaiting}`}>
            <div className={styles.sequenceLabel}><b>…</b>{WAITING[active] || "處理中…"}</div>
            <div className={styles.sequenceArrowRow}>
              <Lifelines />
              <i className={styles.sequencePulse} style={{ left: `${(COLUMN[active] + 0.5) / NODES.length * 100}%` }} />
            </div>
          </li>
        )}
        {error && !running && <li className={`${styles.sequenceStep} ${styles.sequence_blocked}`}><div className={styles.sequenceLabel}><b>!</b>{error}</div></li>}
      </ol>
    </section>
  );
}
