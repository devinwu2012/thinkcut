"""Per-video configuration. Real projects always set AI_EDIT_RUN_DIR; the neutral defaults below only serve unit tests."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(os.environ.get('AI_EDIT_RUN_DIR', str(ROOT/'runs'/'TEST'))).expanduser().resolve()
CONFIG = json.loads((RUN/'project_runtime.json').read_text()) if (RUN/'project_runtime.json').exists() else {}
if 'AI_EDIT_RUN_DIR' in os.environ and not CONFIG:
    # A real project must never fall back to test defaults (project name, media ID, frame count).
    raise ValueError(f'Missing project_runtime.json in {RUN}; run edit_project.py init first')
NAME = CONFIG.get('name', 'TEST')
TOTAL = CONFIG.get('source_frames', 18645)
PROJECT = CONFIG.get('project', '_mcp_TEST')
SOURCE_ID = CONFIG.get('media_pool_item_id')
if CONFIG and (CONFIG.get('fps') != [30000,1001] or type(TOTAL) is not int or TOTAL <= 0):
    raise ValueError('This editing engine currently requires constant 30000/1001 fps input')
if not NAME or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in NAME):
    raise ValueError('Invalid project name stem')
