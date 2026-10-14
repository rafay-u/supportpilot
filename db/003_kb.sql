-- Phase 3: knowledge base with vector search
-- Run in the Supabase SQL Editor AFTER 002_conversations.sql.
--
-- If you get: type "vector" does not exist, run this instead of the next line:
--   create schema if not exists extensions;
--   create extension if not exists vector with schema extensions;
create extension if not exists vector;

-- EMBEDDING SIZE -------------------------------------------------
-- 768 works with OpenAI text-embedding-3-small (dimensions = 768) and with
-- Gemini embeddings (outputDimensionality = 768).
-- The number MUST match what your embedding model returns. If you change the
-- model or the size later, drop this table and ingest again.

-- KB CHUNKS ------------------------------------------------------
-- One row per piece of a policy document.
-- content = text the AI will read. metadata = who it came from (for citations).
-- embedding = the meaning of the text as 768 numbers, used for search.
create table kb_chunks (
  id          bigint generated always as identity primary key,
  doc_id      text not null,                 -- file name without .md
  title       text not null,                 -- document title
  section     text not null,                 -- section heading
  chunk_index int  not null,
  content     text not null,
  metadata    jsonb not null default '{}'::jsonb,
  embedding   vector(768),
  created_at  timestamptz not null default now(),
  unique (doc_id, chunk_index)
);

-- Approximate nearest-neighbour index. With ~60 chunks it is not needed for
-- speed, but it is the pattern you will use on real client data.
create index idx_kb_chunks_embedding on kb_chunks
  using hnsw (embedding vector_cosine_ops);

-- SEARCH FUNCTION ------------------------------------------------
-- Returns the closest chunks to a query embedding.
-- <=> is cosine DISTANCE (0 = identical). similarity = 1 - distance.
-- The parameter names (query_embedding, match_count, filter) are the ones the
-- n8n Supabase Vector Store node expects, so n8n can call this directly.
create or replace function match_kb(
  query_embedding vector(768),
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
