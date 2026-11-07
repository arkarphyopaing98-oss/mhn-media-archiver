"""Pick tab: the footage shortlist the TikTok video maker must use (owner 2026-11-04, "footage ထပ်နေတယ်").

Rules (pure functions, no Google calls — tested in tests/test_core.py):
- JOB = one real job/visit = sender | posted date | material | product_type. Clips of the same job look the same,
  so the video maker takes at most ONE post per job per day, and a job rests 7 days after any of its clips is used.
- A single clip rests 14 days after use (Log column Q `used_on`).
- Mix 50/50: jobs posted in the last 7 days ("new", newest first) alternate with older jobs ("archive",
  never-used first, then least recently used, then oldest).
- Skips: duplicates / excluded / processing rows, qc=reject, rows without a usable file, identical copies
  (same kind + size + duration).
"""
from __future__ import annotations

from datetime import date, timedelta

# Log columns (0-based)
A_ID, B_POSTED, C_SENDER, D_KIND, F_LINK, G_FILE, H_MAT, I_PROD, J_PIL, K_STORY, L_SUM = 0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11
N_DUR, O_SIZE, P_STATUS, Q_USED, R_QC, T_EDIT, U_HQ = 13, 14, 15, 16, 17, 19, 20

CLIP_REST_DAYS = 14
# Per-profile override (owner 2026-11-07: RM 7 days, temporary). Env CLIP_REST_DAYS_<PROFILE> wins.
CLIP_REST_BY_PROFILE = {"rm": 7}
JOB_REST_DAYS = 7
NEW_DAYS = 7
MAX_CLIPS_PER_JOB = 4
BAD_STATUS = ("duplicate", "excluded", "processing", "reject", "skip")

HEADER = ["rank", "bucket", "job_id", "tg_msg_id", "posted_at", "media_type", "material", "product_type", "pillar",
          "duration_s", "size_mb", "edit_link", "hq_parts", "drive_file_id", "ai_summary", "story", "job_last_used",
          "qc", "rules"]


def _c(r: list, i: int) -> str:
    return str(r[i]).strip() if i < len(r) and r[i] is not None else ""


def _f(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


def _d(s: str) -> date | None:
    try:
        return date.fromisoformat(s.strip()[:10])
    except (ValueError, AttributeError):
        return None


def job_id(r: list) -> str:
    return " | ".join([_c(r, C_SENDER), _c(r, B_POSTED)[:10], _c(r, H_MAT) or "?", _c(r, I_PROD) or "?"])


def usable(r: list) -> bool:
    kind = _c(r, D_KIND)
    if kind not in {"video", "photo"} or not _c(r, A_ID).lstrip("-").isdigit():
        return False
    st = _c(r, P_STATUS).lower()
    if any(st.startswith(b) for b in BAD_STATUS) or _c(r, R_QC).lower() == "reject":
        return False
    if kind == "video":
        small = 0 < _f(_c(r, O_SIZE)) < 9.8
        return bool(_c(r, T_EDIT) or _c(r, U_HQ).startswith("[") or (small and _c(r, G_FILE)))
    return bool(_c(r, G_FILE) or _c(r, F_LINK))


def build(rows: list[list], today: date, clip_rest_days: int = CLIP_REST_DAYS) -> list[list]:
    """rows = Log data rows (no header). Returns Pick rows (no header), best first.
    clip_rest_days: per tenant (RM = 7 days, owner 2026-11-07, temporary while RM footage is scarce)."""
    clip_cut, job_cut, new_cut = (today - timedelta(days=clip_rest_days), today - timedelta(days=JOB_REST_DAYS),
                                  today - timedelta(days=NEW_DAYS))
    job_used: dict[str, date] = {}
    for r in rows:  # last use of ANY clip of the job (even unusable rows count)
        u = _d(_c(r, Q_USED))
        if u:
            j = job_id(r)
            job_used[j] = max(u, job_used.get(j, u))

    jobs: dict[str, list[list]] = {}
    seen_sig = set()
    for r in sorted(rows, key=lambda r: _c(r, B_POSTED), reverse=True):
        if not usable(r):
            continue
        u = _d(_c(r, Q_USED))
        if u and u > clip_cut:
            continue
        j = job_id(r)
        if j in job_used and job_used[j] > job_cut:
            continue
        if _c(r, D_KIND) == "video" and _c(r, O_SIZE) and _c(r, N_DUR):  # same video forwarded twice
            sig = (_c(r, O_SIZE), _c(r, N_DUR))  # (photos: sizes are rounded to 0.1 MB, too coarse to dedupe)
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
        jobs.setdefault(j, []).append(r)

    def posted(j):
        return _c(jobs[j][0], B_POSTED)
    new = sorted((j for j in jobs if (_d(posted(j)) or date.min) > new_cut), key=posted, reverse=True)
    old = [j for j in jobs if j not in set(new)]
    old.sort(key=lambda j: (job_used.get(j, date.min), posted(j)))  # never used first, then least recent, oldest

    order, i, k = [], 0, 0
    while i < len(new) or k < len(old):
        if i < len(new):
            order.append(("new", new[i])); i += 1
        if k < len(old):
            order.append(("archive", old[k])); k += 1

    out = []
    for rank, (bucket, j) in enumerate(order, start=1):
        clips = sorted(jobs[j], key=lambda r: (_c(r, D_KIND) != "video", -_f(_c(r, N_DUR))))
        for r in clips[:MAX_CLIPS_PER_JOB]:
            out.append([rank, bucket, j, _c(r, A_ID), _c(r, B_POSTED), _c(r, D_KIND), _c(r, H_MAT), _c(r, I_PROD),
                        _c(r, J_PIL), _c(r, N_DUR), _c(r, O_SIZE), _c(r, T_EDIT) or _c(r, F_LINK), _c(r, U_HQ),
                        _c(r, G_FILE), _c(r, L_SUM)[:200], _c(r, K_STORY)[:160],
                        job_used[j].isoformat() if j in job_used else "", _c(r, R_QC), ""])
    if out:
        out[0][-1] = (f"1 post per job (same rank) per day · job rests {JOB_REST_DAYS}d · clip rests "
                      f"{clip_rest_days}d · new/archive alternate 50/50 · write Log Q used_on when you pick")
    return out
