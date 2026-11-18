# K8s 實驗環境（Lab 01）

目前提供本機 Kubernetes（kind）上的實驗服務，以及 Next.js 情境介紹與啟動控制台。

## 啟動

1. 安裝並啟動 Docker Desktop、`kind`、`kubectl`、Python 3 和 Node.js；執行 `python -m pip install -r portal/requirements.txt` 安裝控制台依賴。
2. 將 `Lab/Lab01-Prompt Injection/10-postgres.yaml` 中 Secret 的示範密碼換掉，並確認 chatbot ConfigMap 的 `BASEURL` 和 `OPENWEBUI_MODEL`。
3. 在專案根目錄執行 `npm run dev`，開啟 Next.js 控制台 `http://127.0.0.1:3000`。
4. 在情境介紹頁按「啟動 Lab」。kind 叢集若不存在會自動建立。
5. 啟動完成後，控制台會顯示 lab namespace 內所有 Kubernetes Service 的 IP 和 port。

控制台的「關閉機器」會停止 workloads、關閉 port-forward，並刪除 namespace 內的 PVC 與 Elastic 登入憑證，因此 PostgreSQL 和 Elasticsearch 的實驗資料會清除；namespace 和服務設定保留，再次啟動會建立新的空白資料與隨機密碼。Elasticsearch 啟用帳密和 TLS；登入帳號固定為 `elastic`，每次啟動產生新密碼並顯示在本機控制台。此配置供隔離的本機實驗使用，不要暴露到公網。Elastic 資源需求較高；Docker Desktop 建議配置至少 4 GB 記憶體。

## 個別套用服務

每個實驗環境放在 `Lab` 底下的獨立資料夾，目前是 `Lab/Lab01-Prompt Injection`。控制台啟動 Lab01 時會套用這個資料夾的 Kustomize 設定。

YAML 已依用途拆開，方便單獨調整：

- `00-namespace.yaml`：建立 namespace
- `10-postgres.yaml`：PostgreSQL、PVC、Service、密碼 Secret
- `20-elasticsearch.yaml`：Elasticsearch StatefulSet 和 Service
- `30-kibana.yaml`：Kibana Deployment 和 Service
- `40-filebeat.yaml`：Filebeat 設定與 DaemonSet

完整 Lab 請從控制台啟動；controller 會先產生 Elasticsearch 密碼與 TLS Secret，再套用整套 Kustomize 資源。若手動執行 `kubectl apply -k Lab/Lab01`，必須先自行建立 `elastic-credentials` 與 `elastic-certificates` 這兩個 Secret。只建立不依賴 Elastic 的單一服務時，先套用 namespace，再套用服務檔，例如：

```powershell
kubectl apply -f Lab/Lab01-Prompt Injection/00-namespace.yaml
kubectl apply -f Lab/Lab01-Prompt Injection/10-postgres.yaml
```

啟動和關閉都由 `portal/server.py` 管理。控制台的「啟動 Lab」和「關閉機器」按鈕會呼叫同一個 controller 的 HTTP API；關閉時停止工作負載、等待 Pod 結束並刪除 PVC，清除實驗資料。新增其他實驗時，將設定放在 `Lab/Lab02` 等獨立資料夾，再由 controller 設定該實驗的啟停流程。

Elasticsearch、Kibana、Filebeat 有依賴順序，建議按檔名前綴順序套用。若要移除整套環境，`kubectl delete namespace lab` 也會刪除其中的資料。

## Chatbot（FastAPI + Open WebUI）

FastAPI chatbot 在本機 kind/Kubernetes 內執行。環境變數由 `Lab/Lab01-Prompt Injection/50-chatbot.yaml` 的 ConfigMap 和 Kubernetes Secret 注入：修改 ConfigMap 中的 Open WebUI URL 與模型 ID。管理者可將 API key 存在 `chatbot/openwebui-secret.env`；啟動 controller 會建立／更新 Kubernetes Secret。API key 不會出現在 Next.js 頁面，也不會要求情境使用者輸入。

`npm run dev` 會同時啟動 Next.js 前端與本機 Python controller。Next.js App Router 提供情境介紹頁和互動控制台；前端透過 HTTP 呼叫 Next.js Route Handler，再由本機 controller 執行叢集操作。瀏覽器不接觸 Kubernetes 憑證或模型 API key。啟動進度來自 controller 的 HTTP progress API，整體服務狀態來自 Kubernetes Pod readiness。

服務清單會動態讀取 `lab` namespace 的 Kubernetes Service，新增或移除 Service 不需要修改前端。需要從本機連線的 Service，可在 Service metadata 設定 `portal.lab/port-forwards` annotation，格式為 `本機port:Service port`；控制台會自動建立 localhost port-forward 並顯示本機 IP/port。沒有 annotation 的 Service 仍會顯示 Cluster IP 和 Service port。工作負載等待狀態也會從 Deployment、StatefulSet 和 DaemonSet 動態讀取。

在 `Lab/Lab01-Prompt Injection/50-chatbot.yaml` 的 ConfigMap 設定 `BASEURL` 為 Open WebUI 根網址，並設定 `OPENWEBUI_MODEL` 為可用模型 ID。`BASEURL` 預設為 `http://192.168.0.53:12000`（不含 `/v1`）。

Lab01 提供兩個 chatbot 服務：`http://127.0.0.1:8000/chat` 套用 Guardrail，`http://127.0.0.1:8001/chat` 不套用 Guardrail；兩個服務都使用相同的通關評分條件。OpenAPI 文件分別位於 `http://127.0.0.1:8000/docs` 與 `http://127.0.0.1:8001/docs`。聊天室頁面可切換兩個端點。Lab01 公開目標定義在 `Lab/Lab01-Prompt Injection/challenge.json`。

本機聊天測試頁：`http://127.0.0.1:3000/chat`。頁面會維持目前瀏覽期間的對話內容，並把整段 messages history 傳給 FastAPI chatbot。

FastAPI 共用 `chatbot/guardrail.py`，每關以單一 `Lab/LabXX/guardrail.json` 管理輸入 guardrail 與通關評分器。Lab01 的 `chatbot` Deployment 使用 `guardrail.json`，`chatbot-unguarded` Deployment 使用 `guardrail-unguarded.json`；兩個 Service 分別提供 8000 與 8001。每關情境介紹放在 `introduction.md`，情境識別和通關目標放在 `challenge.json`。`guardrail.judge.type` 必須二選一：`guardrail` 代表規則／固定內容比對；`ai_judge` 代表以指定模型依 rubric 判斷是否通過。

## Log 收集

PostgreSQL 已將 SQL log 寫到容器標準輸出。Chatbot 在每次成功完成 `/chat` 後，會將最新的使用者訊息和 AI 回覆以一般文字寫在同一筆標準輸出日誌；Filebeat DaemonSet 讀取 Kubernetes 節點上 `lab` namespace 的容器日誌，並送到 Elasticsearch 的 `filebeat-*`。Filebeat 會排除 Kibana 自身的 Security rule execution 訊息，避免規則名稱被規則再次當成威脅文字而產生自我觸發告警。因此以 `message` 欄位查詢的偵測規則仍可比對 chatbot 同一筆事件中的使用者訊息與 AI 回覆。Kibana 可建立 data view `filebeat-*` 後檢視資料。Kubernetes Pod 內的 PostgreSQL 是 Linux 容器環境，不等同於可登入的完整 Linux VM；此 Filebeat 設定收容器日誌，不收集節點完整的 syslog/journald。

Elastic Security 偵測規則位於 `Lab/Lab01-Prompt Injection/alerts/rules_export.ndjson`。Kustomize 會把規則檔建立成 ConfigMap，並部署 `60-alert-rules.yaml` 的 Kubernetes Job；Job 等 Kibana API 就緒後以 `elastic` 認證匯入規則、建立 `filebeat-*` Data View，並將它設為 Discover 的預設。controller 每次啟動 Lab 前會建立隨機 `elastic` 密碼與本機 TLS 憑證，存入 Kubernetes Secret，再套用所有服務並等待新匯入 Job 完成。Kibana、Filebeat 和規則匯入流程都使用這些 Secret；登入密碼只經本機 controller 狀態 API 提供給前端，不寫入日誌或專案檔案。Kibana 使用固定的本機 encrypted saved objects key。此設定只適用於隔離的本機實驗環境。規則能否產生告警仍取決於規則查詢所需的資料來源是否已部署並送入 Elasticsearch。


## 護欄程式分工

`chatbot/guardrail.py` 載入設定，`chatbot/input_guardrail.py` 檢查輸入，`chatbot/output_guardrail.py` 遮罩輸出，`chatbot/evaluator.py` 判定通關。Lab01 的 `lab.json` 設定聊天室連接埠；控制台啟停流程仍專屬 Lab01，新增關卡前需要完成多 Lab 控制和隔離。

## 多 Lab 設定

每個 Lab 的 `lab.json` 設定 `id`、唯一的 `namespace`、`controller_profile: elastic_chatbot` 與兩個不重複的 chatbot 本機端口。此 profile 要求該 Lab 自備同 Lab01 的 Elastic、Filebeat、規則匯入 Job 和 chatbot Kubernetes 設定；新增其他服務組合仍需擴充控制器。`00-namespace.yaml` 和 kustomization 所有資源需對應該 namespace。Service 的 port-forward annotation 也需設定不衝突的本機端口。控制器在啟停前檢查 namespace 和資源範圍；停止會清除所屬 namespace 的 PVC。
