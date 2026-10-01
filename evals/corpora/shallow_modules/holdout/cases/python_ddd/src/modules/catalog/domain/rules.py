from seedwork.domain.rules import BusinessRule
from seedwork.domain.value_objects import Money
from .value_objects import ListingId, ListingStatus, SellerId


class ListingMustBeInDraftState(BusinessRule):
    _neutral_attribute_1 = 0
    neutral_attribute_2: object
    def neutral_method_1(self):
        return None


class ListingAskPriceMustBeGreaterThanZero(BusinessRule):
    _neutral_attribute_3 = 0
    neutral_attribute_4: object
    def neutral_method_2(self):
        return None


class ListingMustBeDraft(BusinessRule):
    _neutral_attribute_5 = 0
    neutral_attribute_6: object
    def neutral_method_3(self):
        return None


class SellerMustBeEligibleForAddingNextListing(BusinessRule):
    _neutral_attribute_7 = 0
    neutral_attribute_8: object
    neutral_attribute_9: object
    def neutral_method_4(self):
        return None


class PublishedListingMustNotBeDeleted(BusinessRule):
    _neutral_attribute_10 = 0
    neutral_attribute_11: object
    def neutral_method_5(self):
        return None


class OnlyListingOwnerCanPublishListing(BusinessRule):
    _neutral_attribute_12 = 0
    neutral_attribute_13: object
    neutral_attribute_14: object
    def neutral_method_6(self):
        return None


class OnlyListingOwnerCanDeleteListing(BusinessRule):
    _neutral_attribute_15 = 0
    neutral_attribute_16: object
    neutral_attribute_17: object
    def neutral_method_7(self):
        return None
