from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, String
import sqlalchemy
from app.database import SessionLocal
from app.models.user import User
from app.models.project import Project
from app.models.file import File
from app.routes.auth import get_current_user
from app.schemas.project import ProjectCreateRequest, ProjectUpdateRequest, ProjectResponse
from app.schemas.file import FileCreateRequest, FileUpdateRequest, FileResponse, FileTreeResponse

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==================== PROJECTS ====================

@router.post("/", response_model=ProjectResponse)
def create_project(
    data: ProjectCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new project"""
    project = Project(
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        is_active=True
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    
    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        description=project.description,
        is_active=project.is_active,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat()
    )

@router.get("/", response_model=list[ProjectResponse])
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all projects for current user"""
    try:
        projects = db.query(Project).filter(
            Project.user_id == current_user.id,
            Project.is_active == True
        ).all()
        
        print(f"DEBUG: Found {len(projects)} projects for user {current_user.id} ({current_user.email})")
        
        return [
            ProjectResponse(
                id=p.id,
                user_id=p.user_id,
                name=p.name,
                description=p.description,
                is_active=p.is_active,
                created_at=p.created_at.isoformat(),
                updated_at=p.updated_at.isoformat()
            )
            for p in projects
        ]
    except Exception as e:
        print(f"ERROR in list_projects: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error listing projects: {str(e)}")

@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific project"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        description=project.description,
        is_active=project.is_active,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat()
    )

@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    data: ProjectUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a project"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if data.name:
        project.name = data.name
    if data.description is not None:
        project.description = data.description
    
    db.commit()
    db.refresh(project)
    
    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        description=project.description,
        is_active=project.is_active,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat()
    )

@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a project (soft delete)"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project.is_active = False
    db.commit()
    
    return {"success": True, "message": "Project deleted"}

# ==================== FILES ====================

@router.post("/{project_id}/files", response_model=FileResponse)
def create_file(
    project_id: int,
    data: FileCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a file or folder in project"""
    # Verify project ownership
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Verify parent file exists if provided
    if data.parent_id:
        parent = db.query(File).filter(
            File.id == data.parent_id,
            cast(File.project_id, String) == str(project_id)
        ).first()
        
        if not parent:
            raise HTTPException(status_code=404, detail="Parent file not found")
    
    file = File(
        project_id=project_id,
        parent_id=data.parent_id,
        name=data.name,
        file_type=data.file_type,
        content=data.content,
        is_active=True
    )
    
    db.add(file)
    db.commit()
    db.refresh(file)
    
    return FileResponse(
        id=file.id,
        project_id=file.project_id,
        parent_id=file.parent_id,
        name=file.name,
        file_type=file.file_type,
        content=file.content,
        is_active=file.is_active,
        created_at=file.created_at.isoformat(),
        updated_at=file.updated_at.isoformat()
    )

@router.get("/{project_id}/files", response_model=list[FileResponse])
def list_files(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all files in project"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    files = db.query(File).filter(
        cast(File.project_id, String) == str(project_id),
        File.is_active == True
    ).all()
    
    return [
        FileResponse(
            id=f.id,
            project_id=f.project_id,
            parent_id=f.parent_id,
            name=f.name,
            file_type=f.file_type,
            content=f.content,
            is_active=f.is_active,
            created_at=f.created_at.isoformat(),
            updated_at=f.updated_at.isoformat()
        )
        for f in files
    ]

@router.get("/{project_id}/files/tree", response_model=list[FileTreeResponse])
def get_file_tree(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get file tree structure for project"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    def build_tree(parent_id=None):
        files = db.query(File).filter(
            cast(File.project_id, String) == str(project_id),
            File.parent_id == parent_id,
            File.is_active == True
        ).all()
        
        tree = []
        for f in files:
            node = FileTreeResponse(
                id=f.id,
                name=f.name,
                file_type=f.file_type,
                children=build_tree(f.id)
            )
            tree.append(node)
        
        return tree
    
    return build_tree()

@router.get("/{project_id}/files/{file_id}", response_model=FileResponse)
def get_file(
    project_id: int,
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific file"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if file_id.isdigit():
        file = db.query(File).filter(
            File.id == int(file_id),
            cast(File.project_id, String) == str(project_id)
        ).first()
    else:
        file = db.query(File).filter(
            File.name == file_id,
            cast(File.project_id, String) == str(project_id)
        ).first()
    
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        id=file.id,
        project_id=file.project_id,
        parent_id=file.parent_id,
        name=file.name,
        file_type=file.file_type,
        content=file.content,
        is_active=file.is_active,
        created_at=file.created_at.isoformat(),
        updated_at=file.updated_at.isoformat()
    )

@router.put("/{project_id}/files/{file_id}", response_model=FileResponse)
def update_file(
    project_id: int,
    file_id: str,
    data: FileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a file"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if file_id.isdigit():
        file = db.query(File).filter(
            File.id == int(file_id),
            cast(File.project_id, String) == str(project_id)
        ).first()
    else:
        file = db.query(File).filter(
            File.name == file_id,
            cast(File.project_id, String) == str(project_id)
        ).first()
    
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    if data.name:
        file.name = data.name
    if data.content is not None:
        file.content = data.content
    
    db.commit()
    db.refresh(file)
    
    return FileResponse(
        id=file.id,
        project_id=file.project_id,
        parent_id=file.parent_id,
        name=file.name,
        file_type=file.file_type,
        content=file.content,
        is_active=file.is_active,
        created_at=file.created_at.isoformat(),
        updated_at=file.updated_at.isoformat()
    )

@router.delete("/{project_id}/files/{file_id}")
def delete_file(
    project_id: int,
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a file (soft delete)"""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    file = db.query(File).filter(
        File.id == file_id,
        cast(File.project_id, String) == str(project_id)
    ).first()
    
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    # Soft delete file and all children
    def delete_recursive(file_id):
        children = db.query(File).filter(File.parent_id == file_id).all()
        for child in children:
            delete_recursive(child.id)
        file = db.query(File).filter(File.id == file_id).first()
        if file:
            file.is_active = False
    
    delete_recursive(file_id)
    db.commit()
    
    return {"success": True, "message": "File deleted"}

