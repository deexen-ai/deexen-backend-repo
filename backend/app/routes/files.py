from fastapi import APIRouter

router = APIRouter()

@router.get("/tree/{project_id}")
def file_tree(project_id: int):
    return {
        "project_id": project_id,
        "files": [
            {
                "id": 1,
                "name": "main.py",
                "type": "file",
                "parent_id": None
            }
        ]
    }
