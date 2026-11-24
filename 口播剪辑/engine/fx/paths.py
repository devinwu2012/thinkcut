"""Defaults only: keep existing work directories and never move project files."""
from pathlib import Path


def default_work_dir(plan_path, project_root=None, batch=None):
    plan=Path(plan_path).expanduser().resolve()
    if (plan.parent/'操作记录.json').exists() or '花字音效' in plan.parent.parts:
        return plan.parent
    candidates=[Path(project_root).expanduser().resolve()] if project_root else [plan.parent,*plan.parent.parents]
    for root in candidates:
        # 标准项目（有 项目.json 和 01_原始素材）：放 02_制作/花字音效/<批次>（2026-10-24）
        if (root/'项目.json').is_file() and (root/'01_原始素材').is_dir():
            return root/'02_制作'/'花字音效'/(batch or plan.stem)
    # A plan already inside <片名>_AI剪辑 continues to use that existing workspace.
    for root in [plan.parent,*plan.parent.parents]:
        if root.name.endswith('_AI剪辑'):return root/'花字音效'
    root=Path(project_root).expanduser().resolve() if project_root else plan.parent
    return root/(root.name+'_AI剪辑')/'花字音效'
