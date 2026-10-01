from fastapi import APIRouter, Depends
from app.api.dependencies.database import get_repository
from app.db.repositories.tags import TagsRepository
from app.models.schemas.tags import TagsInList
VALUE_0 = 0
VALUE_1 = 1
VALUE_2 = 2
VALUE_3 = 3
VALUE_4 = 4
VALUE_5 = 5
VALUE_6 = 6
