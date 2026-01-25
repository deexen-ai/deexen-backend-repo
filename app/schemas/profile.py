from pydantic import BaseModel

class ProfileResponse(BaseModel):
    id: int
    email: str
    name: str
