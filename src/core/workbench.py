import os
import yaml
from datetime import datetime
from typing import Dict, Any

class AgenticWorkbench:
    """
    STORY: File-Based Multi-Agent Workbench.
    STORY-080: CEO-Isolated Workspace Nesting.
    """
    
    def __init__(self, interaction_id: int, ceo_id: str):
        # Nested directory structure: ./workspaces/{ceo_id}/interaction_{id}/
        self.base_dir = f"./workspaces/{ceo_id}/interaction_{interaction_id}"
        self.stages = {
            "planner": "00_planning",
            "librarian": "01_raw_data",
            "quant": "01b_quantitative_analysis",
            "auditor": "02_verification",
            "strategist": "03_analysis",
            "synthesizer": "04_final_brief"
        }
        self._init_workspace()

    def _init_workspace(self):
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        for stage_dir in self.stages.values():
            path = os.path.join(self.base_dir, stage_dir)
            if not os.path.exists(path):
                os.makedirs(path)

    def write_step(self, agent_name: str, filename: str, content: str, metadata: Dict[str, Any] = {}):
        """
        Writes agent output including its internal evaluation.
        """
        stage_folder = self.stages.get(agent_name.lower())
        if not stage_folder:
            raise ValueError(f"Unknown agent: {agent_name}")
            
        header = {
            "agent": agent_name,
            "timestamp": datetime.now().isoformat(),
            "status": "FINALIZED",
            **metadata
        }
        
        # Files are classified as 'Embedded System Prompts' for the next agent
        full_content = f"---\n{yaml.dump(header)}---\n\n{content}"
        file_path = os.path.join(self.base_dir, stage_folder, filename)
        
        with open(file_path, 'w') as f:
            f.write(full_content)
        return file_path

    def read_step(self, agent_name: str, filename: str) -> str:
        """
        Reads the full file including the evaluation header.
        This ensures the next agent 'inherits' the confidence/eval of the previous one.
        """
        stage_folder = self.stages.get(agent_name.lower())
        file_path = os.path.join(self.base_dir, stage_folder, filename)
        
        if not os.path.exists(file_path):
            return ""
            
        with open(file_path, 'r') as f:
            return f.read().strip()
