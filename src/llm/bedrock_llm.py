import os

import anthropic

from src.llm.anthropic_llm import AnthropicLLM
from src.llm.interface import ReasoningLevel
from src.llm.tracing import init_tracing, is_tracing_enabled


# Bedrock model IDs (cross-region inference profiles).
LLM_MODEL_NAME = os.environ.get(
    "LLM_MODEL_NAME", "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
)
CHEAP_LLM_MODEL_NAME = os.environ.get(
    "CHEAP_LLM_MODEL_NAME", "global.anthropic.claude-haiku-4-5-20251001-v1:0"
)
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION"))


class BedrockLLM(AnthropicLLM):
    """AWS Bedrock implementation using Anthropic Claude models.

    Credentials are resolved by the standard AWS chain (env vars,
    ~/.aws/credentials, instance profile). No LLM_API_KEY needed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        tools: list[dict] | None = None,
        quiet: bool = False,
        reasoning_level: ReasoningLevel = "medium",
    ):
        self.api_key = None
        self.model = model or LLM_MODEL_NAME
        self.tools = self._convert_tools(tools) if tools else None
        self.quiet = quiet
        self.reasoning_level = reasoning_level

        if is_tracing_enabled():
            init_tracing()
        self.client = anthropic.AnthropicBedrock(aws_region=AWS_REGION)
