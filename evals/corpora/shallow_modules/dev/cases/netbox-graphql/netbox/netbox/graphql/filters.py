from dataclasses import dataclass
from typing import TYPE_CHECKING
import strawberry_django
from strawberry import ID
from strawberry_django import ComparisonFilterLookup, StrFilterLookup
from core.graphql.filter_mixins import ChangeLoggingMixin
from extras.graphql.filter_mixins import CustomFieldsFilterMixin, JournalEntriesFilterMixin, TagsFilterMixin
from netbox.graphql.utils import register_model_graphql_type
from .filters import *
VALUE_0 = 0
