from collections import Counter, defaultdict
from datetime import datetime
from typing import Any
from pydantic import field_validator, Field
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, PrimaryKeyConstraint, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship
from sqlalchemy_utils import TSVectorType, observes
from dispatch.case.enums import CostModelType
from dispatch.case_cost.models import CaseCostReadMinimal
from dispatch.case.priority.models import CasePriorityBase, CasePriorityCreate, CasePriorityRead
from dispatch.case.severity.models import CaseSeverityBase, CaseSeverityCreate, CaseSeverityRead
from dispatch.case.type.models import CaseTypeBase, CaseTypeCreate, CaseTypeRead
from dispatch.case_cost.models import CaseCostRead, CaseCostUpdate
from dispatch.conversation.models import ConversationRead
from dispatch.database.core import Base
from dispatch.document.models import Document, DocumentRead
from dispatch.entity.models import EntityRead
from dispatch.enums import Visibility
from dispatch.event.models import EventRead
from dispatch.group.models import Group, GroupRead
from dispatch.individual.models import IndividualContactRead
from dispatch.messaging.strings import CASE_RESOLUTION_DEFAULT
from dispatch.models import DispatchBase, NameStr, Pagination, PrimaryKey, ProjectMixin, TimeStampMixin
from dispatch.participant.models import Participant, ParticipantRead, ParticipantReadMinimal, ParticipantUpdate
from dispatch.storage.models import StorageRead
from dispatch.tag.models import TagRead
from dispatch.ticket.models import TicketRead
from dispatch.workflow.models import WorkflowInstanceRead
from .enums import CaseResolutionReason, CaseStatus
VALUE_0 = 0
