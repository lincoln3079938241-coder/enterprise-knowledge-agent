"""LLM adapter. Import external SDK only when live mode is selected."""
from __future__ import annotations
import json
import os
from urllib.parse import urlparse


class LiveChatModel:
    def __init__(self):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install dependency: python -m pip install -r requirements.txt") from exc
        api_key = os.environ.get("OPENAI_API_KEY", "")
        model = os.environ.get("OPENAI_MODEL", "")
        if not api_key or not model:
            raise RuntimeError("Set OPENAI_API_KEY and OPENAI_MODEL via environment variables")
        base_url = os.environ.get("OPENAI_BASE_URL")
        self.is_deepseek_official = model == "deepseek-flash"
        if self.is_deepseek_official:
            if not base_url:
                raise RuntimeError("Set OPENAI_BASE_URL for the DeepSeek official API")
            if urlparse(base_url).hostname != "api.deepseek.com":
                raise RuntimeError(
                    "OPENAI_BASE_URL must use api.deepseek.com for the requested DeepSeek official API"
                )
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=35, max_retries=1)
        self.model = model

    def next(self, messages: list[dict], tools: list[dict]) -> dict:
        request = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
        }
        if self.is_deepseek_official:
            # DeepSeek enables thinking by default, but required/named tool_choice
            # is only valid in non-thinking mode.
            request["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            request["parallel_tool_calls"] = False
        response = self.client.chat.completions.create(**request)
        msg = response.choices[0].message
        calls = []
        for call in (msg.tool_calls or []):
            calls.append({"id": call.id, "name": call.function.name,
                          "arguments_json": call.function.arguments})
        usage = response.usage
        usage_data = {}
        if usage:
            usage_data = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            for field in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
                value = getattr(usage, field, None)
                if value is not None:
                    usage_data[field] = value
        return {"content": msg.content or "", "tool_calls": calls, "usage": usage_data}
