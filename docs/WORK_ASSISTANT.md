# Work Assistant

Hermis Work Assistant is integrated into the Discord tracker and the local tracker DB. It is not a standalone app.

## Safety Model

Normal owner messages in `#work-tracker` follow this flow:

1. Save the raw message in `work_captures` with source metadata.
2. Save an initial `draft_parse_json` with `confidence` and `review_reason`.
3. Mark the capture `unreviewed`.
4. Nightly Hermis review confirms, corrects, splits, ignores with a reason, or asks a clarification.
5. Only confirmed review output creates rows in `work_items`.

The draft parse is only a hint. It must not be treated as final task truth.

Finance uses the same review-first safety shape: raw capture first, AI-led review, validated JSON, then structured records.

## Schedule

- Timezone: `Africa/Casablanca`
- Work window: `14:00-23:00`

These defaults are exposed through `.env.discord-tracker`:

```bash
WORK_CHANNEL_NAME=work-tracker
WORK_START_HOUR=14
WORK_END_HOUR=23
```

## Tables

`work_captures` stores every normal work tracker Discord message:

- `raw_text`
- `source`, `source_message_id`, `source_channel_id`, `source_channel_name`, `logged_by`
- `draft_parse_json`
- `confidence`, `review_reason`
- `review_status`: `unreviewed`, `confirmed`, `clarification`, or `ignored`
- `clarification_question` or `ignore_reason`

`work_items` stores only confirmed work:

- `capture_id`
- `title`, `status`, `priority`
- optional project, area, due date, scheduled date, energy, effort, context, tags, note
- source metadata copied from the capture

## Nightly Review

Run:

```bash
scripts/process_work_reviews.py <YYYY-MM-DD> --all-open
```

The script calls `scripts/run_work_ai_reviewer.py` by default. It validates Hermis JSON before applying anything.

Expected output:

- `reports/work/YYYY-MM-DD-parsing-review.md`
- `inbox/needs-answer/YYYY-MM-DD-work.md` when clarification is needed
- refreshed `state/work.md`

The script itself must not infer final tasks from raw text. If Hermis output is missing, malformed, or incomplete, the capture stays unconfirmed and a clarification is written.

## Discord Commands

- `!work` shows today's confirmed work and open capture review count.
- `!work add <text>` explicitly creates confirmed work from the command text.
- `!work list` shows active confirmed work.
- `!work today` shows confirmed work due or scheduled today.
- `!work focus` shows a compact focus list for the Casablanca work window.
- `!work done <id>` marks confirmed work done.
- `!work block <id> <reason>` marks confirmed work blocked. Reason is required.
- `!work wait <id> <reason>` marks confirmed work waiting. Reason is required.
- `!work review` shows confirmed active work plus unreviewed or unclear captures.

`!work add` is intentionally different from normal `#work-tracker` messages: the command is an explicit user confirmation. Normal channel messages remain review-first.
