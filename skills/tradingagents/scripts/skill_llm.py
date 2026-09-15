"""Stand-in for the upstream chat model.

Upstream agents receive an ``llm`` object and call ``invoke``,
``with_structured_output`` or ``bind_tools`` on it. This object answers those
calls in two modes:

- Without a response it raises :class:`LLMRequest` carrying the exact messages
  (and schema/tools) the agent would have sent to a provider. The driver turns
  that into a task file for a host subagent.
- With a response (the subagent's saved answer) it returns that answer in the
  same shape a provider client would, so the upstream node finishes normally
  and produces its usual state update.
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import RunnableLambda

_ROLE_NAMES = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}


class LLMRequest(BaseException):  # noqa: N818 - control-flow signal, not an error
    """An upstream agent needs a model response that has not been produced yet.

    Derives from ``BaseException`` on purpose: upstream
    ``invoke_structured_or_freetext`` catches ``Exception`` to retry as free
    text, and a pending request must pass through that handler unchanged.
    """

    def __init__(self, messages, schema=None, tools=None):
        super().__init__("model response required")
        self.messages = messages
        self.schema = schema
        self.tools = list(tools or [])


def to_messages(value) -> list[tuple[str, str]]:
    """Normalise any prompt shape upstream passes to ``(role, content)`` pairs."""
    if isinstance(value, PromptValue):
        value = value.to_messages()
    if isinstance(value, str):
        return [("user", value)]
    result = []
    for message in value:
        if isinstance(message, BaseMessage):
            role, content = message.type, message.content
        elif isinstance(message, dict):
            role, content = message["role"], message["content"]
        else:
            role, content = message
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        result.append((_ROLE_NAMES.get(role, role), content))
    return result


def extract_json(text: str) -> str:
    """Return the JSON object in a response, tolerating a Markdown code fence."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def parse_structured(schema, text: str):
    """Validate a response against an upstream Pydantic schema."""
    return schema.model_validate_json(extract_json(text))


class SkillLLM:
    """Chat-model stand-in; see the module docstring."""

    def __init__(self, response: str | None = None):
        self.response = response

    def invoke(self, value, *args, **kwargs):
        if self.response is None:
            raise LLMRequest(to_messages(value))
        return AIMessage(content=self.response)

    def with_structured_output(self, schema, **kwargs):
        return _StructuredSkillLLM(self, schema)

    def bind_tools(self, tools, **kwargs):
        # The subagent runs the tool loop itself through ``ta.py tool``, so the
        # answer it saves is the final report: an AIMessage without tool calls,
        # which routes the upstream analyst straight to its report.
        def call(value):
            if self.response is None:
                raise LLMRequest(to_messages(value), tools=tools)
            return AIMessage(content=self.response)

        return RunnableLambda(call)


class _StructuredSkillLLM:
    def __init__(self, llm: SkillLLM, schema):
        self.llm = llm
        self.schema = schema

    def invoke(self, value, *args, **kwargs):
        if self.llm.response is None:
            raise LLMRequest(to_messages(value), schema=self.schema)
        # A validation error propagates as an ordinary exception, so upstream
        # falls back to free text exactly as it does for a provider.
        return parse_structured(self.schema, self.llm.response)
