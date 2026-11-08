"""Initial journal notes. Existing editorial changes are never overwritten."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.blog import remember
from app.models.blog import BlogPost

INITIAL_POSTS = [
    {
        "slug": "welcome-to-om-and-nutrition", "title": "A new home for OM & Nutrition",
        "excerpt": "Discover the English journal, explore the digital library and start with a complete, complimentary sample issue.",
        "category": "Journal notes", "cover": "journal",
        "body": """OM & Nutrition brings the journal's perspectives on nutrition and integrative medicine to an English-reading audience. Our new digital home gives the issues a place of their own, with a personal library for purchased editions and annual subscriptions.

## Start with a free issue

If you are new to the journal, begin with Main Issue No. 193. Both the Basic and Medical sections are available free of charge, without registration or payment. Read the sample to get a feel for the format, range of contributors and balance of subjects before choosing a subscription.

## Two parts, one main issue

Main issues consist of a Basic section and a Medical section. A purchase of a main issue includes both. Special editions bring contributions on a focused subject together in one complete publication. The digital library clearly identifies these sections so you can choose where to begin.

## More than a library

This blog will be a place for publication news, introductions to the issues and notes from the journal. These opening posts introduce the website and the current editions; further contributions from the publisher will follow.

Explore the available issues, try the free sample and return here for the next chapter.""",
    },
    {
        "slug": "inside-sports-medicine-ii", "title": "Inside the issue: Sports Medicine II",
        "excerpt": "A guide to SH42, a 145-page special edition connecting training and recovery with the wider questions of integrative sports medicine.",
        "category": "Inside the issue", "cover": "sports",
        "body": """Special Issue No. 42, Sports Medicine II, is now available in English. The 145-page edition gathers contributions from a range of professional perspectives, giving readers several routes into its subject.

## Training and recovery

The contents open with regeneration and the long-term demands placed on muscles, tendons, ligaments, cartilage and bones. Other contributions consider exertion and recovery, biomarkers, and the experience of the ageing athlete. Together, these subjects frame the edition's interest in what happens between periods of training as well as during them.

## Nutrition, the gut and the wider picture

Further articles explore chrononutrition, water and salt balance, micronutrients, and the relationship between the gut and sport. Dental health, women's training experiences and hormonal changes also feature in the contents. This breadth lets readers follow a particular interest or move between different areas of practice.

## Choose your route through the edition

The special issue is presented as one complete publication, with its contents page at the beginning. Use it to find the contributions most relevant to your professional interests. This post is an introduction to the edition, rather than a clinical summary or a recommendation of any particular intervention.

SH42 is included in the 2026 Special and Combined subscriptions. It can also be purchased as a single digital issue.""",
    },
    {
        "slug": "coming-next-skin-and-mucous-membranes", "title": "Coming next: skin, mucous membranes and Issue 197",
        "excerpt": "The publisher's next planned releases: SH43 in late November, followed by Main Issue No. 197 in late December or January 2027.",
        "category": "Publication news", "cover": "preview",
        "body": """The publisher has shared the next two planned releases for OM & Nutrition. These notices are now displayed beside the available issues so readers can see what is ready to read and what is still to come.

## SH43: Skin and Mucous Membranes

The next special issue is SH43, Skin and Mucous Membranes, planned for late November 2026. As a special edition, it will bring a focused subject together in a single publication. Its full contents will be introduced when the issue is available.

## Main Issue No. 197

The next main issue is No. 197, planned for late December 2026 or January 2027. Main issues generally contain both a Basic section and a Medical section. The publication window here reflects the publisher's latest update; the issue is not yet available to read or order individually.

## Stay close to the journal

We will use the blog for new issue announcements, introductions and future contributions from the publisher. Current editions remain available in the digital library while these releases are being prepared.

Publication dates are planned windows and can change. The library will identify each new issue and its volume year when it is published.""",
    },
]


def seed_blog(db: Session) -> None:
    for index, data in enumerate(INITIAL_POSTS):
        if db.scalar(select(BlogPost).where(BlogPost.slug == data["slug"])) is not None:
            continue
        post = BlogPost(**data, status="published", published_at=datetime(2026, 11, 8, 23, 0, index, tzinfo=timezone.utc))
        db.add(post)
        db.flush()
        remember(db, post, None)
    db.commit()
