"""借展领域规则的契约测试。

场景围绕「两位画家会面百年」纪念展：一件十六位作者的长卷、
独立作品与合作画来自多家博物馆，出借条件各异。
"""

import unittest

from domain import STAGES, DomainError, LoanRegistry


def make_long_scroll(registry):
    """一件长卷：四个区段、十六位作者，含题跋与合作顺序，分属两家机构。"""
    scroll = registry.add_artwork({"title": "雅集图卷", "category": "长卷", "year": "1326"})
    seg_a = registry.add_segment(scroll["id"], {"name": "引首"})
    seg_b = registry.add_segment(scroll["id"], {"name": "画心"})
    seg_c = registry.add_segment(scroll["id"], {"name": "拖尾", "description": "元明人题跋"})
    seg_d = registry.add_segment(scroll["id"], {"name": "鉴藏题签"})

    artists = [
        ("赵君", "绘", "seg_b", 1), ("钱君", "绘", "seg_b", 2),
        ("孙氏", "绘", "seg_b", 3), ("李氏", "绘", "seg_b", 4),
        ("周氏", "绘", "seg_b", 5), ("吴氏", "绘", "seg_b", 6),
        ("郑氏", "绘", "seg_b", 7), ("王氏", "绘", "seg_b", 8),
        ("冯氏", "题跋", "seg_c", 9), ("陈氏", "题跋", "seg_c", 10),
        ("褚氏", "题跋", "seg_c", 11), ("卫氏", "题跋", "seg_c", 12),
        ("蒋氏", "题跋", "seg_c", 13), ("沈氏", "题跋", "seg_c", 14),
        ("韩氏", "题签", "seg_d", 15), ("杨氏", "鉴藏", "seg_a", 16),
    ]
    seg_ids = {"seg_a": seg_a["id"], "seg_b": seg_b["id"], "seg_c": seg_c["id"], "seg_d": seg_d["id"]}
    for artist, role, key, order in artists:
        registry.add_contribution(
            scroll["id"],
            {"artist": artist, "role": role, "order": order, "segment_id": seg_ids[key]},
        )
    # 画心与引首归甲馆，拖尾与题签归乙馆：同一长卷权属分段持有。
    registry.set_ownership(scroll["id"], {"owner": "甲博物馆", "segment_id": seg_a["id"]})
    registry.set_ownership(scroll["id"], {"owner": "甲博物馆", "segment_id": seg_b["id"]})
    registry.set_ownership(scroll["id"], {"owner": "乙博物馆", "segment_id": seg_c["id"]})
    registry.set_ownership(scroll["id"], {"owner": "乙博物馆", "segment_id": seg_d["id"]})
    registry.add_historical_exhibition(
        scroll["id"], {"title": "元代书画大展", "year": "1998", "venue": "丙美术馆"}
    )
    return scroll, seg_ids


def make_solo_and_collab(registry):
    solo = registry.add_artwork({"title": "寒林独立轴", "category": "独立作品"})
    registry.set_ownership(solo["id"], {"owner": "丙博物馆"})
    collab = registry.add_artwork({"title": "合作山水", "category": "合作画"})
    registry.set_ownership(collab["id"], {"owner": "丁博物馆"})
    registry.add_contribution(collab["id"], {"artist": "黄大家", "role": "绘", "order": 1})
    registry.add_contribution(collab["id"], {"artist": "王二妙", "role": "绘", "order": 2})
    return solo, collab


class ArtworkStructureTest(unittest.TestCase):
    def setUp(self):
        self.registry = LoanRegistry()

    def test_long_scroll_has_sixteen_contributors_ordered_across_segments(self):
        scroll, seg_ids = make_long_scroll(self.registry)
        view = self.registry.artwork_view(scroll["id"])
        self.assertEqual(len(view["contributions"]), 16)
        self.assertEqual([c["order"] for c in view["contributions"]], list(range(1, 17)))
        colophons = [c for c in view["contributions"] if c["segment_id"] == seg_ids["seg_c"]]
        self.assertEqual({c["role"] for c in colophons}, {"题跋"})
        self.assertEqual(len(colophons), 6)

    def test_segment_ownership_differs_within_one_scroll(self):
        scroll, seg_ids = make_long_scroll(self.registry)
        view = self.registry.artwork_view(scroll["id"])
        self.assertEqual(view["ownership_segments"][seg_ids["seg_a"]]["owner"], "甲博物馆")
        self.assertEqual(view["ownership_segments"][seg_ids["seg_c"]]["owner"], "乙博物馆")
        self.assertIsNone(view["ownership_work"])

    def test_unknown_segment_rejected(self):
        scroll, _ = make_long_scroll(self.registry)
        with self.assertRaises(DomainError):
            self.registry.set_ownership(scroll["id"], {"owner": "X", "segment_id": "seg-999"})


class AgreementTest(unittest.TestCase):
    def setUp(self):
        self.registry = LoanRegistry()
        self.scroll, self.seg_ids = make_long_scroll(self.registry)
        self.solo, self.collab = make_solo_and_collab(self.registry)

    def test_different_lenders_get_different_terms(self):
        agr_a = self.registry.create_agreement(
            {
                "lender": "甲博物馆", "borrower": "本馆",
                "scope": [{"artwork_id": self.scroll["id"],
                           "segment_ids": [self.seg_ids["seg_a"], self.seg_ids["seg_b"]]}],
                "start": "2026-03-01", "end": "2026-06-30",
                "galleries": ["一号厅"], "max_lux": 50,
                "transport": "恒温恒湿专车", "insurance": "墙到墙全额",
                "digital_uses": ["官网展页"],
            }
        )
        agr_b = self.registry.create_agreement(
            {
                "lender": "乙博物馆", "borrower": "本馆",
                "scope": [{"artwork_id": self.scroll["id"],
                           "segment_ids": [self.seg_ids["seg_c"], self.seg_ids["seg_d"]]}],
                "start": "2026-03-01", "end": "2026-06-30",
                "galleries": ["一号厅"], "max_lux": 30,
                "transport": "随展押运", "insurance": "钉到钉",
                "digital_uses": ["官网展页", "教育手册"],
            }
        )
        self.assertNotEqual(agr_a["id"], agr_b["id"])
        self.assertEqual(agr_a["max_lux"], 50)
        self.assertEqual(agr_b["digital_uses"], ["官网展页", "教育手册"])

    def test_overlapping_scope_is_conflict(self):
        self.registry.create_agreement(
            {
                "lender": "丙博物馆", "borrower": "本馆",
                "scope": [self.solo["id"]],
                "start": "2026-03-01", "end": "2026-06-30",
            }
        )
        with self.assertRaises(DomainError) as error:
            self.registry.create_agreement(
                {
                    "lender": "丙博物馆", "borrower": "本馆",
                    "scope": [self.solo["id"]],
                    "start": "2026-07-01", "end": "2026-09-30",
                }
            )
        self.assertEqual(error.exception.http_status, 409)

    def test_reschedule_keeps_history_and_new_period_binds_handovers(self):
        agreement = self.registry.create_agreement(
            {
                "lender": "丁博物馆", "borrower": "本馆",
                "scope": [self.collab["id"]],
                "start": "2026-03-01", "end": "2026-05-31",
                "galleries": ["二号厅"], "max_lux": 80,
            }
        )
        # 跨馆改期：原运输档期取消。
        self.registry.reschedule_agreement(
            agreement["id"], {"start": "2026-04-15", "end": "2026-07-15",
                              "reason": "丁博物馆展厅维护延期"}
        )
        updated = self.registry.agreement_view(agreement["id"])
        self.assertEqual(updated["initial"]["start"], "2026-03-01")
        self.assertEqual(updated["start"], "2026-04-15")
        self.assertEqual(len(updated["revisions"]), 1)

        # 旧展期内扫码不再合法。
        with self.assertRaises(DomainError) as error:
            self.registry.scan_handover(
                {"agreement_id": agreement["id"], "artwork_id": self.collab["id"],
                 "stage": "出库", "code": "OLD-1", "scanned_by": "库管员", "at": "2026-03-05"}
            )
        self.assertEqual(error.exception.http_status, 409)

        event = self.registry.scan_handover(
            {"agreement_id": agreement["id"], "artwork_id": self.collab["id"],
             "stage": "出库", "code": "NEW-1", "scanned_by": "库管员", "at": "2026-04-16"}
        )
        self.registry.sign_handover(event["id"], {"party": "出借馆", "actor": "丁馆代表"})
        self.registry.sign_handover(event["id"], {"party": "运输方", "actor": "押运员甲"})


class HandoverTest(unittest.TestCase):
    def setUp(self):
        self.registry = LoanRegistry()
        self.scroll, self.seg_ids = make_long_scroll(self.registry)
        self.agreement = self.registry.create_agreement(
            {
                "lender": "甲博物馆", "borrower": "本馆",
                "scope": [{"artwork_id": self.scroll["id"],
                           "segment_ids": [self.seg_ids["seg_a"], self.seg_ids["seg_b"]]}],
                "start": "2026-03-01", "end": "2026-06-30",
                "galleries": ["一号厅"], "max_lux": 50,
                "transport": "恒温恒湿专车", "insurance": "墙到墙全额",
                "digital_uses": ["官网展页"],
            }
        )

    def _run_stage(self, stage, code, at, extra=None):
        payload = {
            "agreement_id": self.agreement["id"], "artwork_id": self.scroll["id"],
            "stage": stage, "code": code, "scanned_by": "协调员", "at": at,
        }
        if extra:
            payload.update(extra)
        event = self.registry.scan_handover(payload)
        self.assertEqual(event["status"], "待签认")
        from_party, to_party = event["from_party"], event["to_party"]
        self.registry.sign_handover(event["id"], {"party": from_party, "actor": f"{from_party}代表"})
        signed = self.registry.sign_handover(
            event["id"], {"party": to_party, "actor": f"{to_party}代表"}
        )
        self.assertEqual(signed["status"], "已完成")
        return event["id"]

    def test_full_chain_in_order_with_dual_signatures(self):
        self._run_stage("出库", "S-1", "2026-03-02")
        self._run_stage("到馆", "S-2", "2026-03-05")
        self._run_stage("布展", "S-3", "2026-03-10",
                        {"gallery": "一号厅", "lux": 45,
                         "segment_conditions": {self.seg_ids["seg_b"]: "画心完好"}})
        state = self.registry.artwork_state(self.scroll["id"])
        self.assertEqual(state["state"], "展出中")
        self.assertEqual(state["custodian"], "承借馆")
        self.assertEqual(state["installed_gallery"], "一号厅")
        self._run_stage("撤展", "S-4", "2026-06-20")
        self._run_stage("归还", "S-5", "2026-06-25")
        final = self.registry.artwork_state(self.scroll["id"])
        self.assertEqual(final["state"], "已归还")
        self.assertEqual(final["custodian"], "出借馆")

    def test_duplicate_scan_does_not_create_second_handover(self):
        first = self.registry.scan_handover(
            {"agreement_id": self.agreement["id"], "artwork_id": self.scroll["id"],
             "stage": "出库", "code": "DUP-1", "scanned_by": "库管员", "at": "2026-03-02"}
        )
        second = self.registry.scan_handover(
            {"agreement_id": self.agreement["id"], "artwork_id": self.scroll["id"],
             "stage": "出库", "code": "DUP-1", "scanned_by": "另一台终端", "at": "2026-03-02"}
        )
        self.assertTrue(second["already_scanned"])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.registry.events), 1)

    def test_stages_cannot_skip_ahead(self):
        with self.assertRaises(DomainError) as error:
            self.registry.scan_handover(
                {"agreement_id": self.agreement["id"], "artwork_id": self.scroll["id"],
                 "stage": "布展", "code": "SKIP-1", "scanned_by": "x", "at": "2026-03-10",
                 "gallery": "一号厅", "lux": 40}
            )
        self.assertEqual(error.exception.http_status, 409)

    def test_signature_must_come_from_one_of_two_parties(self):
        event = self.registry.scan_handover(
            {"agreement_id": self.agreement["id"], "artwork_id": self.scroll["id"],
             "stage": "出库", "code": "SIG-1", "scanned_by": "x", "at": "2026-03-02"}
        )
        with self.assertRaises(DomainError):
            self.registry.sign_handover(event["id"], {"party": "策展人", "actor": "越权者"})

    def test_installation_rejects_unauthorized_gallery_and_excessive_lux(self):
        self._run_stage("出库", "G-1", "2026-03-02")
        self._run_stage("到馆", "G-2", "2026-03-05")
        with self.assertRaises(DomainError):
            self.registry.scan_handover(
                {"agreement_id": self.agreement["id"], "artwork_id": self.scroll["id"],
                 "stage": "布展", "code": "G-3", "scanned_by": "x", "at": "2026-03-10",
                 "gallery": "三号厅", "lux": 45}
            )
        with self.assertRaises(DomainError):
            self.registry.scan_handover(
                {"agreement_id": self.agreement["id"], "artwork_id": self.scroll["id"],
                 "stage": "布展", "code": "G-4", "scanned_by": "x", "at": "2026-03-10",
                 "gallery": "一号厅", "lux": 120}
            )


class DamageFreezeTest(unittest.TestCase):
    def setUp(self):
        self.registry = LoanRegistry()
        self.scroll, self.seg_ids = make_long_scroll(self.registry)
        self.agreement = self.registry.create_agreement(
            {
                "lender": "甲博物馆", "borrower": "本馆",
                "scope": [{"artwork_id": self.scroll["id"],
                           "segment_ids": [self.seg_ids["seg_a"], self.seg_ids["seg_b"]]}],
                "start": "2026-03-01", "end": "2026-06-30",
                "galleries": ["一号厅"], "max_lux": 50,
            }
        )

    def _complete(self, stage, code, at, extra=None):
        payload = {"agreement_id": self.agreement["id"], "artwork_id": self.scroll["id"],
                   "stage": stage, "code": code, "scanned_by": "x", "at": at}
        if extra:
            payload.update(extra)
        event = self.registry.scan_handover(payload)
        self.registry.sign_handover(event["id"], {"party": event["from_party"], "actor": "甲"})
        self.registry.sign_handover(event["id"], {"party": event["to_party"], "actor": "乙"})
        return event["id"]

    def test_damage_freezes_following_actions_and_preserves_image_hashes(self):
        self._complete("出库", "F-1", "2026-03-02")
        self._complete("到馆", "F-2", "2026-03-05")
        risk = self.registry.report_damage(
            {
                "artwork_id": self.scroll["id"], "segment_id": self.seg_ids["seg_b"],
                "description": "画心上端发现新折痕",
                "image_before_hash": "h" + "b" * 62,
                "image_after_hash": "h" + "a" * 62,
                "reported_by": "布展员",
            }
        )
        self.assertEqual(risk["status"], "未解除")
        state = self.registry.artwork_state(self.scroll["id"])
        self.assertTrue(state["frozen"])
        self.assertEqual(state["state"], "冻结")

        with self.assertRaises(DomainError) as error:
            self._complete("布展", "F-3", "2026-03-10",
                           {"gallery": "一号厅", "lux": 40})
        self.assertEqual(error.exception.http_status, 409)

        # 风险解除后冻结方可恢复，图像哈希始终可查。
        self.registry.resolve_risk(risk["id"], {"resolution": "修复补色并经出借馆确认",
                                                "actor": "文保中心"})
        stored = self.registry.risk_view(risk["id"])
        self.assertEqual(stored["image_before_hash"], "h" + "b" * 62)
        self.assertEqual(stored["image_after_hash"], "h" + "a" * 62)
        self._complete("布展", "F-4", "2026-03-12",
                       {"gallery": "一号厅", "lux": 40})
        self.assertFalse(self.registry.artwork_state(self.scroll["id"])["frozen"])


class LabelSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.registry = LoanRegistry()
        self.scroll, self.seg_ids = make_long_scroll(self.registry)
        self.solo, self.collab = make_solo_and_collab(self.registry)
        self.agreement = self.registry.create_agreement(
            {
                "lender": "甲博物馆", "borrower": "本馆",
                "scope": [{"artwork_id": self.scroll["id"],
                           "segment_ids": [self.seg_ids["seg_a"], self.seg_ids["seg_b"]]}],
                "start": "2026-03-01", "end": "2026-06-30",
                "galleries": ["一号厅"], "max_lux": 50,
                "transport": "恒温恒湿专车", "insurance": "墙到墙全额",
                "digital_uses": ["官网展页"],
            }
        )
        self.registry.add_relation(
            {"from_artwork_id": self.scroll["id"], "to_artwork_id": self.collab["id"],
             "relation": "雅集参与者后世纪念"}
        )
        self.label = self.registry.create_label(
            {"title": "会面百年", "narrative": "据雅集图卷考证……",
             "artwork_ids": [self.scroll["id"], self.collab["id"]], "curator": "林策展"}
        )
        self.registry.publish_label(self.label["id"], {"published_at": "2026-03-01"})

    def test_published_snapshot_is_locked_against_later_corrections(self):
        published = self.registry.label_view(self.label["id"])["versions"][0]
        original_hash = published["snapshot_hash"]
        self.assertEqual(published["status"], "已发布")

        # 发布后：学术更正补入一位作者、协议改期、追加权属注记。
        self.registry.add_contribution(
            self.scroll["id"],
            {"artist": "补考作者", "role": "题跋", "order": 17,
             "segment_id": self.seg_ids["seg_c"]},
        )
        self.registry.reschedule_agreement(
            self.agreement["id"],
            {"start": "2026-04-01", "end": "2026-07-31", "reason": "跨馆改期"},
        )
        self.registry.set_ownership(
            self.scroll["id"],
            {"owner": "甲博物馆（新征集档案确认）", "segment_id": self.seg_ids["seg_b"]},
        )

        old = self.registry.label_view(self.label["id"])["versions"][0]
        self.assertEqual(old["snapshot_hash"], original_hash)
        # 旧快照内容也未被暗中改写：仍为发布时的十六位贡献与旧展期。
        self.assertEqual(
            sum(len(s["contributions"]) for s in old["snapshot"]["artworks"][0]["segments"]),
            16,
        )
        self.assertEqual(old["snapshot"]["artworks"][0]["agreement"]["end"], "2026-06-30")

    def test_revision_creates_new_version_and_trace_locates_everything(self):
        self.registry.revise_label(
            self.label["id"], {"narrative": "据新发现信札更正会面年份……"}
        )
        self.registry.publish_label(self.label["id"], {"published_at": "2026-05-01"})

        # 从旧版展签出发仍能定位实体与贡献区段。
        trace_v1 = self.registry.trace_label(self.label["id"], version=1)
        self.assertEqual(trace_v1["version"], 1)
        entity = trace_v1["entities"][0]
        self.assertEqual(entity["artwork_id"], self.scroll["id"])
        self.assertTrue(any(s["name"] == "画心" for s in entity["segments"]))
        painted = next(s for s in entity["segments"] if s["name"] == "画心")
        self.assertEqual(len(painted["contributions"]), 8)
        self.assertEqual(entity["authorization"]["agreement_id"], self.agreement["id"])
        self.assertEqual(entity["authorization"]["period"]["end"], "2026-06-30")

        # 此后跨馆改期：展签证据快照保持旧展期，追踪中的授权范围反映当前展期。
        self.registry.reschedule_agreement(
            self.agreement["id"],
            {"start": "2026-04-01", "end": "2026-07-31", "reason": "跨馆改期"},
        )
        trace_after = self.registry.trace_label(self.label["id"], version=1)
        entity = trace_after["entities"][0]
        self.assertEqual(entity["authorization"]["period"]["end"], "2026-07-31")
        self.assertEqual(
            entity["state"]["state"], "待出库",  # 协议已改期但尚无任何交接
        )

        # 局部状态争议：画心报损后，追踪中精确落到区段且风险未解除。
        risk = self.registry.report_damage(
            {
                "artwork_id": self.scroll["id"], "segment_id": self.seg_ids["seg_b"],
                "description": "画心争议折痕",
                "image_before_hash": "h" + "1" * 62,
                "image_after_hash": "h" + "2" * 62,
                "reported_by": "策展助理",
            }
        )
        trace = self.registry.trace_label(self.label["id"])
        scroll_entity = trace["entities"][0]
        self.assertEqual(scroll_entity["state"]["state"], "冻结")
        self.assertEqual(scroll_entity["state"]["custodian"], "冻结（责任待界定）")
        seg = next(s for s in scroll_entity["segments"] if s["name"] == "画心")
        self.assertEqual(len(seg["open_risks"]), 1)
        self.assertEqual(seg["open_risks"][0]["risk_id"], risk["id"])
        self.assertEqual(len(scroll_entity["open_risks"]), 1)
        # 关系证据仍来自该版本发布时的快照。
        self.assertEqual(len(trace["relations"]), 1)


if __name__ == "__main__":
    unittest.main()
