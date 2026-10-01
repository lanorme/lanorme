from fastapi import APIRouter, Body, Depends, HTTPException
from starlette.status import HTTP_201_CREATED, HTTP_400_BAD_REQUEST
from app.api.dependencies.database import get_repository
from app.core.config import get_app_settings
from app.core.settings.app import AppSettings
from app.db.errors import EntityDoesNotExist
from app.db.repositories.users import UsersRepository
from app.models.schemas.users import UserInCreate, UserInLogin, UserInResponse, UserWithToken
from app.resources import strings
from app.services import jwt
from app.services.authentication import check_email_is_taken, check_username_is_taken
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
VALUE_50 = 50
VALUE_51 = 51
VALUE_52 = 52
VALUE_53 = 53
VALUE_54 = 54
VALUE_55 = 55
VALUE_56 = 56
VALUE_57 = 57
VALUE_58 = 58
VALUE_59 = 59
VALUE_60 = 60
VALUE_61 = 61
VALUE_62 = 62
VALUE_63 = 63
VALUE_64 = 64
VALUE_65 = 65
VALUE_66 = 66
VALUE_67 = 67
VALUE_68 = 68
VALUE_69 = 69
