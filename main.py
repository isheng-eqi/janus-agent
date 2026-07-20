"""Janus — recursive task decomposition agent framework. Phase 2."""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit(
        "pyyaml is required by Janus.\n"
        "Install it with: pip install pyyaml"
    )

from core.console import (
    Console,
    _qing,
    _zhu,
    _jin,
    _nongmo,
    _danmo,
    set_no_color,
)
from core.gatekeeper import Gatekeeper
from core.planner import Planner
from core.reviewer import Reviewer
from core.session import Session
from core.task_manager import TaskManager
from core.worker import Worker, create_default_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def load_config(path: Path) -> dict:
    """Load and return the YAML configuration dictionary.

    Args:
        path: Path to ``config.yaml``.

    Returns:
        Parsed configuration as nested dicts.

    Raises:
        FileNotFoundError: If the config file does not exist.
        yaml.YAMLError: If the YAML is malformed.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Expected config.yaml in the same directory as main.py."
        )
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_config(config: dict) -> dict:
    """Recursively resolve ``${VAR}`` placeholders to environment variables.

    Walks every string value in *config* and replaces ``${NAME}`` with the
    corresponding environment variable.  Raises ``KeyError`` if a referenced
    variable is not set.

    Returns a new dict — *config* is not mutated.
    """

    def _walk(obj):
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(v) for v in obj]
        if isinstance(obj, str):
            return _resolve_str(obj)
        return obj

    def _resolve_str(value: str) -> str:
        def _replace(m: re.Match) -> str:
            var = m.group(1)
            try:
                return os.environ[var]
            except KeyError:
                raise KeyError(
                    f"Environment variable {var!r} is not set.  "
                    f"Required by config value: {value!r}"
                ) from None

        return _ENV_VAR_RE.sub(_replace, value)

    return _walk(config)


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    """Load environment variables from .env file if present."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip matching outer quotes (single or double), supports:
            #   FOO="bar"  → bar
            #   FOO='bar'  → bar
            #   FOO="it's" → it's  (single quote inside double)
            if len(value) >= 2:
                if (value[0] == '"' and value[-1] == '"') or \
                   (value[0] == "'" and value[-1] == "'"):
                    value = value[1:-1]
            if key and value and key not in os.environ:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for Janus — multi-turn agent framework (Phase 4).

    1. Load and resolve ``config.yaml``.
    2. Wire ``Planner`` (tactical) and ``Gatekeeper`` (strategic).
    3. Start a REPL loop for multi-turn conversation.
    """
    # -- 0. Load .env file -----------------------------------------------
    _load_dotenv()

    # -- 1. Load & resolve config -----------------------------------------
    config_path = Path(__file__).resolve().parent / "config.yaml"
    try:
        raw = load_config(config_path)
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    except yaml.YAMLError as exc:
        sys.exit(f"Failed to parse config.yaml: {exc}")

    try:
        cfg = resolve_config(raw)
    except KeyError as exc:
        sys.exit(str(exc))

    # -- 2. Extract model settings ----------------------------------------
    # Safe model config access — use .get() throughout so missing keys produce
    # clear error messages instead of cryptic KeyError tracebacks.
    model_cfg = cfg.get("model", {})
    if not model_cfg:
        sys.exit(
            "config.yaml is missing the required 'model' section.\n"
            "Expected keys: model.model, model.api_key"
        )
    gatekeeper_model = cfg.get("gatekeeper", {}).get(
        "model", model_cfg.get("model", "")
    )
    worker_model = cfg.get("worker", {}).get("model")
    api_key = model_cfg.get("api_key", "")
    if not api_key:
        sys.exit(
            "config.yaml is missing 'model.api_key'.\n"
            "Set it to your DeepSeek API key (or use ${DEEPSEEK_API_KEY})."
        )
    max_tool_calls = cfg.get("worker", {}).get("max_tool_calls", 50)
    context_window = cfg.get("worker", {}).get("context_window", 8192)
    max_depth = cfg.get("janus", {}).get("max_depth", 3)

    # -- 2.5. Parse CLI flags ─────────────────────────────────────────
    mode = "default"
    args = sys.argv[1:]
    has_verbose = "--verbose" in args or "-v" in args
    has_quiet = "--quiet" in args or "-q" in args
    has_help = "--help" in args or "-h" in args
    has_no_color = "--no-color" in args

    if "--no-color" in args:
        set_no_color()

    if has_help:
        print(
            "用法: janus [选项]\n"
            "\n"
            "选项:\n"
            "  -v, --verbose    显示详细执行日志（含模型思考过程）\n"
            "  -q, --quiet      仅显示最终结果，隐藏中间步骤\n"
            "  -h, --help       显示此帮助信息\n"
            "  --no-color       禁用 ANSI 颜色输出\n"
            "\n"
            "示例:\n"
            "  janus                 默认模式\n"
            "  janus --verbose       详细模式\n"
            "  janus --quiet         静默模式\n"
        )
        sys.exit(0)

    if has_verbose and has_quiet:
        print(f"{_danmo('同时指定了 --verbose 和 --quiet，使用 --verbose（详细模式）。')}")
        mode = "verbose"
    elif has_verbose:
        mode = "verbose"
    elif has_quiet:
        mode = "quiet"
    console = Console(mode=mode)

    # -- 3. Wire components -----------------------------------------------
    registry = create_default_registry()
    tm = TaskManager()

    # Shared reviewer — ONE instance for both Planner and Worker sub-audits
    reviewer = Reviewer(model=gatekeeper_model, api_key=api_key)

    # Factory accepts optional model override for heterogeneous model support
    def _make_worker(model_override: str | None = None) -> Worker:
        return Worker(
            model=model_override or worker_model or gatekeeper_model,
            api_key=api_key,
            registry=registry,
            max_tool_calls=max_tool_calls,
            context_window=context_window,
            reviewer=reviewer,
        )

    # Planner — tactical execution (uses lighter model when available)
    planner = Planner(
        model=worker_model or gatekeeper_model,
        api_key=api_key,
        task_manager=tm,
        worker_factory=_make_worker,
        reviewer=reviewer,
        max_depth=max_depth,
        console=console,
    )

    # Gatekeeper — strategic decision (uses the primary model)
    gk = Gatekeeper(
        model=gatekeeper_model,
        api_key=api_key,
        planner=planner,
        console=console,
    )

    session = Session(gk)

    # -- 5. REPL loop -----------------------------------------------------
    # 太极启动画面：名号悬浮 → 配置淡墨 → 金提示符
    # 四行含空行：阴·阳·阴·阳·阴·阳
    _JANUS_LOGO = '\n'.join([
        '█████╗ █████╗ ███╗   ██╗██╗   ██╗███████╗',
        '╚══██║██╔══██╗████╗  ██║██║   ██║██╔════╝',
        '   ██║███████║██╔██╗ ██║██║   ██║███████╗',
        '   ██║██╔══██║██║╚██╗██║██║   ██║╚════██║',
        '██╗██║██║  ██║██║ ╚████║╚██████╔╝███████║',
        '╚████╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝',
    ])

    _WELCOME = (
        f"\n{_JANUS_LOGO}\n"
        f"\n"
        f"{_danmo(f'{gatekeeper_model}  |  {len(registry)} 工具')}\n"
    )
    _HELP_TEXT = (
        f"\n自然语言描述目标，Janus 自行拆解执行。\n"
        f"\n"
        f"  > 帮我写一个排序 CSV 的 Python 脚本\n"
        f"  > 在 ./my-app 下创建 README.md\n"
        f"\n"
        f"{_danmo('输入 quit 退出，--verbose 查看细节。')}\n"
    )

    # Suppress the big ASCII-art welcome in quiet mode — it breaks the
    # promise of "only final results" and wastes terminal real estate.
    if not console.is_quiet:
        print(_WELCOME)

    while True:
        try:
            user_input = input(f"\n{_jin('❯')} ")
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n{_danmo('再见')}")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print(f"{_danmo('再见')}")
            break
        if not user_input.strip():
            print(f"  {_danmo('help 或 quit。')}")
            continue
        if user_input.lower() in ("help", "h", "?"):
            print(_HELP_TEXT)
            continue

        print()  # blank line before response
        try:
            answer = session.handle(user_input)
            print(answer)
        except KeyboardInterrupt:
            print(f"\n\n{_zhu('已中断')}")
            print(f"\n{_danmo('→ 输入新指令继续，或 quit 退出。')}")
            continue
        except Exception as exc:
            print(f"\n{_zhu(f'错误: {exc}')}", file=sys.stderr)
            print(f"\n{_danmo('可能原因：网络连接中断或 API 密钥无效。')}")
            print(f"{_jin('→ 检查 config.yaml 中的 api_key，然后重试。')}")
            continue


if __name__ == "__main__":
    main()
