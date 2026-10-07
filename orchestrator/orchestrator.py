"""AutoGen Orchestrator and Sub-Agents Implementation.

Implements the Orchestrator-Sub-Agent pattern using Microsoft AutoGen:
- Parallel read task dispatch across Explorer Sub-Agents via a_initiate_chat + asyncio.gather.
- Strict serialization of file mutations through Writer Agent & Mutex-Locked Write Queue.
- Parallel execution of LLM read tool calls (list_dir, read_file, file_search).
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

try:
    import autogen
except ImportError:  # pragma: no cover
    autogen = None

from auth.oauth import retrieve_oauth_token
from llm.llm_client import _get_client_params, default_model
from log.log import get_logger
from orchestrator.tools import (
    async_delete_file,
    async_file_search,
    async_list_dir,
    async_read_file,
    async_write_file,
    get_read_semaphore,
    get_write_lock,
)

logger = get_logger(__name__)


def get_autogen_llm_config(
    model_name: Optional[str] = None,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    """Build AutoGen llm_config dict using harness configuration."""
    resolved_model = model_name or default_model()
    client_params = _get_client_params()

    token_data = retrieve_oauth_token() or {}
    token = token_data.get("access_token") or "dummy_token"

    return {
        "config_list": [
            {
                "model": resolved_model,
                "api_key": token,
                "base_url": client_params.get("base_url"),
            }
        ],
        "temperature": temperature,
    }


def orchestrate_read_tool_calls(
    read_calls: List[Dict[str, Any]],
    registry: Any,
) -> List[Dict[str, Any]]:
    """
    Feed read tool calls (e.g., list_dir, read_file, file_search) to the orchestrator
    to execute in parallel across worker threads.

    Args:
        read_calls: List of OpenAI-style tool call dicts.
        registry: ToolRegistry instance used to dispatch tool calls.

    Returns:
        List of tool result message dicts corresponding to read_calls.
    """
    if not read_calls:
        return []

    if len(read_calls) > 1:
        logger.info("[Orchestrator] Executing %d tool calls in parallel...", len(read_calls), extra={"file_only": True})
    else:
        logger.info("[Orchestrator] Executing 1 tool call...", extra={"file_only": True})

    if len(read_calls) == 1:
        return [registry.dispatch(read_calls[0])]

    with ThreadPoolExecutor(max_workers=min(10, len(read_calls))) as executor:
        futures = [executor.submit(registry.dispatch, call) for call in read_calls]
        results = [f.result() for f in futures]

    return results


class AutoGenOrchestratorSystem:
    """
    Orchestrator and Sub-Agent system built with Microsoft AutoGen.

    Roles:
    - Orchestrator (UserProxyAgent): Coordinates workflow, delegates, synthesizes insights.
    - ExplorerSubAgent (AssistantAgent): Inspects codebase concurrently using read-only tools.
    - WriterAgent (AssistantAgent): Modifies workspace files sequentially under mutex lock.
    """

    def __init__(
        self,
        llm_config: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        human_input_mode: str = "NEVER",
        max_consecutive_auto_reply: int = 10,
    ) -> None:
        if autogen is None:
            raise RuntimeError(
                "Microsoft AutoGen (pyautogen) is required to run AutoGenOrchestratorSystem."
            )

        if llm_config is None:
            llm_config = get_autogen_llm_config(model_name=model_name)

        self.llm_config = llm_config

        # 1. Orchestrator Agent (UserProxyAgent / Manager)
        self.orchestrator = autogen.UserProxyAgent(
            name="Orchestrator",
            human_input_mode=human_input_mode,
            max_consecutive_auto_reply=max_consecutive_auto_reply,
            is_termination_msg=lambda x: "TERMINATE" in (x.get("content") or ""),
            code_execution_config=False,
        )

        # 2. Explorer Sub-Agent (AssistantAgent for read operations)
        self.explorer_agent = autogen.AssistantAgent(
            name="ExplorerSubAgent",
            system_message=(
                "You are a code exploration sub-agent. Inspect and analyze files using "
                "read tools (async_read_file, async_list_dir, async_file_search)."
            ),
            llm_config=self.llm_config,
        )

        # 3. Writer Sub-Agent (AssistantAgent for write operations)
        self.writer_agent = autogen.AssistantAgent(
            name="WriterAgent",
            system_message=(
                "You are a code modification sub-agent. Update target files safely "
                "using the async_write_file and async_delete_file tools."
            ),
            llm_config=self.llm_config,
        )

        self._register_tools()

    def _register_tools(self) -> None:
        """Register tools to AutoGen agents."""
        register_fn = getattr(
            getattr(autogen, "agentchat", autogen),
            "register_function",
            None,
        ) or getattr(autogen, "register_function", None)

        if register_fn is None:
            raise RuntimeError("AutoGen register_function utility is unavailable.")

        # Register parallel-safe read tools to Explorer Agent (executed by Orchestrator)
        register_fn(
            async_read_file,
            caller=self.explorer_agent,
            executor=self.orchestrator,
            description="Read contents of a specific file",
        )
        register_fn(
            async_list_dir,
            caller=self.explorer_agent,
            executor=self.orchestrator,
            description="List directory contents",
        )
        register_fn(
            async_file_search,
            caller=self.explorer_agent,
            executor=self.orchestrator,
            description="Search codebase for files matching a pattern",
        )

        # Register synchronized write/delete tools to Writer Agent (executed by Orchestrator)
        register_fn(
            async_write_file,
            caller=self.writer_agent,
            executor=self.orchestrator,
            description="Safely write or modify a file sequentially using a write lock",
        )
        register_fn(
            async_delete_file,
            caller=self.writer_agent,
            executor=self.orchestrator,
            description="Safely delete a file or directory sequentially using a write lock",
        )

    async def spin_off_subagent(self, agent: Any, task_prompt: str) -> Any:
        """
        Spin off a subagent for a specific task and log the action clearly.
        """
        agent_name = getattr(agent, "name", "SubAgent")
        logger.info("[Subagent Spun Off] Spun off subagent '%s' for task: %s", agent_name, task_prompt)
        return await self.orchestrator.a_initiate_chat(
            agent,
            message=task_prompt,
            clear_history=True,
        )

    async def run_parallel_reads(self, read_prompts: List[str]) -> List[Any]:
        """
        Phase 2: Launch parallel read tasks using AutoGen's async initiation (a_initiate_chat)
        and gather results concurrently.
        """
        logger.info("Executing %d read tasks in parallel...", len(read_prompts), extra={"file_only": True})
        tasks = [
            self.spin_off_subagent(self.explorer_agent, prompt)
            for prompt in read_prompts
        ]
        return list(await asyncio.gather(*tasks, return_exceptions=True))

    async def run_serial_writes(self, write_prompts: List[str]) -> List[Any]:
        """
        Phase 4: Execute write tasks sequentially via Writer Agent & Orchestrator.
        """
        logger.info("Executing %d write tasks sequentially...", len(write_prompts))
        results = []
        for prompt in write_prompts:
            res = await self.spin_off_subagent(self.writer_agent, prompt)
            results.append(res)
        return results

    async def execute_task(
        self,
        read_prompts: List[str],
        write_prompts: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute full Orchestrator workflow:
        1. Concurrent Read Dispatch across Explorer Sub-Agents.
        2. Aggregation & Synthesis.
        3. Serialized Write Execution across Writer Sub-Agent.
        """
        read_results = await self.run_parallel_reads(read_prompts) if read_prompts else []
        write_results = await self.run_serial_writes(write_prompts) if write_prompts else []

        return {
            "read_results": read_results,
            "write_results": write_results,
        }


async def run_orchestrator(
    read_tasks: List[str],
    write_tasks: Optional[List[str]] = None,
    model_name: Optional[str] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    High-level entry point to run an Orchestrator task with AutoGen sub-agents.
    """
    system = AutoGenOrchestratorSystem(llm_config=llm_config, model_name=model_name)
    return await system.execute_task(read_prompts=read_tasks, write_prompts=write_tasks)


__all__ = [
    "AutoGenOrchestratorSystem",
    "get_autogen_llm_config",
    "orchestrate_read_tool_calls",
    "run_orchestrator",
]
