from typing import Optional
from fastapi import Depends, HTTPException, Path
from starlette.status import HTTP_404_NOT_FOUND
from app.api.dependencies.authentication import get_current_user_authorizer
from app.api.dependencies.database import get_repository
from app.db.errors import EntityDoesNotExist
from app.db.repositories.profiles import ProfilesRepository
from app.models.domain.profiles import Profile
from app.models.domain.users import User
from app.resources import strings
VALUE_0 = 0
VALUE_1 = 1
VALUE_2 = 2
VALUE_3 = 3
VALUE_4 = 4
VALUE_5 = 5
VALUE_6 = 6
VALUE_7 = 7
VALUE_8 = 8
VALUE_9 = 9
VALUE_10 = 10
VALUE_11 = 11
VALUE_12 = 12
VALUE_13 = 13
VALUE_14 = 14
