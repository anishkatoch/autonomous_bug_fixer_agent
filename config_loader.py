"""
Configuration loader - supports both Anthropic and OpenAI
with automatic fallback from Anthropic to OpenAI on errors.
No classes - just simple functions!
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage


class FallbackLLM(BaseChatModel):
    """LLM wrapper that tries Anthropic first, falls back to OpenAI on failure."""

    primary: BaseChatModel
    fallback: BaseChatModel
    _using_fallback: bool = False

    @property
    def _llm_type(self) -> str:
        return "fallback_llm"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if not self._using_fallback:
            try:
                return self.primary._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as e:
                error_msg = str(e).lower()
                # Catch auth errors, rate limits, insufficient balance, server errors
                if any(keyword in error_msg for keyword in [
                    'auth', 'api key', 'credit', 'balance', 'quota', 'billing',
                    'rate limit', '401', '403', '429', '402', '500', '502', '503',
                    'overloaded', 'insufficient', 'invalid api', 'expired',
                    'connection', 'timeout', 'refused'
                ]):
                    print(f"\n[FALLBACK] Anthropic failed: {e}")
                    print("[FALLBACK] Switching to OpenAI...")
                    self._using_fallback = True
                    return self.fallback._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
                else:
                    raise
        return self.fallback._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if not self._using_fallback:
            try:
                return await self.primary._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as e:
                error_msg = str(e).lower()
                if any(keyword in error_msg for keyword in [
                    'auth', 'api key', 'credit', 'balance', 'quota', 'billing',
                    'rate limit', '401', '403', '429', '402', '500', '502', '503',
                    'overloaded', 'insufficient', 'invalid api', 'expired',
                    'connection', 'timeout', 'refused'
                ]):
                    print(f"\n[FALLBACK] Anthropic failed: {e}")
                    print("[FALLBACK] Switching to OpenAI...")
                    self._using_fallback = True
                    return await self.fallback._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
                else:
                    raise
        return await self.fallback._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @property
    def active_provider(self):
        return "openai" if self._using_fallback else "anthropic"


def load_config():
    """
    Load configuration from environment variables

    Returns:
        dict: Configuration dictionary or None if failed
    """
    # Load .env file if it exists
    load_dotenv()

    # Get repository path (required)
    repo_path = os.getenv('REPO_PATH')
    if not repo_path:
        print("ERROR: REPO_PATH environment variable required")
        print("Set it in .env file or export REPO_PATH=/path/to/repo")
        return None

    # Get both API keys
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')
    openai_key = os.getenv('OPENAI_API_KEY')

    if not anthropic_key and not openai_key:
        print("ERROR: Either ANTHROPIC_API_KEY or OPENAI_API_KEY required")
        return None

    # Show masked keys so user can verify they loaded correctly
    def mask_key(key):
        if not key:
            return "NOT SET"
        return key[:8] + "..." + key[-4:]

    print(f"[CONFIG] Anthropic API Key: {mask_key(anthropic_key)}")
    print(f"[CONFIG] OpenAI API Key:    {mask_key(openai_key)}")

    # Prefer Anthropic, fallback to OpenAI
    if anthropic_key:
        llm_provider = 'anthropic'
        api_key = anthropic_key
    else:
        llm_provider = 'openai'
        api_key = openai_key

    config = {
        'repo_path': repo_path,
        'llm_provider': llm_provider,
        'api_key': api_key,
        'anthropic_api_key': anthropic_key,
        'openai_api_key': openai_key,
        'cost_limit': float(os.getenv('COST_LIMIT', '5.0')),
        'max_iterations': int(os.getenv('MAX_ITERATIONS', '20')),
        'output_dir': os.getenv('OUTPUT_DIR', './output'),
        'log_level': os.getenv('LOG_LEVEL', 'INFO')
    }

    return config


def setup_llm(config):
    """
    Setup LLM with automatic fallback.
    If both Anthropic and OpenAI keys are available, wraps them in a FallbackLLM
    that tries Anthropic first and switches to OpenAI on failure.

    Returns:
        LLM instance or None if failed
    """
    anthropic_key = config.get('anthropic_api_key')
    openai_key = config.get('openai_api_key')

    # Both keys available: use fallback wrapper
    if anthropic_key and openai_key:
        primary = setup_anthropic_llm(config)
        fallback = setup_openai_llm(config)

        if primary and fallback:
            print("[OK] Fallback enabled: Anthropic -> OpenAI")
            return FallbackLLM(primary=primary, fallback=fallback)
        elif primary:
            return primary
        elif fallback:
            return fallback
        return None

    # Only one key: use that provider directly
    if anthropic_key:
        return setup_anthropic_llm(config)
    elif openai_key:
        return setup_openai_llm(config)

    print("ERROR: No API keys configured")
    return None


CLAUDE_MODEL_ROTATION = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-3-5-sonnet-20240620",
]


def get_claude_model_for_iteration(iteration):
    """
    Get the Claude model to use based on the current iteration (0-indexed).

    Rotation schedule:
      Iterations 0-1: claude-sonnet-4-20250514
      Iterations 2-3: claude-opus-4-20250514
      Iterations 4-5: claude-3-5-sonnet-20240620
      Iterations 6+:  claude-sonnet-4-20250514
    """
    if iteration < 2:
        return CLAUDE_MODEL_ROTATION[0]
    elif iteration < 4:
        return CLAUDE_MODEL_ROTATION[1]
    elif iteration < 6:
        return CLAUDE_MODEL_ROTATION[2]
    else:
        return CLAUDE_MODEL_ROTATION[0]


def setup_anthropic_llm(config, model_name=None):
    """Setup Anthropic Claude LLM"""
    try:
        from langchain_anthropic import ChatAnthropic

        model = model_name or "claude-sonnet-4-20250514"
        llm = ChatAnthropic(
            model=model,
            anthropic_api_key=config.get('anthropic_api_key') or config['api_key'],
            temperature=0.3,
            max_tokens=4096
        )
        print(f"[OK] Anthropic Claude ready (model: {model})")
        return llm

    except ImportError:
        print("ERROR: langchain-anthropic not installed")
        print("Install: pip install langchain-anthropic")
        return None
    except Exception as e:
        print(f"ERROR setting up Anthropic: {e}")
        return None


def setup_openai_llm(config):
    """Setup OpenAI GPT LLM"""
    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model="gpt-4o",
            openai_api_key=config.get('openai_api_key') or config['api_key'],
            temperature=0.3,
            max_tokens=4096
        )

        print(f"[OK] OpenAI GPT-4 Turbo ready")
        return llm

    except ImportError:
        print("ERROR: langchain-openai not installed")
        print("Install: pip install langchain-openai")
        return None
    except Exception as e:
        print(f"ERROR setting up OpenAI: {e}")
        return None


def get_model_costs(provider):
    """
    Get pricing information for the LLM provider
    
    Returns:
        dict: Pricing per million tokens
    """
    costs = {
        'anthropic': {
            'model': 'claude-3-5-sonnet-20241022',
            'input_per_million': 3.00,
            'output_per_million': 15.00
        },
        'openai': {
            'model': 'gpt-4-turbo-preview',
            'input_per_million': 10.00,
            'output_per_million': 30.00
        }
    }
    
    return costs.get(provider, {})
