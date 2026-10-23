alter table ai_chat_message
  add column if not exists knowledge_base_ids jsonb not null default '[]'::jsonb;
