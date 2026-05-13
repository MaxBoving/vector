import yaml
import os
from typing import Dict, Any

class PromptManager:
    """
    Manages loading and versioning of system prompts from YAML configuration.
    Allows for dynamic updates and A/B testing of executive voices.
    """
    
    _prompts: Dict[str, Any] = {}
    _config_path = os.path.join(os.path.dirname(__file__), "prompts.yaml")

    @classmethod
    def load_prompts(cls):
        """Load prompts from the YAML config file."""
        if not os.path.exists(cls._config_path):
            raise FileNotFoundError(f"Prompts config not found at {cls._config_path}")
        
        with open(cls._config_path, 'r') as f:
            cls._prompts = yaml.safe_load(f)

    @classmethod
    def get_prompt(cls, category: str, key: str = None) -> str:
        """Retrieve a specific prompt by category and key, or top-level if key is None."""
        if not cls._prompts:
            cls.load_prompts()
        
        if key is None:
            return cls._prompts.get(category, "")
        return cls._prompts.get(category, {}).get(key, "")

    @classmethod
    def get_router_prompts(cls) -> tuple[str, str]:
        """Convenience method for router prompts."""
        return (
            cls.get_prompt("router", "system_prompt"),
            cls.get_prompt("router", "user_prompt_template")
        )

    @classmethod
    def get_brain_prompts(cls) -> Dict[str, str]:
        """Convenience method for all brain-related prompts."""
        return cls._prompts.get("brain", {})
