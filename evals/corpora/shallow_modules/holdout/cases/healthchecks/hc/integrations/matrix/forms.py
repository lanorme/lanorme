from __future__ import annotations
from django import forms
from hc.integrations.matrix import client


class AddMatrixForm(forms.Form):
    neutral_attribute_1 = 0
    neutral_attribute_2 = 0
    def neutral_method_1(self):
        value_1 = 0
        value_2 = value_1 + 1
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        value_5 = value_4 + 1
        value_6 = value_5 + 1
        return value_6
