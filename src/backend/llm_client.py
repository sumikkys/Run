from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 12,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or ""
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-pro"
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
            "max_tokens": 650,
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
        "task": "基于人工标注经验帖，输出给赶车用户看的极简换乘建议。不要重新计算可行性，不要编造时刻，只解释该怎么走和风险点。",
    }
    try:
        text = client.chat(
            [
                {
                    "role": "system",
                    "content": "你是 Run! 出行救急插件的行动卡文案助手。规则引擎已经完成严肃判断，你只负责把人工经验整理为可执行中文提示。",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
        validation_error = validate_action_card_text(text, compact_result)
        if validation_error:
            raise RuntimeError(f"llm_output_failed_validation: {validation_error}")
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
        "task": (
            "请生成 Run! 误车救急行动卡。只能总结规则引擎已经验证过的方案，"
            "不要编造车次、票价、时刻或余票，也不要从候选方案中重新选择。"
            "输出包括：一句话结论、现在立刻做什么、为什么不去原站、换乘/失败阈值、风险提示。"
            "中文，短句，适合手机卡片。"
        ),
    }
    try:
        text = client.chat(
            [
                {
                    "role": "system",
                    "content": "你是 Run! 出行救急插件的行动卡助手。规则引擎负责计算，你负责把结果压缩成可执行行动卡。",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
        return {"source": "deepseek", "text": text}
    except Exception as exc:
        rescue = rule_engine_result.get("rescue_search", {})
        recommendation = rescue.get("recommendation") if isinstance(rescue, dict) else None
        if isinstance(recommendation, dict):
            train = recommendation["rescue_train"]
            rejoin = recommendation["original_train_rejoin"]
            text = (
                f"结论：原站已赶不上，优先买 {train['train_no']} 从{train['from_station']}去{train['to_station']}，"
                f"{train['arrival_time']} 到达后在{rejoin['station']}接回原车 {rejoin['train_no']}。"
                f"接回原车开车时间 {rejoin['departure_time']}，当前换乘余量约 {recommendation['transfer_margin_min']} 分钟。"
                "若 ETA 再增加或余票消失，立即切换 Plan B。"
            )
        else:
            text = "结论：暂未找到可验证追车方案，建议进入改签、退票重买或航班兜底。"
        return {"source": "template_fallback", "text": text, "error": str(exc)}


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
