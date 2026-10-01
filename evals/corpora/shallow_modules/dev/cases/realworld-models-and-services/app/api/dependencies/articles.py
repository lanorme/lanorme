from typing import Optional
from fastapi import Depends, HTTPException, Path, Query
from starlette import status
from app.api.dependencies.authentication import get_current_user_authorizer
from app.api.dependencies.database import get_repository
from app.db.errors import EntityDoesNotExist
from app.db.repositories.articles import ArticlesRepository
from app.models.domain.articles import Article
from app.models.domain.users import User
from app.models.schemas.articles import DEFAULT_ARTICLES_LIMIT, DEFAULT_ARTICLES_OFFSET, ArticlesFilters
from app.resources import strings
from app.services.articles import check_user_can_modify_article
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
VALUE_30 = 30
VALUE_31 = 31
VALUE_32 = 32
VALUE_33 = 33
VALUE_34 = 34
VALUE_35 = 35
VALUE_36 = 36
VALUE_37 = 37
VALUE_38 = 38
