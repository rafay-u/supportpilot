-- Reset the test fixtures to their starting state, relative to NOW().
-- Run in the Supabase SQL Editor: once before an eval run, and again before each case
-- marked [RESET] in `python score_eval.py --plan` (the cases that escalate).
-- Safe to run any time. It only touches the fixture orders SE-1001..SE-1006 and your test chat.

-- 1) Fixture orders back to their scenarios
update orders set status='delivered',        payment_method='cod',  tracking_no=coalesce(tracking_no,'SE-TRK-100001'),
  placed_at=now()-interval '7 days',  shipped_at=now()-interval '6 days',  delivered_at=now()-interval '3 days'  where order_no='SE-1001';
update orders set status='delivered',        payment_method='card', tracking_no=coalesce(tracking_no,'SE-TRK-100002'),
  placed_at=now()-interval '25 days', shipped_at=now()-interval '24 days', delivered_at=now()-interval '20 days' where order_no='SE-1002';
update orders set status='placed',           payment_method='cod',  tracking_no=null,
  placed_at=now()-interval '2 hours', shipped_at=null,                     delivered_at=null                     where order_no='SE-1003';
update orders set status='shipped',          payment_method='cod',  tracking_no=coalesce(tracking_no,'SE-TRK-100004'),
  placed_at=now()-interval '4 days',  shipped_at=now()-interval '3 days',  delivered_at=null                     where order_no='SE-1004';
update orders set status='shipped',          payment_method='cod',  tracking_no=coalesce(tracking_no,'SE-TRK-100005'),
  placed_at=now()-interval '11 days', shipped_at=now()-interval '10 days', delivered_at=null                     where order_no='SE-1005';
update orders set status='out_for_delivery', payment_method='cod',  tracking_no=coalesce(tracking_no,'SE-TRK-100006'),
  placed_at=now()-interval '5 days',  shipped_at=now()-interval '4 days',  delivered_at=null                     where order_no='SE-1006';

-- 2) Remove returns, pending actions and refund tickets created by earlier test runs
delete from returns where order_id in (select id from orders where order_no between 'SE-1001' and 'SE-1006');
delete from pending_actions where status = 'pending';
delete from tickets where order_id in (select id from orders where order_no between 'SE-1001' and 'SE-1006');

-- 3) Test conversation back to open
update conversations
   set status='open', escalation_reason=null, escalated_at=null, handoff_note=null
 where channel in ('telegram','web');
