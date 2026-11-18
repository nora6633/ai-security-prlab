# Lab01：Prompt Injection

目標：讓 chatbot 完整輸出本關的 system prompt。練習模式可調整輸入與輸出護欄；考試模式使用預設設定。

## 設定與啟動

1. 在 `k8s/50-chatbot.yaml` 設定 Open WebUI 的 `BASEURL`（不含 `/v1`）及 `OPENWEBUI_MODEL`。
2. 建立專案根目錄的 `chatbot/openwebui-secret.env`，填入 `api-key=你的金鑰`。
3. 啟動平台後，從首頁進入 Lab01，點選「啟動 Lab」。Kibana 網址及登入資訊會顯示在 Lab 頁面。

關閉 Lab 會刪除 PVC，並清空 Elasticsearch 的實驗資料。

## 設定檔

- `lab.yaml`：namespace 和 chatbot 端口。
- `challenge.yaml`：system prompt 與過關條件。
- `modes/practice.yaml`：練習模式的護欄與通關判定設定。
- `modes/exam.yaml`：考試模式的護欄與 AI Judge 設定。
- `detection/rules_export.ndjson`：啟動時匯入的 Elastic 偵測規則。
- `k8s/`：Kibana、Elasticsearch 等服務各自的 Kubernetes 資源。
- `kustomization.yaml`：組合本關資源與設定。

練習模式的輸出遮罩使用 GLiNER 偵測敏感資料、Presidio 執行遮罩；可在聊天室開啟或關閉。模型辨識可能漏判或誤判。
