"""Chat-client factory.

Routes (role → client) based on `AppConfig.provider`. Falls back to a stub
client when `stub_mode` is set or no credentials are configured for the chosen
provider — so the pipeline always runs even without secrets.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Awaitable, Mapping, Sequence

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Message,
    ResponseStream,
)

from blog_writer.config import AppConfig
from blog_writer.models.config import AgentRole, ModelMap


# -----------------------------------------------------------------------------
# Stub client — used in tests / when no creds are configured.
# -----------------------------------------------------------------------------

_STUB_RESPONSES: dict[AgentRole, str] = {
    "orchestrator": (
        "APPROVED. Final review: the draft cites Microsoft Learn first, the PoC samples "
        "run successfully, and the critic's score is above threshold. Ready to publish."
    ),
    "ideation": (
        "## 1. Landing zones for agentic workloads\n"
        "**Why it matters:** Agentic systems need the same governance baseline as any production workload.\n"
        "**Learn area:** CAF / Well-Architected\n"
        "**Audience:** platform engineers\n\n"
        "## 2. Operationalising agents on AKS vs Container Apps\n"
        "**Why it matters:** Choosing the wrong runtime costs months of operational pain.\n"
        "**Learn area:** Azure Architecture Center\n"
        "**Audience:** infra leads\n\n"
        "## 3. Grounding strategies for production agents\n"
        "**Why it matters:** MCP, AI Search, and Bing grounding solve different problems — pick deliberately.\n"
        "**Learn area:** AI Foundry\n"
        "**Audience:** ML platform owners\n"
    ),
    "internal_knowledge": (
        "Internal best-practice hits (from MS Learn):\n"
        "- [CAF: Adopt AI responsibly](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/scenarios/ai/) — governance baseline.\n"
        "- [Well-Architected: AI workloads](https://learn.microsoft.com/en-us/azure/well-architected/ai/) — five-pillar guidance.\n"
        "- [AI Foundry: agent service overview](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview) — first-party agent runtime."
    ),
    "research": (
        "External sources to complement the internal best practices:\n"
        "- Recent Microsoft blog on agent observability in Foundry.\n"
        "- GitHub sample repo demonstrating MCP + Bing grounding."
    ),
    "planner": (
        "title: Landing Zones for Agentic Workloads on Azure\n"
        "summary: A practical map of how to apply Cloud Adoption Framework patterns to a multi-agent system.\n"
        "sections:\n"
        "  - heading: The problem\n"
        "    argues: Agentic workloads outgrow ad-hoc deployments fast.\n"
        "    leans_on: [L1]\n"
        "  - heading: CAF landing-zone patterns applied to agents\n"
        "    argues: Use management groups, policy, and identity as the baseline.\n"
        "    leans_on: [L1, L2]\n"
        "  - heading: Reference architecture\n"
        "    argues: AKS vs Container Apps trade-offs for agent runtimes.\n"
        "    leans_on: [L2]\n"
        "  - heading: Grounding\n"
        "    argues: MCP + AI Search + Bing grounding cover different shapes of context.\n"
        "    leans_on: [L3, E1]\n"
        "  - heading: Conclusion + checklist\n"
        "    argues: Pre-flight checklist before shipping agent workloads.\n"
        "    leans_on: [L1]\n"
        "pocs:\n"
        "  - id: agent-mcp-demo\n"
        "    section: Grounding\n"
        "    description: A minimal Python agent that calls the MS Learn MCP server.\n"
        "    language: python\n"
        "    sandbox: local\n"
    ),
    "poc_builder": (
        "Generated PoC `agent-mcp-demo`:\n"
        "```python\n"
        "# minimal agent calling an MCP tool\n"
        "from agent_framework import Agent, MCPStreamableHTTPTool\n"
        "agent = Agent(client, instructions='Answer using MS Learn.', tools=[MCPStreamableHTTPTool(url='https://learn.microsoft.com/api/mcp')])\n"
        "print(await agent.run('What is an Azure landing zone?'))\n"
        "```\n"
        "Sandbox run: exit_code=0, output_snippet='An Azure landing zone is...'"
    ),
    "writer": (
        "# Landing Zones for Agentic Workloads on Azure\n\n"
        "Agentic workloads need the same disciplined foundation any production workload "
        "deserves. The Cloud Adoption Framework's landing-zone pattern is the right "
        "starting point [1]. ...\n\n"
        "## References\n"
        "[1] Microsoft Learn — Cloud Adoption Framework, Landing Zones.\n"
        "[2] Microsoft Learn — Well-Architected AI workloads.\n"
    ),
    "fact_checker": (
        "All 7 claims verified against cited sources. No unsupported assertions found."
    ),
    "critic": (
        '{"scores": {"internal_first_citations": 23, "claim_support": 18, '
        '"structural_fidelity": 14, "poc_integration": 13, "voice_and_clarity": 12, '
        '"reader_payoff": 8}, "total": 88, "verdict": "accept", '
        '"feedback": ["Strong Learn-first citations.", '
        '"Add a one-line summary at the top of the article."]}'
    ),
}


class StubChatClient(BaseChatClient):
    """Fake chat client that returns canned responses for a given agent role.

    Used in stub mode (tests, dry runs, no-credentials environments). Implements
    the single abstract method `_inner_get_response`.
    """

    def __init__(self, role: AgentRole, model_name: str = "stub-model") -> None:
        super().__init__()
        self._role: AgentRole = role
        self._model_name = model_name

    def _inner_get_response(  # type: ignore[override]
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, object],
        **kwargs: object,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        if stream:
            return self._stream_response()
        return self._make_response()

    async def _make_response(self) -> ChatResponse:
        text = _STUB_RESPONSES.get(self._role, f"[stub:{self._role}] no canned response defined")
        return ChatResponse(
            messages=Message("assistant", contents=[text]),
            model=self._model_name,
            finish_reason="stop",
        )

    def _stream_response(self) -> ResponseStream[ChatResponseUpdate, ChatResponse]:
        # Streaming is not used by our pipeline; provide a minimal implementation that
        # raises if accessed, so we fail loudly rather than silently producing nothing.
        raise NotImplementedError("StubChatClient does not implement streaming.")

    @property
    def service_url(self) -> str:
        return f"stub://{self._role}"


# -----------------------------------------------------------------------------
# Real-client factories (lazy-imported so tests / stub mode don't pay the cost).
# -----------------------------------------------------------------------------


def _make_foundry_client(model: str) -> BaseChatClient:
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
    if not endpoint:
        warnings.warn(
            "AZURE_AI_PROJECT_ENDPOINT is not set — falling back to stub client.",
            stacklevel=2,
        )
        return StubChatClient(role="orchestrator", model_name=model)
    return FoundryChatClient(
        project_endpoint=endpoint,
        model=model,
        credential=DefaultAzureCredential(),
    )


def _make_openai_client(model: str) -> BaseChatClient:
    from agent_framework.openai import OpenAIChatClient

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        warnings.warn("OPENAI_API_KEY is not set — falling back to stub client.", stacklevel=2)
        return StubChatClient(role="orchestrator", model_name=model)
    return OpenAIChatClient(api_key=api_key, model_id=model)


def _make_azure_openai_client(model: str) -> BaseChatClient:
    from agent_framework.openai import OpenAIChatClient

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    if not (endpoint and api_key):
        warnings.warn(
            "AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_API_KEY not set — falling back to stub client.",
            stacklevel=2,
        )
        return StubChatClient(role="orchestrator", model_name=model)
    # OpenAIChatClient accepts a base_url; Azure OpenAI is OpenAI-compatible at
    # /openai/deployments/<deployment>/chat/completions?api-version=<v>.
    base_url = f"{endpoint.rstrip('/')}/openai/deployments/{model}"
    return OpenAIChatClient(
        api_key=api_key,
        base_url=base_url,
        model_id=model,
        default_query={"api-version": api_version},
    )


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


def get_chat_client(
    role: AgentRole,
    *,
    config: AppConfig,
    models: ModelMap,
) -> BaseChatClient:
    """Return the chat client to use for `role`, honouring config and env."""
    model = models.for_role(role)

    if config.stub or config.provider == "stub":
        return StubChatClient(role=role, model_name=model)

    if config.provider == "foundry":
        return _make_foundry_client(model)
    if config.provider == "azure_openai":
        return _make_azure_openai_client(model)
    if config.provider == "openai":
        return _make_openai_client(model)

    raise ValueError(f"Unknown provider: {config.provider}")
