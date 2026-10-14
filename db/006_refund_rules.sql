-- Phase 5, step 3: refund limit rule
-- Run in the Supabase SQL Editor AFTER 005_actions.sql. Safe to run twice.
--
-- RULE: a change-of-mind return refunds (order total - return fee).
--   refund <= refund_auto_limit_pkr  ->  return is APPROVED automatically
--   refund >  refund_auto_limit_pkr  ->  stays 'requested' + a ticket for a human
-- The bot only approves. Paying the money stays a human/finance job.

alter table returns add column if not exists refund_amount numeric(10,2);

create or replace function create_return_request(
  p_order_no text, p_customer_id bigint, p_reason text, p_conversation_id bigint default null)
returns jsonb language plpgsql as $$
declare
  o      orders%rowtype;
  chk    jsonb;
  res    jsonb;
  fee    numeric;
  refund numeric;
  limit_ numeric := setting_num('refund_auto_limit_pkr');
  auto   boolean;
begin
  -- lock first so a double submit waits, then sees the first request
  select * into o from orders
   where order_no = p_order_no and customer_id = p_customer_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'order_not_on_account');
  end if;

  chk := check_return_eligibility(p_order_no, p_customer_id, p_reason);
  if chk->>'decision' = 'eligible' then
    fee    := (chk->>'fee_pkr')::numeric;
    refund := greatest(o.total - fee, 0);
    auto   := refund <= limit_;

    insert into returns (order_id, reason, status, refund_amount, resolved_at)
    values (o.id, p_reason, case when auto then 'approved' else 'requested' end,
            refund, case when auto then now() end);

    if not auto then
      insert into tickets (customer_id, order_id, category, priority, summary)
      values (p_customer_id, o.id, 'refund', 'medium',
              'Return on '||o.order_no||' needs approval: refund PKR '||refund||
              ' is above the auto-approve limit of PKR '||limit_||'.');
    end if;

    res := jsonb_build_object('ok', true, 'approved', auto,
                              'fee_pkr', fee, 'refund_pkr', refund);
  else
    res := jsonb_build_object('ok', false, 'decision', chk->>'decision', 'reason', chk->>'reason');
  end if;

  insert into action_log (conversation_id, customer_id, order_id, action, params, result)
  values (p_conversation_id, p_customer_id, o.id, 'return_request',
          jsonb_build_object('order_no', p_order_no, 'reason', p_reason),
          case when (res->>'ok')::boolean
               then case when (res->>'approved')::boolean then 'done: approved' else 'done: needs human approval' end
               else 'refused: '||(res->>'reason') end);
  return res;
end $$;
