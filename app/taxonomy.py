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
