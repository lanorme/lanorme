"""Importer stub: keeps only the imports that reach the labelled package."""
from config.api_config import ApiConfig
from config.container import ApplicationContainer
from seedwork.domain.exceptions import DomainException, EntityNotFoundException


def neutral_deferred_imports():
    from modules.iam.application.services import IamService
    return None
