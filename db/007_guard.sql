-- Phase 6, step 2: output guard log
-- Run in the Supabase SQL Editor. Safe to run twice.
-- Every reply the guard blocks is saved here, so you can see what the AI tried to say.
create table if not exists guard_events (
  id              bigint generated always as identity primary key,
  conversation_id bigint references conversations(id),
  reason          text not null,
  blocked_text    text not null,
  created_at      timestamptz not null default now()
);
create index if not exists idx_guard_events_conv on guard_events(conversation_id, created_at);
alter table guard_events enable row level security;
