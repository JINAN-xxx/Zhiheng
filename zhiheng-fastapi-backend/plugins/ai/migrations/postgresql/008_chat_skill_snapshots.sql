alter table ai_chat_message
  add column if not exists skill_snapshots jsonb not null default '[]'::jsonb;

