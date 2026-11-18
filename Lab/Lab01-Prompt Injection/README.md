# Lab01：Prompt Injection

目標：讓 chatbot 完整輸出本關的 system prompt。練習模式可調整輸入與輸出護欄；考試模式使用預設設定。

## 設定與啟動

1. 在 `50-chatbot.yaml` 設定 Open WebUI 的 `BASEURL`（不含 `/v1`）及 `OPENWEBUI_MODEL`。
2. 建立專案根目錄的 `chatbot/openwebui-secret.env`，填入 `api-key=你的金鑰`。
3. 啟動平台後，從首頁進入 Lab01，點選「啟動 Lab」。Kibana 網址及登入資訊會顯示在 Lab 頁面。

關閉 Lab 會刪除 PVC，並清空 Elasticsearch 的實驗資料。

## 設定檔

- `lab.json`：namespace 和 chatbot 端口。
- `challenge.json`：system prompt 與過關條件。
- `guardrail.json`：練習模式護欄與通關設定。
- `guardrail-unguarded.json`：考試模式預設設定，目前護欄關閉。
- `alerts/rules_export.ndjson`：啟動時匯入的 Elastic 偵測規則。
- `kustomization.yaml` 與各服務 YAML：本關 Kubernetes 資源。
