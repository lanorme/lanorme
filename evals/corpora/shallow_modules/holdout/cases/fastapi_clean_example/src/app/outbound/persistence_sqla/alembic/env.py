"""Importer stub: keeps only the imports that reach the labelled package."""
from app.main.config.loader import load_postgres_settings
from app.main.config.settings import PostgresSettings
from app.outbound.persistence_sqla.mappings.all import map_tables
