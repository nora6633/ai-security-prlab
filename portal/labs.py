"""Discover lab definitions and validate their isolation settings."""
import json
import re
import subprocess
from pathlib import Path
import yaml

LAB_ROOT = Path(__file__).resolve().parents[1] / "Lab"


def load_labs():
    labs = {}
    namespaces = set()
    ports = set()
    for file in sorted(LAB_ROOT.glob("*/lab.yaml")):
        try:
            config = yaml.safe_load(file.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {file}") from exc
        if not isinstance(config, dict):
            raise ValueError(f"Invalid lab configuration in {file}")
        lab_id, namespace = config.get("id"), config.get("namespace")
        if not isinstance(lab_id, str) or not re.fullmatch(r"Lab\d+", lab_id):
            raise ValueError(f"Invalid lab id in {file}")
        if not isinstance(namespace, str) or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", namespace):
            raise ValueError(f"Invalid namespace in {file}")
        if lab_id in labs or namespace in namespaces:
            raise ValueError(f"Duplicate lab ID or namespace in {file}")
        if config.get("controller_profile") != "elastic_chatbot":
            raise ValueError(f"Unsupported controller profile in {file}")
        chat = config.get("chat", {})
        for field in ("guarded_port", "unguarded_port"):
            port = chat.get(field)
            if type(port) is not int or not 1 <= port <= 65535 or port in ports:
                raise ValueError(f"Invalid or duplicate chatbot port in {file}")
            ports.add(port)
        config["directory"] = file.parent
        labs[lab_id] = config
        namespaces.add(namespace)
    return labs


def get_lab(lab_id):
    return load_labs().get(lab_id)


def validate_manifests(lab):
    """Allow only explicitly namespaced resources in the configured lab namespace."""
    result = subprocess.run(["kubectl", "kustomize", str(lab["directory"])], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode:
        raise ValueError("無法讀取 Lab Kubernetes 設定：" + result.stderr.strip()[-300:])
    allowed = {"ConfigMap", "Secret", "Service", "Deployment", "StatefulSet", "DaemonSet", "Job", "PersistentVolumeClaim"}
    namespace = lab["namespace"]
    namespace_file = yaml.safe_load((lab["directory"] / "k8s" / "00-namespace.yaml").read_text(encoding="utf-8"))
    if not isinstance(namespace_file, dict) or namespace_file.get("kind") != "Namespace" or namespace_file.get("metadata", {}).get("name") != namespace:
        raise ValueError("00-namespace.yaml 與 Lab 的 namespace 不一致")
    found_namespace = False
    for item in yaml.safe_load_all(result.stdout):
        if not isinstance(item, dict):
            continue
        kind, metadata = item.get("kind"), item.get("metadata", {})
        if kind == "Namespace":
            if metadata.get("name") != namespace:
                raise ValueError("Lab namespace 與 Kubernetes 設定不一致")
            found_namespace = True
        elif kind not in allowed or metadata.get("namespace") != namespace:
            raise ValueError(f"Lab 資源 {kind} 不屬於指定 namespace 或類型不受支援")
    if not found_namespace:
        raise ValueError("Lab Kubernetes 設定缺少對應的 Namespace")
