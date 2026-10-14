-- ShopEase schema (Phase 0)
-- Run this ONCE in a fresh database. To start over, run the DROP block first.

-- ---------------------------------------------------------------
-- DROP block (uncomment to reset everything)
-- drop table if exists tickets, returns, order_items, orders, products, customers cascade;
-- ---------------------------------------------------------------

-- CUSTOMERS ------------------------------------------------------
-- The phone number is how WhatsApp identifies a person, so it must be unique.
create table customers (
  id          bigint generated always as identity primary key,
  phone       text not null unique,            -- E.164 format: +923001234567
  name        text not null,
  email       text,
  city        text,
  language    text not null default 'en'
              check (language in ('en', 'ur', 'roman_ur')),
  created_at  timestamptz not null default now()
);

-- PRODUCTS -------------------------------------------------------
create table products (
  id              bigint generated always as identity primary key,
  name            text not null,
  category        text not null,
  price           numeric(10,2) not null check (price >= 0),  -- never use float for money
  warranty_months int not null default 0,
  returnable      boolean not null default true               -- policy lives in data
);

-- ORDERS ---------------------------------------------------------
-- order_no is the human-friendly ID customers type ("SE-1001").
-- id is the internal ID used for foreign keys.
create table orders (
  id             bigint generated always as identity primary key,
  order_no       text not null unique,
  customer_id    bigint not null references customers(id),
  status         text not null
                 check (status in ('placed','packed','shipped','out_for_delivery',
                                   'delivered','cancelled','returned')),
  payment_method text not null
                 check (payment_method in ('cod','card','easypaisa','jazzcash')),
  total          numeric(10,2) not null check (total >= 0),
  address        text not null,
  tracking_no    text,
  placed_at      timestamptz not null,
  shipped_at     timestamptz,
  delivered_at   timestamptz,

  -- The database itself refuses impossible states:
  constraint delivered_needs_date
    check (status not in ('delivered','returned') or delivered_at is not null),
  constraint shipped_needs_date
    check (status not in ('shipped','out_for_delivery','delivered','returned')
           or shipped_at is not null)
);

-- ORDER ITEMS ----------------------------------------------------
-- One row per product in an order. unit_price is a SNAPSHOT of the price at
-- purchase time, so changing the product price later does not rewrite history.
create table order_items (
  id         bigint generated always as identity primary key,
  order_id   bigint not null references orders(id) on delete cascade,
  product_id bigint not null references products(id),
  qty        int not null check (qty > 0),
  unit_price numeric(10,2) not null check (unit_price >= 0)
);

-- RETURNS --------------------------------------------------------
create table returns (
  id            bigint generated always as identity primary key,
  order_id      bigint not null references orders(id),
  reason        text not null,
  status        text not null
                check (status in ('requested','approved','rejected','refunded')),
  requested_at  timestamptz not null default now(),
  resolved_at   timestamptz
);

-- TICKETS --------------------------------------------------------
-- order_id is nullable: some tickets are not about a specific order.
create table tickets (
  id          bigint generated always as identity primary key,
  customer_id bigint not null references customers(id),
  order_id    bigint references orders(id),
  category    text not null
              check (category in ('delivery','refund','product_issue','billing','other')),
  priority    text not null default 'medium'
              check (priority in ('low','medium','high','urgent')),
  status      text not null default 'open'
              check (status in ('open','pending','resolved')),
  summary     text not null,
  assigned_to text,
  created_at  timestamptz not null default now()
);

-- INDEXES --------------------------------------------------------
-- Postgres indexes primary keys and UNIQUE columns automatically, but NOT
-- foreign keys. We look things up by these columns constantly.
create index idx_orders_customer   on orders(customer_id);
create index idx_orders_status     on orders(status);
create index idx_items_order       on order_items(order_id);
create index idx_returns_order     on returns(order_id);
create index idx_tickets_customer  on tickets(customer_id);

-- SECURITY (Supabase) --------------------------------------------
-- Supabase exposes the public schema through an HTTP API. Turning on Row Level
-- Security with NO policies means that API can see nothing. Our own code
-- connects with the database connection string, which bypasses RLS.
alter table customers   enable row level security;
alter table products    enable row level security;
alter table orders      enable row level security;
alter table order_items enable row level security;
alter table returns     enable row level security;
alter table tickets     enable row level security;
