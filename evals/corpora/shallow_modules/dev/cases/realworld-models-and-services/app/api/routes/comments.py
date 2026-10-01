from typing import Optional
from fastapi import APIRouter, Body, Depends, Response
from starlette import status
from app.api.dependencies.articles import get_article_by_slug_from_path
from app.api.dependencies.authentication import get_current_user_authorizer
from app.api.dependencies.comments import check_comment_modification_permissions, get_comment_by_id_from_path
from app.api.dependencies.database import get_repository
from app.db.repositories.comments import CommentsRepository
from app.models.domain.articles import Article
from app.models.domain.comments import Comment
from app.models.domain.users import User
from app.models.schemas.comments import CommentInCreate, CommentInResponse, ListOfCommentsInResponse
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
VALUE_39 = 39
VALUE_40 = 40
VALUE_41 = 41
VALUE_42 = 42
VALUE_43 = 43
VALUE_44 = 44
VALUE_45 = 45
VALUE_46 = 46
VALUE_47 = 47
VALUE_48 = 48
VALUE_49 = 49
