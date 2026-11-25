"""Shared project paths and atomic JSON writes (the old sentence-retake reviewer was removed)."""
import json

from project_runtime import ROOT, RUN


def write(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)
