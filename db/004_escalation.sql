-- Phase 4: escalation + handoff
-- Run in the Supabase SQL Editor AFTER 002_conversations.sql. Safe to run twice.
-- (You already ran this from chat. This file is the saved copy for your repo.)

alter table conversations
  add column if not exists escalation_reason text,
  add column if not exists escalated_at timestamptz,
  add column if not exists handoff_note text;

-- One row per agent decision: audit log, and the data your Phase 7 evals will read.
create table if not exists agent_runs (
  id              bigint generated always as identity primary key,
  conversation_id bigint not null references conversations(id),
  route           text not null check (route in ('answer','escalate')),
  reason          text,
  tools_used      text[] not null default '{}',
  latency_ms      int,
  created_at      timestamptz not null default now()
);
create index if not exists idx_agent_runs_conv on agent_runs (conversation_id, created_at);
alter table agent_runs enable row level security;
