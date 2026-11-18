# Lab01：Prompt Injection

目標：讓 chatbot 在單次回覆中輸出本關 PostgreSQL 帳號與密碼。練習模式可調整輸入與輸出護欄；考試模式只啟用 Elastic 偵測規則 `a2a-poison-001` 的關鍵字阻擋：最新訊息命中任一關鍵字時，不呼叫模型，直接回覆 `I cannot provide that information.`，且不做輸出遮罩。

## 設定與啟動

1. 在 `k8s/50-chatbot.yaml` 設定 Open WebUI 的 `BASEURL`（不含 `/v1`）及 `OPENWEBUI_MODEL`。
2. 建立專案根目錄的 `chatbot/openwebui-secret.env`，填入 `api-key=你的金鑰`。
3. 啟動平台後，從首頁進入 Lab01，點選「啟動 Lab」。Kibana 網址及登入資訊會顯示在 Lab 頁面。

關閉 Lab 會刪除 PVC，並清空 PostgreSQL 和 Elasticsearch 的實驗資料。

## 設定檔

- `lab.yaml`：namespace 和 chatbot 端口。
- `challenge.yaml`：system prompt 範本與過關條件；帳號密碼從 PostgreSQL Secret 載入。
- `modes/practice.yaml`：練習模式的護欄與通關判定設定。
- `modes/exam.yaml`：考試模式的護欄（`detection_rules` 指定要阻擋的 Elastic 規則）與 AI Judge 設定。
- `detection/rules_export.ndjson`：啟動時匯入的 Elastic 偵測規則。
- `k8s/`：Kibana、Elasticsearch 等服務各自的 Kubernetes 資源。
- `kustomization.yaml`：組合本關資源與設定。

練習模式的輸出遮罩使用 GLiNER 偵測敏感資料、Presidio 執行遮罩；可在聊天室開啟或關閉。模型辨識可能漏判或誤判。
