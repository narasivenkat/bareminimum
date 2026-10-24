"""
Token usage counter and tracking utilities.

Provides functions to count tokens for strings, message dicts, and lists of messages,
as well as a TokenTracker class to keep track of cumulative input and output token usage
and calculate monetary cost based on model pricing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# Default pricing fallback per 1M tokens (USD)
DEFAULT_INPUT_COST_PER_1M: float = 0.15
DEFAULT_OUTPUT_COST_PER_1M: float = 0.60


def count_string_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """
    Count or estimate the number of tokens in a string.

    Uses `tiktoken` if installed and valid for `encoding_name`.
    Falls back to a ~4 characters per token heuristic if `tiktoken` is unavailable.
    """
    if not text:
        return 0

    try:
        import tiktoken

        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(text))
    except Exception:
        # Fallback heuristic: approx 4 characters per token for English / code
        return max(1, math.ceil(len(text) / 4.0))


def count_message_tokens(message: Dict[str, Any], encoding_name: str = "cl100k_base") -> int:
    """
    Recursively count tokens contained within an OpenAI-compatible message dict.
    """
    if not message or not isinstance(message, dict):
        return 0

    tokens = 0
    for key, val in message.items():
        if key.startswith("_"):
            continue
        if isinstance(val, str):
            tokens += count_string_tokens(val, encoding_name)
        elif isinstance(val, dict):
            tokens += count_message_tokens(val, encoding_name)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    tokens += count_message_tokens(item, encoding_name)
                elif isinstance(item, str):
                    tokens += count_string_tokens(item, encoding_name)
    return tokens


def count_tokens(data: Any, encoding_name: str = "cl100k_base") -> int:
    """
    Count total tokens in string, message dict, list of messages, or list of tool schemas.
    """
    if data is None:
        return 0
    if isinstance(data, str):
        return count_string_tokens(data, encoding_name)
    if isinstance(data, dict):
        return count_message_tokens(data, encoding_name)
    if isinstance(data, (list, tuple)):
        return sum(count_tokens(item, encoding_name) for item in data)
    return count_string_tokens(str(data), encoding_name)


def _get_default_pricing() -> tuple[float, float]:
    """Retrieve default (input_cost_per_1m, output_cost_per_1m) from LLM configuration."""
    default_in = DEFAULT_INPUT_COST_PER_1M
    default_out = DEFAULT_OUTPUT_COST_PER_1M
    try:
        from config.config import ConfigManager

        cfg_in = ConfigManager.get_raw("LLM", "INPUT_COST_PER_1M")
        if cfg_in is not None:
            try:
                default_in = float(cfg_in)
            except (TypeError, ValueError):
                pass

        cfg_out = ConfigManager.get_raw("LLM", "OUTPUT_COST_PER_1M")
        if cfg_out is not None:
            try:
                default_out = float(cfg_out)
            except (TypeError, ValueError):
                pass
    except Exception:
        pass

    return default_in, default_out


def get_model_pricing(model_name: Optional[str] = None) -> tuple[float, float]:
    """
    Retrieve (input_cost_per_1m, output_cost_per_1m) for a given model.
    Falls back to default pricing in configuration if not found.
    """
    default_in, default_out = _get_default_pricing()

    if model_name:
        try:
            from config.config import ConfigManager

            models = ConfigManager.get_raw("LLM", "MODELS", [])
            if isinstance(models, list):
                for entry in models:
                    if isinstance(entry, dict) and entry.get("NAME") == model_name:
                        in_cost = entry.get("INPUT_COST_PER_1M")
                        out_cost = entry.get("OUTPUT_COST_PER_1M")
                        try:
                            in_val = float(in_cost) if in_cost is not None else default_in
                        except (TypeError, ValueError):
                            in_val = default_in
                        try:
                            out_val = float(out_cost) if out_cost is not None else default_out
                        except (TypeError, ValueError):
                            out_val = default_out
                        return in_val, out_val
        except Exception:
            pass

    return default_in, default_out


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    model_name: Optional[str] = None,
    *,
    input_cost_per_1m: Optional[float] = None,
    output_cost_per_1m: Optional[float] = None,
) -> float:
    """
    Compute total monetary cost in USD for given input and output token counts.

    Args:
        input_tokens: Number of prompt/input tokens.
        output_tokens: Number of completion/output tokens.
        model_name: Optional model name to lookup pricing in configuration.
        input_cost_per_1m: Override cost per 1 Million input tokens (USD).
        output_cost_per_1m: Override cost per 1 Million output tokens (USD).

    Returns:
        Calculated total cost in USD as float.
    """
    if input_cost_per_1m is None or output_cost_per_1m is None:
        default_in, default_out = get_model_pricing(model_name)
        if input_cost_per_1m is None:
            input_cost_per_1m = default_in
        if output_cost_per_1m is None:
            output_cost_per_1m = default_out

    input_cost = (max(0, input_tokens) * input_cost_per_1m) / 1_000_000.0
    output_cost = (max(0, output_tokens) * output_cost_per_1m) / 1_000_000.0
    return input_cost + output_cost


@dataclass
class TokenTracker:
    """
    Tracks cumulative input (prompt) and output (completion) token usage
    both via local tiktoken estimation and actual LLM API response telemetry.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    api_input_tokens: int = 0
    api_output_tokens: int = 0
    model_name: Optional[str] = None
    turn_history: List[Dict[str, Any]] = field(default_factory=list)
    _last_input_count: int = field(default=0, repr=False)

    @property
    def total_tokens(self) -> int:
        """Total input and output tokens combined (tiktoken)."""
        return self.input_tokens + self.output_tokens

    @property
    def api_total_tokens(self) -> int:
        """Total input and output tokens combined (LLM API response)."""
        return self.api_input_tokens + self.api_output_tokens

    def calculate_cost(
        self,
        model_name: Optional[str] = None,
        input_cost_per_1m: Optional[float] = None,
        output_cost_per_1m: Optional[float] = None,
        source: str = "tiktoken",
    ) -> float:
        """
        Calculate total USD cost based on token counts and model pricing.

        Args:
            model_name: Optional model name to lookup pricing.
            input_cost_per_1m: Override cost per 1M input tokens.
            output_cost_per_1m: Override cost per 1M output tokens.
            source: Token source to calculate cost for ('tiktoken' or 'api').
        """
        target_model = model_name or self.model_name
        if source == "api":
            in_toks = self.api_input_tokens
            out_toks = self.api_output_tokens
        else:
            in_toks = self.input_tokens
            out_toks = self.output_tokens

        return compute_cost(
            in_toks,
            out_toks,
            model_name=target_model,
            input_cost_per_1m=input_cost_per_1m,
            output_cost_per_1m=output_cost_per_1m,
        )

    @property
    def total_cost(self) -> float:
        """Total estimated cost in USD using tiktoken counts."""
        return self.calculate_cost(source="tiktoken")

    @property
    def api_total_cost(self) -> float:
        """Total actual cost in USD using LLM API response token counts."""
        return self.calculate_cost(source="api")

    def add_input(
        self,
        data: Any,
        encoding_name: str = "cl100k_base",
        api_usage: Optional[Dict[str, int]] = None,
    ) -> int:
        """
        Add input tokens. If `data` is an int, adds directly. Otherwise counts `data`.
        Returns the number of tokens added.
        """
        if isinstance(data, int):
            count = max(0, data)
        else:
            count = count_tokens(data, encoding_name)
        self.input_tokens += count
        self._last_input_count = count
        if api_usage:
            self.api_input_tokens += max(0, api_usage.get("prompt_tokens", 0))
        return count

    def add_output(
        self,
        data: Any,
        encoding_name: str = "cl100k_base",
        api_usage: Optional[Dict[str, int]] = None,
    ) -> int:
        """
        Add output tokens. If `data` is an int, adds directly. Otherwise counts `data`.
        Returns the number of tokens added.
        """
        if isinstance(data, int):
            count = max(0, data)
        else:
            count = count_tokens(data, encoding_name)
        self.output_tokens += count

        if api_usage is None and isinstance(data, dict):
            api_usage = data.get("_api_usage") or data.get("api_usage")

        api_prompt = 0
        api_comp = 0
        if api_usage and isinstance(api_usage, dict):
            api_prompt = max(0, api_usage.get("prompt_tokens", 0))
            api_comp = max(0, api_usage.get("completion_tokens", 0))
            if api_prompt > 0 and self.api_input_tokens == 0 and self._last_input_count > 0:
                self.api_input_tokens += api_prompt
            elif api_prompt > 0 and not api_usage.get("_input_already_counted"):
                self.api_input_tokens += api_prompt
                api_usage["_input_already_counted"] = True
            self.api_output_tokens += api_comp

        turn_entry = {
            "turn": len(self.turn_history) + 1,
            "tiktoken": {
                "input": self._last_input_count,
                "output": count,
                "total": self._last_input_count + count,
            },
            "api_response": {
                "input": api_prompt,
                "output": api_comp,
                "total": api_prompt + api_comp,
            },
            "difference": {
                "input": api_prompt - self._last_input_count,
                "output": api_comp - count,
                "total": (api_prompt + api_comp) - (self._last_input_count + count),
            },
            "input": self._last_input_count,
            "output": count,
            "total": self._last_input_count + count,
        }
        self.turn_history.append(turn_entry)
        return count

    def add_turn(
        self,
        tiktoken_in: int,
        tiktoken_out: int,
        api_usage: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """
        Record a full turn side-by-side with tiktoken and API response usage data.
        """
        tiktoken_in = max(0, tiktoken_in)
        tiktoken_out = max(0, tiktoken_out)
        self.input_tokens += tiktoken_in
        self.output_tokens += tiktoken_out
        self._last_input_count = tiktoken_in

        api_prompt = 0
        api_comp = 0
        if api_usage and isinstance(api_usage, dict):
            api_prompt = max(0, api_usage.get("prompt_tokens", 0))
            api_comp = max(0, api_usage.get("completion_tokens", 0))
            self.api_input_tokens += api_prompt
            self.api_output_tokens += api_comp

        turn_entry = {
            "turn": len(self.turn_history) + 1,
            "tiktoken": {
                "input": tiktoken_in,
                "output": tiktoken_out,
                "total": tiktoken_in + tiktoken_out,
            },
            "api_response": {
                "input": api_prompt,
                "output": api_comp,
                "total": api_prompt + api_comp,
            },
            "difference": {
                "input": api_prompt - tiktoken_in,
                "output": api_comp - tiktoken_out,
                "total": (api_prompt + api_comp) - (tiktoken_in + tiktoken_out),
            },
            "input": tiktoken_in,
            "output": tiktoken_out,
            "total": tiktoken_in + tiktoken_out,
        }
        self.turn_history.append(turn_entry)
        return turn_entry

    def get_comparison_report(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Return a side-by-side comparison report between tiktoken token computation
        and LLM API response telemetry.
        """
        target_model = model_name or self.model_name
        tiktoken_cost = self.calculate_cost(model_name=target_model, source="tiktoken")
        api_cost = self.calculate_cost(model_name=target_model, source="api")

        return {
            "tiktoken": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "cost": round(tiktoken_cost, 6),
            },
            "api_response": {
                "input_tokens": self.api_input_tokens,
                "output_tokens": self.api_output_tokens,
                "total_tokens": self.api_total_tokens,
                "cost": round(api_cost, 6),
            },
            "difference": {
                "input_tokens": self.api_input_tokens - self.input_tokens,
                "output_tokens": self.api_output_tokens - self.output_tokens,
                "total_tokens": self.api_total_tokens - self.total_tokens,
                "cost": round(api_cost - tiktoken_cost, 6),
            },
            "turns": list(self.turn_history),
        }

    def reset(self) -> None:
        """Reset input and output token counts to zero."""
        self.input_tokens = 0
        self.output_tokens = 0
        self.api_input_tokens = 0
        self.api_output_tokens = 0
        self.turn_history.clear()
        self._last_input_count = 0


__all__ = [
    "TokenTracker",
    "compute_cost",
    "count_message_tokens",
    "count_string_tokens",
    "count_tokens",
    "get_model_pricing",
]
