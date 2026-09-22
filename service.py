"""馆际作品借展服务入口。

在原有健康检查之外，把 domain.LoanRegistry 的领域操作暴露为 JSON 接口。
所有写请求为 POST + JSON 体；查询为 GET。
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from domain import DomainError, LoanRegistry

SERVICE_ID = "museum-loan"
SERVICE_NAME = "馆际作品借展"

# 进程级登记处；生产环境应替换为持久化实现，接口契约不变。
REGISTRY = LoanRegistry()


def health_payload():
    """返回稳定的服务身份信息。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


class Handler(BaseHTTPRequestHandler):
    """健康检查与借展领域接口。"""

    server_version = "museum-loan/1.0"

    # ---- 路由 -------------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if path == "/health":
                self._write_json(200, health_payload())
                return
            if path.startswith("/artworks/"):
                parts = path.split("/")
                if len(parts) == 3:
                    self._serve(lambda: REGISTRY.artwork_view(parts[2]))
                    return
                if len(parts) == 4 and parts[3] == "state":
                    self._serve(lambda: REGISTRY.artwork_state(parts[2]))
                    return
            if path.startswith("/agreements/"):
                parts = path.split("/")
                if len(parts) == 3:
                    self._serve(lambda: REGISTRY.agreement_view(parts[2]))
                    return
            if path.startswith("/labels/"):
                parts = path.split("/")
                if len(parts) == 3:
                    self._serve(lambda: REGISTRY.label_view(parts[2]))
                    return
                if len(parts) == 4 and parts[3] == "trace":
                    version = query.get("version", [None])[0]
                    version = int(version) if version else None
                    label_id = parts[2]
                    self._serve(lambda: REGISTRY.trace_label(label_id, version))
                    return
            if path.startswith("/handovers/"):
                parts = path.split("/")
                if len(parts) == 3:
                    self._serve(lambda: REGISTRY.event_view(parts[2]))
                    return
            self._not_found()
        except DomainError as error:
            self._write_json(error.http_status, {"error": error.message})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            body = self._read_json()
            if path == "/artworks":
                self._serve(lambda: REGISTRY.add_artwork(body), status=201)
            elif path.startswith("/artworks/"):
                parts = path.split("/")
                if len(parts) == 4:
                    routes = {
                        "segments": REGISTRY.add_segment,
                        "contributions": REGISTRY.add_contribution,
                        "historical-exhibitions": REGISTRY.add_historical_exhibition,
                        "ownership": REGISTRY.set_ownership,
                        "state": None,
                    }
                    action = routes.get(parts[3])
                    if parts[3] == "state":
                        self._write_json(405, {"error": "状态请通过 GET 获取"})
                        return
                    if action is None:
                        self._not_found()
                        return
                    self._serve(lambda: action(parts[2], body), status=201)
                    return
                self._not_found()
            elif path == "/relations":
                self._serve(lambda: REGISTRY.add_relation(body), status=201)
            elif path == "/agreements":
                self._serve(lambda: REGISTRY.create_agreement(body), status=201)
            elif path.startswith("/agreements/"):
                parts = path.split("/")
                if len(parts) == 4 and parts[3] == "reschedule":
                    self._serve(lambda: REGISTRY.reschedule_agreement(parts[2], body))
                elif len(parts) == 4 and parts[3] == "amend":
                    self._serve(lambda: REGISTRY.amend_agreement(parts[2], body))
                else:
                    self._not_found()
            elif path == "/handovers":
                self._serve(lambda: REGISTRY.scan_handover(body), status=201)
            elif path.startswith("/handovers/"):
                parts = path.split("/")
                if len(parts) == 4 and parts[3] == "sign":
                    self._serve(lambda: REGISTRY.sign_handover(parts[2], body))
                else:
                    self._not_found()
            elif path == "/damages":
                self._serve(lambda: REGISTRY.report_damage(body), status=201)
            elif path.startswith("/risks/"):
                parts = path.split("/")
                if len(parts) == 4 and parts[3] == "resolve":
                    self._serve(lambda: REGISTRY.resolve_risk(parts[2], body))
                else:
                    self._not_found()
            elif path == "/labels":
                self._serve(lambda: REGISTRY.create_label(body), status=201)
            elif path.startswith("/labels/"):
                parts = path.split("/")
                if len(parts) == 4 and parts[3] == "publish":
                    self._serve(lambda: REGISTRY.publish_label(parts[2], body))
                elif len(parts) == 4 and parts[3] == "revise":
                    self._serve(lambda: REGISTRY.revise_label(parts[2], body))
                else:
                    self._not_found()
            else:
                self._not_found()
        except DomainError as error:
            self._write_json(error.http_status, {"error": error.message})

    # ---- 基础设施 ---------------------------------------------------------

    def _serve(self, action, status=200):
        # 领域注册表不是线程安全容器，整次调用在锁内完成。
        with REGISTRY.locked():
            payload = action()
        self._write_json(status, payload)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DomainError("请求体必须是合法 JSON")
        if not isinstance(data, dict):
            raise DomainError("请求体必须是 JSON 对象")
        return data

    def _not_found(self):
        self._write_json(404, {"error": "未知路由或资源"})

    def _write_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        assert health_payload()["service"] == SERVICE_ID
        # 领域注册表必须可实例化，防止基础接线断裂。
        LoanRegistry().add_artwork({"title": "自检作品"})
        print("基础检查通过")
        return
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
