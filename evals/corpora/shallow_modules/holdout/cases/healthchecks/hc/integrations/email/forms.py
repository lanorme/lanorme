from __future__ import annotations
import json
from django import forms


class EmailForm(forms.Form):
    neutral_attribute_1 = 0
    neutral_attribute_2 = 0
    neutral_attribute_3 = 0
    neutral_attribute_4 = 0
    def neutral_method_1(self):
        value_1 = 0
        value_2 = value_1 + 1
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        return value_4
    def neutral_method_2(self):
        return None
