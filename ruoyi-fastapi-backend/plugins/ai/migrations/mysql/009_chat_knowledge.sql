alter table ai_chat_message add column knowledge_base_ids json null after skill_snapshots;
update ai_chat_message set knowledge_base_ids = json_array() where knowledge_base_ids is null;
alter table ai_chat_message modify column knowledge_base_ids json not null;
