import json
import os
import random
from datetime import datetime
from typing import Any, Dict, Optional


class ArtifactStore:
    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            root = base_dir
        elif os.getenv("VERCEL"):
            root = os.path.join("/tmp", "artifacts", "runs")
        else:
            root = os.path.join(os.path.dirname(__file__), "artifacts", "runs")
        self.base_dir = root
        os.makedirs(self.base_dir, exist_ok=True)

    def new_run(self, meta: Optional[Dict[str, Any]] = None) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"{random.randint(1000, 9999)}"
        run_id = f"{timestamp}_{suffix}"
        run_dir = self.run_dir(run_id)
        os.makedirs(run_dir, exist_ok=True)
        metadata = meta or {}
        metadata.update({"run_id": run_id, "created_at": timestamp})
        self._write_json(os.path.join(run_dir, "metadata.json"), metadata)
        return run_id

    def run_dir(self, run_id: str) -> str:
        return os.path.join(self.base_dir, run_id)

    def story_dir(self, run_id: str, story_id: str) -> str:
        story_dir = os.path.join(self.run_dir(run_id), "stories", str(story_id))
        os.makedirs(story_dir, exist_ok=True)
        return story_dir

    def save_text(self, run_id: str, story_id: str, stage: str, content: str, suffix: str = "txt") -> str:
        path = os.path.join(self.story_dir(run_id, story_id), f"{stage}.{suffix}")
        with open(path, "w", encoding="utf-8") as file:
            file.write(content or "")
        return path

    def save_json(self, run_id: str, story_id: str, stage: str, payload: Dict[str, Any]) -> str:
        path = os.path.join(self.story_dir(run_id, story_id), f"{stage}.json")
        self._write_json(path, payload)
        return path

    def save_run_json(self, run_id: str, filename: str, payload: Dict[str, Any]) -> str:
        path = os.path.join(self.run_dir(run_id), filename)
        self._write_json(path, payload)
        return path

    def save_run_text(self, run_id: str, filename: str, content: str) -> str:
        path = os.path.join(self.run_dir(run_id), filename)
        with open(path, "w", encoding="utf-8") as file:
            file.write(content or "")
        return path

    def list_runs(self):
        if not os.path.isdir(self.base_dir):
            return []
        runs = []
        for name in os.listdir(self.base_dir):
            run_dir = self.run_dir(name)
            meta_path = os.path.join(run_dir, "metadata.json")
            if os.path.isdir(run_dir) and os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as file:
                        meta = json.load(file)
                except Exception:
                    meta = {"run_id": name}
                runs.append(meta)
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return runs

    def load_overrides(self, run_id: str) -> Dict[str, Dict[str, str]]:
        path = os.path.join(self.run_dir(run_id), "overrides.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return {}

    def save_overrides(self, run_id: str, overrides: Dict[str, Dict[str, str]]) -> str:
        return self.save_run_json(run_id, "overrides.json", overrides)

    def _write_json(self, path: str, payload: Dict[str, Any]) -> None:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
