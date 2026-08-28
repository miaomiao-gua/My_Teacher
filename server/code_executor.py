import ast
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict


BANNED_MODULES = {
    "os",
    "subprocess",
    "shutil",
    "socket",
    "ctypes",
    "pickle",
    "multiprocessing",
    "threading",
    "psutil",
    "sys",
    "pathlib",
    "glob",
    "signal",
}


def _is_dangerous(code: str) -> bool:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return True

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BANNED_MODULES:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in BANNED_MODULES:
                return True
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "compile"}:
                return True
    return False


def execute_python_code(code: str, timeout: int = 5) -> Dict[str, str]:
    if not code.strip():
        return {"stdout": "", "stderr": "没有收到代码输入。", "status": "error"}

    if _is_dangerous(code):
        return {"stdout": "", "stderr": "代码中包含受限模块或危险调用，已拒绝执行。", "status": "error"}

    with tempfile.TemporaryDirectory(prefix="teacher_exec_") as tmpdir:
        script_path = Path(tmpdir) / "student_code.py"
        script_path.write_text(code, encoding="utf-8")

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
            )
            output = result.stdout or ""
            error = result.stderr or ""
            status = "ok" if result.returncode == 0 else "error"
            return {"stdout": output, "stderr": error, "status": status}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": f"代码执行超时，已在 {timeout} 秒后停止。", "status": "error"}
