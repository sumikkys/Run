from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CARD_JSON = REPO_ROOT / "card.json"


ACTION_CARD_FIELDS = {
    "行动简述": [],
    "花费": [],
    "出发时间": [],
    "预计到达地": [],
    "预计到达时间": [],
    "原因/说明": [],
}


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 12,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or ""
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
        self.base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/chat/completions"
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        errors: list[str] = []
        models = [self.model]
        for fallback in ("deepseek-chat", "deepseek-reasoner"):
            if fallback not in models:
                models.append(fallback)
        for model in models:
            try:
                return self._chat_with_model(messages, model)
            except Exception as exc:
                errors.append(f"{model}: {exc}")
        raise RuntimeError("; ".join(errors))

    def _chat_with_model(self, messages: list[dict[str, str]], model: str) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 900,
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            raise RuntimeError(f"HTTP {exc.code}: {body[:240]}") from exc
        return data["choices"][0]["message"]["content"].strip()


def generate_transfer_advice(
    transfer_entry: dict[str, object] | None,
    candidate: dict[str, object],
    client: DeepSeekClient | None = None,
) -> dict[str, object]:
    if transfer_entry is None:
        return {
            "source": "template_no_manual_transfer",
            "text": "该换乘点暂无人工标注。规则引擎已按同站换乘默认缓冲处理；现场请优先看站内指示牌，保持足够提前量。",
        }

    client = client or DeepSeekClient()
    prompt = {
        "manual_transfer": transfer_entry,
        "candidate_plan": candidate,
        "task": (
            "基于人工标注经验，输出给赶车用户看的极简换乘建议。"
            "不要重新计算可行性，不要编造时刻，只解释该怎么走和风险点。"
        ),
    }
    try:
        text = client.chat(
            [
                {
                    "role": "system",
                    "content": "你是 Run! 出行救急插件的行动卡文案助手。规则引擎已经完成判断，你只负责把人工经验整理为可执行中文提示。",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
        return {"source": "deepseek", "text": text}
    except Exception as exc:
        advice = str(transfer_entry.get("advice", "")).strip()
        text = (
            f"人工标注建议：{advice[:220]} "
            f"预计换乘缓冲 p90 为 {transfer_entry.get('p90_min')} 分钟。"
        )
        return {"source": "template_fallback", "text": text, "error": str(exc)}


def generate_action_card(
    rule_engine_result: dict[str, object],
    client: DeepSeekClient | None = None,
) -> dict[str, object]:
    client = client or DeepSeekClient()
    compact_result = compact_action_card_payload(rule_engine_result)
    prompt = {
        "verified_result": compact_result,
        "required_output": ACTION_CARD_FIELDS,
        "field_rules": {
            "行动简述": "数组。每一项是一条可执行动作，例如购票、前往车站、乘车、换乘。",
            "花费": "数组。每一项对应同下标动作的花费；没有可靠数据写'未知'，不要编造。",
            "出发时间": "数组。每一项对应同下标动作的开始/出发时间；没有可靠数据写'未知'。",
            "预计到达地": "数组。每一项对应同下标动作预计到达的地点。",
            "预计到达时间": "数组。每一项对应同下标动作预计到达时间；没有可靠数据写'未知'。",
            "原因/说明": "数组。每一项解释对应动作的依据、风险、余票、换乘余量或失败阈值。",
        },
        "hard_constraints": [
            "只能使用 verified_result 中已有的信息。",
            "不要编造车次、票价、时刻、地点、余票状态。",
            "不要重新选择方案，只能总结规则引擎的 recommendation。",
            "所有字段都必须存在，且都必须是数组。",
            "所有数组长度必须一致，同一个下标代表同一个执行步骤。",
            "没有可靠数据时填字符串'未知'。",
            "输出必须且只能是一个 Markdown 代码块，代码块语言为 json。",
            "代码块外不要输出任何解释。",
        ],
        "expected_shape": "```json\n{\"行动简述\": [], \"花费\": [], \"出发时间\": [], \"预计到达地\": [], \"预计到达时间\": [], \"原因/说明\": []}\n```",
    }
    try:
        text = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 Run! 出行救急插件的结构化输出助手。"
                        "规则引擎负责计算，你只把已验证结果整理为前端可解析的 JSON。"
                        "必须返回被 ```json 和 ``` 包裹的 JSON 对象。"
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
        text = normalize_action_card_codeblock(text)
        save_action_card_json(text)
        return {"source": "deepseek", "text": text}
    except Exception as exc:
        text = build_template_action_card_codeblock(rule_engine_result)
        save_action_card_json(text)
        return {"source": "template_fallback", "text": text, "error": str(exc)}


def normalize_action_card_codeblock(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        return stripped
    parsed = extract_json_object(stripped)
    if parsed is None:
        return stripped
    return "```json\n" + json.dumps(parsed, ensure_ascii=False, indent=2) + "\n```"


def extract_json_object(text: str) -> dict[str, object] | None:
    codeblock = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if codeblock:
        text = codeblock.group(1)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def save_action_card_json(text: str, path: str | Path = DEFAULT_CARD_JSON) -> None:
    payload = extract_json_object(text)
    if payload is None:
        raise ValueError("action card text does not contain a JSON object")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def build_template_action_card_codeblock(rule_engine_result: dict[str, object]) -> str:
    rescue = rule_engine_result.get("rescue_search", {})
    recommendation = rescue.get("recommendation") if isinstance(rescue, dict) else None
    if isinstance(recommendation, dict):
        train = recommendation["rescue_train"]
        rejoin = recommendation["original_train_rejoin"]
        inventory = recommendation.get("inventory") or {}
        payload = {
            "行动简述": [
                f"购买并乘坐 {train.get('train_no', '未知')}",
                f"到达后在{rejoin.get('station', '未知')}接回原车 {rejoin.get('train_no', '未知')}",
            ],
            "花费": [
                f"{inventory.get('price_cny')}元" if inventory.get("price_cny") is not None else "未知",
                "0元",
            ],
            "出发时间": [
                str(train.get("departure_time") or "未知"),
                str(rejoin.get("departure_time") or "未知"),
            ],
            "预计到达地": [
                str(train.get("to_station") or "未知"),
                str(rejoin.get("destination_station") or "未知"),
            ],
            "预计到达时间": [
                str(train.get("arrival_time") or "未知"),
                "未知",
            ],
            "原因/说明": [
                f"补救车余票状态：{inventory.get('status', '未知')}；风险：{inventory.get('risk', '未知')}",
                f"换乘余量约 {recommendation.get('transfer_margin_min', '未知')} 分钟；若 ETA 增加或余票消失，切换 Plan B",
            ],
        }
    else:
        payload = {
            "行动简述": ["进入改签、退票重买或航班兜底"],
            "花费": ["未知"],
            "出发时间": ["未知"],
            "预计到达地": ["未知"],
            "预计到达时间": ["未知"],
            "原因/说明": ["规则引擎暂未找到可验证追车方案"],
        }
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def compact_action_card_payload(rule_engine_result: dict[str, object]) -> dict[str, object]:
    rescue = rule_engine_result.get("rescue_search", {})
    recommendation = rescue.get("recommendation") if isinstance(rescue, dict) else None
    first = rule_engine_result.get("first_reminder", {})
    second = rule_engine_result.get("second_reminder", {})
    if not isinstance(recommendation, dict):
        return {
            "first_reminder": first,
            "second_reminder": second,
            "recommendation": None,
        }
    return {
        "first_reminder": {
            "can_catch": first.get("can_catch"),
            "safety_margin_min": first.get("safety_margin_min"),
            "departure_time": first.get("departure_time"),
        },
        "second_reminder": {
            "can_catch": second.get("can_catch"),
            "safety_margin_min": second.get("safety_margin_min"),
            "reason": second.get("reason"),
            "eta_to_origin": second.get("eta_to_origin"),
            "station_buffer": second.get("station_buffer"),
        },
        "recommendation": {
            "title": recommendation.get("title"),
            "rescue_train": recommendation.get("rescue_train"),
            "original_train_rejoin": recommendation.get("original_train_rejoin"),
            "transfer_margin_min": recommendation.get("transfer_margin_min"),
            "inventory": recommendation.get("inventory"),
            "data_sources": recommendation.get("data_sources"),
        },
    }


def validate_action_card_text(text: str, payload: dict[str, object]) -> str | None:
    allowed = collect_allowed_time_strings(payload)
    used_times = set(re.findall(r"\b\d{1,2}:\d{2}\b", text))
    unknown_times = sorted(time for time in used_times if time not in allowed)
    if unknown_times:
        return f"unknown time strings: {', '.join(unknown_times)}"
    return None


def collect_allowed_time_strings(payload: dict[str, object]) -> set[str]:
    serialized = json.dumps(payload, ensure_ascii=False)
    times = set(re.findall(r"\b\d{1,2}:\d{2}\b", serialized))
    normalized = set(times)
    for time in times:
        hour, minute = time.split(":")
        normalized.add(f"{int(hour):02d}:{minute}")
    return normalized
