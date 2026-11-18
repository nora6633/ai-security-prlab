"""Loopback-only controller for local kind lab workloads."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
import json
import re
import base64
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from labs import get_lab, validate_manifests
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chatbot"))
from input_guardrail import _RULES as INPUT_RULES

LAB_ROOT = ROOT / "Lab"
LAB_CONTEXT = threading.local()


def current_lab():
    return LAB_CONTEXT.lab

SECRET_FILE = ROOT / "chatbot" / "openwebui-secret.env"
JOBS = {}
JOBS_LOCK = threading.Lock()
PORT_FORWARDS = {}
PORT_LOCK = threading.Lock()
POLICY_LOCK = threading.Lock()
CLUSTER = "kind"


def update_job(job_id, **values):
    with JOBS_LOCK:
        JOBS[job_id].update(values)


def add_log(job_id, message):
    with JOBS_LOCK:
        JOBS[job_id]["logs"].append(message)
        JOBS[job_id]["logs"] = JOBS[job_id]["logs"][-80:]


def run_command(job_id, title, args, timeout=240):
    add_log(job_id, title)
    try:
        result = subprocess.run(
            args, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到指令：{args[0]}，請確認 Docker、kind、kubectl 已安裝並在 PATH 中。") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"逾時：{title}") from exc
    output = (result.stdout or "") + (result.stderr or "")
    for line in output.splitlines()[-8:]:
        add_log(job_id, line[:400])
    if result.returncode:
        detail = output.strip().splitlines()
        raise RuntimeError(detail[-1][:400] if detail else f"{title} 失敗，exit code {result.returncode}")
    return result.stdout.strip()


def saved_api_key():
    try:
        for line in SECRET_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("api-key="):
                value = line.partition("=")[2].strip()
                if value and value != "PASTE_YOUR_OPENWEBUI_API_KEY_HERE":
                    return value
    except OSError:
        pass
    return ""


def elastic_credentials():
    """Read local lab credentials from Kubernetes without logging their values."""
    result = subprocess.run(
        ["kubectl", "-n", current_lab()["namespace"], "get", "secret", "elastic-credentials", "-o", "json"],
        capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        return None
    try:
        data = json.loads(result.stdout).get("data", {})
        username = base64.b64decode(data["username"]).decode("utf-8")
        password = base64.b64decode(data["password"]).decode("utf-8")
        return {"username": username, "password": password}
    except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def ensure_elastic_credentials(job_id):
    existing = elastic_credentials()
    if existing:
        add_log(job_id, "沿用現有 Elasticsearch 登入密碼（不會寫入啟動日誌）。")
        return
    password = secrets.token_urlsafe(36)
    username_b64 = base64.b64encode(b"elastic").decode("ascii")
    password_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    secret_yaml = (
        "apiVersion: v1\nkind: Secret\nmetadata:\n"
        f"  name: elastic-credentials\n  namespace: {current_lab()['namespace']}\n"
        "type: Opaque\ndata:\n"
        f"  username: {username_b64}\n  password: {password_b64}\n"
    )
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"], input=secret_yaml,
        capture_output=True, text=True, timeout=20,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or "建立 Elasticsearch Secret 失敗").strip()[-300:])
    add_log(job_id, "已產生新的 Elasticsearch elastic 密碼並存入 Kubernetes Secret。")


def ensure_elastic_certificates(job_id):
    existing = subprocess.run(
        ["kubectl", "-n", current_lab()["namespace"], "get", "secret", "elastic-certificates", "-o", "name"],
        capture_output=True, text=True, timeout=10,
    )
    if existing.returncode == 0:
        add_log(job_id, "沿用現有 Elasticsearch TLS 憑證。")
        return
    add_log(job_id, "產生 Elasticsearch 本機 TLS 憑證。")
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Lab01 Local Elastic CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "elasticsearch")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name).issuer_name(ca_name).public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("elasticsearch"),
            x509.DNSName(f"elasticsearch.{current_lab()['namespace']}.svc"),
            x509.DNSName(f"elasticsearch.{current_lab()['namespace']}.svc.cluster.local"),
        ]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH,
        ]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    data = {
        "ca.crt": ca_cert.public_bytes(serialization.Encoding.PEM),
        "tls.crt": server_cert.public_bytes(serialization.Encoding.PEM),
        "tls.key": server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    }
    encoded = {key: base64.b64encode(value).decode("ascii") for key, value in data.items()}
    secret_yaml = (
        "apiVersion: v1\nkind: Secret\nmetadata:\n"
        f"  name: elastic-certificates\n  namespace: {current_lab()['namespace']}\n"
        "type: Opaque\ndata:\n"
        + "".join(f"  {key}: {value}\n" for key, value in encoded.items())
    )
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"], input=secret_yaml,
        capture_output=True, text=True, timeout=20,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or "建立 Elasticsearch TLS Secret 失敗").strip()[-300:])
    add_log(job_id, "TLS 憑證已存入 Kubernetes Secret；私鑰不會寫入專案檔案。")


def start_port_forward(job_id, service, local_port, target_port):
    forward_id = f"{current_lab()['id']}:{service}:{local_port}"
    with PORT_LOCK:
        proc = PORT_FORWARDS.get(forward_id)
        if proc and proc.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.25):
                    return
            except OSError:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                PORT_FORWARDS.pop(forward_id, None)
        elif proc:
            PORT_FORWARDS.pop(forward_id, None)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", local_port))
        except OSError as exc:
            raise RuntimeError(f"本機連接埠 {local_port} 已被佔用，無法連接 {service}。") from exc
        args = ["kubectl", "-n", current_lab()["namespace"], "port-forward", "--address", "127.0.0.1",
                f"svc/{service}", f"{local_port}:{target_port}"]
        try:
            proc = subprocess.Popen(
                args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise RuntimeError(f"無法啟動 {service} 的本機連接埠轉送：{exc}") from exc
        PORT_FORWARDS[forward_id] = proc

    deadline = time.time() + 12
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{service} 的連接埠轉送啟動失敗（本機連接埠 {local_port} 可能已被佔用）。")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"等待 {service} 本機連接埠 {local_port} 逾時。")


def stop_port_forwards(lab_id=None):
    with PORT_LOCK:
        for key, proc in list(PORT_FORWARDS.items()):
            if lab_id is not None and not key.startswith(f"{lab_id}:"):
                continue
            if proc.poll() is None:
                proc.terminate()
            PORT_FORWARDS.pop(key, None)


def service_addresses():
    try:
        raw = subprocess.run(
            ["kubectl", "-n", current_lab()["namespace"], "get", "services", "-o", "json"],
            capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace",
        )
        items = json.loads(raw.stdout).get("items", []) if raw.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        items = []
    by_name = {item["metadata"]["name"]: item for item in items}
    result = []
    for name, svc in by_name.items():
        spec = svc.get("spec", {})
        cluster_ip = spec.get("clusterIP", "")
        annotations = svc.get("metadata", {}).get("annotations", {})
        forwards = {}
        for entry in annotations.get("portal.lab/port-forwards", "").split(","):
            try:
                local_port, service_port = (int(part.strip()) for part in entry.split(":", 1))
                forwards[service_port] = local_port
            except (ValueError, TypeError):
                continue
        ports = spec.get("ports", [])
        if not ports:
            result.append({"name": name, "cluster_ip": cluster_ip or "—", "service_port": "—",
                           "local_ip": "—", "local_port": "—", "protocol": ""})
        for port in ports:
            service_port = port.get("port", "—")
            local_port = forwards.get(service_port)
            result.append({
                "name": name,
                "cluster_ip": cluster_ip if cluster_ip and cluster_ip != "None" else "—",
                "service_port": f"{service_port}/{port.get('protocol', 'TCP')}",
                "local_ip": "127.0.0.1" if local_port else "—",
                "local_port": local_port or "—",
                "protocol": port.get("protocol", "TCP"),
            })
    return result


def ready_service_names():
    """Return Services with ready pod endpoints so status checks never wait on stopped workloads."""
    try:
        result = subprocess.run(
            ["kubectl", "-n", current_lab()["namespace"], "get", "endpoints", "-o", "json"],
            capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace",
        )
        if result.returncode:
            return set()
        items = json.loads(result.stdout).get("items", [])
        return {
            item.get("metadata", {}).get("name")
            for item in items
            if any(subset.get("addresses") for subset in item.get("subsets", []))
        }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return set()


def environment_started_at(pods):
    if not pods:
        return None
    result = subprocess.run(["kubectl", "-n", current_lab()["namespace"], "get", "configmap", "lab-runtime-state", "-o", "json"],
                            capture_output=True, text=True, timeout=8,
                            encoding="utf-8", errors="replace")
    if result.returncode == 0:
        try:
            value = json.loads(result.stdout).get("data", {}).get("started-at")
            if value:
                return value
        except json.JSONDecodeError:
            pass
    pod_times = [p.get("metadata", {}).get("creationTimestamp") for p in pods]
    pod_times = [value for value in pod_times if value]
    return min(pod_times) if pod_times else None


def save_environment_started_at(value):
    args = ["kubectl", "-n", current_lab()["namespace"], "create", "configmap", "lab-runtime-state",
            f"--from-literal=started-at={value}", "--dry-run=client", "-o", "yaml"]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=15,
                            encoding="utf-8", errors="replace", check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or "儲存啟動時間失敗").strip()[-300:])
    applied = subprocess.run(["kubectl", "apply", "-f", "-"], input=result.stdout,
                             capture_output=True, text=True, timeout=15,
                             encoding="utf-8", errors="replace", check=False)
    if applied.returncode:
        raise RuntimeError((applied.stderr or "儲存啟動時間失敗").strip()[-300:])


def verify_namespace_owner(lab, allow_missing=False):
    result = subprocess.run(["kubectl", "get", "namespace", lab["namespace"], "-o", "json"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    if result.returncode:
        if allow_missing and "NotFound" in result.stderr:
            return
        raise RuntimeError("無法確認 Lab namespace 的歸屬。")
    data = json.loads(result.stdout)
    owner = data.get("metadata", {}).get("labels", {}).get("portal.lab/id")
    legacy_lab01 = lab["id"] == "Lab01" and lab["namespace"] == "lab" and not owner
    if owner != lab["id"] and not legacy_lab01:
        raise RuntimeError("namespace 不屬於此 Lab，已停止操作。")


def run_start(job_id, lab):
    LAB_CONTEXT.lab = lab
    steps = [
        ("檢查 Docker", ["docker", "info"], 90),
        ("確認 kind 叢集", None, 30),
        ("建置 FastAPI chatbot 映像", ["docker", "build", "-t", "lab-chatbot:dev", str(ROOT / "chatbot")], 240),
        ("建置輸出遮罩服務映像", ["docker", "build", "-t", "lab-output-masker:dev", str(ROOT / "output-masker")], 1200),
        ("載入映像至 kind", ["kind", "load", "docker-image", "lab-chatbot:dev", "--name", CLUSTER], 180),
        ("載入輸出遮罩服務至 kind", ["kind", "load", "docker-image", "lab-output-masker:dev", "--name", CLUSTER], 600),
        ("建立 lab namespace", ["kubectl", "apply", "-f", str(current_lab()["directory"] / "k8s" / "00-namespace.yaml")], 45),
        ("清除舊的規則匯入 Job", ["kubectl", "-n", current_lab()["namespace"], "delete", "job", "import-elastic-detection-rules", "--ignore-not-found=true"], 30),
    ]
    try:
        validate_manifests(lab)
        for index, (title, args, timeout) in enumerate(steps, 1):
            update_job(job_id, current=title, percent=int((index - 1) * 45 / len(steps)))
            if args is None:
                clusters = run_command(job_id, title, ["kind", "get", "clusters"], timeout)
                if CLUSTER not in clusters.splitlines():
                    run_command(job_id, "建立 kind 叢集", ["kind", "create", "cluster", "--name", CLUSTER], 180)
                run_command(job_id, "切換 Kubernetes context", ["kubectl", "config", "use-context", f"kind-{CLUSTER}"], 30)
                continue
            else:
                if title == "建立 lab namespace":
                    verify_namespace_owner(lab, allow_missing=True)
                run_command(job_id, title, args, timeout)
                if title == "建立 lab namespace":
                    run_command(job_id, "標記 Lab namespace", ["kubectl", "label", "namespace", lab["namespace"], f"portal.lab/id={lab['id']}", "--overwrite"], 20)

        update_job(job_id, current="產生 Elasticsearch 登入密碼", percent=44)
        ensure_elastic_credentials(job_id)
        ensure_elastic_certificates(job_id)
        run_command(job_id, f"套用 {current_lab()['id']} 資源", ["kubectl", "apply", "-k", str(current_lab()["directory"])], 90)

        secret_configured = bool(saved_api_key())
        if secret_configured:
            update_job(job_id, current="設定 Open WebUI 憑證", percent=48)
            add_log(job_id, "建立／更新 Open WebUI Secret（機密內容不會顯示）。")
            secret_result = subprocess.run(
                ["kubectl", "-n", current_lab()["namespace"], "create", "secret", "generic", "openwebui-credentials",
                 f"--from-env-file={SECRET_FILE}", "--dry-run=client", "-o", "yaml"],
                cwd=ROOT, capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace", check=False,
            )
            if secret_result.returncode:
                raise RuntimeError((secret_result.stderr or "建立 Secret 失敗").strip()[-400:])
            result = subprocess.run(["kubectl", "apply", "-f", "-"], input=secret_result.stdout,
                                    capture_output=True, text=True, timeout=30,
                                    encoding="utf-8", errors="replace", check=False)
            if result.returncode:
                raise RuntimeError((result.stderr or "套用 Secret 失敗").strip()[-400:])
            add_log(job_id, "Kubernetes Secret 已更新（機密值不會顯示）。")
        else:
            existing = subprocess.run(["kubectl", "-n", current_lab()["namespace"], "get", "secret", "openwebui-credentials", "-o", "name"],
                                      capture_output=True, text=True, timeout=10)
            secret_configured = existing.returncode == 0
            if not secret_configured:
                add_log(job_id, "Open WebUI Secret 尚未設定；Lab 可啟動，但 chatbot 呼叫模型需要管理者設定 Secret。")

        if secret_configured:
            update_job(job_id, current="重新載入 chatbot 憑證", percent=50)
            for deployment in ("chatbot", "chatbot-unguarded"):
                run_command(job_id, f"重新啟動 {deployment} Deployment 以載入最新 Secret",
                            ["kubectl", "-n", current_lab()["namespace"], "rollout", "restart", f"deployment/{deployment}"], 30)

        update_job(job_id, current="探索 namespace 工作負載", percent=52)
        workload_json = run_command(job_id, "讀取所有 Deployment／StatefulSet／DaemonSet",
                                    ["kubectl", "-n", current_lab()["namespace"], "get", "deployments,statefulsets,daemonsets", "-o", "json"], 30)
        workloads = json.loads(workload_json).get("items", [])
        rollout_names = {"Deployment": "deployment", "StatefulSet": "statefulset", "DaemonSet": "daemonset"}
        for index, item in enumerate(workloads, 1):
            kind = rollout_names.get(item.get("kind"))
            name = item.get("metadata", {}).get("name")
            if not kind or not name:
                continue
            percent = 52 + index * 28 // max(len(workloads), 1)
            update_job(job_id, current=f"等待 {name} 就緒", percent=percent)
            run_command(job_id, f"等待 {name} 就緒", ["kubectl", "-n", current_lab()["namespace"], "rollout", "status",
                                                        f"{kind}/{name}", "--timeout=300s"], 310)

        update_job(job_id, current="等待 Elastic 偵測規則匯入", percent=80)
        run_command(job_id, "等待偵測規則匯入完成", ["kubectl", "-n", current_lab()["namespace"], "wait", "--for=condition=complete",
                                                        "job/import-elastic-detection-rules", "--timeout=240s"], 250)
        run_command(job_id, "讀取偵測規則匯入結果", ["kubectl", "-n", current_lab()["namespace"], "logs", "job/import-elastic-detection-rules"], 30)

        services = service_addresses()
        forward_pairs = {(row["name"], row["local_port"], int(row["service_port"].split("/")[0]))
                         for row in services if row["local_port"] != "—"}
        for index, (name, local, remote) in enumerate(sorted(forward_pairs), 1):
            update_job(job_id, current=f"準備 {name} 本機連線", percent=80 + index * 15 // max(len(forward_pairs), 1))
            start_port_forward(job_id, name, local, remote)
            add_log(job_id, f"{name} 已連至 127.0.0.1:{local}")

        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        save_environment_started_at(started_at)
        update_job(job_id, current="完成", percent=100, done=True, ok=True,
                   services=service_addresses(), started_at=started_at)
    except Exception as exc:  # surfaced in the local portal only
        add_log(job_id, f"啟動失敗：{exc}")
        update_job(job_id, current="啟動失敗", done=True, ok=False, error=str(exc),
                   services=service_addresses(), started_at=None)


def run_stop(job_id, lab):
    LAB_CONTEXT.lab = lab
    try:
        validate_manifests(lab)
        context = run_command(job_id, "檢查 Kubernetes 叢集", ["kubectl", "config", "current-context"], 10)
        if context != f"kind-{CLUSTER}":
            raise RuntimeError("目前 Kubernetes context 不是 Lab 的 kind 叢集，已停止操作。")
        verify_namespace_owner(lab)
        update_job(job_id, current="停止本機連線", percent=20)
        stop_port_forwards(current_lab()["id"])
        workload_json = run_command(job_id, "讀取 Lab 工作負載",
                                    ["kubectl", "-n", current_lab()["namespace"], "get", "deployments,statefulsets,daemonsets", "-o", "json"], 30)
        workloads = json.loads(workload_json).get("items", [])
        kinds = {"Deployment": "deployment", "StatefulSet": "statefulset", "DaemonSet": "daemonset"}
        for index, item in enumerate(workloads, 1):
            kind = kinds.get(item.get("kind"))
            name = item.get("metadata", {}).get("name")
            if not kind or not name:
                continue
            update_job(job_id, current=f"關閉 {name}", percent=20 + index * 50 // max(len(workloads), 1))
            if kind == "daemonset":
                run_command(job_id, f"停止 {name}", ["kubectl", "-n", current_lab()["namespace"], "delete", kind, name, "--wait=true"], 120)
            else:
                run_command(job_id, f"停止 {name}", ["kubectl", "-n", current_lab()["namespace"], "scale", f"{kind}/{name}", "--replicas=0"], 60)
        update_job(job_id, current="等待實驗機器關閉", percent=78)
        add_log(job_id, "等待所有實驗 Pod 結束。")
        deadline = time.time() + 180
        while time.time() < deadline:
            result = subprocess.run(["kubectl", "-n", current_lab()["namespace"], "get", "pods", "-o", "json"],
                                    capture_output=True, text=True, timeout=10,
                                    encoding="utf-8", errors="replace")
            if result.returncode:
                raise RuntimeError((result.stderr or "讀取 Pod 狀態失敗").strip()[-300:])
            if not json.loads(result.stdout).get("items", []):
                break
            time.sleep(2)
        else:
            raise RuntimeError("等待 Pods 關閉逾時。")
        run_command(job_id, "清除實驗資料", ["kubectl", "-n", current_lab()["namespace"], "delete", "pvc", "--all", "--wait=true"], 90)
        run_command(job_id, "清除啟動時間", ["kubectl", "-n", current_lab()["namespace"], "delete", "configmap", "lab-runtime-state", "--ignore-not-found=true"], 20)
        run_command(job_id, "清除 Elasticsearch 登入密碼", ["kubectl", "-n", current_lab()["namespace"], "delete", "secret", "elastic-credentials", "--ignore-not-found=true"], 20)
        run_command(job_id, "清除 Elasticsearch TLS 憑證", ["kubectl", "-n", current_lab()["namespace"], "delete", "secret", "elastic-certificates", "--ignore-not-found=true"], 20)
        update_job(job_id, current="機器已關閉；資料已清除", percent=100, done=True, ok=True, services=[], started_at=None)
    except Exception as exc:
        update_job(job_id, current="停止失敗", done=True, ok=False, error=str(exc), services=service_addresses())


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, data, content_type="application/json"):
        raw = data.encode("utf-8") if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        lab_id = parse_qs(parsed.query).get("lab", ["Lab01"])[0]
        if parsed.path == "/api/guardrail":
            try:
                lab = get_lab(lab_id)
                if not lab:
                    self._send(404, {"error": "找不到 Lab"})
                    return
                with POLICY_LOCK:
                    policy = yaml.safe_load((lab["directory"] / "modes" / "practice.yaml").read_text(encoding="utf-8"))["guardrail"]
                self._send(200, {
                    "enabled": policy.get("enabled", True), "input": policy["input"], "output": policy["output"],
                    "patterns": {
                        "input": {name: INPUT_RULES[name].pattern for name in policy["input"].get("rules", {}) if name in INPUT_RULES},
                    },
                })
            except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
                self._send(503, {"error": f"無法讀取護欄設定：{exc}"})
            return
        if parsed.path == "/api/status":
            try:
                lab = get_lab(lab_id)
            except (ValueError, OSError) as exc:
                self._send(503, {"error": str(exc)})
                return
            if not lab:
                self._send(404, {"error": "此 Lab 的控制流程尚未設定"})
                return
            LAB_CONTEXT.lab = lab
            context = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True, timeout=8)
            if context.returncode or context.stdout.strip() != f"kind-{CLUSTER}":
                self._send(503, {"error": "目前 Kubernetes context 不是 Lab 的 kind 叢集"})
                return
        if parsed.path == "/api/status":
            result = subprocess.run(["kubectl", "-n", current_lab()["namespace"], "get", "pods", "-o", "json"],
                                    capture_output=True, text=True, timeout=8,
                                    encoding="utf-8", errors="replace")
            pods = json.loads(result.stdout).get("items", []) if result.returncode == 0 else []
            pods = [p for p in pods if not any(owner.get("kind") == "Job"
                                                for owner in p.get("metadata", {}).get("ownerReferences", []))]
            workload_result = subprocess.run(
                ["kubectl", "-n", current_lab()["namespace"], "get", "deployments,statefulsets,daemonsets", "-o", "json"],
                capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace",
            )
            workloads = json.loads(workload_result.stdout).get("items", []) if workload_result.returncode == 0 else []

            def workload_ready(item):
                kind = item.get("kind")
                spec = item.get("spec", {})
                state = item.get("status", {})
                if kind in {"Deployment", "StatefulSet"}:
                    desired = spec.get("replicas", 1)
                    ready = state.get("readyReplicas", 0)
                elif kind == "DaemonSet":
                    desired = state.get("desiredNumberScheduled", 0)
                    ready = state.get("numberReady", 0)
                else:
                    return False
                return desired > 0 and ready >= desired

            pods_ready = bool(pods) and all(
                p.get("status", {}).get("phase") == "Running" and
                all(c.get("ready") for c in p.get("status", {}).get("containerStatuses", []))
                for p in pods
            )
            workloads_ready = bool(workloads) and all(workload_ready(item) for item in workloads)
            running = pods_ready and workloads_ready
            starting = bool(pods or workloads) and not running
            if running:
                with JOBS_LOCK:
                    busy = any(not job.get("done") for job in JOBS.values())
                if not busy:
                    ready_services = ready_service_names()
                    for name, local, remote in {(row["name"], row["local_port"], int(row["service_port"].split("/")[0]))
                                                for row in service_addresses()
                                                if row["local_port"] != "—" and row["name"] in ready_services}:
                        try:
                            start_port_forward("", name, local, remote)
                        except RuntimeError:
                            pass
            secret = subprocess.run(["kubectl", "-n", current_lab()["namespace"], "get", "secret", "openwebui-credentials", "-o", "name"],
                                    capture_output=True, text=True, timeout=8)
            self._send(200, {"running": running, "starting": starting,
                "started_at": environment_started_at(pods) if running else None,
                "chatbot_configured": secret.returncode == 0, "services": service_addresses() if pods else [],
                "elastic_credentials": elastic_credentials() if running else None})
        elif parsed.path == "/api/progress":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                snapshot = dict(job) if job else None
            self._send(200 if snapshot else 404, snapshot or {"error": "找不到這次操作"})
        else:
            self._send(404, "找不到頁面", "text/plain")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/guardrail":
            lab_id = parse_qs(parsed.query).get("lab", ["Lab01"])[0]
            try:
                lab = get_lab(lab_id)
                if not lab:
                    self._send(404, {"error": "找不到 Lab"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 32768:
                    raise ValueError("設定大小無效")
                incoming = json.loads(self.rfile.read(length))
                path = lab["directory"] / "modes" / "practice.yaml"
                with POLICY_LOCK:
                    config = yaml.safe_load(path.read_text(encoding="utf-8"))
                    policy = config["guardrail"]
                    for section in ("input", "output"):
                        value = incoming.get(section)
                        if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
                            raise ValueError(f"{section} 設定無效")
                        current = policy[section]
                        current["enabled"] = value["enabled"]
                        if section == "input":
                            rules = value.get("rules")
                            if not isinstance(rules, dict) or set(rules) != set(current.get("rules", {})) or any(type(v) is not bool for v in rules.values()):
                                raise ValueError("input 規則無效")
                            current["rules"] = rules
                        else:
                            current.pop("rules", None)
                    inp = incoming["input"]
                    if inp.get("action") not in ("instruct", "custom_response"):
                        raise ValueError("輸入處理方式無效")
                    for field in ("instruction", "response"):
                        if not isinstance(inp.get(field), str) or not inp[field].strip() or len(inp[field]) > 8000:
                            raise ValueError(f"{field} 不可為空或過長")
                        policy["input"][field] = inp[field]
                    policy["input"]["action"] = inp["action"]
                    policy["enabled"] = True
                    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
                    try:
                        temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    applied = False
                    note = "設定已儲存；啟動 Lab 後會套用。"
                    try:
                        context = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True, timeout=8)
                        if context.returncode == 0 and context.stdout.strip() == f"kind-{CLUSTER}":
                            namespace = lab["namespace"]
                            existing = subprocess.run(["kubectl", "-n", namespace, "get", "configmap", "chatbot-guardrail", "-o", "name"], capture_output=True, text=True, timeout=8)
                            if existing.returncode == 0:
                                verify_namespace_owner(lab)
                                create = subprocess.run(["kubectl", "-n", namespace, "create", "configmap", "chatbot-guardrail", f"--from-file=practice.yaml={path}", "--dry-run=client", "-o", "yaml"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
                                if create.returncode == 0:
                                    result = subprocess.run(["kubectl", "apply", "-f", "-"], input=create.stdout, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
                                    applied = result.returncode == 0
                                note = "設定已儲存並更新，執行中的服務通常會在數十秒至數分鐘內讀取。" if applied else "設定已儲存，但更新執行中的服務失敗；重新啟動 Lab 後會套用。"
                    except (OSError, subprocess.SubprocessError, RuntimeError):
                        note = "設定已儲存，但無法更新執行中的服務；重新啟動 Lab 後會套用。"
                self._send(200, {"applied": applied, "message": note})
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, RuntimeError, yaml.YAMLError) as exc:
                self._send(400, {"error": f"無法更新護欄設定：{exc}"})
            return
        if parsed.path == "/api/stop-forwards":
            stop_port_forwards()
            self._send(200, {"ok": True})
            return
        if parsed.path not in ("/api/start", "/api/stop"):
            self._send(404, {"error": "找不到操作"})
            return
        lab_id = parse_qs(parsed.query).get("lab", ["Lab01"])[0]
        try:
            lab = get_lab(lab_id)
        except (ValueError, OSError) as exc:
            self._send(503, {"error": str(exc)})
            return
        if not lab:
            self._send(404, {"error": "此 Lab 的控制流程尚未設定"})
            return
        job_id = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            active = next((jid for jid, job in JOBS.items() if not job.get("done")), None)
            if active:
                self._send(409, {"error": "已有啟動或停止作業進行中", "id": active})
                return
            JOBS[job_id] = {"id": job_id, "current": "準備啟動", "percent": 0,
                            "done": False, "ok": False, "logs": [], "services": []}
        target = run_start if parsed.path == "/api/start" else run_stop
        threading.Thread(target=target, args=(job_id, lab), daemon=True).start()
        self._send(202, {"id": job_id})

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8081), Handler)
    print("Lab controller listening on http://127.0.0.1:8081")
    server.serve_forever()


