"""馆际作品借展领域模型。

覆盖五类相互独立又彼此关联的信息：

* 作品实体、组成区段、作者贡献（含合作顺序与题跋）、历史展览、当前权属；
* 借展协议对展期、展厅、照度、运输、保险与数字传播用途的约束及改期修订；
* 出库、到馆、布展、撤展、归还五段交接，双方签认、扫码幂等、按序推进；
* 损伤即冻结后续动作，保全前后图像哈希，风险解除后方可恢复；
* 展签发布时锁定证据快照，此后学术更正只产生新版本，旧版不可变。
"""

import copy
import hashlib
import json
import threading
from datetime import date

# ---- 固定领域称谓 ---------------------------------------------------------

STAGES = ["出库", "到馆", "布展", "撤展", "归还"]

# 每一交接阶段的交出方与接收方，签认人必须代表这两方。
STAGE_PARTIES = {
    "出库": ("出借馆", "运输方"),
    "到馆": ("运输方", "承借馆"),
    "布展": ("承借馆", "策展人"),
    "撤展": ("策展人", "运输方"),
    "归还": ("运输方", "出借馆"),
}

# 已完成到某一阶段后，实体的当前保管责任方。
CUSTODIAN_AFTER = {
    "出库": "运输方",
    "到馆": "承借馆",
    "布展": "承借馆",
    "撤展": "运输方",
    "归还": "出借馆",
}


class DomainError(Exception):
    """领域规则被违反；http_status 决定对外返回的状态码。"""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _today():
    return date.today().isoformat()


def _parse_day(value, field):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise DomainError(f"{field} 必须是 ISO 日期（YYYY-MM-DD）")


def _sha256_json(payload):
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LoanRegistry:
    """内存登记处；所有写操作经由同一把锁，接口层按调用加锁。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._seq = 0
        self.artworks = {}
        self.agreements = {}
        self.events = []
        self.risks = []
        self.labels = {}
        self.relations = []
        self._scan_codes = {}  # 扫码码 -> 交接事件 id，全局唯一

    # 上下文：HTTP 层一次请求一个事务边界。
    def locked(self):
        return self._lock

    def _new_id(self, prefix):
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    # ---- 作品实体与组成 ---------------------------------------------------

    def add_artwork(self, payload):
        title = (payload or {}).get("title")
        if not title:
            raise DomainError("作品必须提供 title")
        category = payload.get("category", "独立作品")
        if category not in ("独立作品", "合作画", "历史图录", "长卷"):
            raise DomainError("category 只能是 独立作品/合作画/历史图录/长卷")
        artwork = {
            "id": self._new_id("art"),
            "title": title,
            "category": category,
            "year": payload.get("year"),
            "medium": payload.get("medium"),
            "segments": [],
            "contributions": [],
            "historical_exhibitions": [],
            "ownership_work": None,
            "ownership_segments": {},
            "created_at": _today(),
        }
        self.artworks[artwork["id"]] = artwork
        return self.artwork_view(artwork["id"])

    def add_segment(self, artwork_id, payload):
        artwork = self._artwork(artwork_id)
        name = (payload or {}).get("name")
        if not name:
            raise DomainError("区段必须提供 name")
        ordinal = payload.get("ordinal")
        if ordinal is None:
            ordinal = len(artwork["segments"]) + 1
        segment = {
            "id": self._new_id("seg"),
            "artwork_id": artwork_id,
            "ordinal": int(ordinal),
            "name": name,
            "description": payload.get("description", ""),
        }
        artwork["segments"].append(segment)
        artwork["segments"].sort(key=lambda item: item["ordinal"])
        return copy.deepcopy(segment)

    def add_contribution(self, artwork_id, payload):
        artwork = self._artwork(artwork_id)
        artist = (payload or {}).get("artist")
        if not artist:
            raise DomainError("贡献必须提供 artist")
        role = payload.get("role", "绘")
        segment_id = payload.get("segment_id")
        if segment_id is not None:
            if segment_id not in {s["id"] for s in artwork["segments"]}:
                raise DomainError("segment_id 不属于该作品", 404)
        contribution = {
            "id": self._new_id("ctr"),
            "artwork_id": artwork_id,
            "segment_id": segment_id,  # None 表示对整卷/整幅的贡献
            "artist": artist,
            "role": role,              # 绘、题跋、题签、鉴藏等
            "order": int(payload.get("order", 0)),  # 合作顺序
            "note": payload.get("note", ""),
        }
        artwork["contributions"].append(contribution)
        artwork["contributions"].sort(key=lambda item: (item["order"], item["id"]))
        return copy.deepcopy(contribution)

    def add_historical_exhibition(self, artwork_id, payload):
        artwork = self._artwork(artwork_id)
        for field in ("title", "year", "venue"):
            if not (payload or {}).get(field):
                raise DomainError(f"历史展览必须提供 {field}")
        record = {
            "id": self._new_id("hex"),
            "artwork_id": artwork_id,
            "title": payload["title"],
            "year": payload["year"],
            "venue": payload["venue"],
        }
        artwork["historical_exhibitions"].append(record)
        return copy.deepcopy(record)

    def set_ownership(self, artwork_id, payload):
        artwork = self._artwork(artwork_id)
        owner = (payload or {}).get("owner")
        if not owner:
            raise DomainError("权属必须提供 owner")
        segment_id = payload.get("segment_id")
        record = {
            "owner": owner,
            "note": payload.get("note", ""),
            "since": payload.get("since", _today()),
        }
        if segment_id is None:
            artwork["ownership_work"] = record
        else:
            if segment_id not in {s["id"] for s in artwork["segments"]}:
                raise DomainError("segment_id 不属于该作品", 404)
            artwork["ownership_segments"][segment_id] = record
        return self.artwork_view(artwork_id)

    def add_relation(self, payload):
        for field in ("from_artwork_id", "to_artwork_id", "relation"):
            if not (payload or {}).get(field):
                raise DomainError(f"作品关系必须提供 {field}")
        self._artwork(payload["from_artwork_id"])
        self._artwork(payload["to_artwork_id"])
        relation = {
            "id": self._new_id("rel"),
            "from_artwork_id": payload["from_artwork_id"],
            "to_artwork_id": payload["to_artwork_id"],
            "relation": payload["relation"],
        }
        self.relations.append(relation)
        return copy.deepcopy(relation)

    def artwork_view(self, artwork_id):
        return copy.deepcopy(self._artwork(artwork_id))

    def _artwork(self, artwork_id):
        artwork = self.artworks.get(artwork_id)
        if artwork is None:
            raise DomainError(f"未知作品 {artwork_id}", 404)
        return artwork

    def _segment(self, artwork, segment_id):
        for segment in artwork["segments"]:
            if segment["id"] == segment_id:
                return segment
        raise DomainError(f"未知区段 {segment_id}", 404)

    # ---- 借展协议 ---------------------------------------------------------

    def create_agreement(self, payload):
        payload = payload or {}
        for field in ("lender", "borrower", "scope", "start", "end"):
            if not payload.get(field):
                raise DomainError(f"协议必须提供 {field}")
        scope = self._normalize_scope(payload["scope"])
        self._validate_scope_free(scope)
        start = _parse_day(payload["start"], "start")
        end = _parse_day(payload["end"], "end")
        if end < start:
            raise DomainError("协议结束日期不得早于开始日期")
        terms = self._terms_from_payload(payload)
        agreement = {
            "id": self._new_id("agr"),
            "lender": payload["lender"],
            "borrower": payload["borrower"],
            "scope": scope,
            "initial": {"start": payload["start"], "end": payload["end"], **terms},
            "revisions": [],
            "created_at": _today(),
            "start": payload["start"],
            "end": payload["end"],
            **terms,
        }
        self.agreements[agreement["id"]] = agreement
        return self.agreement_view(agreement["id"])

    def reschedule_agreement(self, agreement_id, payload):
        """跨馆改期：产生新修订，旧展期保留可查，未决交接按新展期校验。"""
        agreement = self._agreement(agreement_id)
        payload = payload or {}
        for field in ("start", "end", "reason"):
            if not payload.get(field):
                raise DomainError(f"改期必须提供 {field}")
        start = _parse_day(payload["start"], "start")
        end = _parse_day(payload["end"], "end")
        if end < start:
            raise DomainError("改期结束日期不得早于开始日期")
        revision = {
            "revision": len(agreement["revisions"]) + 2,
            "start": payload["start"],
            "end": payload["end"],
            "reason": payload["reason"],
            "at": payload.get("at", _today()),
        }
        agreement["revisions"].append(revision)
        agreement["start"] = payload["start"]
        agreement["end"] = payload["end"]
        return self.agreement_view(agreement_id)

    def amend_agreement(self, agreement_id, payload):
        """展厅、照度、数字用途等条款的变更同样留下修订痕迹。"""
        agreement = self._agreement(agreement_id)
        payload = payload or {}
        if not payload.get("reason"):
            raise DomainError("修订必须提供 reason")
        before = {k: agreement[k] for k in ("galleries", "max_lux", "transport", "insurance", "digital_uses")}
        terms = self._terms_from_payload({**before, **payload})
        revision = {
            "revision": len(agreement["revisions"]) + 2,
            "start": agreement["start"],
            "end": agreement["end"],
            "reason": payload["reason"],
            "at": payload.get("at", _today()),
            "changes": {
                key: {"from": before[key], "to": terms[key]}
                for key in ("galleries", "max_lux", "transport", "insurance", "digital_uses")
                if before[key] != terms[key]
            },
        }
        agreement.update(terms)
        agreement["revisions"].append(revision)
        return self.agreement_view(agreement_id)

    def agreement_view(self, agreement_id):
        return copy.deepcopy(self._agreement(agreement_id))

    def _agreement(self, agreement_id):
        agreement = self.agreements.get(agreement_id)
        if agreement is None:
            raise DomainError(f"未知协议 {agreement_id}", 404)
        return agreement

    @staticmethod
    def _terms_from_payload(payload):
        galleries = payload.get("galleries") or []
        if isinstance(galleries, str):
            galleries = [galleries]
        digital_uses = payload.get("digital_uses") or []
        if isinstance(digital_uses, str):
            digital_uses = [digital_uses]
        return {
            "galleries": list(galleries),
            "max_lux": int(payload.get("max_lux", 150)),
            "transport": payload.get("transport", ""),
            "insurance": payload.get("insurance", ""),
            "digital_uses": list(digital_uses),
        }

    def _normalize_scope(self, raw_scope):
        scope = []
        for entry in raw_scope:
            if isinstance(entry, str):
                artwork_id, segment_ids = entry, None
            else:
                artwork_id = entry.get("artwork_id")
                segment_ids = entry.get("segment_ids")
            self._artwork(artwork_id)
            if segment_ids:
                known = {s["id"] for s in self.artworks[artwork_id]["segments"]}
                unknown = [sid for sid in segment_ids if sid not in known]
                if unknown:
                    raise DomainError(f"协议区段不属于该作品: {unknown}", 404)
            scope.append({"artwork_id": artwork_id, "segment_ids": list(segment_ids or [])})
        return scope

    def _validate_scope_free(self, scope):
        for entry in scope:
            for agreement in self.agreements.values():
                for covered in agreement["scope"]:
                    if covered["artwork_id"] != entry["artwork_id"]:
                        continue
                    if not entry["segment_ids"] or not covered["segment_ids"]:
                        raise DomainError(
                            f"作品 {entry['artwork_id']} 已存在生效协议 {agreement['id']}", 409
                        )
                    overlap = set(entry["segment_ids"]) & set(covered["segment_ids"])
                    if overlap:
                        raise DomainError(
                            f"区段 {sorted(overlap)} 已由协议 {agreement['id']} 覆盖", 409
                        )

    def _agreement_for(self, artwork_id):
        for agreement in self.agreements.values():
            for covered in agreement["scope"]:
                if covered["artwork_id"] == artwork_id:
                    return agreement, covered
        return None, None

    def _require_agreement_covers(self, agreement_id, artwork_id, segment_ids):
        agreement = self._agreement(agreement_id)
        covered = next(
            (item for item in agreement["scope"] if item["artwork_id"] == artwork_id), None
        )
        if covered is None:
            raise DomainError(f"协议 {agreement_id} 不覆盖作品 {artwork_id}", 409)
        if segment_ids and covered["segment_ids"]:
            unknown = set(segment_ids) - set(covered["segment_ids"])
            if unknown:
                raise DomainError(f"协议不覆盖区段 {sorted(unknown)}", 409)
        return agreement, covered

    # ---- 五段交接 ---------------------------------------------------------

    def scan_handover(self, payload):
        """扫码发起交接。同一扫码码重复扫描不产生第二次交接。"""
        payload = payload or {}
        for field in ("agreement_id", "artwork_id", "stage", "code", "scanned_by"):
            if not payload.get(field):
                raise DomainError(f"交接扫码必须提供 {field}")
        artwork_id = payload["artwork_id"]
        stage = payload["stage"]
        if stage not in STAGES:
            raise DomainError(f"未知交接阶段 {stage}，合法值：{STAGES}")

        # 幂等：同一码永远只对应一次交接事件。
        existing_id = self._scan_codes.get(payload["code"])
        if existing_id is not None:
            event = next(item for item in self.events if item["id"] == existing_id)
            view = self.event_view(event["id"])
            view["already_scanned"] = True
            return view

        agreement, _ = self._require_agreement_covers(
            payload["agreement_id"], artwork_id, payload.get("segment_ids")
        )
        artwork = self._artwork(artwork_id)
        segment_ids = payload.get("segment_ids") or []
        for segment_id in segment_ids:
            self._segment(artwork, segment_id)

        self._assert_no_open_risk(artwork_id)
        self._assert_stage_due(artwork_id, stage)
        self._assert_within_period(agreement, payload.get("at"), stage)
        from_party, to_party = STAGE_PARTIES[stage]

        gallery = payload.get("gallery")
        lux = payload.get("lux")
        if stage == "布展":
            if not gallery:
                raise DomainError("布展交接必须登记展厅 gallery")
            if gallery not in agreement["galleries"]:
                raise DomainError(
                    f"展厅 {gallery} 不在协议约定范围 {agreement['galleries']}", 409
                )
            if lux is None:
                raise DomainError("布展交接必须登记照度 lux")
            if int(lux) > int(agreement["max_lux"]):
                raise DomainError(
                    f"照度 {lux} lux 超过协议上限 {agreement['max_lux']} lux", 409
                )

        event = {
            "id": self._new_id("hnd"),
            "agreement_id": agreement["id"],
            "artwork_id": artwork_id,
            "stage": stage,
            "code": payload["code"],
            "from_party": from_party,
            "to_party": to_party,
            "scanned_by": payload["scanned_by"],
            "at": payload.get("at", _today()),
            "gallery": gallery,
            "lux": int(lux) if lux is not None else None,
            "segment_ids": segment_ids,
            "segment_conditions": payload.get("segment_conditions", {}),
            "notes": payload.get("notes", ""),
            "signatures": {},  # party -> {"actor":..., "signed_at":...}
            "status": "待签认",
            "completed_at": None,
        }
        self.events.append(event)
        self._scan_codes[payload["code"]] = event["id"]
        return self.event_view(event["id"])

    def sign_handover(self, event_id, payload):
        payload = payload or {}
        event = self._event(event_id)
        if event["status"] == "已完成":
            if payload.get("party") in event["signatures"]:
                # 同一方重复签认只回显既有结果，不改变状态。
                return self.event_view(event_id)
            raise DomainError("该交接已完成签认", 409)
        party = payload.get("party")
        actor = payload.get("actor")
        if not party or not actor:
            raise DomainError("签认必须提供 party 与 actor")
        if party not in (event["from_party"], event["to_party"]):
            raise DomainError(
                f"签认方必须是交接双方之一：{event['from_party']}、{event['to_party']}"
            )
        event["signatures"][party] = {
            "actor": actor,
            "signed_at": payload.get("signed_at", _today()),
        }
        if event["from_party"] in event["signatures"] and event["to_party"] in event["signatures"]:
            event["status"] = "已完成"
            event["completed_at"] = payload.get("signed_at", _today())
        return self.event_view(event_id)

    def event_view(self, event_id):
        return copy.deepcopy(self._event(event_id))

    def _event(self, event_id):
        for event in self.events:
            if event["id"] == event_id:
                return event
        raise DomainError(f"未知交接事件 {event_id}", 404)

    def _completed_stages(self, artwork_id):
        done = set()
        for event in self.events:
            if event["artwork_id"] == artwork_id and event["status"] == "已完成":
                done.add(event["stage"])
        return done

    def _assert_stage_due(self, artwork_id, stage):
        index = STAGES.index(stage)
        if index > 0:
            previous = STAGES[index - 1]
            if previous not in self._completed_stages(artwork_id):
                raise DomainError(f"必须先完成 {previous} 交接，才能进行 {stage}", 409)
        if stage in self._completed_stages(artwork_id):
            raise DomainError(f"{stage} 交接已完成，不得重复交接", 409)
        pending = next(
            (
                event
                for event in self.events
                if event["artwork_id"] == artwork_id
                and event["stage"] == stage
                and event["status"] == "待签认"
            ),
            None,
        )
        if pending is not None:
            raise DomainError(f"{stage} 交接 {pending['id']} 尚待双方签认", 409)

    @staticmethod
    def _assert_within_period(agreement, day, stage):
        if not day:
            return
        current = _parse_day(day, "at")
        start = _parse_day(agreement["start"], "start")
        end = _parse_day(agreement["end"], "end")
        if current < start or current > end:
            raise DomainError(
                f"{stage}日期 {day} 落在当前展期 {agreement['start']}~{agreement['end']} 之外",
                409,
            )

    # ---- 损伤、冻结与风险 -------------------------------------------------

    def report_damage(self, payload):
        """登记损伤即冻结该作品的全部后续交接，并保全前后图像哈希。"""
        payload = payload or {}
        for field in ("artwork_id", "description", "image_before_hash", "image_after_hash", "reported_by"):
            if not payload.get(field):
                raise DomainError(f"损伤报告必须提供 {field}")
        artwork = self._artwork(payload["artwork_id"])
        segment_id = payload.get("segment_id")
        if segment_id is not None:
            self._segment(artwork, segment_id)
        risk = {
            "id": self._new_id("rsk"),
            "artwork_id": payload["artwork_id"],
            "segment_id": segment_id,
            "description": payload["description"],
            "image_before_hash": payload["image_before_hash"],
            "image_after_hash": payload["image_after_hash"],
            "reported_by": payload["reported_by"],
            "reported_at": payload.get("at", _today()),
            "status": "未解除",
            "resolution": None,
            "resolved_by": None,
            "resolved_at": None,
        }
        self.risks.append(risk)
        return copy.deepcopy(risk)

    def resolve_risk(self, risk_id, payload):
        payload = payload or {}
        risk = self._risk(risk_id)
        if risk["status"] == "已解除":
            raise DomainError("风险已解除，无需重复处理", 409)
        if not payload.get("resolution") or not payload.get("actor"):
            raise DomainError("解除风险必须提供 resolution 与 actor")
        risk["status"] = "已解除"
        risk["resolution"] = payload["resolution"]
        risk["resolved_by"] = payload["actor"]
        risk["resolved_at"] = payload.get("at", _today())
        return copy.deepcopy(risk)

    def risk_view(self, risk_id):
        return copy.deepcopy(self._risk(risk_id))

    def _risk(self, risk_id):
        for risk in self.risks:
            if risk["id"] == risk_id:
                return risk
        raise DomainError(f"未知风险 {risk_id}", 404)

    def _assert_no_open_risk(self, artwork_id):
        open_risks = [
            risk for risk in self.risks
            if risk["artwork_id"] == artwork_id and risk["status"] == "未解除"
        ]
        if open_risks:
            raise DomainError(
                f"作品存在未解除损伤风险 {[r['id'] for r in open_risks]}，后续动作已冻结", 409
            )

    def open_risks(self, artwork_id):
        return [
            copy.deepcopy(risk)
            for risk in self.risks
            if risk["artwork_id"] == artwork_id and risk["status"] == "未解除"
        ]

    # ---- 作品状态与保管责任 -----------------------------------------------

    def artwork_state(self, artwork_id):
        self._artwork(artwork_id)
        frozen = bool(self.open_risks(artwork_id))
        completed = self._completed_stages(artwork_id)
        agreement, _ = self._agreement_for(artwork_id)
        if frozen:
            state = "冻结"
        elif agreement is None:
            state = "洽借中"
        elif "归还" in completed:
            state = "已归还"
        elif "撤展" in completed:
            state = "运输中"
        elif "布展" in completed:
            state = "展出中"
        elif "到馆" in completed:
            state = "在馆"
        elif "出库" in completed:
            state = "运输中"
        else:
            state = "待出库"
        latest_stage = next(
            (stage for stage in reversed(STAGES) if stage in completed), None
        )
        custodian = CUSTODIAN_AFTER.get(latest_stage, "出借馆") if not frozen else "冻结（责任待界定）"
        gallery = None
        for event in self.events:
            if event["artwork_id"] == artwork_id and event["stage"] == "布展" and event["status"] == "已完成":
                gallery = event["gallery"]
        return {
            "artwork_id": artwork_id,
            "state": state,
            "frozen": frozen,
            "custodian": custodian,
            "completed_stages": [stage for stage in STAGES if stage in completed],
            "installed_gallery": gallery,
            "agreement_id": agreement["id"] if agreement else None,
        }

    # ---- 展签与证据快照 ---------------------------------------------------

    def create_label(self, payload):
        payload = payload or {}
        for field in ("title", "narrative", "curator"):
            if not payload.get(field):
                raise DomainError(f"展签必须提供 {field}")
        artwork_ids = payload.get("artwork_ids") or []
        if not artwork_ids:
            raise DomainError("展签至少引用一件作品")
        for artwork_id in artwork_ids:
            self._artwork(artwork_id)
        label = {
            "id": self._new_id("lbl"),
            "versions": [
                {
                    "version": 1,
                    "status": "草稿",
                    "title": payload["title"],
                    "narrative": payload["narrative"],
                    "artwork_ids": list(artwork_ids),
                    "curator": payload["curator"],
                    "created_at": _today(),
                    "published_at": None,
                    "snapshot": None,
                    "snapshot_hash": None,
                }
            ],
        }
        self.labels[label["id"]] = label
        return self.label_view(label["id"])

    def publish_label(self, label_id, payload):
        """发布日期确认即锁定证据快照；已发布版本不可再改。"""
        label = self._label(label_id)
        draft = label["versions"][-1]
        if draft["status"] == "已发布":
            raise DomainError("最新版本已发布，如需纳入更正请先创建新版本", 409)
        published_at = (payload or {}).get("published_at") or _today()
        snapshot = self._build_snapshot(draft["artwork_ids"], published_at)
        draft["status"] = "已发布"
        draft["published_at"] = published_at
        draft["snapshot"] = snapshot
        draft["snapshot_hash"] = _sha256_json(snapshot)
        return self.label_view(label_id)

    def revise_label(self, label_id, payload):
        """学术更正只能另立草稿版本，绝不回写已发布快照。"""
        label = self._label(label_id)
        latest = label["versions"][-1]
        if latest["status"] == "草稿":
            raise DomainError("当前已有待发布草稿，请先发布或放弃该草稿", 409)
        payload = payload or {}
        new_version = {
            "version": latest["version"] + 1,
            "status": "草稿",
            "title": payload.get("title", latest["title"]),
            "narrative": payload.get("narrative", latest["narrative"]),
            "artwork_ids": list(payload.get("artwork_ids", latest["artwork_ids"])),
            "curator": payload.get("curator", latest["curator"]),
            "created_at": _today(),
            "published_at": None,
            "snapshot": None,
            "snapshot_hash": None,
        }
        for artwork_id in new_version["artwork_ids"]:
            self._artwork(artwork_id)
        label["versions"].append(new_version)
        return self.label_view(label_id)

    def label_view(self, label_id):
        return copy.deepcopy(self._label(label_id))

    def _label(self, label_id):
        label = self.labels.get(label_id)
        if label is None:
            raise DomainError(f"未知展签 {label_id}", 404)
        return label

    def _published_version(self, label_id, version):
        label = self._label(label_id)
        if version is None:
            target = next(
                (item for item in reversed(label["versions"]) if item["status"] == "已发布"),
                None,
            )
        else:
            target = next((item for item in label["versions"] if item["version"] == version), None)
        if target is None:
            raise DomainError("展签尚未发布或版本不存在", 404)
        if target["status"] != "已发布":
            raise DomainError(f"版本 v{target['version']} 尚未发布", 409)
        return target

    def _build_snapshot(self, artwork_ids, published_at):
        artworks = []
        for artwork_id in artwork_ids:
            artwork = self._artwork(artwork_id)
            agreement, covered = self._agreement_for(artwork_id)
            artworks.append(
                {
                    "artwork_id": artwork["id"],
                    "title": artwork["title"],
                    "category": artwork["category"],
                    "year": artwork["year"],
                    "medium": artwork["medium"],
                    "ownership": {
                        "work": copy.deepcopy(artwork["ownership_work"]),
                        "segments": copy.deepcopy(artwork["ownership_segments"]),
                    },
                    "segments": [
                        {
                            "segment_id": segment["id"],
                            "ordinal": segment["ordinal"],
                            "name": segment["name"],
                            "description": segment["description"],
                            "contributions": [
                                {
                                    "artist": c["artist"],
                                    "role": c["role"],
                                    "order": c["order"],
                                    "note": c["note"],
                                }
                                for c in artwork["contributions"]
                                if c["segment_id"] == segment["id"]
                            ],
                        }
                        for segment in artwork["segments"]
                    ],
                    "contributions": [
                        {
                            "artist": c["artist"],
                            "role": c["role"],
                            "order": c["order"],
                            "note": c["note"],
                        }
                        for c in artwork["contributions"]
                        if c["segment_id"] is None
                    ],
                    "historical_exhibitions": copy.deepcopy(artwork["historical_exhibitions"]),
                    "agreement": (
                        {
                            "agreement_id": agreement["id"],
                            "lender": agreement["lender"],
                            "borrower": agreement["borrower"],
                            "start": agreement["start"],
                            "end": agreement["end"],
                            "revision": len(agreement["revisions"]) + 1,
                            "galleries": list(agreement["galleries"]),
                            "max_lux": agreement["max_lux"],
                            "transport": agreement["transport"],
                            "insurance": agreement["insurance"],
                            "digital_uses": list(agreement["digital_uses"]),
                            "scope_segment_ids": list(covered["segment_ids"]),
                        }
                        if agreement
                        else None
                    ),
                }
            )
        return {
            "published_at": published_at,
            "artworks": artworks,
            "relations": [
                {
                    "from_artwork_id": relation["from_artwork_id"],
                    "to_artwork_id": relation["to_artwork_id"],
                    "relation": relation["relation"],
                }
                for relation in self.relations
                if relation["from_artwork_id"] in artwork_ids
                and relation["to_artwork_id"] in artwork_ids
            ],
        }

    def trace_label(self, label_id, version=None):
        """从展签版本定位到实体、贡献区段、当前保管责任、授权范围与未解除风险。"""
        target = self._published_version(label_id, version)
        snapshot = target["snapshot"]
        entities = []
        for item in snapshot["artworks"]:
            artwork_id = item["artwork_id"]
            state = self.artwork_state(artwork_id)
            live_agreement, _ = self._agreement_for(artwork_id)
            latest_conditions = self._latest_segment_conditions(artwork_id)
            entities.append(
                {
                    "artwork_id": artwork_id,
                    "title_in_snapshot": item["title"],
                    "title_current": self.artworks[artwork_id]["title"],
                    "state": state,
                    "segments": [
                        {
                            "segment_id": segment["segment_id"],
                            "ordinal": segment["ordinal"],
                            "name": segment["name"],
                            "owner": (
                                item["ownership"]["segments"].get(segment["segment_id"])
                                or item["ownership"]["work"]
                                or {}
                            ).get("owner"),
                            "latest_condition": latest_conditions.get(segment["segment_id"]),
                            "contributions": segment["contributions"],
                            "open_risks": [
                                {
                                    "risk_id": risk["id"],
                                    "description": risk["description"],
                                    "image_before_hash": risk["image_before_hash"],
                                    "image_after_hash": risk["image_after_hash"],
                                    "reported_at": risk["reported_at"],
                                }
                                for risk in self.open_risks(artwork_id)
                                if risk["segment_id"] == segment["segment_id"]
                            ],
                        }
                        for segment in item["segments"]
                    ],
                    "whole_work_contributions": item["contributions"],
                    "custodian": state["custodian"],
                    "installed_gallery": state["installed_gallery"],
                    "authorization": (
                        {
                            "agreement_id": live_agreement["id"],
                            "lender": live_agreement["lender"],
                            "period": {"start": live_agreement["start"], "end": live_agreement["end"]},
                            "galleries": list(live_agreement["galleries"]),
                            "max_lux": live_agreement["max_lux"],
                            "transport": live_agreement["transport"],
                            "insurance": live_agreement["insurance"],
                            "digital_uses": list(live_agreement["digital_uses"]),
                        }
                        if live_agreement
                        else None
                    ),
                    "open_risks": [
                        {
                            "risk_id": risk["id"],
                            "segment_id": risk["segment_id"],
                            "description": risk["description"],
                            "image_before_hash": risk["image_before_hash"],
                            "image_after_hash": risk["image_after_hash"],
                            "reported_at": risk["reported_at"],
                        }
                        for risk in self.open_risks(artwork_id)
                    ],
                }
            )
        return {
            "label_id": label_id,
            "version": target["version"],
            "published_at": target["published_at"],
            "snapshot_hash": target["snapshot_hash"],
            "relations": snapshot["relations"],
            "entities": entities,
        }

    def _latest_segment_conditions(self, artwork_id):
        conditions = {}
        for event in sorted(self.events, key=lambda item: (item["at"], item["id"])):
            if event["artwork_id"] != artwork_id or event["status"] != "已完成":
                continue
            for segment_id, note in event["segment_conditions"].items():
                conditions[segment_id] = {"stage": event["stage"], "at": event["at"], "note": note}
        return conditions
