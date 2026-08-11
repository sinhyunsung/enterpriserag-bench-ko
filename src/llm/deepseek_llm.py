import json
import os
from collections.abc import Generator
from typing import Any

from openai import OpenAI

from src.llm.interface import LLMInterface, Message, ReasoningLevel, ToolCall
from src.llm.tracing import init_tracing, is_tracing_enabled


DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_NAME = os.environ.get("DEEPSEEK_MODEL_NAME", "deepseek-v4-pro")
DEEPSEEK_CHEAP_MODEL_NAME = os.environ.get(
    "DEEPSEEK_CHEAP_MODEL_NAME", "deepseek-v4-flash"
)
# 문서 하나가 길어서 기본값으로는 잘린다. 잘리면 JSON 이 깨져 그 문서를 통째로 버리게 된다
DEEPSEEK_MAX_TOKENS = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "16000"))


class DeepSeekLLM(LLMInterface):
    """
    DeepSeek implementation of the LLM interface.

    Why a separate class instead of pointing OpenAILLM at DeepSeek's base_url:
    OpenAILLM uses the **Responses API** (``client.responses.create``) with a
    ``reasoning`` parameter.  DeepSeek only speaks **Chat Completions**, so the
    request shape, the streaming events and the tool-call format all differ.
    Reusing OpenAILLM would fail on the very first call.

    Reasoning is deliberately **not** forwarded.  Document generation is a
    fixed-format task, and DeepSeek has been observed spending its whole token
    budget on reasoning and returning an empty body.  ``reasoning_level`` is
    accepted so the call sites stay identical, and ignored.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        tools: list[dict] | None = None,
        quiet: bool = False,
        reasoning_level: ReasoningLevel = "medium",
    ):
        """
        Args:
            api_key: DeepSeek API key. Defaults to DEEPSEEK_API_KEY env var.
            model: Model to use. Defaults to DEEPSEEK_MODEL_NAME env var.
            tools: List of tool schemas in OpenAI format.
            quiet: If True, suppress status print statements.
            reasoning_level: Accepted for interface compatibility, ignored.
        """
        self.api_key = api_key or DEEPSEEK_API_KEY
        if not self.api_key:
            raise ValueError(
                "DeepSeek API key required. Set DEEPSEEK_API_KEY env var or pass api_key."
            )
        self.model = model or DEEPSEEK_MODEL_NAME
        self.tools = tools
        self.quiet = quiet
        self.reasoning_level = reasoning_level

        if is_tracing_enabled():
            init_tracing()
        self.client = OpenAI(api_key=self.api_key, base_url=DEEPSEEK_BASE_URL)

    @staticmethod
    def _chat_tools(tools: list[dict]) -> list[dict[str, Any]]:
        """
        Convert Responses-style tool schemas to the Chat Completions shape.

        This repository declares tools flat, as the Responses API wants them::

            {"type": "function", "name": ..., "parameters": {...}}

        Chat Completions nests the same fields under ``function`` and rejects the
        flat form outright::

            missing field `function`

        Schemas that already use the nested form are passed through, so a caller
        can hand over either shape.
        """
        converted: list[dict[str, Any]] = []
        for tool in tools:
            if "function" in tool:
                converted.append(tool)
                continue
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    },
                }
            )
        return converted

    def _build_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """
        Convert our Message list to Chat Completions format.

        Chat Completions keeps tool calls **on the assistant message** and
        answers them with a separate ``role: "tool"`` message keyed by
        ``tool_call_id`` — unlike the Responses API, which uses standalone
        ``function_call`` / ``function_call_output`` items.
        """
        out: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role in ("system", "user", "assistant"):
                out.append({"role": msg.role, "content": msg.content})
            elif msg.role == "tool_call" and msg.tool_call:
                out.append(
                    {
                        "role": "assistant",
                        "content": msg.content or None,
                        "tool_calls": [
                            {
                                "id": msg.tool_call.call_id,
                                "type": "function",
                                "function": {
                                    "name": msg.tool_call.name,
                                    "arguments": json.dumps(
                                        msg.tool_call.args, ensure_ascii=False
                                    ),
                                },
                            }
                        ],
                    }
                )
            elif msg.role == "tool_result" and msg.call_id:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.call_id,
                        "content": msg.content,
                    }
                )

        return out

    def generate(
        self, messages: list[Message]
    ) -> Generator[str | ToolCall, None, None]:
        """
        Stream a response from DeepSeek.

        Yields:
            String chunks for text output, then one ToolCall per requested call.
        """
        if not self.quiet:
            print("Waiting on LLM...", flush=True)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(messages),
            "stream": True,
            "max_tokens": DEEPSEEK_MAX_TOKENS,
            # 추론을 끈다. 켜 두면 두 가지가 문제다 — 형식이 정해진 생성인데 예산을
            # 추론에 다 쓰고 본문을 비우는 응답이 나오고, 대화를 이어갈 때
            # reasoning_content 를 되돌려 보내라며 400 을 낸다.
            # OpenAI SDK 가 모르는 필드라 extra_body 로 실어 보낸다
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if self.tools:
            kwargs["tools"] = self._chat_tools(self.tools)

        stream = self.client.chat.completions.create(**kwargs)

        # Chat Completions streams tool calls in fragments identified by `index`,
        # so the name arrives once and the arguments arrive a few characters at a
        # time. They have to be stitched back together per index before use —
        # the Responses API delivers them as whole events instead.
        pending: dict[int, dict[str, str]] = {}
        announced: set[int] = set()

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "content", None):
                yield delta.content

            for fragment in getattr(delta, "tool_calls", None) or []:
                slot = pending.setdefault(
                    fragment.index, {"name": "", "call_id": "", "args": ""}
                )
                if fragment.id:
                    slot["call_id"] = fragment.id
                function = getattr(fragment, "function", None)
                if function is None:
                    continue
                if function.name:
                    slot["name"] = function.name
                    if not self.quiet and fragment.index not in announced:
                        announced.add(fragment.index)
                        yield f"\n[Tool Call: {function.name}]\n"
                if function.arguments:
                    slot["args"] += function.arguments
                    if not self.quiet:
                        yield function.arguments

        if pending and not self.quiet:
            yield "\n[/Tool Call]\n"

        for _, call in sorted(pending.items()):
            if not call["name"]:
                continue
            yield ToolCall(
                name=call["name"],
                args=json.loads(call["args"]) if call["args"] else {},
                call_id=call["call_id"],
            )
