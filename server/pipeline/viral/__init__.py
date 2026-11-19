"""Viral clip finder: link in, ranked vertical clips out.

Public surface (everything else is an implementation detail):

    finder.prepare(url)           metadata for a link, no download
    finder.check_source(info)     raises Blocked for upcoming/live/private
    finder.run_job(job_id)        the whole pipeline for one background job
    finder.estimate(url)          cost/time preview before starting
    renderer.render_viral_clip()  cut + reframe + burn captions for one clip
"""
from __future__ import annotations

from . import finder

prepare = finder.prepare
check_source = finder.check_source
run_job = finder.run_job
estimate = finder.estimate
Blocked = finder.Blocked

__all__ = ["prepare", "check_source", "run_job", "estimate", "Blocked", "finder"]
