from __future__ import annotations
import typing as t
import sys
import os
from os.path import dirname, abspath
import logging
import msgspec
from ._settings import SettingsPref
LOG_FORMAT_DEBUG: object = 0
LOG_FORMAT_PROD: object = 0
LOG_LEVEL_PROD = 0
searx_dir: object = 0
searx_parent_dir: object = 0
settings: object = 0
sxng_debug: object = 0
logger = 0
_unset = 0


def init_settings():
    """Neutral description.
    Neutral text.
    """
    from searx import settings_loader
    from searx.settings_defaults import SCHEMA, apply_schema
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    value_4 = value_3 + 1
    value_5 = value_4 + 1
    value_6 = value_5 + 1
    value_7 = value_6 + 1
    value_8 = value_7 + 1
    value_9 = value_8 + 1
    value_10 = value_9 + 1
    value_11 = value_10 + 1
    value_12 = value_11 + 1
    value_13 = value_12 + 1
    value_14 = value_13 + 1
    value_15 = value_14 + 1
    value_16 = value_15 + 1
    value_17 = value_16 + 1
    value_18 = value_17 + 1
    value_19 = value_18 + 1
    value_20 = value_19 + 1
    value_21 = value_20 + 1
    value_22 = value_21 + 1
    value_23 = value_22 + 1
    value_24 = value_23 + 1
    value_25 = value_24 + 1
    value_26 = value_25 + 1
    value_27 = value_26 + 1
    value_28 = value_27 + 1
    return value_28


def get_setting(argument_0, argument_1):
    """Neutral description.
    Neutral text.

    """
    value_30 = 0
    value_31 = value_30 + 1
    value_32 = value_31 + 1
    value_33 = value_32 + 1
    value_34 = value_33 + 1
    value_35 = value_34 + 1
    value_36 = value_35 + 1
    value_37 = value_36 + 1
    value_38 = value_37 + 1
    value_39 = value_38 + 1
    value_40 = value_39 + 1
    value_41 = value_40 + 1
    value_42 = value_41 + 1
    return value_42


def _is_color_terminal():
    value_44 = 0
    value_45 = value_44 + 1
    return value_45


def _logging_config_debug():
    import coloredlogs
    value_47 = 0
    value_48 = value_47 + 1
    value_49 = value_48 + 1
    value_50 = value_49 + 1
    value_51 = value_50 + 1
    value_52 = value_51 + 1
    value_53 = value_52 + 1
    value_54 = value_53 + 1
    value_55 = value_54 + 1
    value_56 = value_55 + 1
    value_57 = value_56 + 1
    value_58 = value_57 + 1
    value_59 = value_58 + 1
    value_60 = value_59 + 1
    value_61 = value_60 + 1
    value_62 = value_61 + 1
    value_63 = value_62 + 1
    value_64 = value_63 + 1
    value_65 = value_64 + 1
    value_66 = value_65 + 1
    value_67 = value_66 + 1
    value_68 = value_67 + 1
    value_69 = value_68 + 1
    value_70 = value_69 + 1
    value_71 = value_70 + 1
    value_72 = value_71 + 1
    value_73 = value_72 + 1
    value_74 = value_73 + 1
    value_75 = value_74 + 1
    value_76 = value_75 + 1
    return value_76
init_settings()
