@rem Prerequisite: install backend requirements and Nuitka in the selected Python environment.
@rem Example: C:\xgvenv\Scripts\python.exe -m pip install nuitka
@echo off
setlocal

set "PYTHON=C:\xgvenv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

cd /d "%~dp0\..\backend"
"%PYTHON%" -m nuitka server_entry.py ^
  --standalone ^
  --onefile ^
  --assume-yes-for-downloads ^
  --output-dir=dist ^
  --output-filename=xigua-backend.exe ^
  --include-package=app ^
  --include-package=fastapi ^
  --include-package=starlette ^
  --include-package=uvicorn ^
  --include-package=pydantic ^
  --include-package=pydantic_settings ^
  --include-package=sqlalchemy ^
  --include-package=httpx ^
  --include-package=pypinyin ^
  --include-package=multipart ^
  --include-package=python_multipart ^
  --include-data-dir=app/workflows=app/workflows ^
  --include-data-dir=dict=dict

endlocal
