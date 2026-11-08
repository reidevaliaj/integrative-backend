import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.deps import get_current_user
from app.api.routes import blog, magazines, samples
from app.db.base import Base
from app.db.session import get_db
from app.models.blog import BlogPost, BlogRevision
from app.models.user import User
from app.services.access import order_covers_magazine
from app.services.blog_seed import seed_blog
from app.services.seed import seed_magazines


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(email="editor@example.com", hashed_password="unused", is_editor=True)
        self.db.add(self.user); self.db.commit()
        self.app = FastAPI()
        self.app.include_router(blog.router, prefix="/blog")
        self.app.include_router(samples.router, prefix="/samples")
        self.app.include_router(magazines.router, prefix="/magazines")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

    def payload(self, **changes):
        result = dict(slug="a-journal-note", title="A journal note", excerpt="A short introduction to the journal.", body="A useful paragraph about the publication and its available editions.", status="draft")
        result.update(changes)
        return result

    def test_drafts_are_private_and_publication_is_explicit(self):
        created = self.client.post("/blog/editor/posts", json=self.payload())
        self.assertEqual(created.status_code, 201)
        post = created.json()
        self.assertEqual(self.client.get("/blog/").json(), [])
        self.assertEqual(self.client.get("/blog/a-journal-note").status_code, 404)
        saved = self.client.post(f"/blog/editor/posts/{post['id']}", json=self.payload(status="published", version=1))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(len(self.client.get("/blog/").json()), 1)
        self.assertEqual(self.client.get("/blog/a-journal-note").json()["body"], self.payload()["body"])
        self.assertEqual(self.client.get("/blog/").headers['cache-control'], 'no-store')

    def test_ordinary_and_anonymous_users_cannot_edit(self):
        self.user.is_editor = False
        self.assertEqual(self.client.post("/blog/editor/posts", json=self.payload()).status_code, 403)
        self.assertEqual(self.client.get("/blog/editor/posts").status_code, 403)
        self.app.dependency_overrides.pop(get_current_user)
        self.assertEqual(self.client.post("/blog/editor/posts", json=self.payload()).status_code, 401)
        self.assertEqual(self.client.get("/blog/").status_code, 200)

    def test_versions_prevent_overwrites_and_restore_as_draft(self):
        post = self.client.post("/blog/editor/posts", json=self.payload()).json()
        post_id = post['id']
        self.assertEqual(self.client.post(f"/blog/editor/posts/{post_id}", json=self.payload(title="Updated journal note", status="published", version=1)).status_code, 200)
        self.assertEqual(self.client.post(f"/blog/editor/posts/{post_id}", json=self.payload(title="Stale edit", version=1)).status_code, 409)
        revisions = self.client.get(f"/blog/editor/posts/{post_id}/revisions").json()
        self.assertEqual([r['version'] for r in revisions], [2, 1])
        restored = self.client.post(f"/blog/editor/posts/{post_id}/restore/{revisions[-1]['id']}", json={"version": 2})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()['title'], 'A journal note')
        self.assertEqual(restored.json()['status'], 'draft')
        self.assertEqual(restored.json()['version'], 3)
        self.assertEqual(self.client.get("/blog/a-journal-note").status_code, 404)
        self.assertEqual(len(self.db.scalars(select(BlogRevision)).all()), 3)

    def test_duplicate_slug_and_future_publication_do_not_leak(self):
        self.assertEqual(self.client.post("/blog/editor/posts", json=self.payload()).status_code, 201)
        self.assertEqual(self.client.post("/blog/editor/posts", json=self.payload()).status_code, 409)
        post = self.db.scalar(select(BlogPost))
        post.status = 'published'; post.published_at = datetime.now(timezone.utc) + timedelta(days=2); self.db.commit()
        self.assertEqual(self.client.get("/blog/").json(), [])
        self.assertEqual(self.client.get("/blog/a-journal-note").status_code, 404)

    def test_initial_notes_do_not_overwrite_editor_changes(self):
        seed_blog(self.db)
        first = self.db.scalar(select(BlogPost).order_by(BlogPost.id))
        first.title = 'Publisher edited title'; self.db.commit()
        seed_blog(self.db)
        self.assertEqual(first.title, 'Publisher edited title')
        self.assertEqual(len(self.db.scalars(select(BlogPost)).all()), 3)

    def test_only_explicit_samples_are_public_paid_documents_stay_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = b'%PDF-1.7\nSynthetic sample for route testing'
            (root/'193-basic-en.pdf').write_bytes(data)
            (root/'193-medical-en.pdf').write_bytes(data)
            with patch.object(samples, 'PDF_DIRECTORY', root):
                self.app.dependency_overrides.pop(get_current_user)
                sample = self.client.get('/samples/om193/basic')
                self.assertEqual(sample.status_code, 200)
                self.assertEqual(sample.content, data)
                self.assertEqual(self.client.get('/samples/om193/medical', headers={'Range': 'bytes=0-7'}).status_code, 206)
                self.assertEqual(self.client.get('/samples/om193/195-basic-en.pdf').status_code, 404)
                self.assertEqual(self.client.get('/magazines/main-issue-195/document?section=basic').status_code, 401)

    def test_new_catalogue_preserves_ids_sections_and_annual_entitlements(self):
        seed_magazines(self.db)
        from app.models.magazine import Magazine
        before = {m.slug: m.id for m in self.db.scalars(select(Magazine)).all()}
        seed_magazines(self.db)
        all_issues = self.db.scalars(select(Magazine)).all()
        self.assertEqual(before, {m.slug: m.id for m in all_issues})
        self.assertEqual(len(all_issues), 6)
        self.assertEqual(sorted((m.issue_number, sum(d.page_count for d in m.documents)) for m in all_issues), [('194', 115), ('195', 119), ('196', 127), ('40', 116), ('41', 103), ('42', 145)])
        classic_order = SimpleNamespace(volume_year=2026, magazine_id=None, plan=SimpleNamespace(category='classic'))
        for issue in all_issues:
            self.assertEqual(order_covers_magazine(classic_order, issue), issue.issue_type == 'classic')
