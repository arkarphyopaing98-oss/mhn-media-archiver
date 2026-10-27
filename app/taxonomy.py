"""Folder taxonomy: TIKTOK RAW / <MATERIAL> / <PILLAR>.

Product type is not a folder level; it goes into the file name and the log sheet
so the content machine can filter on it without deep folder trees.
"""
from __future__ import annotations

MATERIALS = ["UPVC", "ALUMINIUM", "STEEL", "IRON ROD", "GENERAL"]

PILLARS = [
    "BEHIND THE SCENES",      # factory production, machines, QC, loading
    "INSTALLATION",           # on-site fitting, finished site walkthroughs
    "PRODUCT SHOWCASE",       # finished product, close-ups, features, hardware
    "CUSTOMER & TESTIMONIAL", # customer speaking, reviews, handover moments
    "KNOWLEDGE & FAQ",        # staff explaining, tips, comparisons, warranty
    "TEAM & STORY",           # people, company story, events, daily life
]

PRODUCT_TYPES = [
    "Swing Door", "Slide Door", "Kitchen Door", "Casement Window", "Slide Window",
    "Fixed Window", "Partition", "Lock & Hardware", "Profile & Material", "Other",
]

# Products MHN never shows. Media mentioning them is archived but marked excluded.
BANNED_KEYWORDS = [
    "folding door", "french door", "frosted glass", "ခေါက်တံခါး",
]  # the AI classifier also flags these in Burmese wording (see classifier.py)

TO_SORT = "_TO SORT"


def folder_path(material: str, pillar: str, confident: bool) -> list[str]:
    if not confident or material not in MATERIALS or pillar not in PILLARS:
        return [TO_SORT]
    return [material, pillar]


def is_banned(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in BANNED_KEYWORDS)


# ---------------------------------------------------------------------------
# Business profiles (one archiver service can watch several Telegram groups).
from dataclasses import dataclass, field as _field


@dataclass(frozen=True)
class Profile:
    key: str
    business: str                 # one-paragraph description for the classifier
    materials: list               # folder level 1 (product group)
    pillars: list                 # folder level 2
    product_types: list
    banned: list = _field(default_factory=list)
    banned_rule: str = ""

    def folder_path(self, material: str, pillar: str, confident: bool) -> list:
        if not confident or material not in self.materials or pillar not in self.pillars:
            return [TO_SORT]
        return [material, pillar]

    def is_banned(self, text: str) -> bool:
        low = (text or "").lower()
        return any(k in low for k in self.banned)


PROFILES = {
    "mhn": Profile(
        key="mhn",
        business=("MHN Window & Door Center (Myanmar) manufactures and wholesales UPVC and aluminium windows/doors, "
                  "plus steel and iron rod."),
        materials=MATERIALS, pillars=PILLARS, product_types=PRODUCT_TYPES, banned=BANNED_KEYWORDS,
        banned_rule="banned: true if the item shows or mentions a folding door, French door or frosted glass, else false",
    ),
    "rm": Profile(
        key="rm",
        business=("RM Mahar Nwe (မဟာနွယ်) is a building-materials shop in Hlaing Tharyar, Yangon, selling retail and "
                  "wholesale: bricks (အုတ်), sand (သဲ), stone/aggregate (ကျောက်), cement (ဘိလပ်မြေ), deformed bar, "
                  "angle bar, flat bar, coil and steel beams."),
        materials=["BRICK", "SAND & STONE", "CEMENT", "DEFORMED BAR", "ANGLE & FLAT BAR", "COIL", "BEAM", "GENERAL"],
        pillars=["SHOP & STOCK", "DELIVERY", "PRODUCT SHOWCASE", "CUSTOMER & TESTIMONIAL", "KNOWLEDGE & FAQ", "TEAM & STORY"],
        product_types=["Retail order", "Wholesale order", "Stock arrival", "Loading", "Site delivery", "Product close-up", "Other"],
        banned=[], banned_rule="banned: always false",
    ),
}
