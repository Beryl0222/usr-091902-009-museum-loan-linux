"""服务级契约：健康检查与借展领域接口的端到端 HTTP 行为。"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service import Handler, REGISTRY, SERVICE_ID, SERVICE_NAME, health_payload


class HttpFixture:
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    @classmethod
    def request(cls, method, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            f"{cls.base_url}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=3) as response:
            return response.status, json.load(response)

    @classmethod
    def request_expect_error(cls, method, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            f"{cls.base_url}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(request, timeout=3)
        except HTTPError as error:
            return error.code, json.load(error)
        self.fail("应当返回错误状态码")


class ServiceContractTest(HttpFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 模块级 REGISTRY 在多个测试类间共享，端到端场景单独建一套资源即可。
        super().setUpClass()

    def test_health_payload_has_stable_identity(self):
        self.assertEqual(health_payload(), {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME})

    def test_health_endpoint_returns_json(self):
        status, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, health_payload())

    def test_unknown_route_is_not_exposed(self):
        code, body = self.request_expect_error("GET", "/unknown")
        self.assertEqual(code, 404)
        self.assertIn("error", body if isinstance(body, dict) else {})


class LoanFlowContractTest(HttpFixture, unittest.TestCase):
    """完整叙事：建长卷 → 两馆协议 → 五段交接（含重复扫码）→ 损伤冻结 → 展签快照追踪。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 每个场景用不同标题，避免与领域级资源相互干扰（注册表为进程级内存态）。
        _, scroll = cls.request("POST", "/artworks",
                                {"title": "HTTP雅集卷", "category": "长卷"})
        cls.scroll_id = scroll["id"]
        _, seg = cls.request("POST", f"/artworks/{cls.scroll_id}/segments", {"name": "画心"})
        cls.seg_id = seg["id"]
        for i in range(1, 17):
            cls.request("POST", f"/artworks/{cls.scroll_id}/contributions",
                        {"artist": f"作者{i}", "role": "绘", "order": i,
                         "segment_id": cls.seg_id})
        cls.request("POST", f"/artworks/{cls.scroll_id}/ownership",
                    {"owner": "戊博物馆", "segment_id": cls.seg_id})
        _, agreement = cls.request("POST", "/agreements", {
            "lender": "戊博物馆", "borrower": "本馆",
            "scope": [{"artwork_id": cls.scroll_id, "segment_ids": [cls.seg_id]}],
            "start": "2026-03-01", "end": "2026-06-30",
            "galleries": ["一号厅"], "max_lux": 50,
            "transport": "恒温恒湿专车", "insurance": "墙到墙全额",
            "digital_uses": ["官网展页"],
        })
        cls.agreement_id = agreement["id"]

    def _handover(self, stage, code, at, extra=None):
        payload = {"agreement_id": self.agreement_id, "artwork_id": self.scroll_id,
                   "stage": stage, "code": code, "scanned_by": "终端", "at": at}
        if extra:
            payload.update(extra)
        status, event = self.request("POST", "/handovers", payload)
        self.assertEqual(status, 201)
        self.request("POST", f"/handovers/{event['id']}/sign",
                     {"party": event["from_party"], "actor": f"{event['from_party']}代表"})
        _, signed = self.request("POST", f"/handovers/{event['id']}/sign",
                                 {"party": event["to_party"], "actor": f"{event['to_party']}代表"})
        self.assertEqual(signed["status"], "已完成")
        return signed

    def test_01_segment_and_sixteen_contributors_persisted(self):
        _, view = self.request("GET", f"/artworks/{self.scroll_id}")
        self.assertEqual(len(view["contributions"]), 16)
        self.assertEqual(view["ownership_segments"][self.seg_id]["owner"], "戊博物馆")

    def test_02_handover_chain_idempotent_scan_and_freeze(self):
        self._handover("出库", "H-1", "2026-03-02")
        self._handover("到馆", "H-2", "2026-03-05")

        # 重复扫码：返回同一交接记录，不新增事件。
        _, repeat = self.request("POST", "/handovers", {
            "agreement_id": self.agreement_id, "artwork_id": self.scroll_id,
            "stage": "出库", "code": "H-1", "scanned_by": "另一终端", "at": "2026-03-02",
        })
        self.assertTrue(repeat["already_scanned"])

        # 协议外展厅被拒。
        code, _ = self.request_expect_error("POST", "/handovers", {
            "agreement_id": self.agreement_id, "artwork_id": self.scroll_id,
            "stage": "布展", "code": "H-X", "scanned_by": "x", "at": "2026-03-10",
            "gallery": "三号厅", "lux": 40,
        })
        self.assertEqual(code, 409)

        # 长卷局部报损：后续动作冻结。
        _, risk = self.request("POST", "/damages", {
            "artwork_id": self.scroll_id, "segment_id": self.seg_id,
            "description": "画心折痕", "image_before_hash": "b" * 64,
            "image_after_hash": "a" * 64, "reported_by": "布展员",
        })
        code, body = self.request_expect_error("POST", "/handovers", {
            "agreement_id": self.agreement_id, "artwork_id": self.scroll_id,
            "stage": "布展", "code": "H-3", "scanned_by": "x", "at": "2026-03-10",
            "gallery": "一号厅", "lux": 40,
        })
        self.assertEqual(code, 409)
        self.assertIn("冻结", body["error"])

        _, state = self.request("GET", f"/artworks/{self.scroll_id}/state")
        self.assertTrue(state["frozen"])

        # 解除后完成布展。
        self.request("POST", f"/risks/{risk['id']}/resolve",
                     {"resolution": "出借馆确认无碍", "actor": "戊馆代表"})
        self._handover("布展", "H-4", "2026-03-12",
                       {"gallery": "一号厅", "lux": 45})

    def test_03_label_snapshot_and_trace(self):
        _, label = self.request("POST", "/labels", {
            "title": "HTTP会面百年", "narrative": "叙事文本",
            "artwork_ids": [self.scroll_id], "curator": "林策展",
        })
        label_id = label["id"]
        self.request("POST", f"/labels/{label_id}/publish", {"published_at": "2026-03-12"})

        # 更正只产生新版本，旧版快照哈希不变。
        _, before = self.request("GET", f"/labels/{label_id}")
        old_hash = before["versions"][0]["snapshot_hash"]
        self.request("POST", f"/labels/{label_id}/revise", {"narrative": "更正后叙事"})
        _, after = self.request("GET", f"/labels/{label_id}")
        self.assertEqual(after["versions"][0]["snapshot_hash"], old_hash)
        self.assertEqual(after["versions"][-1]["status"], "草稿")
        self.request("POST", f"/labels/{label_id}/publish", {"published_at": "2026-05-01"})

        # 追踪链：展签 → 实体 → 区段贡献 → 保管责任与授权。
        _, trace = self.request("GET", f"/labels/{label_id}/trace?version=1")
        self.assertEqual(trace["version"], 1)
        entity = trace["entities"][0]
        self.assertEqual(entity["artwork_id"], self.scroll_id)
        self.assertEqual(entity["state"]["state"], "展出中")
        self.assertEqual(entity["custodian"], "承借馆")
        self.assertEqual(len(entity["segments"][0]["contributions"]), 16)
        self.assertEqual(entity["authorization"]["digital_uses"], ["官网展页"])

    def test_04_validation_errors_are_json(self):
        code, body = self.request_expect_error("POST", "/artworks", {})
        self.assertEqual(code, 400)
        self.assertIn("title", body["error"])
        code, _ = self.request_expect_error("GET", "/artworks/art-9999")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
