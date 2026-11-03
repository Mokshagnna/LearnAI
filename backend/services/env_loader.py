"""
env_loader.py
=============
Safe environment loading for LearnAI.
Supports explicit environment file overrides via LEARNAI_ENV_FILE (e.g. for staging).
Fails closed if an explicit environment file is specified but cannot be resolved.
"""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv


def resolve_and_load_env(base_dir: Optional[Path] = None) -> Path:
    """
    Resolve and load environment variables for LearnAI.

    Behavior:
    1. If LEARNAI_ENV_FILE is set (and non-empty):
       - Resolves path to an absolute Path object.
       - If the file does not exist, raises FileNotFoundError immediately (fails closed).
       - Does NOT silently fall back to default .env.
       - Loads that file with override=True.
       - Returns the resolved Path.
    2. If LEARNAI_ENV_FILE is unset or empty:
       - Uses default: base_dir / ".env".
       - If base_dir / ".env" is an existing file, loads it with override=True.
       - Else, calls load_dotenv(override=True) for standard discovery.
       - Returns base_dir / ".env".
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent

    explicit_path = os.getenv("LEARNAI_ENV_FILE")
    if explicit_path is not None and explicit_path.strip():
        resolved = Path(explicit_path.strip()).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(
                f"Configured LEARNAI_ENV_FILE not found: {resolved} "
                "(explicit environment file does not exist; silent fallback prohibited)"
            )
        load_dotenv(resolved, override=True)
        return resolved

    default_file = base_dir / ".env"
    if default_file.is_file():
        load_dotenv(default_file, override=True)
    else:
        load_dotenv(override=True)
    return default_file
