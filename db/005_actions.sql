-- Phase 5: safe write actions
-- Run in the Supabase SQL Editor AFTER 002_conversations.sql (and 004 if you made it).
-- Safe to run twice.
--
-- THE IDEA: the AI never writes to orders itself. It can only call these
-- functions. Each function checks the rules inside the database, in one
-- transaction, and refuses anything that breaks them. If the AI is tricked,
-- the database still says no.

-- SETTINGS -------------------------------------------------------
-- Business rules live in data, not in prompts. Change a number here and
-- every action follows it.
create table if not exists settings (
  key   text primary key,
  value text not null
);
insert into settings (key, value) values
  ('return_window_days',   '7'),
  ('return_fee_pkr',       '250'),
  ('refund_auto_limit_pkr','3000')
on conflict (key) do nothing;
alter table settings enable row level security;

create or replace function setting_num(p_key text) returns numeric
language sql stable as $$ select value::numeric from settings where key = p_key $$;

-- ACTION LOG -----------------------------------------------------
-- Every write action leaves a row: who, what, when, result.
create table if not exists action_log (
  id              bigint generated always as identity primary key,
  conversation_id bigint references conversations(id),
  customer_id     bigint references customers(id),
  order_id        bigint references orders(id),
  action          text not null,
  params          jsonb not null default '{}'::jsonb,
  result          text not null,
  created_at      timestamptz not null default now()
);
create index if not exists idx_action_log_order on action_log(order_id);
alter table action_log enable row level security;

-- PENDING ACTIONS ------------------------------------------------
-- Step 2 uses this: the agent PROPOSES an action, the customer must say yes,
-- and only then does code run it. Nothing runs from a pending row on its own.
create table if not exists pending_actions (
  id              bigint generated always as identity primary key,
  conversation_id bigint not null references conversations(id),
  customer_id     bigint not null references customers(id),
  action          text not null check (action in ('cancel_order','return_request')),
  params          jsonb not null,
  status          text not null default 'pending'
                  check (status in ('pending','confirmed','cancelled','expired')),
  expires_at      timestamptz not null default now() + interval '10 minutes',
  created_at      timestamptz not null default now()
);
create index if not exists idx_pending_conv on pending_actions(conversation_id, status);
alter table pending_actions enable row level security;

-- Only one open return per order (also blocks double-clicks and retries).
create unique index if not exists uq_returns_one_open
  on returns(order_id) where status in ('requested','approved','refunded');

-- 1) CHECK RETURN ELIGIBILITY (read only) ------------------------
-- decision: eligible | not_eligible | needs_human | not_found
create or replace function check_return_eligibility(
  p_order_no text, p_customer_id bigint, p_reason text default 'change_of_mind')
returns jsonb language plpgsql stable as $$
declare
  o     orders%rowtype;
  hrs   numeric;
  win   numeric := setting_num('return_window_days');
begin
  -- ownership check: the customer id is part of the lookup
  select * into o from orders where order_no = p_order_no and customer_id = p_customer_id;
  if not found then
    return jsonb_build_object('decision','not_found','reason','order_not_on_account');
  end if;

  if p_reason not in ('change_of_mind','damaged_or_wrong') then
    return jsonb_build_object('decision','needs_human','reason','unknown_reason');
  end if;

  if o.status <> 'delivered' then
    return jsonb_build_object('decision','not_eligible','reason','not_delivered','order_status',o.status);
  end if;

  hrs := extract(epoch from (now() - o.delivered_at)) / 3600;
  if hrs > win * 24 then
    return jsonb_build_object('decision','not_eligible','reason','outside_return_window',
                              'window_days',win,'hint','may_have_warranty');
  end if;

  if exists (select 1 from returns where order_id = o.id
             and status in ('requested','approved','refunded')) then
    return jsonb_build_object('decision','not_eligible','reason','return_already_requested');
  end if;

  -- Damaged or wrong item: needs photo review by a person (the bot cannot judge photos).
  if p_reason = 'damaged_or_wrong' then
    return jsonb_build_object('decision','needs_human','reason','damaged_needs_photo_review',
                              'within_48h', hrs <= 48);
  end if;

  -- Change of mind: final-sale items are refused.
  if exists (select 1 from order_items i join products p on p.id = i.product_id
             where i.order_id = o.id and not p.returnable) then
    return jsonb_build_object('decision','not_eligible','reason','final_sale_item');
  end if;

  return jsonb_build_object('decision','eligible','reason','change_of_mind',
                            'fee_pkr', setting_num('return_fee_pkr'),
                            'days_left', round(win - hrs / 24, 1));
end $$;

-- 2) CANCEL ORDER (write) ----------------------------------------
-- Only 'placed' or 'packed' orders. Locks the row so two requests cannot race.
-- Prepaid orders create a refund ticket for a human: this function never moves money.
create or replace function cancel_order(
  p_order_no text, p_customer_id bigint, p_conversation_id bigint default null)
returns jsonb language plpgsql as $$
declare
  o orders%rowtype;
  res jsonb;
begin
  select * into o from orders
   where order_no = p_order_no and customer_id = p_customer_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'order_not_on_account');
  end if;

  if o.status not in ('placed','packed') then
    res := jsonb_build_object('ok', false, 'reason',
             case o.status when 'cancelled' then 'already_cancelled' else 'cannot_cancel_after_'||o.status end,
             'order_status', o.status);
  else
    update orders set status = 'cancelled' where id = o.id;
    if o.payment_method <> 'cod' then
      insert into tickets (customer_id, order_id, category, priority, summary)
      values (p_customer_id, o.id, 'refund', 'medium',
              'Prepaid order '||o.order_no||' cancelled by customer via bot. Refund of PKR '||o.total||' to process.');
    end if;
    res := jsonb_build_object('ok', true, 'order_no', o.order_no,
             'refund_needed', o.payment_method <> 'cod', 'amount_pkr', o.total);
  end if;

  insert into action_log (conversation_id, customer_id, order_id, action, params, result)
  values (p_conversation_id, p_customer_id, o.id, 'cancel_order',
          jsonb_build_object('order_no', p_order_no),
          case when (res->>'ok')::boolean then 'done' else 'refused: '||(res->>'reason') end);
  return res;
end $$;

-- 3) CREATE RETURN REQUEST (write) -------------------------------
-- Creates a 'requested' row only. A human or later rule approves the refund.
create or replace function create_return_request(
  p_order_no text, p_customer_id bigint, p_reason text, p_conversation_id bigint default null)
returns jsonb language plpgsql as $$
declare
  o orders%rowtype;
  chk jsonb;
  res jsonb;
begin
  -- lock first so a double submit waits, then sees the first request
  select * into o from orders
   where order_no = p_order_no and customer_id = p_customer_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'order_not_on_account');
  end if;

  chk := check_return_eligibility(p_order_no, p_customer_id, p_reason);
  if chk->>'decision' = 'eligible' then
    insert into returns (order_id, reason, status) values (o.id, p_reason, 'requested');
    res := jsonb_build_object('ok', true, 'fee_pkr', chk->'fee_pkr');
  else
    res := jsonb_build_object('ok', false, 'decision', chk->>'decision', 'reason', chk->>'reason');
  end if;

  insert into action_log (conversation_id, customer_id, order_id, action, params, result)
  values (p_conversation_id, p_customer_id, o.id, 'return_request',
          jsonb_build_object('order_no', p_order_no, 'reason', p_reason),
          case when (res->>'ok')::boolean then 'done' else 'refused: '||(res->>'reason') end);
  return res;
end $$;
