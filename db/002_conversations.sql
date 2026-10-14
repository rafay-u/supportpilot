-- Phase 1: remember conversations and messages
-- Run in the Supabase SQL Editor AFTER schema.sql.

-- CONVERSATIONS --------------------------------------------------
-- One row per chat (one Telegram chat = one conversation, for now).
-- customer_id is nullable: at first we don't know WHO the person is.
-- Phase 2 will link the chat to a real customer.
create table conversations (
  id               bigint generated always as identity primary key,
  customer_id      bigint references customers(id),
  channel          text not null check (channel in ('telegram', 'whatsapp', 'web')),
  external_chat_id text not null,            -- the ID Telegram/WhatsApp uses for this chat
  status           text not null default 'open'
                   check (status in ('open', 'escalated', 'resolved')),
  started_at       timestamptz not null default now(),
  last_message_at  timestamptz not null default now(),

  -- The same chat can only exist once per channel.
  -- This also powers "insert or update" (UPSERT) in n8n.
  constraint uq_conversation_chat unique (channel, external_chat_id)
);

-- MESSAGES -------------------------------------------------------
-- One row per message, whoever sent it.
create table messages (
  id                  bigint generated always as identity primary key,
  conversation_id     bigint not null references conversations(id) on delete cascade,
  role                text not null
                      check (role in ('customer', 'agent', 'human', 'system')),
  content             text not null,
  external_message_id text,                  -- Telegram's own message number
  created_at          timestamptz not null default now()
);

create index idx_messages_conversation on messages(conversation_id, created_at);
create index idx_conversations_customer on conversations(customer_id);

alter table conversations enable row level security;
alter table messages      enable row level security;
