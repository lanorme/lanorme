from typing import Optional
from fastapi import Depends, HTTPException, Path
from starlette import status
from app.api.dependencies import articles, authentication, database
from app.db.errors import EntityDoesNotExist
from app.db.repositories.comments import CommentsRepository
from app.models.domain.articles import Article
from app.models.domain.comments import Comment
from app.models.domain.users import User
from app.resources import strings
from app.services.comments import check_user_can_modify_comment
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
VALUE_15 = 15
VALUE_16 = 16
VALUE_17 = 17
VALUE_18 = 18
VALUE_19 = 19
VALUE_20 = 20
VALUE_21 = 21
VALUE_22 = 22
VALUE_23 = 23
VALUE_24 = 24
VALUE_25 = 25
VALUE_26 = 26
VALUE_27 = 27
VALUE_28 = 28
VALUE_29 = 29
