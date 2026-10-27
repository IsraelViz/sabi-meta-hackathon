-- Sabi ledger schema. Runs on Postgres (Supabase) and on the phone in PGlite.
-- Money is integer kobo everywhere. Quantities are in each product's base unit.
-- Ids are text (uuid strings by default) so seed data can use readable ids.

create table if not exists businesses (
  id            text primary key default gen_random_uuid()::text,
  name          text not null,
  payment_details text not null default '',
  currency      text not null default 'NGN',
  minor_units   int  not null default 2,
  timezone      text not null default 'Africa/Lagos',
  locale        text not null default 'en-NG',
  tax_rate_bp   int  not null default 0,          -- basis points; 750 = 7.5% VAT
  coa_template  text not null default 'ng_retail',
  keep_on_phone boolean not null default true,
  keep_in_cloud boolean not null default true,
  constraint books_somewhere check (keep_on_phone or keep_in_cloud)
);

create table if not exists accounts (
  code  text primary key,                       -- 'cash', 'bank', 'ar', ...
  name  text not null,
  kind  text not null check (kind in ('asset','liability','equity','income','expense'))
);

create table if not exists products (
  id              text primary key default gen_random_uuid()::text,
  name            text not null,
  aliases         text[] not null default '{}',
  base_unit       text not null,                -- 'carton', 'bag', 'piece'
  sell_price_kobo bigint not null check (sell_price_kobo >= 0),
  min_price_kobo  bigint,                       -- lowest price she accepts; null = 80% of sell price
  max_price_kobo  bigint,                       -- null = 120% of sell price
  avg_cost_kobo   bigint not null default 0,    -- cache of the moving average; rebuilt from stock_moves
  reorder_level   int not null default 0,
  countable       boolean not null default true -- can a shelf photo count it?
);

create table if not exists contacts (
  id        text primary key default gen_random_uuid()::text,
  name      text not null,
  aliases   text[] not null default '{}',
  phone     text unique,
  ig_handle text,
  kind      text not null default 'customer' check (kind in ('customer','supplier')),
  last_inbound_at timestamptz                  -- last message from them; WhatsApp's 24-hour window
);
alter table contacts add column if not exists last_inbound_at timestamptz;

create table if not exists reminders (
  id          text primary key default gen_random_uuid()::text,
  created_at  timestamptz not null default now(),
  contact_id  text not null references contacts(id),
  owed_kobo   bigint not null,
  mode        text not null check (mode in ('session','template')),  -- free text inside 24h, template outside
  body        text not null,                    -- exactly what the customer sees
  status      text not null default 'draft' check (status in ('draft','sent','skipped','failed')),
  error       text
);

create table if not exists evidence (
  id              text primary key default gen_random_uuid()::text,
  created_at      timestamptz not null default now(),
  channel         text not null,                -- 'whatsapp', 'instagram', 'app'
  sender          text,
  media_url       text,
  original_text   text,                         -- transcript or typed text, exactly as received
  normalized_text text,                         -- after amount normalisation
  extracted_json  jsonb,
  model           text,
  prompt_version  text
);

create table if not exists journal_entries (
  id          text primary key default gen_random_uuid()::text,
  seq         bigserial unique,                 -- sync cursor for the phone
  entry_date  date not null default current_date,
  kind        text not null,                    -- 'sale','purchase','payment_in','payment_out','expense','drawing','stock_loss','reversal'
  memo        text not null default '',
  status      text not null default 'committed' check (status in ('committed','reversed')),
  reverses_id text references journal_entries(id),
  evidence_id text references evidence(id),
  contact_id  text references contacts(id),
  due_date    date,                             -- for credit sales and supplier credit
  created_by  text not null,                    -- 'agent:auto', 'agent:approved', 'app'
  created_at  timestamptz not null default now()
);

create table if not exists journal_lines (
  id          bigserial primary key,
  entry_id    text not null references journal_entries(id),
  account     text not null references accounts(code),
  debit_kobo  bigint not null default 0,
  credit_kobo bigint not null default 0,
  product_id  text references products(id),
  contact_id  text references contacts(id),
  qty         int,
  constraint one_side check (
    (debit_kobo > 0 and credit_kobo = 0) or (credit_kobo > 0 and debit_kobo = 0)
  )
);
create index if not exists journal_lines_entry on journal_lines(entry_id);
create index if not exists journal_lines_contact on journal_lines(contact_id, account);

create table if not exists stock_moves (
  id              bigserial primary key,
  entry_id        text not null references journal_entries(id),
  product_id      text not null references products(id),
  qty             int not null check (qty <> 0),  -- + in, - out
  unit_cost_kobo  bigint not null check (unit_cost_kobo >= 0)
);
create index if not exists stock_moves_product on stock_moves(product_id);

create table if not exists orders (
  id          text primary key default gen_random_uuid()::text,
  contact_id  text not null references contacts(id),
  items       jsonb not null,                   -- [{product_id, qty, unit_price_kobo}]
  total_kobo  bigint not null,
  status      text not null default 'open' check (status in ('open','claimed_paid','paid','cancelled','expired')),
  expires_at  timestamptz not null default now() + interval '24 hours',
  claim_evidence_id text references evidence(id),
  entry_id    text references journal_entries(id),
  created_at  timestamptz not null default now()
);

create table if not exists approvals (
  id          text primary key default gen_random_uuid()::text,
  kind        text not null,                    -- 'proposal', 'order_paid', 'post', 'reminder', 'stock_gap'
  payload     jsonb not null,                   -- the exact proposal that will be committed on Yes
  question    text not null,
  options     jsonb not null default '[]',
  status      text not null default 'pending' check (status in ('pending','approved','rejected','expired')),
  answer      text,
  answered_at timestamptz,
  created_at  timestamptz not null default now()
);

create table if not exists posts (
  id             text primary key default gen_random_uuid()::text,
  created_at     timestamptz not null default now(),
  product_id     text references products(id),
  reason         text not null default '',        -- why the books picked this product
  caption        text not null,
  caption_source text not null default 'model',   -- 'model' | 'template'
  asset_url      text,                            -- public URL of the flyer
  asset_path     text,
  cutout_path    text,                            -- kept so a "Change" can re-compose
  background_path text,
  tools          jsonb not null default '{}',     -- which cutter / background model made it
  status         text not null default 'draft' check (status in ('draft','published','skipped','failed')),
  platform_ids   jsonb,
  error          text
);

create table if not exists agent_runs (
  id            bigserial primary key,
  created_at    timestamptz not null default now(),
  trigger       text not null,
  persona       text not null,
  evidence_id   text references evidence(id),
  tool_calls    jsonb not null default '[]',
  reply         jsonb,
  tokens        int,
  latency_ms    int,
  error         text
);

-- Double entry: every entry balances. Checked at COMMIT, after all its lines exist.
create or replace function check_entry_balanced() returns trigger
language plpgsql as $$
declare
  eid text := coalesce(new.entry_id, old.entry_id);
  d bigint; c bigint;
begin
  select coalesce(sum(debit_kobo), 0), coalesce(sum(credit_kobo), 0)
    into d, c from journal_lines where entry_id = eid;
  if d <> c then
    raise exception 'entry % unbalanced: debits % <> credits %', eid, d, c;
  end if;
  return null;
end $$;

drop trigger if exists journal_balanced on journal_lines;
create constraint trigger journal_balanced
  after insert or update or delete on journal_lines
  deferrable initially deferred
  for each row execute function check_entry_balanced();

-- An entry with no lines is also unbalanced in spirit: reject it at commit.
create or replace function check_entry_has_lines() returns trigger
language plpgsql as $$
begin
  if not exists (select 1 from journal_lines where entry_id = new.id) then
    raise exception 'entry % has no lines', new.id;
  end if;
  return null;
end $$;

drop trigger if exists journal_has_lines on journal_entries;
create constraint trigger journal_has_lines
  after insert on journal_entries
  deferrable initially deferred
  for each row execute function check_entry_has_lines();

-- Append-only: corrections are reversing entries, never edits.
create or replace function forbid_change() returns trigger
language plpgsql as $$
begin
  raise exception '% is append-only; post a reversing entry', tg_table_name;
end $$;

drop trigger if exists journal_lines_append_only on journal_lines;
create trigger journal_lines_append_only
  before update or delete on journal_lines
  for each row execute function forbid_change();

drop trigger if exists stock_moves_append_only on stock_moves;
create trigger stock_moves_append_only
  before update or delete on stock_moves
  for each row execute function forbid_change();

-- On an entry, only the status may change, and only committed -> reversed.
create or replace function guard_entry_update() returns trigger
language plpgsql as $$
begin
  if (to_jsonb(new) - 'status') <> (to_jsonb(old) - 'status')
     or not (old.status = 'committed' and new.status = 'reversed') then
    raise exception 'journal entry % can only go from committed to reversed', old.id;
  end if;
  return new;
end $$;

drop trigger if exists journal_entries_guard on journal_entries;
create trigger journal_entries_guard
  before update on journal_entries
  for each row execute function guard_entry_update();

drop trigger if exists journal_entries_no_delete on journal_entries;
create trigger journal_entries_no_delete
  before delete on journal_entries
  for each row execute function forbid_change();

-- Stock is never stored: it is the sum of moves.
create or replace view stock_on_hand as
  select p.id as product_id, coalesce(sum(m.qty), 0)::int as qty
  from products p left join stock_moves m on m.product_id = p.id
  group by p.id;
