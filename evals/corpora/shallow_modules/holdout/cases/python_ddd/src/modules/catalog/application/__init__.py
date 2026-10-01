from lato import ApplicationModule
import importlib
catalog_module = 0
importlib.import_module("modules.catalog.application.command")
importlib.import_module("modules.catalog.application.query")
