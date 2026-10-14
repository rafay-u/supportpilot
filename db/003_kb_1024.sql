-- Phase 3 (Mistral version): knowledge base with 1024-number embeddings
-- Use this INSTEAD of 003_kb.sql when your embedding model is mistral-embed.
-- Safe to run even if you already ran 003_kb.sql: it removes the old (768) table
-- and function first. The knowledge base is re-created by running ingest_kb.py,
-- so nothing important is lost.
--
-- If you get: type "vector" does not exist, run this instead of the next line:
--   create schema if not exists extensions;
--   create extension if not exists vector with schema extensions;
create extension if not exists vector;

drop function if exists match_kb(vector, int, jsonb);
drop table if exists kb_chunks;

-- 1024 = size of one mistral-embed vector. It MUST match the model's output.
create table kb_chunks (
  id          bigint generated always as identity primary key,
  doc_id      text not null,
  title       text not null,
  section     text not null,
  chunk_index int  not null,
  content     text not null,
  metadata    jsonb not null default '{}'::jsonb,
  embedding   vector(1024),
  created_at  timestamptz not null default now(),
  unique (doc_id, chunk_index)
);

create index idx_kb_chunks_embedding on kb_chunks
  using hnsw (embedding vector_cosine_ops);

-- Parameter names match what the n8n Supabase Vector Store node expects.
create or replace function match_kb(
  query_embedding vector(1024),
  match_count     int   default 4,
  filter          jsonb default '{}'::jsonb
)
returns table (id bigint, content text, metadata jsonb, similarity float)
language sql stable
as $$
  select k.id, k.content, k.metadata,
         1 - (k.embedding <=> query_embedding) as similarity
  from kb_chunks k
  where k.metadata @> filter
  order by k.embedding <=> query_embedding
  limit match_count;
$$;

alter table kb_chunks enable row level security;
