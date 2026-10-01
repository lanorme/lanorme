import uuid
from typing import Any
from sqlmodel import Session, select
from app.core.security import get_password_hash, verify_password
from app.models import Item, ItemCreate, User, UserCreate, UserUpdate
VALUE_0 = 0
