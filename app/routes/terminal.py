from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import os
import subprocess

from app.models.user import User
from app.routes.auth import get_current_user

router = APIRouter()


class TerminalExecuteRequest(BaseModel):
    command: str


class TerminalExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


@router.post('/execute', response_model=TerminalExecuteResponse)
def execute_terminal_command(
    payload: TerminalExecuteRequest,
    current_user: User = Depends(get_current_user),
):
    command = (payload.command or '').strip()
    if not command:
        raise HTTPException(status_code=400, detail='Command is required')

    workdir = os.getenv('TERMINAL_WORKDIR') or os.getcwd()

    try:
        if os.name == 'nt':
            proc = subprocess.run(
                ['powershell', '-NoProfile', '-Command', command],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            proc = subprocess.run(
                ['bash', '-lc', command],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=30,
            )

        return TerminalExecuteResponse(
            stdout=proc.stdout or '',
            stderr=proc.stderr or '',
            exit_code=proc.returncode,
        )
    except subprocess.TimeoutExpired:
        return TerminalExecuteResponse(
            stdout='',
            stderr='Command timed out after 30 seconds.',
            exit_code=124,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Terminal execution failed: {exc}')
