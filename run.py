"""CLI entry point and interactive REPL interface for the agentic harness."""

import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import List, Optional

from llm.llm_client import available_models, default_model
from log.log import get_logger
from loop.loop import make_llm_model, run_agent
from orchestrator import run_orchestrator
from tools import build_default_registry

logger = get_logger(__name__)
active_model = default_model()


def handle_sigint(sig=None, frame=None):
    """Graceful terminal shutdown when user presses CTRL C."""
    print("\n\nCTRL+C detected. Shutting down session gracefully...")
    sys.stdout.flush()
    sys.exit(0)


# Register signal handler for SIGINT (CTRL C)
try:
    signal.signal(signal.SIGINT, handle_sigint)
except (ValueError, AttributeError):
    # May occur in non-main threads during testing
    pass


def change_model_dialog() -> str:
    """CLI selector menu for the /model command."""
    global active_model
    models = available_models()
    if not models:
        print(f"No models available. Keeping active model: {active_model}\n")
        return active_model

    print("\n--- Available Models ---")
    for idx, name in enumerate(models, start=1):
        marker = " (active)" if name == active_model else ""
        print(f"  {idx}. {name}{marker}")

    choice = input("\nSelect model number (or press Enter to keep current): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(models):
        active_model = models[int(choice) - 1]
        print(f"Active model changed to: {active_model}\n")
    else:
        print(f"Keeping active model: {active_model}\n")
    return active_model


def select_model() -> str:
    """Backward compatible wrapper for model selection."""
    return change_model_dialog()


def run_bash_mode() -> None:
    """Interactive command line mode until user exits."""
    print(
        "\nEntering bash mode. Commands will run in shell. Type 'exit' or 'quit' to return to LLM prompt.\n"
    )
    while True:
        try:
            cmd = input("bash> ").strip()
            if not cmd:
                continue

            if cmd.lower() in ("exit", "quit", "/exit", "/bash"):
                print("Exiting bash mode.\n")
                break

            # Handle directory changes inside bash mode
            if cmd == "cd" or cmd.startswith("cd ") or cmd.startswith("cd\t"):
                parts = cmd.split(maxsplit=1)
                target = parts[1].strip() if len(parts) > 1 else str(Path.home())
                try:
                    new_path = Path(target).expanduser().resolve()
                    if new_path.is_dir():
                        os.chdir(new_path)
                        os.environ["HARNESS_WORKSPACE"] = str(new_path)
                    else:
                        print(f"bash: cd: {target}: No such file or directory")
                except Exception as e:
                    print(f"bash: cd: {e}")
                continue

            subprocess.run(cmd, shell=True)
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt")
            continue
        except EOFError:
            print("\nExiting bash mode.\n")
            break


def run_orchestrator_cli(
    read_tasks: List[str], write_tasks: Optional[List[str]] = None
) -> dict:
    """Execute AutoGen Orchestrator & Sub-Agents task from CLI."""
    print(
        f"\nRunning AutoGen Orchestrator with model '{active_model}'..."
    )
    print(f"  Parallel Read Tasks: {len(read_tasks)}")
    print(f"  Sequential Write Tasks: {len(write_tasks) if write_tasks else 0}")

    results = asyncio.run(
        run_orchestrator(
            read_tasks=read_tasks,
            write_tasks=write_tasks,
            model_name=active_model,
        )
    )
    print("\nExecution finished successfully.")
    return results


def start_cli() -> None:
    """Main CLI entrypoint loop post-setup."""
    global active_model
    print("AI Assistant initialized.")
    while True:
        try:
            user_input = input("\nWhat task do you want to perform? ").strip()
            if not user_input:
                continue

            # Intercept model command
            if user_input.lower() == "/model":
                change_model_dialog()
                continue

            # Intercept bash command line mode
            if user_input.lower() == "/bash":
                run_bash_mode()
                continue

            # Intercept orchestrator command
            if user_input.lower() == "/orchestrator":
                print(
                    "\nOrchestrator is active. When multiple read tool calls "
                    "(e.g. list_dir, read_file, file_search) are needed, "
                    "they are automatically executed in parallel."
                )
                continue

            registry = build_default_registry()
            print(f"\nRunning agent task with model '{active_model}'...")

            # Execute agent loop with direct stdout streaming
            result = run_agent(
                user_input,
                model=make_llm_model(active_model),
                tools=registry,
                model_name=active_model,
            )
            print("\nAgent finished:")
            print(result.final_text)
            print(
                f"\n[Tokens: {result.input_tokens} in, {result.output_tokens} out "
                f"({result.input_tokens + result.output_tokens} total) | "
                f"Cost: ${result.total_cost:.6f}]"
            )

        except (KeyboardInterrupt, EOFError):
            handle_sigint(None, None)


def main() -> None:
    """CLI entrypoint supporting direct command-line task or interactive mode."""
    if len(sys.argv) > 1:
        task_args = " ".join(sys.argv[1:]).strip()
        if task_args.lower() == "/model":
            change_model_dialog()
            start_cli()
        elif task_args.lower() == "/bash":
            run_bash_mode()
            start_cli()
        else:
            registry = build_default_registry()
            print(f"\nRunning agent task with model '{active_model}'...")
            result = run_agent(
                task_args,
                model=make_llm_model(active_model),
                tools=registry,
                model_name=active_model,
            )
            print("\nAgent finished:")
            print(result.final_text)
            print(
                f"\n[Tokens: {result.input_tokens} in, {result.output_tokens} out "
                f"({result.input_tokens + result.output_tokens} total) | "
                f"Cost: ${result.total_cost:.6f}]"
            )
    else:
        start_cli()


if __name__ == "__main__":
    main()
